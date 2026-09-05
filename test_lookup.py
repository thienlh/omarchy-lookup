#!/usr/bin/env python3
import contextlib
import gzip
import io
import os
import pathlib
import sqlite3
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lookup as app


def write_stardict(directory, name, entries, trailing=b""):
    base = os.path.join(directory, name)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    body, idx = b"", b""
    for word, definition in entries:
        blob = definition.encode()
        idx += word.encode() + b"\0" + struct.pack(">II", len(body), len(blob))
        body += blob
    with gzip.open(base + ".dict.dz", "wb") as fh:
        fh.write(body)
    with open(base + ".idx", "wb") as fh:
        fh.write(idx + trailing)
    with open(base + ".ifo", "w") as fh:
        fh.write("StarDict's dict ifo file\n")
    return base


def memory_db(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE entries(dict TEXT, word TEXT, lword TEXT, norm TEXT, body TEXT)")
    con.executemany(
        "INSERT INTO entries VALUES(?,?,?,?,?)",
        [(d, w, w.lower(), app.normalize(w), b) for d, w, b in rows],
    )
    return con


class TestNormalize(unittest.TestCase):
    def test_strips_vietnamese_tone_marks(self):
        self.assertEqual(app.normalize("nghiên cứu"), "nghien cuu")
        self.assertEqual(app.normalize("hạnh phúc"), "hanh phuc")

    def test_folds_d_with_stroke(self):
        self.assertEqual(app.normalize("đường"), "duong")

    def test_lowercases_and_trims(self):
        self.assertEqual(app.normalize("  Hạnh Phúc  "), "hanh phuc")

    def test_leaves_ascii_alone(self):
        self.assertEqual(app.normalize("Running"), "running")


class TestCleanQuery(unittest.TestCase):
    def test_strips_surrounding_punctuation(self):
        self.assertEqual(app.clean_query('"serendipity,"'), "serendipity")
        self.assertEqual(app.clean_query("(character)"), "character")
        self.assertEqual(app.clean_query("word."), "word")

    def test_collapses_whitespace(self):
        self.assertEqual(app.clean_query("  nghiên   cứu \n"), "nghiên cứu")

    def test_keeps_inner_punctuation(self):
        self.assertEqual(app.clean_query("well-known"), "well-known")

    def test_caps_length(self):
        self.assertEqual(len(app.clean_query("a" * 200)), 80)

    def test_punctuation_only_yields_empty(self):
        self.assertEqual(app.clean_query("..."), "")


class TestBaseForms(unittest.TestCase):
    def assertResolves(self, word, expected):
        self.assertIn(expected, app.base_forms(word))

    def test_regular_plurals(self):
        self.assertResolves("characters", "character")
        self.assertResolves("cars", "car")
        self.assertResolves("boxes", "box")

    def test_y_plurals(self):
        self.assertResolves("studies", "study")
        self.assertResolves("flies", "fly")

    def test_f_plurals(self):
        self.assertResolves("wolves", "wolf")
        self.assertResolves("knives", "knife")

    def test_past_and_progressive(self):
        self.assertResolves("watched", "watch")
        self.assertResolves("stopped", "stop")
        self.assertResolves("running", "run")
        self.assertResolves("making", "make")

    def test_comparatives(self):
        self.assertResolves("happier", "happy")
        self.assertResolves("quickest", "quick")

    def test_adverbs(self):
        self.assertResolves("quickly", "quick")

    def test_most_specific_rule_wins(self):
        self.assertEqual(app.base_forms("studies")[0], "study")
        self.assertEqual(app.base_forms("wolves")[0], "wolf")

    def test_never_returns_the_word_itself(self):
        for word in ("characters", "running", "happier", "less"):
            self.assertNotIn(word, app.base_forms(word))

    def test_ignores_words_too_short_to_reduce(self):
        self.assertEqual(app.base_forms("is"), [])
        self.assertEqual(app.base_forms("as"), [])

    def test_no_candidate_is_a_single_character(self):
        for word in ("ties", "ads", "ing"):
            self.assertTrue(all(len(f) > 1 for f in app.base_forms(word)))


class TestReadStardict(unittest.TestCase):
    def test_round_trips_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_stardict(tmp, "d/d", [("run", "chạy"), ("walk", "đi bộ")])
            self.assertEqual(list(app.read_stardict(base)), [("run", "chạy"), ("walk", "đi bộ")])

    def test_handles_non_ascii_headwords(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_stardict(tmp, "d/d", [("nghiên cứu", "to study"), ("辞書", "dictionary")])
            self.assertEqual([w for w, _ in app.read_stardict(base)], ["nghiên cứu", "辞書"])

    def test_tolerates_stray_trailing_byte(self):
        # jmdict-en-ja ends with a newline after the last entry; that used to crash the build.
        with tempfile.TemporaryDirectory() as tmp:
            base = write_stardict(tmp, "d/d", [("run", "chạy")], trailing=b"\n")
            self.assertEqual(list(app.read_stardict(base)), [("run", "chạy")])


class TestFetchTarball(unittest.TestCase):
    def make_tarball(self, directory, members):
        path = os.path.join(directory, "dict.tar.bz2")
        with tarfile.open(path, "w:bz2") as tar:
            for name, content in members.items():
                blob = content.encode()
                info = tarfile.TarInfo(name)
                info.size = len(blob)
                tar.addfile(info, io.BytesIO(blob))
        return pathlib.Path(path).as_uri()

    def test_extracts_the_three_stardict_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = self.make_tarball(
                tmp,
                {
                    "stardict-ej-2.4.2/ej.ifo": "ifo",
                    "stardict-ej-2.4.2/ej.idx": "idx",
                    "stardict-ej-2.4.2/ej.dict.dz": "dict",
                },
            )
            target = os.path.join(tmp, "out", "ej")
            os.makedirs(os.path.dirname(target))
            app.fetch_tarball(url, target)
            self.assertEqual(pathlib.Path(target + ".ifo").read_text(), "ifo")
            self.assertEqual(pathlib.Path(target + ".idx").read_text(), "idx")
            self.assertEqual(pathlib.Path(target + ".dict.dz").read_text(), "dict")

    def test_ignores_paths_that_escape_the_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = self.make_tarball(
                tmp,
                {
                    "../../escaped.txt": "evil",
                    "pkg/ej.ifo": "ifo",
                    "pkg/ej.idx": "idx",
                    "pkg/ej.dict.dz": "dict",
                },
            )
            target = os.path.join(tmp, "out", "ej")
            os.makedirs(os.path.dirname(target))
            app.fetch_tarball(url, target)
            self.assertFalse(os.path.exists(os.path.join(tmp, "escaped.txt")))
            self.assertFalse(os.path.exists(os.path.abspath(os.path.join(tmp, "..", "escaped.txt"))))
            self.assertTrue(os.path.exists(target + ".ifo"))

    def test_reports_an_incomplete_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = self.make_tarball(tmp, {"pkg/ej.ifo": "ifo"})
            target = os.path.join(tmp, "out", "ej")
            os.makedirs(os.path.dirname(target))
            with self.assertRaises(SystemExit):
                app.fetch_tarball(url, target)


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.con = memory_db(
            [
                ("Anh-Việt", "character", "@character\n- nhân vật"),
                ("Anh-Nhật", "character", "登場人物"),
                ("Anh-Việt", "characterise", "- mô tả"),
                ("Việt-Anh", "nghiên cứu", "- to study"),
                ("Anh-Việt", "less", "- ít hơn"),
            ]
        )
        self.addCleanup(self.con.close)

    def test_exact_match_reports_the_query_as_matched(self):
        rows, suggestions, matched = app.search(self.con, "character")
        self.assertEqual(matched, "character")
        self.assertEqual(suggestions, [])
        self.assertEqual(len(rows), 2)

    def test_returns_every_dictionary_holding_the_word(self):
        rows, _, _ = app.search(self.con, "character")
        self.assertEqual([r[0] for r in rows], ["Anh-Việt", "Anh-Nhật"])

    def test_matches_without_diacritics(self):
        rows, _, matched = app.search(self.con, "nghien cuu")
        self.assertEqual(rows[0][1], "nghiên cứu")
        self.assertEqual(matched, "nghien cuu")

    def test_falls_back_to_base_form_and_reports_it(self):
        rows, _, matched = app.search(self.con, "characters")
        self.assertEqual(matched, "character")
        self.assertEqual(len(rows), 2)

    def test_exact_hit_beats_inflection(self):
        # "less" must not be reduced to "les" when it is a headword itself.
        rows, _, matched = app.search(self.con, "less")
        self.assertEqual(matched, "less")
        self.assertEqual(rows[0][1], "less")

    def test_ignores_surrounding_whitespace(self):
        _, _, matched = app.search(self.con, "  character  ")
        self.assertEqual(matched, "character")

    def test_misses_return_prefix_suggestions(self):
        rows, suggestions, _ = app.search(self.con, "charac")
        self.assertEqual(rows, [])
        self.assertEqual(suggestions, ["character", "characterise"])

    def test_total_miss_returns_nothing(self):
        rows, suggestions, _ = app.search(self.con, "asdfqwer")
        self.assertEqual((rows, suggestions), ([], []))


class TestRender(unittest.TestCase):
    def render(self, rows, suggestions=(), query="x", matched=None):
        return app.render(rows, list(suggestions), query, matched or query, color=False)

    def test_not_found_message(self):
        out = self.render([], query="asdfqwer")
        self.assertIn("asdfqwer", out)
        self.assertIn("not found", out)

    def test_suggestions_are_listed(self):
        out = self.render([], suggestions=["character", "characterise"], query="charac")
        self.assertIn("did you mean", out)
        self.assertIn("characterise", out)

    def test_shows_hint_when_a_base_form_was_used(self):
        out = self.render([("Anh-Việt", "character", "- nhân vật")], query="characters", matched="character")
        self.assertIn("characters → character", out)

    def test_no_hint_on_a_direct_hit(self):
        out = self.render([("Anh-Việt", "character", "- nhân vật")], query="character")
        self.assertNotIn("→ character", out)

    def test_pronunciation_moves_into_the_header(self):
        out = self.render([("Anh-Việt", "run", "@run /rʌn/\n- chạy")])
        self.assertIn("run  /rʌn/  [Anh-Việt]", out)
        self.assertNotIn("@run", out)

    def test_examples_are_split_on_the_separator(self):
        out = self.render([("Anh-Việt", "run", "=at a run+đang chạy")])
        self.assertIn("at a run → đang chạy", out)

    def test_example_without_translation_still_renders(self):
        out = self.render([("Anh-Việt", "run", "=at a run")])
        self.assertIn("at a run", out)
        self.assertNotIn("→", out)

    def test_plain_body_is_kept(self):
        out = self.render([("Anh-Nhật", "dictionary", "辞典,辞書")])
        self.assertIn("辞典,辞書", out)

    def test_no_escape_codes_when_color_is_off(self):
        out = app.render(
            [("Anh-Việt", "run", "@run /rʌn/\n* danh từ\n- chạy\n=at a run+đang chạy")],
            [],
            "runs",
            "run",
            color=False,
        )
        self.assertNotIn("\033", out)

    def test_escape_codes_when_color_is_on(self):
        out = app.render([("Anh-Việt", "run", "- chạy")], [], "run", "run", color=True)
        self.assertIn("\033", out)


class TestBuild(unittest.TestCase):
    def test_builds_a_queryable_index_from_the_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_stardict(tmp, "en-vi/en", [("character", "- nhân vật")])
            write_stardict(tmp, "vi-en/vi", [("nghiên cứu", "- to study")])
            db = os.path.join(tmp, "index.db")
            dicts = [
                {"name": "Anh-Việt", "base": "en-vi/en"},
                {"name": "Việt-Anh", "base": "vi-en/vi"},
            ]
            with mock.patch.multiple(app, DB=db, DATA_DIR=tmp, APP_DIR=tmp, DICTS=dicts):
                with contextlib.redirect_stderr(io.StringIO()):
                    app.build()
                self.assertTrue(os.path.exists(db))
                con = sqlite3.connect(db)
                self.addCleanup(con.close)
                self.assertEqual(
                    con.execute("SELECT dict, word, norm FROM entries ORDER BY dict").fetchall(),
                    [
                        ("Anh-Việt", "character", "character"),
                        ("Việt-Anh", "nghiên cứu", "nghien cuu"),
                    ],
                )
                self.assertEqual(app.search(con, "characters")[2], "character")

    def test_missing_data_tells_the_user_to_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dicts = [{"name": "Anh-Việt", "base": "en-vi/en"}]
            with mock.patch.multiple(app, DB=os.path.join(tmp, "i.db"), DATA_DIR=tmp, APP_DIR=tmp, DICTS=dicts):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        app.build()
            self.assertIn("fetch", str(caught.exception))


class TestFirstRun(unittest.TestCase):
    def test_missing_index_prints_the_setup_hint_on_stdout(self):
        # stdout, not stderr: the popup pipes stdout into the pager and would
        # otherwise show an empty window on a fresh install.
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(app, "DB", os.path.join(tmp, "absent.db")):
                with mock.patch.object(sys, "argv", ["lookup", "character"]):
                    with contextlib.redirect_stdout(out):
                        app.main()
        self.assertIn("no dictionary index", out.getvalue())
        self.assertIn("lookup setup", out.getvalue())

    def test_setup_fetches_then_builds(self):
        calls = []
        with mock.patch.object(app, "fetch", lambda: calls.append("fetch")):
            with mock.patch.object(app, "build", lambda: calls.append("build")):
                app.setup()
        self.assertEqual(calls, ["fetch", "build"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
