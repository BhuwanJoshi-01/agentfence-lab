#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Turn the recorded demo (test-results/demo-video/*.webm) into docs/images/demo.gif.
set -euo pipefail
cd "$(dirname "$0")/.."
video=$(ls -t test-results/demo-video/*.webm | head -1)
out=../docs/images/demo.gif
filters="fps=8,scale=880:-1:flags=lanczos"
ffmpeg -loglevel error -y -i "$video" -vf "$filters,palettegen=max_colors=96" /tmp/agentfence-demo-palette.png
ffmpeg -loglevel error -y -i "$video" -i /tmp/agentfence-demo-palette.png \
  -lavfi "$filters [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4" "$out"
rm -f /tmp/agentfence-demo-palette.png
ls -l "$out"
