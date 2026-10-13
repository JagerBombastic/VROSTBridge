# VROSTBridge 0.2.3 — desktop connection workflow

Reviewed 24 September 2026. This update supersedes the 0.2.2 installable packages.
It addresses the missing steps between starting the two desktop sessions and
actually establishing a connection.

## Why this update is needed

The user has confirmed that the Valve Index works locally on Bazzite after
power-cycling the headset. The earlier Windows host and Bazzite client pages can
both start their sessions, but the machines have not linked successfully.

The previous host page could start an ALVR Dashboard process without ensuring
host SteamVR had started. Client discovery and trust also required using that
separate dashboard. A running process was therefore insufficient evidence of a
ready host, and the page did not expose all the actions required to connect.

The agreed normal workflow is to open each VROSTBridge app, which opens its page
and starts the connection automatically on the trusted home LAN. SteamVR remains
the runtime on both machines. ALVR remains the reused host and transport
implementation. Neither choice requires a separate ALVR setup interface.

## Changes

| Area | Updated behavior |
| --- | --- |
| Automatic startup | Opening each app opens its page and starts the session once. Stop and Start session remain explicit stop/retry controls. |
| Host start | Host startup handles conflict-safe virtual-headset driver registration and host SteamVR startup rather than relying on a separately operated dashboard. |
| Connection | Connect automatically on my home network is enabled by default and allows headset clients through ALVR automatic trust. This mode requires an active Windows Private network and no active Public network. Disable it for individual Allow & connect approval. |
| Manual connection | The client page exposes its exact transport Client ID and LAN addresses. The host page accepts that identity and client LAN address without requiring a separate ALVR interface. |
| Firewall setup | Host startup ensures scoped private-network rules with Windows administrator consent when needed. Allow through Windows Firewall retries that setup. No network profile or router settings are changed. |
| Client network access | Bazzite needs inbound ALVR control and video traffic as well as mDNS discovery. Allow home-network connection requests authenticated access from the observed home subnet. Rules apply immediately and persist across reboots; the page reports missing permission. |
| Connection reporting | Runtime startup, waiting for a client, pending approval, connection and client video progress are distinct. A running host process is not labelled a successful stream. |
| First-test resolution | H.264 now explicitly requests 1440 pixels per eye (2880 stereo width), avoiding the inherited 4288-pixel stereo width. Height follows the actual headset aspect ratio. HEVC explicitly uses 2144 pixels per eye; switching codecs reapplies the corresponding profile. |
| Stop and recovery | Host runtime shutdown is requested through the bundled driver's control interface; failures are reported rather than hidden behind a closed dashboard process. |
| Desktop wording | Host and client explanations follow the physical role of each machine. The host renders here and sends the scene to the headset there; the client keeps the headset here. |
| Upgrades | Installers verify the staged payload, close the prior companion, preserve preferences/connection identity and remove old owned files. Windows backs up and migrates the host profile/driver registration and uses a stable Apps entry. Bazzite switches the active version atomically and adds a menu uninstaller. |
| Documentation | Both packages must be upgraded; setup, first-Index testing and package instructions use the VROSTBridge pages throughout. Earlier 0.2.2 evidence remains historical. |

The build retains the user's original logo and the existing open-source notices
and credits. It does not change the supported hardware targets: Valve Index and
Bigscreen Beyond 2e with Index controllers/Lighthouse tracking, a Windows render
host and a native Linux/Bazzite headset client. The design is not specific to the
handheld, its processor or the dock used in testing.

Uninstall removes only recorded or ownership-marked VROSTBridge firewall rules,
with operating-system authentication when required. A failed cleanup retains the
application and rule records so it can be retried; existing unrelated rules are
not removed.

## Evidence and limits

Automated source checks on this revision: 57 Python tests, with 56 passing and
one Unix-datagram test skipped because the workspace forbids that socket; both
native C++ contract tests passed. The JavaScript DOM workflow checks passed,
including both role headlines, active-session pairing, connection states and
firewall actions. These checks use controlled fixtures for Windows and firewalld;
they do not execute those operating-system services. Bash syntax and NSIS
installer compilation were also checked. Packaged install/repair/upgrade/uninstall
results are recorded separately in `docs/evidence/release-smoke-0.2.3.json` when
included in the source archive.

