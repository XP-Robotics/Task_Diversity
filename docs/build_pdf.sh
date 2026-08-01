#!/usr/bin/env bash
# Render DOCUMENTATION.md to Egocentric-Capture-QA-Documentation.pdf.
#
#   pandoc  DOCUMENTATION.md -> HTML fragment   (no pandoc default CSS)
#   wrapper adds pdf.css + a cover page
#   Chrome headless prints it to PDF
#
# Requires: pandoc, and a Chrome/Chromium binary on PATH.
set -euo pipefail

cd "$(dirname "$0")"

OUT="Egocentric-Capture-QA-Documentation.pdf"
FRAG="$(mktemp -t qadoc-frag-XXXXXX.html)"
PAGE="$(mktemp -t qadoc-page-XXXXXX.html)"
trap 'rm -f "$FRAG" "$PAGE"' EXIT

CHROME=""
for c in google-chrome chromium chromium-browser google-chrome-stable; do
  command -v "$c" >/dev/null 2>&1 && { CHROME="$c"; break; }
done
[ -n "$CHROME" ] || { echo "error: no Chrome/Chromium binary found on PATH" >&2; exit 1; }

pandoc DOCUMENTATION.md -f gfm -t html5 -o "$FRAG"

{
  printf '<!doctype html><html lang="en"><head><meta charset="utf-8">'
  printf '<title>Egocentric Capture QA — Technical Documentation</title><style>'
  cat pdf.css
  printf '</style></head><body>'
  cat "$FRAG"
  printf '</body></html>'
} > "$PAGE"

"$CHROME" --headless --disable-gpu --no-sandbox \
          --no-pdf-header-footer \
          --print-to-pdf="$OUT" "$PAGE" 2>/dev/null

echo "wrote $(pwd)/$OUT"
