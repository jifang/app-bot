#!/usr/bin/env bash
# speedup_section.sh — speed up the [START, END] section of a video by SPEED x
# and overlay a "N倍加速" label on the top-left during that section. The rest
# of the video keeps its original speed. The whole video is re-encoded
# (H.264 / yuv420p) to keep the cut points frame-accurate. The audio track is
# preserved: outside the section it is untouched, inside the section the audio
# is time-compressed to match the new video duration and silenced.
#
# START and END accept seconds (12.5), MM:SS (1:23), or HH:MM:SS (1:02:03).
# SPEED must be a positive number (e.g. 5 = 5x faster).
#
# Example:
#   speedup_section.sh clip.mp4 00:12 01:12 5 out.mp4
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") INPUT START END SPEED OUTPUT

  Speed up the [START, END] section of INPUT by SPEED x and overlay a
  "N倍加速" label on the top-left during that section. Audio is preserved;
  the sped-up region is time-compressed and silenced.

Arguments:
  INPUT   path to source video
  START   section start (seconds, MM:SS, or HH:MM:SS)
  END     section end   (seconds, MM:SS, or HH:MM:SS)
  SPEED   speedup multiplier (positive number, e.g. 5)
  OUTPUT  destination path

Example:
  $(basename "$0") clip.mp4 00:12 01:12 5 out.mp4
EOF
  exit 2
}

to_seconds() {
  local value=$1
  local first second third
  local -a parts

  IFS=: read -r -a parts <<< "$value"
  case ${#parts[@]} in
    1) first=0; second=0; third=${parts[0]} ;;
    2) first=0; second=${parts[0]}; third=${parts[1]} ;;
    3) first=${parts[0]}; second=${parts[1]}; third=${parts[2]} ;;
    *) return 1 ;;
  esac

  [[ $first =~ ^[0-9]+$ ]] || return 1
  [[ $second =~ ^[0-9]+$ ]] || return 1
  [[ $third =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1

  awk -v h="$first" -v m="$second" -v s="$third" 'BEGIN {
    if (m >= 60 || s >= 60) exit 1
    printf "%.9f", h * 3600 + m * 60 + s
  }'
}

[[ $# -eq 5 ]] || usage

input=$1
start_arg=$2
end_arg=$3
speed=$4
output=$5

[[ -f $input ]] || { printf 'Input not found: %s\n' "$input" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { printf 'ffmpeg is required.\n' >&2; exit 1; }

start=$(to_seconds "$start_arg") || { printf 'Invalid start time: %s\n' "$start_arg" >&2; exit 1; }
end=$(to_seconds "$end_arg")     || { printf 'Invalid end time: %s\n'   "$end_arg"   >&2; exit 1; }

awk -v s="$speed" 'BEGIN { exit !(s+0 > 0) }' || {
  printf 'SPEED must be a positive number: %s\n' "$speed" >&2; exit 1;
}
awk -v a="$start" -v b="$end" 'BEGIN { exit !(b > a) }' || {
  printf 'END must be later than START.\n' >&2; exit 1;
}

# The homebrew ffmpeg build commonly lacks drawtext/subtitles (no freetype/
# libass), so we render the label to a transparent PNG with ImageMagick and
# overlay it. Locate a CJK-capable font.
font_file=""
for candidate in \
    "/System/Library/Fonts/PingFang.ttc" \
    "/System/Library/Fonts/STHeiti Medium.ttc" \
    "/Library/Fonts/Arial Unicode.ttf" \
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc" \
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"; do
  if [[ -f $candidate ]]; then font_file=$candidate; break; fi
done
if [[ -z $font_file ]] && command -v fc-match >/dev/null 2>&1; then
  font_file=$(fc-match -f '%{file}' 'sans:lang=zh' 2>/dev/null || true)
fi
if [[ -z $font_file || ! -f $font_file ]]; then
  printf 'Could not find a CJK font for the overlay text.\n' >&2
  exit 1
fi
command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1 || {
  printf 'ImageMagick (magick/convert) is required to render the label.\n' >&2
  exit 1
}

label="${speed}倍加速"

# Render the label to a transparent PNG: white text on a semi-transparent black
# pill. PingFang is a TTC; index 0 is the regular face.
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
label_png="$tmpdir/label.png"

magick_bin=magick
command -v magick >/dev/null 2>&1 || magick_bin=convert

"$magick_bin" \
  -background 'rgba(0,0,0,0.55)' \
  -fill white \
  -font "$font_file" \
  -pointsize 48 \
  "label:${label}" \
  -bordercolor 'rgba(0,0,0,0.55)' \
  -border 10 \
  PNG32:"$label_png"

# PTS factor: output PTS = (input PTS - STARTPTS) / SPEED.
setpts_expr="(PTS-STARTPTS)/${speed}"

# Audio for the sped-up region is time-compressed with atempo to match the new
# video duration, then muted with volume=0. atempo accepts 0.5–100 (our speeds
# are within range). The pre/post segments keep their original audio.
ffmpeg -hide_banner -y \
  -i "$input" \
  -i "$label_png" \
  -filter_complex "\
[0:v]trim=0:${start},setpts=PTS-STARTPTS[v0];\
[0:v]trim=start=${start}:end=${end},setpts=${setpts_expr}[v1base];\
[1:v]format=rgba[lbl];\
[v1base][lbl]overlay=24:24:format=auto[v1];\
[0:v]trim=start=${end},setpts=PTS-STARTPTS[v2];\
[v0][v1][v2]concat=n=3:v=1:a=0[vout];\
[0:a]atrim=0:${start},asetpts=PTS-STARTPTS[a0];\
[0:a]atrim=start=${start}:end=${end},asetpts=PTS-STARTPTS,atempo=${speed},volume=0[a1];\
[0:a]atrim=start=${end},asetpts=PTS-STARTPTS[a2];\
[a0][a1][a2]concat=n=3:v=0:a=1[aout]" \
  -map "[vout]" -map "[aout]" \
  -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 128k \
  "$output"

printf 'Wrote %s (middle %s-%s sped up %sx, label: %s)\n' \
  "$output" "$start_arg" "$end_arg" "$speed" "$label"
