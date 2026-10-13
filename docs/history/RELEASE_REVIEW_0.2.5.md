# VROSTBridge 0.2.5 — controller, pacing and session review

This revision follows the user's successful remote Index test. Video, tracking,
both controller poses and audio were observed working on 0.2.4. Left-hand input,
host menu access and image smoothness still needed attention. The reported
approximately 20 ms source-frame age is useful telemetry, not a measured
motion-to-photon result. The changes below require a new physical test.

## Changes against all ten requests

| Request | Implementation and remaining test |
| --- | --- |
| Index menu opens host dashboard | Both physical system actions are bound and forwarded. Local `steamvr/sendSystemButtonToAllApps` is enabled reversibly. Web menu and left B + thumbstick click remain fallbacks. Test physical availability and host dashboard response; custom SteamVR bindings may need the supplied default. |
| Client owns session Start | The host waits with SteamVR closed. The client discovers/pairs with its companion, sends validated codec/bitrate preferences, starts both runtimes and maintains the session. The host has only an Advanced emergency Stop. |
| Image judder | Fixed selection of an unfinished newest GPU conversion that hid another completed frame. A bounded queue chooses the newest completed image while preserving its source pose. Physical improvement is not yet measured. |
| Simpler UI | Removed the oversized marketing area and “Local by design”; session controls, health, quality and diagnostics are grouped by purpose. Host and client roles remain explicit. |
| Low delay without quality cuts | Tracking leaves before controller/diagnostic work; presentation queues are bounded; new per-stage/p95/cadence diagnostics expose stalls. No automatic resolution reduction, extra foveation or prediction layer was added. |
| 40 Mbit/s default | New client preferences default to H.264 at 80 Mbit/s. Adjustable 10–300. This is a quality-oriented starting point, not a measured optimum. More bitrate can improve detail and can increase delay if the link is congested. |
| Left input absent | Fixed the confirmed ALVR input-map replacement bug: each hand's profile registration now includes the union of both hands' 36 input components. Input and haptics queries are explicitly hand-scoped. |
| Start/Stop SteamVR on both PCs | Client Start requests the host and starts/adopts local SteamVR. Stop restores local preferences, closes the bridge and stops the exact owned/adopted runtime processes. Heartbeat expiry cleans the host after an interrupted client. |
| Bazzite Game Mode | Added browserless **VROSTBridge Play**, suitable for a native non-Steam shortcut. Desktop use is supported by the existing architecture; actual Game Mode headset-display leasing remains a separate unverified gate. Do not force Proton. |
| Shared icon | New transparent, textless SVG/PNG/ICO mark based on the project's supplied bridge artwork. Embedded in the Windows launcher/installer and used for both desktop entries and the web UI. Original artwork and credits remain included. |

## Why these fixes are credible

The pinned ALVR handler replaces its entire controller mapping manager whenever
an interaction profile is registered. Sending only the left paths followed by
only the right paths discarded left-hand inputs. Registering the complete map
for both updates fixes the cause without changing the transport revision.
The native tests model that replacement contract, not just an expected label.

Valve exposes `k_pch_SteamVR_SendSystemButtonToAllApps_Bool`; the earlier statement
that physical system buttons could never be exposed to ordinary applications
was too broad. We now request the documented setting, read back the effective
value, preserve physical values when combining the fallback and restore the
previous preference on Stop. The new per-hand INPUT diagnostics show binding
availability, errors, changes, axes and system-press counts. A runtime or saved
binding that does not supply the action is distinguishable from a host-side
forwarding failure.

Host recovery also fixes two independent restart problems: graceful shutdown
now accounts for a surviving owned compositor after vrserver exits, and the
persisted negotiated restart hash is considered before treating ALVR's immediate
shutdown as a crash. Shutdown verifies executable identity and creation time;
it does not kill an unrelated SteamVR by process name.

## Session and network model

The browser controls stay on authenticated loopback URLs. A separate limited
companion broker uses TCP/UDP 9946 on private, directly connected IPv4 LANs.
UDP only discovers a host. Pairing creates a persistent credential; Start,
Stop, status and heartbeat are the allowed operations. Start accepts only codec
and bitrate. A lease prevents another paired client from stopping the active
client's session. No remote paths, shell commands or arbitrary URLs are accepted.

