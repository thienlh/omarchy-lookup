# omarchy-lookup

macOS-style **Lookup** for [Omarchy](https://omarchy.org). Select a word anywhere, press a
key, and the definition opens in a native floating TUI popup that follows your theme.

Three offline dictionaries, searched together:

| Dictionary | Entries | Direction |
| --- | --- | --- |
| Anh-Việt | 387,517 | English → Vietnamese |
| Việt-Anh | 42,252 | Vietnamese → English |
| Anh-Nhật | 57,369 | English → Japanese |

## Install

From the AUR:

```bash
omarchy pkg aur add omarchy-lookup   # or: yay -S omarchy-lookup
lookup setup                         # downloads dictionaries, builds the index
```

Or from a clone:

```bash
git clone https://github.com/thienlh/omarchy-lookup.git
cd omarchy-lookup
./install.sh
```

That installs `lookup` and `lookup-popup` into `~/.local/bin`, adds a launcher entry,
downloads the dictionaries (~12 MB) and builds a local index in
`~/.local/share/omarchy-lookup` (~105 MB, takes a few seconds).

Then bind a key in `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + D", "Dictionary", os.getenv("HOME") .. "/.local/bin/lookup-popup")
```

## Use

- **Select a word** anywhere and press your key — the definition pops up. `esc` closes it,
  `/` searches inside the entry.
- **Launch "Lookup"** from the app launcher with nothing selected and it prompts you to
  type a word.
- **From a terminal:** `lookup serendipity`

## What it handles

- **Diacritics are optional** — `nghien cuu` finds *nghiên cứu*, so you can look up
  Vietnamese without switching input methods.
- **Inflected forms** — `characters` → *character*, `studies` → *study*, `stopped` → *stop*.
  Exact matches always win, and the popup shows `characters → character` so you know why
  the headword differs.
- **Punctuation** from a sloppy selection (`"serendipity,"`) is stripped.
- **Near misses** fall back to prefix suggestions.

## Requirements

- Omarchy (uses `omarchy-launch-tui`, `gum`, and the `TUI.float` window treatment)
- `wl-clipboard`, `python3` — no third-party Python packages, stdlib only
- Japanese needs a CJK font, or it renders as tofu boxes:

  ```bash
  omarchy pkg add adobe-source-han-sans-jp-fonts
  ```

## Dictionary data

The dictionaries are **downloaded at install time, not redistributed here**. The Vietnamese
pair comes from the [Open Vietnamese Dictionary Project](https://github.com/dynamotn/stardict-vi)
and the English–Japanese one is [EJ-GENE95](http://download.huzheng.org/ja/), both in
StarDict format. Neither upstream states redistribution terms, so check them yourself before
mirroring the data.

## Development

```bash
python3 -m unittest test_lookup   # 47 tests, no network, no fixtures
lookup setup                      # fetch + build, the first-run step
lookup fetch                      # re-download dictionaries only
lookup build                      # rebuild the index only
```

## Uninstall

```bash
rm -f ~/.local/bin/lookup ~/.local/bin/lookup-popup
rm -f ~/.local/share/applications/omarchy-lookup.desktop
rm -rf ~/.local/share/omarchy-lookup
```

## License

MIT for the code in this repository. The dictionary data is not covered — see above.
