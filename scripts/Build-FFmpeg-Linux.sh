#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
source_dir="$root/upstream/FFmpeg"
prefix="$root/build-ffmpeg"
revision=34277e12e80031c7f89494ba543684bc1dd0be8f
if [[ ! -d "$source_dir" ]]; then
    git clone --depth 1 --branch n6.1.4 https://github.com/FFmpeg/FFmpeg.git "$source_dir"
fi
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$revision" ]] || { echo 'FFmpeg revision mismatch'; exit 1; }
cd -- "$source_dir"
./configure --prefix="$prefix" --enable-shared --disable-static --disable-programs --disable-doc --disable-debug --disable-everything --enable-avcodec --enable-avutil --enable-swscale --enable-decoder=h264,hevc,av1 --enable-parser=h264,hevc,av1 --enable-hwaccel=h264_vaapi,hevc_vaapi,av1_vaapi --enable-vaapi --enable-vulkan --enable-libdrm --disable-x86asm --disable-xlib
make -j "$(nproc)"
make install
for library in "$prefix"/lib/*.so.*.*.*; do patchelf --set-rpath '$ORIGIN' "$library"; done
