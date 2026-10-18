#!/usr/bin/env bash
# FFmpeg 6.1.4 for the VROSTBridge Windows client (1.0.0.7), cross-compiled on Linux with mingw-w64.
#
# Shared libraries avcodec, avutil and swscale: the H.264, HEVC and AV1 decoders with D3D11VA hardware
# acceleration, the Vulkan hardware context (the client renders with Vulkan on SteamVR's GPU), Windows
# threads, no programs. No GPL or non-free parts: the result is LGPL 2.1 or later, as on Linux
# (scripts/Build-FFmpeg-Linux.sh; third_party/ffmpeg/BUILD-Windows.txt lists the arguments).
#
# The source is the unmodified snapshot shipped in third_party/ffmpeg (checked by SHA-256). The build runs in
# a work folder with the Vulkan headers copied beside the source and the prefix /ffmpeg (installed into
# --output through DESTDIR), so the configure line FFmpeg embeds in the DLLs names no build-machine folder.
# Linking without a time stamp makes the DLLs reproducible.
#
#   scripts/Build-FFmpeg-Windows.sh [--work DIR] [--output DIR] [--vulkan-headers DIR] [--jobs N]
#
# --output receives bin/ (the DLLs), lib/ (their .def files, which scripts/Build-Client-Windows.py turns into
# MSVC import libraries) and include/. Needs x86_64-w64-mingw32-gcc (the win32 thread model), nasm and make.
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
work="$root/build-ffmpeg-windows"
output="$root/build-ffmpeg-windows/out"
headers=/usr/include
jobs="$(nproc)"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --work) work="$2"; shift 2 ;;
        --output) output="$2"; shift 2 ;;
        --vulkan-headers) headers="$2"; shift 2 ;;
        --jobs) jobs="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
tarball="$root/third_party/ffmpeg/ffmpeg-n6.1.4-source.tar.xz"
expected=735b0713aa8f7218b1f77bf0407d6db6c9c4dde77d75f2e9e2d19f9b5c46ef55
[[ "$(sha256sum "$tarball" | cut -d' ' -f1)" == "$expected" ]] || { echo 'FFmpeg source snapshot does not match its SHA-256' >&2; exit 1; }
for tool in x86_64-w64-mingw32-gcc nasm make; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
[[ -f "$headers/vulkan/vulkan_core.h" && -d "$headers/vk_video" ]] || { echo "No Vulkan headers in $headers" >&2; exit 1; }
version="$(sed -n 's/^#define VK_HEADER_VERSION \([0-9]*\)$/\1/p' "$headers/vulkan/vulkan_core.h")"
(( version >= 255 )) || { echo "FFmpeg 6.1 needs Vulkan headers 1.3.255 or later (found 1.3.$version)" >&2; exit 1; }

source="$work/ffmpeg-n6.1.4"
rm -rf "$source" "$work/stage"
mkdir -p "$source" "$work/stage"
tar -xJf "$tarball" -C "$source"
mkdir -p "$source/vulkan-headers"
cp -r "$headers/vulkan" "$headers/vk_video" "$source/vulkan-headers/"
cd -- "$source"
# Relative paths only from here on: they are recorded in the libraries (avcodec_configuration()).
./configure --prefix=/ffmpeg --target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32- \
    --enable-cross-compile --pkg-config=false --enable-shared --disable-static --disable-programs --disable-doc \
    --disable-debug --disable-everything --enable-avcodec --enable-avutil --enable-swscale \
    --disable-avformat --disable-avdevice --disable-avfilter --disable-swresample --disable-postproc --disable-network \
    --enable-decoder=h264,hevc,av1 --enable-parser=h264,hevc,av1 \
    --enable-hwaccel=h264_d3d11va,h264_d3d11va2,hevc_d3d11va,hevc_d3d11va2,av1_d3d11va,av1_d3d11va2 \
    --enable-d3d11va --enable-vulkan --disable-dxva2 --enable-w32threads --disable-pthreads \
    --extra-cflags='-Ivulkan-headers -O2' --extra-ldflags='-static-libgcc -Wl,--no-insert-timestamp'
grep -q 'License: LGPL version 2.1 or later' config.h 2>/dev/null || grep -q '"LGPL version 2.1 or later"' config.h \
    || { echo 'The configured FFmpeg is not LGPL 2.1 or later' >&2; exit 1; }
make -j "$jobs"
make install DESTDIR="$work/stage"
rm -rf "$output"
mkdir -p "$output"
cp -r "$work/stage/ffmpeg/bin" "$work/stage/ffmpeg/lib" "$work/stage/ffmpeg/include" "$output/"
rm -rf "$output/lib/pkgconfig"
# The DLLs must name no folder of this machine (their configure line is the one above).
for dll in "$output"/bin/*.dll; do
    if grep -a -q -e "$work" -e "$root" -e "$headers/vulkan" "$dll"; then echo "Build path in $dll" >&2; exit 1; fi
done
sha256sum "$output"/bin/*.dll