The real frozen Bazzite package subsequently passed all nine smoke gates: fresh
installation in a spaced/Unicode path, authenticated page/resource/report access,
stable Stop, reinstall while running, corruption detection, corruption repair,
web Quit, upgrade from a running real 0.2.2 installation, and uninstall. The
upgrade removed the managed old payload; uninstall preserved exact settings,
client identity, custom logs, unrelated folders and symlinks. These tests did not
have a physical headset or graphical desktop and did not change a privileged
firewall.

The final Windows NSIS installer passed archive validation. Its updated launcher,
controller, installer helper and page bytes matched the verified portable
payload. Both distribution ZIPs passed CRC and every packaged SHA-256 entry;
the source ZIP separately verifies its complete file manifest. The Windows entry
point remains a native x64 GUI executable using only the three standard Windows
system DLL imports. These are packaging checks, not Windows execution tests.

The user-reported local Index success is meaningful prerequisite evidence. It
is not evidence of a complete streamed session. Likewise, earlier launchers
starting does not qualify this updated host startup, client approval or shutdown
workflow. Tests performed on this revision must be recorded separately from the
unchanged [0.2.2 review](RELEASE_REVIEW_0.2.2.md) and `docs/evidence/*0.2.2*`.

This document does not claim a passed Windows/SteamVR run, network pairing,
physical stereo image, controller response, hardware video import, audio or
reconnect test for 0.2.3. Build and automated verification results belong in the
release's accompanying evidence record; they do not replace target-machine
acceptance.

Outstanding runtime limits remain unchanged:

- Real VA-API/Vulkan surface import, texture lifetime, stereo geometry and motion
  timing require a physical two-PC run.
- Automatic decoder-capability probing is still needed. The first-test profile
  now limits H.264 dimensions, but the native client's broad advertised maximum
  is not a measurement of the attached GPU's decoder limits.
- Measured Index finger-skeleton forwarding remains disabled pending correct
  joint conversion. Ordinary controller inputs, poses and haptics remain
  implemented but require physical acceptance.
- Host game audio, opt-in microphone, interruption/reconnection and shutdown need
  hardware tests.
- Beyond 2e eye tracking still requires external inference/calibration and
  verification. Combined OSC gaze is not host OpenXR eye-gaze support or dynamic
  foveated rendering.
- Stream geometry or headset changes require a client restart. Timing values
  which were not measured are not latency guarantees.
- The existing Windows FFmpeg corresponding-source distribution follow-up in the
  0.2.2 review is not resolved by this connection-workflow update.

Automatic mode is intended for a trusted home LAN. It is ALVR's auto-trust mode,
not cryptographic pairing or a rule to select only one new client. Disabling it
restores individual approval. Removing a client's access disables automatic mode
before forgetting it, preventing immediate reapproval.

## Next physical test

1. Stop and quit the previous VROSTBridge sessions; install 0.2.3 on both PCs.
2. Keep the now-working local Bazzite SteamVR setup and confirm the local image.
3. Open both apps. Their pages open and connection starts automatically. Use
   Check this PC and confirm H.264/40 Mbit/s, 90 Hz, microphone off, gaze off and
   hardware decoding. Stop/change/save/restart if older saved settings differ.
4. In automatic mode, wait for the client to connect on the private home LAN.
   Approve a Windows administrator prompt for first-time firewall setup if needed.
   If the client requests it, use Allow home-network connection and approve
   Bazzite's authentication prompt for access on the current home subnet.
   Use Allow & connect if manual approval is enabled. If discovery fails, use the
   exact Client ID and LAN address shown by the client page with the manual form.
5. Confirm connection and increasing decoded frames, then verify a simple host
   scene reaches both physical eyes and follows head movement correctly.
6. Save reports from both pages at the first failure or successful stage. Record
   the first incorrect behavior and the approximate time before changing settings.

The detailed sequence is in [INDEX_FIRST_TEST.md](INDEX_FIRST_TEST.md).
