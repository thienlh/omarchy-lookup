#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
bin="${XDG_BIN_HOME:-$HOME/.local/bin}"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

install -Dm755 "$here/lookup.py" "$bin/lookup"
install -Dm755 "$here/lookup-popup" "$bin/lookup-popup"
sed "s|@BIN@|$bin|g" "$here/omarchy-lookup.desktop" |
  install -Dm644 /dev/stdin "$apps/omarchy-lookup.desktop"

"$bin/lookup" fetch
"$bin/lookup" build

case ":$PATH:" in
*":$bin:"*) ;;
*) echo "warning: $bin is not on your PATH" >&2 ;;
esac

cat <<EOF

Installed. To bind a key, add this to ~/.config/hypr/bindings.lua:

  o.bind("SUPER + ALT + D", "Dictionary", "$bin/lookup-popup")

Select a word anywhere and press the key, or run: lookup <word>
Japanese needs a CJK font: omarchy pkg add adobe-source-han-sans-jp-fonts
EOF
