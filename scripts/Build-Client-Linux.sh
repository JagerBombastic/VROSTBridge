#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd -- "$root"
for tool in git cargo cmake pkg-config glslangValidator python3 patchelf; do
    command -v "$tool" >/dev/null || { echo "Missing $tool. See docs/BUILDING.md for the build-container setup."; exit 1; }
done
python3 scripts/fetch_upstream.py
bash scripts/Build-FFmpeg-Linux.sh
export PKG_CONFIG_PATH="$root/build-ffmpeg/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
# Run from ALVR so its exact Rust toolchain is selected for the adapter too. The source folder is written into
# the library as /src, so it carries no path from the build machine.
(cd upstream/ALVR && RUSTFLAGS="--remap-path-prefix=$root=/src ${RUSTFLAGS:-}" \
    cargo build --manifest-path "$root/client/transport/Cargo.toml" --release --locked)
client/transport/target/release/vrostbridge-defaults > host/default-session.json
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release
cmake --build build-linux --parallel
ctest --test-dir build-linux --output-on-failure
cmake --install build-linux --prefix "$root/dist/client"
mkdir -p dist/client/lib
for library in libavcodec.so.60 libavutil.so.58 libswscale.so.7; do cp -L "build-ffmpeg/lib/$library" "dist/client/lib/$library"; done
for library in libwayland-server.so.0 libSDL2-2.0.so.0; do
    file=$(ldconfig -p | awk -v lib="$library" '$1==lib && /x86-64/ {print $NF; exit}')
    [[ -n "$file" ]] || { echo "Missing runtime library: $library"; exit 1; }
    cp -L -- "$file" "dist/client/lib/$library"
done
echo 'Native client built. Launch Start-Client.sh in the desktop session.'
