# VROSTBridge 1.0.0-rc4.2 — steady picture, Wi-Fi without stalls, Game Mode

RC4.2 follows the RC4.1 test over Wi-Fi 7 (3 October 2026). Motion and frame pacing were close to the goal, but head-locked menus and held objects jittered slightly while the head moved, and the stream stalled briefly about every five minutes. RC4.2 fixes both, reworks Steam Game Mode, polishes the in-headset cards and the pages, and makes reports easier to collect. It changes no stream format and keeps every RC4.1 setting.

**Install:** Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc4.2.zip` and Windows host `VROSTBridge-Host-Setup-1.0.0-rc4.2.exe`. The host is a companion update: its SteamVR driver is the RC4.1 build (patches 0001–0015), unchanged. Install both. Settings, pairing and logs are kept.

## What changed

### 1. Even pose timing (client)
**Found:** the host renders each frame with the newest head pose it has received. RC4.1 sampled poses every 4 ms on a free-running timer. 4 ms does not divide a 75 Hz frame (13.3 ms), so consecutive frames were rendered for poses 12.2 or 16.2 ms apart instead of 13.3 ms: a small error repeating about 21 times a second. While the head turned, head-locked text and held objects shook in place.

**Now:** the client samples the head on a grid locked to the headset's refresh: four evenly spaced samples per refresh at 75 and 90 Hz (3.3 and 2.8 ms apart), re-anchored to the display's vsync every second. Every frame the host renders is then the same age. If the host's pick point sits right on a sample (its frames alternate between two neighbours), the grid moves by half a step. The client log's `TIMING` line adds `tracking_per_frame`, `tracking_spacing_ms`, `tracking_shifts`, `pose_slot_changes` and `pose_step` (how evenly the poses behind consecutive frames advance).

### 2. Frames are re-aimed to your head (client)
**Found:** each frame is rendered for a predicted head pose. The prediction is made tens of milliseconds ahead and is never exact. Whatever error remains moves the whole picture against your head.

**Now:** *Motion & timing → Re-aim frames to your head* (on). Just before a frame goes to SteamVR, the client turns it from the pose it was rendered for to the pose SteamVR will show it with. This is a rotation only, done on the GPU with a sharp (Catmull-Rom) filter. Position changes need depth, which the stream does not carry, so they are not corrected. It can be switched during a session. The `TIMING` line adds `reaimed_frames`, `reaim_fallbacks` and `reaim_degrees`. `--no-reaim` turns it off for the native client.

**Check alignment** (same section, during a session) finds out whether SteamVR on your PC already re-aims frames itself. For 10 seconds every other second's frames are labelled with a pose turned 3° left. Answer whether the picture jumped sideways once a second:
- *Yes* means SteamVR re-aims, so VROSTBridge's own re-aim is turned off (one resampling less).
- *No* keeps it on.

The headset shows a countdown card during the check.

### 3. No Wi-Fi stalls every five minutes (client)
**Found:** the RC4.1 Wi-Fi 7 session stalled briefly about every five minutes. NetworkManager scans in the background so Wi-Fi can roam. When more than one access point broadcasts your network, it scans every 300 s, and every 30 s with a weak signal. Each scan takes the radio off its channel for a moment.

**Now:** *Network → Stay on one access point* (on). For the session, the Wi-Fi connection is locked to the access point it is using:
- NetworkManager does not scan a connection that is locked to one access point (`802-11-wireless.bssid`).
- The change is made in memory only (`nmcli connection modify --temporary`), so restarting the PC or NetworkManager also undoes it.
- Wi-Fi reconnects once as the session starts (a few seconds, before any video). After Stop, normal roaming returns at the next reconnect.
- A receipt file lets the next start undo the lock if the companion ended during a session. The receipt is deleted only once the lock is known to be gone.

This is skipped when it is not needed or not safe:
- only your access point is known to broadcast the network, its signal is good and security is personal (NetworkManager then scans once a day);
- your connection profile already names an access point;
- the connection uses Wi-Fi 7 multi-link (its address belongs to the access point, not one radio);
- NetworkManager is unavailable or refuses.

The page shows what happened.

### 4. Wi-Fi or Ethernet, detected (client)
The *Network* section now says how this PC reaches the host, for example *Wi-Fi · 6 GHz · −41 dBm · 2.9 Gbit/s link* or *Ethernet · 2.5 Gbit/s*. It shows only the options that apply:
- **Wi-Fi options** (one access point, power saving, other adapters) appear only on Wi-Fi.
- **Wi-Fi warnings:** 2.4 GHz and a weak signal.
- **Ethernet warnings:** a link under 1 Gbit/s (cable, port or adapter), half duplex and a missing cable link. A USB adapter is named, and active Energy-Efficient Ethernet is mentioned as a possible cause of small stutters (reported, never changed).

### 5. In-headset cards (client)
The status pop-up is now a card in the style of SteamVR's own:
- dark and rounded, with the VROSTBridge logo, a bold title and a grey message;
- the pages' fonts (Outfit and Inter, anti-aliased) and the logo's blue-to-pink accent;
- a timer where one helps (seconds without video, the alignment check's countdown bar).

It appears when video stops (*Reconnecting*, *Starting SteamVR on the host*, errors) and during the alignment check. The optional quality line is a small pill near the bottom of the view. If the fonts cannot be loaded, the RC4 panel is used.

### 6. Steam Game Mode (client)
- **The handheld's screen stays on.** gamescope picked any external output before the built-in screen, so a connected headset became the Game Mode display and the handheld's screen went dark. Now:
  - The VROSTBridge gamescope wrapper ranks headset outputs after every other output, including the built-in screen. Headsets are recognised by their display identity, so a monitor on a port a headset once used keeps its place.
  - The headset is still reserved for SteamVR where gamescope supports it.
  - Any failure in the wrapper starts plain gamescope, and so does a wrapper left behind after VROSTBridge was removed.
- **One button:** *This PC → Steam Game Mode → Add to Steam* (or *Remove from Steam*).
  - Steam closes, VROSTBridge appears in your library as **VROSTBridge** with its cover, wide capsule, background and logo, and Steam opens again.
  - It also sets up the Game Mode display handling.
  - Your other shortcuts are kept exactly. The file is backed up first, and nothing is changed if its format is not recognised.
  - Do this from Desktop Mode: Steam restarts to apply it. Uninstalling removes the entry. Remove any *VROSTBridge Game Mode* entry you added by hand; *Add to Steam* replaces it.
- **A clean Game Mode screen:** redrawn at the screen's own resolution with the pages' fonts, colours and a background image. The pages (Stream, Host, Reports, About) have bigger controls, and the focus moves to the nearest control in the direction you press.

### 7. Reports (both)
- **Clear names:** every report carries `report_info` (which end saved it, version, local and UTC time). Its file name matches, for example `VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-45-12.json`.
- **Send report to host** (client page and Game Mode): the client's report goes to the host over the paired connection.
  - It runs in the background. Start and Stop are never held up, and the page shows the outcome.
  - The host keeps the latest 20 (at most 96 MB) and lists them under *Reports → From your headset PC* for download.
- **Diagnostics** is a collapsible section on both pages.
- **The host driver log is readable again:** ALVR writes a `[GRAPH]` line per frame. Those lines filled 92 % of the log in RC4.1 reports and pushed everything else out. They are dropped, together with per-event tracking, button and haptics lines, and only the latest 120 `[STATS]` lines are kept.

### 8. Clearer pages (both)
Both pages were reorganised and reworded, and every option has a one-line explanation of what it does:
- **Client:** Headset & stream, Motion & timing, Reports, Diagnostics, Network, This PC (including Steam Game Mode), Play area and Preferences. The old *Display details*, which explained the stream size rather than the display, is now *About the stream size* under the stream options; the detected headset and its refresh rate are shown at the top of that section.
- **Host:** Headset connection, Reports, Diagnostics, Power & network, Play area and Preferences.

The pages use the logo's light blue and purple-pink and the Outfit and Inter fonts (bundled; no Internet needed).

### 9. Fixes
- **Reprojection check:** RC4.1 ignored SteamVR's compositor log whenever the settings file was newer. SteamVR rewrites that file whenever it closes, so a real *Async support disabled* was almost never reported. Now only a repair made after SteamVR last wrote its log hides it.
- **Version label:** the RC4.1 native client and runtime probe still called themselves 1.0.0-rc4 (built before the version change). Both are rebuilt, and packaging now refuses native files whose label does not match the release.

## Compatibility
- New companion feature `report-upload`: an RC4.2 client sends reports only to a host that advertises it, and otherwise says the host needs RC4.2.
- Pose timing, re-aim, the alignment check, the access-point lock and the cards are client-only. They work with any host.
- The RC4.2 host driver is the RC4.1 build, so an RC4.1 host works with an RC4.2 client apart from report upload.

## How to test (please report each)
1. **Install** both. Both pages show 1.0.0-rc4.2.
2. **Steady picture:** in a session, look at the SteamVR dashboard or a game menu and turn your head slowly and quickly. Text should stay still. In the client log's `TIMING` lines, `tracking_per_frame` should be 4 and `pose_slot_changes` small.
3. **Alignment check:** run *Check alignment* during a session and answer. Compare *Re-aim frames to your head* on and off.
4. **Wi-Fi:** play for 15 minutes or more on Wi-Fi. The five-minute stalls should be gone. The page and client log say whether Wi-Fi stayed on one access point. After Stop, Wi-Fi works as before.
5. **Ethernet:** connect a cable and select it. The page should show Ethernet with its speed and no Wi-Fi options.
6. **Game Mode:** in Desktop Mode use *Add to Steam*, then check the library entry and artwork. Enter Game Mode with the headset connected: the handheld's screen must stay on. Open VROSTBridge, check the screen looks sharp, and start a session.
7. **Cards:** switch Wi-Fi off for a few seconds during a session. The headset shows the reconnecting card, and the picture comes back.
8. **Reports:** *Send report to host*, then download it on the host under *Reports*. Save one on each PC and check the names.

Build and test evidence: [evidence/rc4.2-build-1.0.0-rc4.2.json](../evidence/rc4.2-build-1.0.0-rc4.2.json).
