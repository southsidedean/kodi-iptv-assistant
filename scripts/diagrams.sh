#!/bin/sh
# Render the docs' diagrams: docs/diagrams/<name>.mmd (Mermaid) becomes
# images/diagrams/<name>-light.svg and <name>-dark.svg, which the docs show with a
# <picture> tag (GitHub picks the one for the reader's theme). Images instead of Mermaid
# blocks: GitHub puts pan and zoom buttons on every Mermaid diagram, and an image works
# the same everywhere, a blog post included.
#
# Needs mermaid-cli (npm install -g @mermaid-js/mermaid-cli), which uses Chrome. If it
# can't find one, point it at yours: PUPPETEER_CONFIG=puppeteer.json, holding
# {"executablePath": "/path/to/chrome"}.
#
# Each image records its source's SHA-256; tests/test_docs.py fails when they differ.
# Run this after editing a diagram: scripts/diagrams.sh
set -eu
cd "$(dirname "$0")/.."

MMDC=${MMDC:-mmdc}
config=$(mktemp)
trap 'rm -f "$config"' EXIT
# Labels as SVG text, not HTML: some viewers drop HTML inside an SVG shown as an image.
printf '{"htmlLabels": false, "flowchart": {"htmlLabels": false}}\n' >"$config"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

mkdir -p images/diagrams
for src in docs/diagrams/*.mmd; do
    name=$(basename "$src" .mmd)
    hash=$(sha256 "$src")
    for theme in light dark; do
        out="images/diagrams/$name-$theme.svg"
        mermaid_theme=default
        [ "$theme" = dark ] && mermaid_theme=dark
        set -- -q -t "$mermaid_theme" -b transparent -c "$config" -i "$src" -o "$out"
        if [ -n "${PUPPETEER_CONFIG:-}" ]; then set -- "$@" -p "$PUPPETEER_CONFIG"; fi
        "$MMDC" "$@"
        # Mermaid sizes the drawing to "100%" of the page; an image needs its own size.
        box=$(sed -n '1s/^<svg[^>]*viewBox="\([^"]*\)".*/\1/p' "$out")  # the drawing's own, not a marker's
        width=$(echo "$box" | awk '{printf "%d", $3 + 0.5}')
        height=$(echo "$box" | awk '{printf "%d", $4 + 0.5}')
        sed "s/<svg id=\"my-svg\" width=\"100%\"/<svg id=\"my-svg\" width=\"$width\" height=\"$height\"/" "$out" >"$out.tmp"
        mv "$out.tmp" "$out"
        printf '\n<!-- kia diagram: %s, source sha256 %s -->\n' "$src" "$hash" >>"$out"
        echo "$out"
    done
done
