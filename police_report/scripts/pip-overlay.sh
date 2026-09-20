#!/usr/bin/env bash
# Compose a PIP overlay: places <pip> in the top-left of <main>.
# PIP width = one-third of main width, height auto-derived to preserve aspect ratio.
#
# Usage: pip-overlay.sh <main.mp4> <pip.mp4> [output.mp4]
#   output defaults to <main>_PIP.mp4 in the same directory as <main>.

set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 <main.mp4> <pip.mp4> [output.mp4]" >&2
  echo "  PIP is overlaid at (0,0); width = main_w/3, height preserves aspect." >&2
  exit 2
fi

main="$1"
pip="$2"
if [[ $# -eq 3 ]]; then
  out="$3"
else
  ext="${main##*.}"
  out="${main%.*}_PIP.${ext}"
fi

main_w=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$main")
pip_w=$((main_w / 3))

ffmpeg -y -hide_banner -loglevel error \
  -i "$main" -i "$pip" \
  -filter_complex "[1:v]scale=${pip_w}:-2[pip];[0:v][pip]overlay=0:0[v]" \
  -map "[v]" -map "0:a?" \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
  -c:a aac -b:a 128k \
  -shortest -movflags +faststart \
  "$out"

echo "$out"
