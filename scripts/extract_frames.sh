#!/usr/bin/env bash
# Extract still frames from videos for the piece dataset.
#
# Usage: scripts/extract_frames.sh <video_dir> <out_dir> [seconds_between_frames]
# Example: scripts/extract_frames.sh data/raw_data data/frames 2
#
# - takes 1 frame every N seconds (default 2)
# - drops near-duplicate frames (ffmpeg mpdecimate)
# - downscales anything wider than 2560 px (4K -> 2560x1440), keeps smaller videos as is
# Needs only ffmpeg. Undo: delete <out_dir>.
set -euo pipefail

src="${1:?video dir}"
out="${2:?output dir}"
step="${3:-2}"

mkdir -p "$out"
shopt -s nullglob nocaseglob
for video in "$src"/*.mp4 "$src"/*.mov; do
    name="$(basename "${video%.*}")"
    echo "== $name"
    ffmpeg -hide_banner -loglevel error -nostdin -i "$video" \
        -vf "fps=1/${step},mpdecimate,scale='min(2560,iw)':-2" \
        -fps_mode vfr -q:v 2 "$out/${name}_%04d.jpg"
done
echo "Total frames in $out: $(ls "$out" | wc -l)"
