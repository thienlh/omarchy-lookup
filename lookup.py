#!/usr/bin/env python3
import gzip
import os
import re
import sqlite3
import struct
import sys
import tarfile
import tempfile
import unicodedata
import urllib.request

APP = "omarchy-lookup"
DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
APP_DIR = os.path.join(DATA_HOME, APP)
DATA_DIR = os.path.join(APP_DIR, "data")
DB = os.path.join(APP_DIR, "index.db")

STARDICT_EXTS = (".ifo", ".idx", ".dict.dz")

# The dictionaries are downloaded rather than shipped: they are large and their
# redistribution terms are not stated by either upstream.
DICTS = [
    {
        "name": "Anh-Việt",
        "base": "en-vi/star_anhviet",
        "url": "https://raw.githubusercontent.com/dynamotn/stardict-vi/master/en-vi/star_anhviet",
    },
    {
        "name": "Việt-Anh",
        "base": "vi-en/star_vietanh",
        "url": "https://raw.githubusercontent.com/dynamotn/stardict-vi/master/vi-en/star_vietanh",
    },
    {
        "name": "Anh-Nhật",
        "base": "en-ja/ej-gene95",
        "tarball": "http://download.huzheng.org/ja/stardict-ej-gene95-2.4.2.tar.bz2",
    },
]


def normalize(s):
    s = unicodedata.normalize("NFD", s.strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("đ", "d")


def read_stardict(base):
    with gzip.open(base + ".dict.dz", "rb") as fh:
        body = fh.read()
    with open(base + ".idx", "rb") as fh:
        idx = fh.read()
    i, n = 0, len(idx)
    while i < n:
        j = idx.find(b"\0", i)
        if j < 0 or j + 9 > n:  # some indexes carry a stray trailing byte
            break
        word = idx[i:j].decode("utf-8", "replace")
        off, size = struct.unpack(">II", idx[j + 1 : j + 9])
        yield word, body[off : off + size].decode("utf-8", "replace")
        i = j + 9


def fetch():
    for dictionary in DICTS:
        target = os.path.join(DATA_DIR, dictionary["base"])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if all(os.path.exists(target + ext) for ext in STARDICT_EXTS):
            print(f"have {dictionary['name']}", file=sys.stderr)
            continue
        print(f"downloading {dictionary['name']}", file=sys.stderr)
        if "tarball" in dictionary:
            fetch_tarball(dictionary["tarball"], target)
        else:
            for ext in STARDICT_EXTS:
                urllib.request.urlretrieve(dictionary["url"] + ext, target + ext)


def fetch_tarball(url, target):
    wanted = {os.path.basename(target) + ext: target + ext for ext in STARDICT_EXTS}
    with tempfile.NamedTemporaryFile(suffix=".tar.bz2") as tmp:
        urllib.request.urlretrieve(url, tmp.name)
        with tarfile.open(tmp.name, "r:bz2") as tar:
            for member in tar.getmembers():
                # Extract by basename so a hostile path in the archive cannot escape.
                out = wanted.get(os.path.basename(member.name))
                if not member.isfile() or not out:
                    continue
                with tar.extractfile(member) as src, open(out, "wb") as dst:
                    dst.write(src.read())
    missing = [name for name, path in wanted.items() if not os.path.exists(path)]
    if missing:
        sys.exit(f"archive at {url} did not contain {', '.join(missing)}")


def setup():
    fetch()
    build()


def build():
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = DB + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE entries(dict TEXT, word TEXT, lword TEXT, norm TEXT, body TEXT)")
    for dictionary in DICTS:
        base = os.path.join(DATA_DIR, dictionary["base"])
        if not os.path.exists(base + ".idx"):
            sys.exit(f"missing dictionary data at {base} — run: {sys.argv[0]} fetch")
        name = dictionary["name"]
        rows = ((name, w, w.lower(), normalize(w), b) for w, b in read_stardict(base))
        con.executemany("INSERT INTO entries VALUES(?,?,?,?,?)", rows)
        print(f"indexed {name}", file=sys.stderr)
    con.execute("CREATE INDEX i_lword ON entries(lword)")
    con.execute("CREATE INDEX i_norm ON entries(norm)")
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, DB)
    print(f"built {DB}", file=sys.stderr)


