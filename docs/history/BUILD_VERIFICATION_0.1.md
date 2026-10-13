# Build verification

Verified 21 September 2026 in the Linux development environment.

| Check | Result |
| --- | --- |
| Windows x64 GUI cross-build | Passed with Zig 0.16.0 C++17; PE32+ Windows GUI executable |
| Native CMake configure / build / CTest | Passed; GNU C++ 13.3.0 |
| Core checks | 26 passed |
| Address / undefined-behaviour sanitizers | 26 checks passed; leak detection disabled because the environment uses ptrace |
| SteamVR asset references | 34 unique actions, 32 bound outputs; no system-button binding |
| Actual Windows launch | Not run here |
| Runtime HLSL compilation and D3D11 draw | Not run here |
| Physical SteamVR / Index / Beyond | Not run here |
| ALVR Windows build / linking | Not run here; source audit only |
| Live vendor gaze output | Not run here; source layout and synthetic validation only |

Portable cases cover rigid-transform inversion/composition, rotated eye offsets,
asymmetric projection orientation, delayed-frame selection, pending GPU exclusion,
epoch mismatch, stale/future/invalid-time rejection, source-pose preservation,
tracking/focus recovery, and gaze validity, startup, frozen samples and timestamp reset.

The Windows binary imports Windows graphics/UI/UCRT libraries and dynamically
loads the bundled OpenVR loader. No MinGW libstdc++ or libgcc DLLs are required.
It is unsigned. No test results imply that the intended handheld/dock can drive
the Beyond's DP/DSC output.

Cross-build command used (tool path shortened):

```sh
zig c++ -target x86_64-windows-gnu -std=c++17 -O2 -municode \
  -Wl,--subsystem,windows -DUNICODE -D_UNICODE -DWIN32_LEAN_AND_MEAN \
  -DNOMINMAX -Wno-ignored-attributes -I. -Ithird_party/openvr \
  client/main.cpp client/renderer.cpp client/runtime.cpp \
  -ld3d11 -ldxgi -ld3dcompiler_47 -luser32 -lgdi32 -lshell32 -lole32 \
  -o VROSTBridge.exe
```

The ignored-attributes suppression is for an upstream OpenVR inline/dllimport
declaration. It is not used to suppress project compile errors. Windows builds
from Visual Studio use the supplied CMake project instead.
