# VROSTBridge 1.0.0-rc1 release guide

**Update both the Windows host and Bazzite client.** This is the first 1.0 release
candidate. Your previous Index stream and finger tracking are working well, but
this build still needs the hardware acceptance checks below before a stable 1.0
release. Saved quality, pairing and desktop/Game Mode setup are retained.

## Install and start

1. Stop the session on the client. Quit the client companion and choose **Exit**
   from the Windows VROSTBridge tray menu. Close any remaining Windows SteamVR.
2. Run **VROSTBridge-Host-Setup-1.0.0-rc1.exe** on Windows.
3. On Bazzite desktop, extract the whole
   **VROSTBridge-Bazzite-Installer-1.0.0-rc1.zip** and open
   **Install VROSTBridge.desktop**. Keep the adjacent payload folder in place.
4. Open both companions. The host waits in its tray. Start and Stop sessions from
   the client; both SteamVR runtimes are managed from there.

Install over the existing versions. Old application files identified as owned by
VROSTBridge are removed after migration; preferences, pairing, reports and logs
are kept. No terminal, compiler or separate Python install is required. SteamVR
and GPU drivers remain prerequisites. A failed cleanup keeps the relevant old
files for an explicit retry instead of deleting unverified data.

The host tray provides **Settings** and **Exit**. **Launch at Windows sign-in**
starts it without opening a browser or SteamVR. If Windows is still bringing up
the Private home LAN, RC1 keeps checking quietly. Firewall setup gets one attempt;
a denied prompt is retried through host settings. Network categories are not
changed automatically and an active Public network is not bypassed.

## What changed

**Controller recovery.** The report contains a right-controller optical tracking
reset and recovery 18.313 seconds later. That establishes a local tracking
interruption, not its physical cause. A separate bridge defect discarded input
updates while poses were invalid. RC1 accepts valid controller inputs independently
of pose validity, sends full state immediately on recovery and refreshes it every
250 ms. Normal button changes remain immediate. Disconnects, focus loss and action
failures still release inputs; missing physical poses are not invented.

Grip capacitance and trackpad pressure bindings are corrected. Haptics are mixed
into bounded, separate per-hand envelopes with non-overlapping output slices;
expired vibration is not replayed after stalls. Diagnostics distinguish
**Tracking lost** from **Disconnected**, and retain recovery/haptic counters.
These changes still need the Alyx controller test.

**Clearer stream resolution.** The headset is detected at Start. Desktop and Game
Mode show the same headset-specific choices with dimensions per eye. Index
**Sharper** becomes **High**, retaining the exact saved setting. For your reported
Index mode it remains **2016 × 2240 per eye**. Encoded stereo dimensions and the
local SteamVR application recommendation are displayed separately. Before headset
detection no numeric size is guessed. Beyond has Standard, High and Ultra options;
its explicit 2560/3200 widths are not capped by the client GPU's application
recommendation. Auto chooses the headset profile and codec at session start.

**Play area and diagnostics.** The host's rectangular boundary vertices previously
used full width/depth as half extents. RC1 corrects that geometry and verifies the
live bounds/origins before deciding whether a new commit is needed. Unchanged
reconnections avoid redundant writes/backups. The physical client's boundary stays
active; an irregular perimeter is not replicated. Host reports use the client
session summary when available instead of showing misleading empty host counters.
Read-only client GPU, frequency and power observations are collected by a separate
active-only worker, outside the frame loop; unsupported measurements are omitted.

**Installer and sign-in polish.** RC versions are recognized correctly during
upgrade, repair and old-file cleanup. Preserved ALVR session preferences remain
separate from the immutable installed native API identity, retaining the 0.2.14.1
startup/shutdown correction.

## Smoothness findings and Ethernet comparison

The two supplied runs used the same 120.019 Hz, H.264, 2016 × 2240-per-eye,
80 Mbit/s configuration. The first had substantial local compositor stalls. The
second's retained windows showed approximately 119 unique frames/s and 120
submits/s, then a transport interruption while local tracking and host encoding
continued. The logs do not prove why the cold launch was choppy or which network
component interrupted traffic. No unmeasured latency improvement or complete
stutter cure is claimed. The stable AMF encoder algorithm is unchanged in RC1.
Frame age and ALVR stage timings are not motion-to-photon measurements.

For the initial comparison keep **Index 120 Hz · High · H.264 · 80 Mbit/s**.
For Ethernet: Stop, connect the cable, select the wired adapter in client
**Host connection**, and restart with those same settings. Automatic follows the
OS-selected path; a saved explicit Wi-Fi preference remains Wi-Fi. Both adapters
can stay available, but a stream does not switch between them mid-session. If the
wired interface uses a different firewall zone, the client may request its usual
home-network allowance once. No router port forwarding is needed.

## Checks before stable 1.0

- **Two cold launches:** reboot both PCs between attempts, start the bridge and
  enter the same Alyx area. Record whether first-launch performance differs.
- **Two warm Start/Stop cycles:** both runtimes should close and restart cleanly;
  the companions remain available.
- **At least 15 continuous minutes in Alyx:** exercise both controllers' buttons,
  sticks, grips, fingers, menus and haptics. Confirm input recovery if a tracking
  interruption occurs. Note any hitch or controller issue and its approximate time.
- **Save a host report**, preferably while the stream is still connected. It
  includes the client report when reachable; save the client report separately if
  collection fails. Compare Ethernet with unchanged quality settings.

Targeted controller/recovery/haptic, installer migration/repair, startup, geometry
and UI checks are automated. Native Windows builds and dependency inspection are
separate from executing SteamVR/GPU code on Windows. Native Game Mode display
layouts were visually checked; browser rendering was not verified in this build
environment. Final package/build evidence is included in the source release.

Beyond physical display operation, calibrated Linux Beyond 2e eye tracking and
the Game Mode physical display lease remain separate unverified paths. Game audio
is already user-confirmed; optional microphone forwarding still requires a working
Windows virtual audio endpoint. Eye inference/calibration uses separately
installed Baballonia; host OpenXR gaze exposure and dynamic foveated rendering are
not supplied.

The source release contains the complete local patches and open-source credits
for ALVR, OpenVR, FFmpeg, Vulkan and the other included components. Historical
release reviews remain available for comparison. VROSTBridge is independent of
Valve, Bigscreen and the upstream projects.
