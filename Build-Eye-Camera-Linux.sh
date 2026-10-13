#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
command -v go >/dev/null || { echo 'Install Go 1.24.5 or later in your development container.'; exit 1; }
pkg-config --exists libusb-1.0 || { echo 'Install libusb-1.0 development files in the development container.'; exit 1; }
mkdir -p dist/tools
cd third_party/go-bsb-cams
# Source is pinned and vendored. VROSTBridge's changes (loopback-only HTTP listener, and its own
# MJPEG server in mjpeg_stream.go) are listed in VROSTBRIDGE_CHANGES.md.
CGO_ENABLED=1 go build -mod=vendor -buildvcs=false -trimpath -o ../../dist/tools/go-bsb-cams .
printf '%s\n' 'Camera helper built. Return to Bazzite desktop and use Start eye cameras in the launcher.'
