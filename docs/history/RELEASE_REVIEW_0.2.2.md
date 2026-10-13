# VROSTBridge 0.2.2 — package review and next test

Reviewed 24 September 2026. This revision supersedes the 0.2.1 installable packages.

## Assessment

Keep the Windows ALVR/SteamVR host and native Bazzite OpenVR/Vulkan client design.
It reuses established transport, encoder, audio and tracking components while
leaving the physical headset and Lighthouse tracking with the client SteamVR
installation. The supported headset targets remain Valve Index and Bigscreen
Beyond 2e. The implementation is not tied to a particular handheld, dock or LAN
adapter. Hardware capability remains the agreed working assumption.

The software is ready for a more useful first Index test, but is not a verified
end-to-end VR release. We reviewed the exact previous host payload, not only its
source. Its PE header, appended ZIP and embedded hash were valid. No specific
Windows exception or error message was supplied, and the old launcher discarded
important startup errors. The user's exact failure therefore remains unidentified;
this release fixes known failure-handling defects and makes the next failure
observable rather than claiming a proven cure for every Windows machine.

## Changes

| Area | Finding and correction |
| --- | --- |
| Windows installation | Replaced the custom EXE → hidden PowerShell extraction → pythonw chain with a conventional offline NSIS installer and a small native launcher. The native entry imports only standard Windows system libraries and invokes bundled console Python without displaying a console window. |
| Startup diagnostics | Native stdout/stderr capture starts before Python executes. An early Python bootstrap records import and startup exceptions. Visible failure dialogs identify the log location. |
| Reopening | A per-user OS lock and authenticated loopback record let repeated launches reopen the running desktop page. A crash releases the lock. Missing browser association produces a recovery address. |
| Installed files | Self-test verifies the shipped SHA-256 manifest and logo. Reinstall repairs Windows package files; Bazzite verifies files before switching its active installation. |
| Host dependencies | Readiness checks load the actual host DLLs in an isolated Windows child process. They can report a missing/old C++ dependency without crashing the desktop UI. Microsoft's bundled runtime installer is available in setup. |
| SteamVR registration | Runtime-registry discovery supports nondefault Steam libraries. Missing Steam registry entries no longer prevent the file picker. Recognized older VROSTBridge driver registrations migrate with backup/rollback; unrelated ALVR registrations require explicit resolution. |
| Index controller poses | The host profile removes the inherited 11 cm ALVR controller offset, custom rotation offsets and velocity cutoffs. Raw physical controller poses now have the intended passthrough profile. Save the profile after upgrading. |
| Fingers | Removed an incorrect SteamVR-bone/OpenXR-joint reinterpretation. Exact finger-joint streaming is disabled pending a verified conversion. Pose, button, touch and analog forwarding remain; ALVR supplies input-driven hand animation. |
| Defaults | A fresh host uses H.264 at 40 Mbit/s, microphone and gaze off. Existing saved settings are restored in the desktop interface. The required profile is generated if Start is used before Save. |
| Branding and attribution | Uses the original supplied logo, unchanged. Desktop credits and included notices cover actual upstream components, distinguishing Windows GPL FFmpeg from Linux LGPL FFmpeg. |

## Evidence and limits

- Python: 22 tests total; 21 passed and one live Unix-datagram test skipped because
  this environment denies that socket type.
- Native Linux client: release rebuild and both C++ test suites passed. Version
  output is 0.2.2; dynamic dependencies resolve; rebuilt shader matches the previous
  shader. Matching Rust transport and FFmpeg libraries were retained.
- Packaged Linux launcher: install, repeat install, spaced/non-ASCII paths,
  HTTP page/logo/status/report, token enforcement, repeat launch, clean quit and
  damaged-file rejection all passed using the frozen application.
- Windows: native launcher cross-compilation and NSIS compilation passed. Static
  PE and package extraction/integrity evidence is recorded with the source release.
  These checks are not Windows execution tests.
- A Wine smoke attempt could not run: the environment denied Wine's server socket.
  No Wine success, native Windows execution, visual browser-render review or
  physical headset session is claimed.

Remaining practical gates: Windows startup, real VA-API/Vulkan surface import,
stereo geometry, tracking stability, controller bindings/haptics, audio routing
and reconnection. Future-vsync timing is still an unmeasured placeholder. IPD or
video geometry changes and fatal decoder failures currently require restarting.
Beyond 2e calibrated gaze and exact Index finger-joint conversion remain unfinished.

The Windows host preserves its upstream dependency notices. Its BtbN FFmpeg DLL
identifies commit c573a95381 and GPL/version3 configuration. The included Linux
FFmpeg source is a different build and must not be described as corresponding
source for those Windows binaries; reconstructing the Windows external dependency
source set remains a distribution follow-up.

## What to use

- Windows: **VROSTBridge-Host-Setup-0.2.2.exe**, then **VROSTBridge Host** in the
  Start menu. A portable ZIP contains the same launcher for an alternate unpacked
  launch path; it still needs SteamVR and the host C++ runtime.
- Bazzite: extract **VROSTBridge-Bazzite-Installer-0.2.2.zip**, then open
  **Install VROSTBridge.desktop**.
- Source: **VROSTBridge_0.2.2_Host_Client_Source.zip** contains the reviewed source,
  build scripts, native client, tests and technical audit.

First verify the Index works locally in Bazzite SteamVR. Then keep local SteamVR
running, open both VROSTBridge desktops, save the corrected host profile, register
the host driver, start both ends and trust the client in ALVR. Use 90 Hz,
H.264/40 Mbit/s, microphone/gaze off. Validate the remote image before controllers,
recovery and audio. The included **INDEX_FIRST_TEST.md** gives the complete sequence.

If Windows fails before the page opens, capture the exact error and these files
from `%LOCALAPPDATA%\VROSTBridge` when present: **host-native.log**,
**host-startup.log** and **latest.log**. State whether setup, the installed
shortcut or Start session failed. If streaming fails, save reports from both
apps and describe the first incorrect behavior.
