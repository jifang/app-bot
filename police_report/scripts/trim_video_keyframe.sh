#!/usr/bin/env bash
# trim_video_keyframe.sh — trim a video between START and END by cutting
# on the closest keyframe at or before START, then stream-copying up to
# END. Output strips audio and avoids re-encoding.
#
# With -a/--accurate the cut starts exactly at START instead: the video is
# re-encoded with x265 (CRF 24) so no keyframe alignment is needed.
#
# START and END accept seconds (12.5), MM:SS (1:23), or HH:MM:SS (1:02:03).
#
# Examples:
#   trim_video_keyframe.sh clip.mp4 00:01:23 00:02:45 out.mp4
#   trim_video_keyframe.sh clip.mp4 12.5 45 out.mp4
#   trim_video_keyframe.sh --accurate clip.mp4 00:01:23.4 00:02:45 out.mp4
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") [-a|--accurate] INPUT START END OUTPUT

  Trim INPUT to [START, END], cutting on the nearest keyframe at or before
  START and copying up to END. Audio is dropped; the video is not re-encoded.

Options:
  -a, --accurate  start exactly at START: re-encode video with x265, CRF 24
                  (slower; needed because stream copy can only cut on keyframes)

Arguments:
  INPUT   path to source video
  START   cut start time (seconds, MM:SS, or HH:MM:SS)
  END     cut end time   (seconds, MM:SS, or HH:MM:SS)
  OUTPUT  destination path

Examples:
  $(basename "$0") clip.mp4 00:01:23 00:02:45 out.mp4
  $(basename "$0") clip.mp4 12.5        45      out.mp4
  $(basename "$0") --accurate clip.mp4 12.5 45 out.mp4
EOF
  exit 2
}

to_seconds() {
  local value=$1
  local first second third
  local -a parts

  IFS=: read -r -a parts <<< "$value"
  case ${#parts[@]} in
    1)
      first=0
      second=0
      third=${parts[0]}
      ;;
    2)
      first=0
      second=${parts[0]}
      third=${parts[1]}
      ;;
    3)
      first=${parts[0]}
      second=${parts[1]}
      third=${parts[2]}
      ;;
    *)
      return 1
      ;;
  esac

  [[ $first =~ ^[0-9]+$ ]] || return 1
  [[ $second =~ ^[0-9]+$ ]] || return 1
  [[ $third =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1

  awk -v h="$first" -v m="$second" -v s="$third" 'BEGIN {
    if (m >= 60 || s >= 60) exit 1
    printf "%.9f", h * 3600 + m * 60 + s
  }'
}

accurate=0
args=()
for arg in "$@"; do
  case $arg in
    -a|--accurate) accurate=1 ;;
    -h|--help) usage ;;
    *) args+=("$arg") ;;
  esac
done
set -- ${args[@]+"${args[@]}"}

[[ $# -eq 4 ]] || usage

input=$1
start_arg=$2
end_arg=$3
output=$4

[[ -f $input ]] || { printf 'Input not found: %s\n' "$input" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { printf 'ffmpeg is required.\n' >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { printf 'ffprobe is required.\n' >&2; exit 1; }

start=$(to_seconds "$start_arg") || { printf 'Invalid start time: %s\n' "$start_arg" >&2; exit 1; }
end=$(to_seconds "$end_arg") || { printf 'Invalid end time: %s\n' "$end_arg" >&2; exit 1; }

awk -v start="$start" -v end="$end" 'BEGIN { exit !(end > start) }' || {
  printf 'END must be later than START.\n' >&2
  exit 1
}

if (( accurate )); then
  duration=$(awk -v start="$start" -v end="$end" 'BEGIN { printf "%.9f", end - start }')
  printf 'Accurate mode: re-encoding with x265 CRF 24 from %s seconds\n' "$start"
  ffmpeg -hide_banner -y \
    -ss "$start" \
    -i "$input" \
    -t "$duration" \
    -map 0:v:0 \
    -c:v libx265 -crf 24 -preset medium -tag:v hvc1 \
    -an \
    -avoid_negative_ts make_zero \
    "$output"
  exit 0
fi

keyframe=$(
  ffprobe -v error \
    -select_streams v:0 \
    -skip_frame nokey \
    -show_frames \
    -show_entries frame=best_effort_timestamp_time \
    -of csv=p=0 \
    "$input" |
  awk -F, -v target="$start" '
    $1 ~ /^-?[0-9]+([.][0-9]+)?$/ && $1 + 0 <= target + 0.0000005 {
      selected = $1
    }
    END {
      if (selected != "") print selected
    }
  '
)

[[ -n $keyframe ]] || {
  printf 'No keyframe exists at or before %s.\n' "$start_arg" >&2
  exit 1
}

duration=$(awk -v start="$keyframe" -v end="$end" 'BEGIN {
  duration = end - start
  if (duration <= 0) exit 1
  printf "%.9f", duration
}') || {
  printf 'The selected keyframe is not before END.\n' >&2
  exit 1
}

printf 'Requested start: %s; using keyframe: %s seconds\n' "$start_arg" "$keyframe"

ffmpeg -hide_banner -y \
  -ss "$keyframe" \
  -i "$input" \
  -t "$duration" \
  -map 0:v:0 \
  -c:v copy \
  -an \
  -avoid_negative_ts make_zero \
  "$output"