Automatic first pairing is for the user's trusted home LAN. It is not an
Internet-facing, TLS-authenticated service. Windows rules are restricted to
Private/LocalSubnet, with broker traffic scoped to the bundled Python executable.
Client discovery binds UDP 9946 on each connected physical interface and the
client firewall allows replies only from its observed private subnet. This avoids
relying on broadcast-to-unicast replies being accepted by ordinary connection
tracking at an ephemeral port. Existing installations may need the client
**Allow home-network connection** action once to add this new allowance; no
firewall mutation occurs on Start. See [SESSION_NETWORK_0.2.5.md](SESSION_NETWORK_0.2.5.md).
ALVR retains its existing streaming/discovery ports. The PC must be awake, signed
in and running the host companion; this update does not implement Wake-on-LAN.
The installer offers starting the waiting companion at Windows sign-in.

The host stops its session after 30 seconds without client heartbeats. A brief
network interruption does not silently launch a second runtime. Stop and startup
failure use the same cleanup path. An already running client SteamVR session is
explicitly adopted for this session and will be closed by Stop, as requested.
An unrelated host SteamVR session must be closed before the bridge can start.

## Quality, performance and attribution

H.264 retains the established 1440-pixel per-eye width to stay within common
hardware decode limits. HEVC retains the existing 2144-pixel per-eye profile;
changing codec therefore also changes this preset's resolution. Height follows
the headset aspect ratio. Keep codec/resolution/refresh fixed when comparing
bitrate. 80 Mbit/s does not claim lossless video or guaranteed lower delay.

ALVR remains the VR transport. Moonlight/Apollo/Sunshine were reviewed for queue,
cadence and bitrate practices; their desktop capture path is not substituted for
VR source-pose metadata and headset reprojection. No code from those projects was
copied in this revision. Primary-source findings and timing definitions are in
[PERFORMANCE_RESEARCH_0.2.5.md](PERFORMANCE_RESEARCH_0.2.5.md). Existing ALVR,
OpenVR, FFmpeg, camera-helper, packaging and other notices are retained.

## Test the matching packages

1. Stop and quit the old companions; install **both** 0.2.5 packages over the
   managed installs. Keep the working SteamVR installation and local room setup.
2. Open the host once; allow its scoped firewall update. It should wait without
   starting SteamVR. Open the client and choose **Allow home-network connection**
   if shown to add UDP 9946 discovery permission. Select **Start session** there only.
3. Check both hands' buttons, sticks, triggers, haptics and physical menu buttons.
   If menu access fails, use **Open host menu** and save the combined host report.
4. In the same scene and refresh mode, compare 40 and 80 Mbit/s on separate
   sessions. Inspect detail and new-image cadence, not just average age.
5. Stop on the client. Confirm SteamVR closes on both PCs, then start again.
6. Try **VROSTBridge Play** in Desktop Mode. Treat a Game Mode display failure as
   its own compositor compatibility test, not proof the network bridge failed.

Save the host's combined report while connected when possible. Full reports are
also requested during Stop; summaries are collected at low frequency. No raw
pose/gaze history is added to the report stream. Current local boundaries remain
active; only the rectangular play-area representation is transferred. Irregular
polygon transfer, calibrated Beyond 2e eye tracking, further decoder/encoder
optimisation and true Game Mode compatibility remain open.

Build, contract, installer and integrity evidence lives in `docs/evidence/`.
Windows execution and physical 0.2.5 headset acceptance must be performed on the
actual machines; cross-compilation and static inspection cannot establish them.

## Build-workspace acceptance record

The final Python suite covers the controller/session/network contracts and the browserless wrapper; its exact results, including one environment-limited skip, are in `docs/evidence/python-tests-0.2.5.txt`. Four native contract suites, action/binding validation,
desktop JavaScript tests and synthetic browser rendering passed. Real frozen
Bazzite package tests cover installation in paths with spaces/Unicode, idle
background startup, repair, corruption detection, authenticated HTTP, graceful
signals, the actual 0.2.4 upgrade and uninstall with retained user state.

The Windows launcher is cross-compiled as a native x64 GUI executable with icon
resources and only system DLL imports. The NSIS installer is built offline from
the manifest-verified matching host runtime. Final archive/payload verification
and the exact tested package hashes are recorded in the source's evidence files.
These are software/package checks, not Windows SteamVR execution or headset tests.