# Tried in order, so the most specific ending wins: studies -> study, not studie.
INFLECTIONS = [
    ("ies", ["y"]),
    ("ves", ["f", "fe"]),
    ("es", ["", "e"]),
    ("s", [""]),
    ("ing", ["", "e"]),
    ("ed", ["", "e"]),
    ("iest", ["y"]),
    ("ier", ["y"]),
    ("est", ["", "e"]),
    ("er", ["", "e"]),
    ("ly", ["", "e"]),
]


def base_forms(word):
    w = word.lower()
    forms = []
    for suffix, replacements in INFLECTIONS:
        stem = w[: -len(suffix)]
        if not w.endswith(suffix) or len(stem) < 2:
            continue
        doubled = stem[-1] == stem[-2] and stem[-1] not in "aeiou"
        for rep in replacements:
            forms.append(stem + rep)
            if doubled:  # stopped -> stop, running -> run
                forms.append(stem[:-1] + rep)
    return [f for f in dict.fromkeys(forms) if len(f) > 1 and f != w]


def lookup(con, form):
    for sql, arg in (
        ("SELECT dict, word, body FROM entries WHERE lword=?", form.lower()),
        ("SELECT dict, word, body FROM entries WHERE norm=?", normalize(form)),
    ):
        rows = con.execute(sql, (arg,)).fetchall()
        if rows:
            return rows
    return []


def search(con, query):
    q = query.strip()
    rows = lookup(con, q)
    if rows:
        return rows, [], q
    for form in base_forms(q):
        rows = lookup(con, form)
        if rows:
            return rows, [], form
    suggestions = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT word FROM entries WHERE norm LIKE ? ORDER BY length(word) LIMIT 15",
            (normalize(q) + "%",),
        )
    ]
    return [], suggestions, q


# A selection often drags along quotes or sentence punctuation.
QUERY_JUNK = " \t\"'“”‘’.,;:!?()[]{}<>…–—-"


def clean_query(text):
    return re.sub(r"\s+", " ", text).strip(QUERY_JUNK)[:80]


BOLD, DIM, CYAN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[36m", "\033[33m", "\033[0m"


def render(rows, suggestions, query, matched, color):
    b, d, c, y, r = (BOLD, DIM, CYAN, YELLOW, RESET) if color else ("",) * 5
    out = []
    if rows and matched.lower() != query.lower():
        out.append(f"{d}{query} → {matched}{r}")
        out.append("")
    if not rows:
        out.append(f"{b}{query}{r} — không tìm thấy / not found")
        if suggestions:
            out.append("")
            out.append(f"{d}Có phải bạn tìm / did you mean:{r}")
            out.extend(f"  {c}{s}{r}" for s in suggestions)
        return "\n".join(out)
    for dict_name, word, body in rows:
        ipa, entry = "", []
        for line in body.splitlines():
            s = line.rstrip()
            if s.startswith("@"):
                m = re.search(r"/([^/]+)/", s)
                if m:
                    ipa = m.group(1)
            elif s.startswith("*"):
                entry.append(f"{y}{s.lstrip('* ')}{r}")
            elif s.startswith("-"):
                entry.append(f"  {b}{s.lstrip('- ')}{r}")
            elif s.startswith("="):
                example, sep, trans = s[1:].partition("+")
                tail = f" {c}→{r} {trans.strip()}" if sep else ""
                entry.append(f"    {d}{example.strip()}{r}{tail}")
            elif s:
                entry.append(f"  {s}")
        head = f"{b}{c}{word}{r}"
        if ipa:
            head += f"  {d}/{ipa}/{r}"
        out.append(f"{head}  {d}[{dict_name}]{r}")
        out.extend(entry)
        out.append("")
    return "\n".join(out).rstrip()


def main():
    args = [a for a in sys.argv[1:] if a != "--color"]
    color = "--color" in sys.argv[1:]
    commands = {"fetch": fetch, "build": build, "setup": setup}
    if args and args[0] in commands:
        commands[args[0]]()
        return
    if not os.path.exists(DB):
        # Printed on stdout so the popup's pager shows it rather than swallowing it.
        me = os.path.basename(sys.argv[0])
        print(
            f"Chưa có dữ liệu từ điển / no dictionary index yet.\n\n"
            f"Chạy lệnh này rồi thử lại / run this, then try again:\n\n    {me} setup"
        )
        return
    query = clean_query(" ".join(args))
    if not query:
        try:
            query = clean_query(input("Tra từ / look up: "))
        except (EOFError, KeyboardInterrupt):
            return
    if not query:
        return
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows, suggestions, matched = search(con, query)
    print(render(rows, suggestions, query, matched, color))


if __name__ == "__main__":
    main()
