#!/usr/bin/env bash
# Download a Drive file shared as "anyone with the link" by its file ID.
# usage: ./fetch.sh <file_id> <output_path>
set -euo pipefail
id="$1"; out="$2"
mkdir -p "$(dirname "$out")"
curl -fL --retry 3 -o "$out" "https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t"
if head -c 200 "$out" | grep -qi "<html"; then
  echo "got a web page instead of the video. Is JCube Raw shared as 'Anyone with the link'?" >&2
  exit 1
fi
ls -la "$out"
