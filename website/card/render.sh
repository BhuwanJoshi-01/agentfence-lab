#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Render the social card (GitHub's recommended 1280x640) with headless Chrome.
set -euo pipefail
cd "$(dirname "$0")"
chrome=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
"$chrome" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
  --window-size=1280,640 --screenshot="$PWD/../social-card.png" "file://$PWD/social-card.html" 2>/dev/null
cp ../social-card.png ../../docs/images/social-card.png
python3 -c "import struct; print('social-card.png', struct.unpack('>II', open('../social-card.png','rb').read(24)[16:24]))"
