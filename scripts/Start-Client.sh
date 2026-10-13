#!/usr/bin/env bash
set -euo pipefail
# Starts the companion as the headset PC's client: from the source folder (this script is in scripts/) or from a
# CMake install, where it sits beside launcher/.
here="$(dirname -- "$(readlink -f -- "$0")")"
if [ -f "$here/launcher/app.py" ]; then cd -- "$here"; else cd -- "$here/.."; fi
exec python3 launcher/app.py --role client
