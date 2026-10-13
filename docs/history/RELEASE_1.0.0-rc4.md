# VROSTBridge 1.0.0-rc4 — host and client update

RC4 continues the RC3 aim: a networked session that feels like a headset plugged into the host in another room. It adds foveated encoding (fixed for any headset, eye-tracked for the Bigscreen Beyond 2e), 10-bit colour and AV1, a measured bitrate, video pacing for faster host links, gradual loss recovery, in-headset status, and the system integration that a PC in another room needs: keep-awake, Wake-on-LAN, sleep after the session and quick resume.

**Install:** Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc4.zip`. Windows host `VROSTBridge-Host-Setup-1.0.0-rc4.exe`, with a SteamVR driver rebuilt from all twelve patches and **no FFmpeg** (see *Host driver build*). Install both: foveated encoding, pacing, quick resume and the network test need an RC4 host. The RC4 client still works with RC1–RC3 hosts; it sends RC4 settings only to hosts that advertise them. Settings, pairing and logs are kept.

## What changed

### Foveated encoding (client + host; patches 0010 and 0012)
The host sends the edges of each eye at reduced resolution and the client restores the full image on its GPU. The same bitrate and decoder budget then go to the part of the image you actually see sharply. The client side is new: the native client undoes ALVR's compression per eye in its conversion shader, with the centres the host used for each frame.

- **Client page → Headset display & stream → Foveated encoding:**
  - *Auto* (default): *Subtle* on a Valve Index or a Beyond without eye tracking; *Eye-tracked* on a Beyond with calibrated eye tracking on. Unknown headsets stay off.
  - *Off*: full resolution everywhere (RC3 behaviour).
  - *Subtle*: 70 % x 65 % of each eye at full resolution, the edges at half. About 70 % of the pixels.
  - *Balanced*: 55 % x 50 % at full resolution, edges at a third. About 47 % of the pixels.
  - *Eye-tracked*: 40 % x 40 % at full resolution, edges at a quarter, and the sharp region **follows your gaze**. About 30 % of the pixels. Beyond 2e with calibrated eye tracking (see [BEYOND_EYE_TRACKING.md](../BEYOND_EYE_TRACKING.md)); without gaze it falls back to *Balanced*.
- **Optical centre (patch 0012):** the fixed region sits on each eye's straight-ahead direction, worked out from that eye's actual field of view and pose. Asymmetric lens views (Index, Beyond) are therefore centred correctly without a per-headset setting.
- **Eye-tracked (patch 0010):** the host turns the latest gaze direction into each eye's image position and moves the region there, snapped to the same grid ALVR's negotiation uses. Small eye movements inside a dead band (2.5 % of the eye width) do not move it, to avoid shimmer. Gaze older than 150 ms (a blink, lost tracking) returns to the optical centre.
- The page shows the encoded eye size. For example, a Beyond at 2560 x 2560 per eye with *Eye-tracked* encodes 1408 x 1408 per eye. That leaves room to try the *Ultra* (3200) stream size at a similar bitrate and decoder load.
- CPU decoding (troubleshooting option) cannot undo foveation, so foveation is off while it is selected.
- **Diagnostics:**
  - The fresh-video line shows *Foveated* or *Foveation following your eyes*.
  - Host logs `VROST_GAZE_FOVEATION` (gaze frames, fallbacks, moves, current centres) every 5 s.
  - The native `TIMING` line counts foveated and gaze-centred frames.

### 10-bit colour and AV1 (client + host profile)
- **10-bit colour** (on by default, HEVC and AV1 only) removes banding in dark gradients. The client decodes P010 surfaces directly on the GPU. H.264 stays 8-bit.
- **AV1** is a new codec choice. It needs an AV1 encoder on the host GPU (NVIDIA RTX 40, AMD RX 7000 or newer) and AV1 decode on the client (recent integrated Radeon graphics have it). *Auto* still picks H.264 for the Index and HEVC for the Beyond.
- **Picture conversion:**
  - Chroma is sampled at the position the stream signals (left-sited by default), not the pixel centre. This removes a slight colour fringe.
  - The 8-bit output is dithered, so smooth gradients stay smooth.
  - The conversion is checked on a software Vulkan device against a CPU reference and against the original image through ALVR's host compression (`tests/convert_shader_tests.cpp`).

### Automatic bitrate from a network test (client + host)
*Automatic bitrate* (on by default) measures the path to the host once per adapter, before the first session on it (about 10 seconds, overlapped with local SteamVR startup). It then uses a bitrate with headroom:
- 70 % of the highest clean rate on Ethernet, 60 % on Wi-Fi;
- capped by the codec limit.

The test sends what the video sends: frame-sized bursts at rising rates. The client measures packet and frame loss, burst spread and jitter. The host only sends to the client that asked, and only between sessions.

*Test network now* re-measures; the result appears under the button and in reports. If the test receives nothing (for example a firewall), the manual bitrate is kept.

### Video pacing for faster host links (patch 0009)
When the host has a faster link than the client (for example a 10 Gbit/s host and a 1–2.5 Gbit/s or Wi-Fi client), every video frame leaves the host as a burst that the slower link cannot carry at once. A switch or router with small buffers then drops the excess. The host now spreads each frame at the client's link rate. This adds no delay, because the slower link would take that long anyway.
- **Ethernet:** 92 % of the adapter's link speed.
- **Wi-Fi:** the larger of the last clean test rate and 60 % of the radio's receive rate.
- **Turned off** when it would be below twice the bitrate, or when the client also has 10 Gbit/s.
- **Toggle:** *Frame pacing → Pace video to this PC's network link* (on).
- **Diagnostics:** host logs `VROST_STREAM_CONFIG pacing_mbps=… paced=1`.

### Gradual loss recovery (patches 0008 and 0011, NVENC setting)
*Frame pacing → Gradual loss recovery* (off by default; recommended on Wi-Fi). The host refreshes the picture in stripes (intra refresh): NVENC through its session settings, AMF through patch 0011, for H.264, HEVC and AV1. A lost packet then heals within half a second without a keyframe. Large keyframes are what make a loss on Wi-Fi turn into more losses.

The client switches to gradual recovery only after the host confirms intra refresh is active. NVENC is confirmed by its settings; AMF reports `VROST_INTRA_REFRESH accepted=1`. In gradual mode the client still asks for a keyframe after three consecutive lost frames.

The client now counts frames, losses, keyframe requests, decoder saturation and frames skipped while waiting for a keyframe. The fresh-video line shows lost frames.

### In-headset status (client)
The native client shows a small head-locked panel in the headset whenever video is not fresh: waiting for the host, starting video, reconnecting, the host's status (for example *SteamVR is restarting to apply the headset settings*) and errors. Previously the headset showed a near-black frame and all status was on the PC screen.

*Frame pacing → Show stream quality in the headset* adds a line with frame rate, latency, frame age and lost frames.

### The PC in another room (host)
- **Keep awake:** the host PC stays awake during a session. Controller and headset movement is not keyboard or mouse input to Windows, so it could otherwise sleep mid-game. The display may still turn off.
- **Sleep after a session** (host page → *Power & network*; off by default): 15 minutes to 2 hours after the session ends, the host sleeps unless someone uses it or a session starts.
- **Wake-on-LAN readiness:** the host page reads, without changing anything, whether the wired adapter has *Wake on Magic Packet* enabled, and says what to change.
- **Wi-Fi priority for video** (optional): a Windows QoS policy marks SteamVR's video packets for the Wi-Fi voice queue. It needs administrator approval and, on PCs outside a Windows domain, one registry value and a restart. It is reversible from the same page.
- **Quick resume** (client page → *Frame pacing*; off by default): host SteamVR stays open for 5–60 minutes after Stop. Starting again within that time with the same settings skips the host startup. Games on the host keep running meanwhile. A Stop on the host page always closes it.

### The headset PC (client)
- **Wake the host:** after one session the client remembers the host adapter's address. The host reports it; the client's neighbour table is the fallback. If the host does not answer on Start, the client sends Wake-on-LAN and waits up to two minutes. *Wake host now* does the same on request.
- **Keep this PC awake** (on): blocks sleep and the lid switch for the session and selects the performance power mode, then restores yours. The screen can still turn off.
- **Headset speakers and microphone** (on): the headset's own audio devices become the defaults for the session, then yours return. The Index's DisplayPort audio and USB mic, or a Beyond's USB audio, are matched by name. Nothing changes when no match is found.
- **SteamVR asynchronous reprojection check:** SteamVR on Linux needs `CAP_SYS_NICE` on `vrcompositor-launcher`. A SteamVR update silently removes it, and without it late frames judder. The client checks this on Start and *Fix SteamVR reprojection* repairs it with one password prompt.
- **Wi-Fi power saving:** NetworkManager cannot switch power saving off for a running connection, so the RC2 per-session change was usually refused. *Turn off Wi-Fi power saving* now changes it for your home network's saved connection and reconnects once (between sessions).
- **Wi-Fi radio diagnostics:** during Wi-Fi sessions the client samples the radio every 5 s: signal, bit rates, retries, failures, beacon loss and roaming. The summary is in the session report, and a roam to another access point is logged.

### Per-headset settings (client)
Codec, bitrate, stream size, foveation, 10-bit, gradual recovery and automatic bitrate are remembered per headset family. Switching between an Index and a Beyond switches their settings too.

### Host driver build
The SteamVR driver is rebuilt from the pinned ALVR revision with patches 0001–0012, using the same toolchain as RC3: Rust 1.97.1, Clang/LLD 19.1.1 (MSVC ABI), MSVC CRT 14.44 and Windows SDK 10.0.26100 (static CRT), and a static libvpl 2.15.0.

It is built **without ALVR's optional `gpl` feature**. That feature only adds an x264/FFmpeg CPU encoder fallback, which is too slow for VR. The driver imports no FFmpeg library, so the GPL FFmpeg 8.1 runtime RC1–RC3 carried (`avcodec-62.dll` and six others) is removed from the host package. Hardware encoding (AMF, NVENC, VPL) is unaffected.

The embedded Python runtime, launcher and helper programs are unchanged from RC1. The manifest `dist/host/vrostbridge-build.json` records the compiler, patch hashes and driver hash.

## Compatibility
- The RC4 client strips RC4 settings for RC1–RC3 hosts. AV1 becomes HEVC there.
- An RC3 client with an RC4 host works as RC3.
- The host advertises driver features (`pacing`, `gaze-foveation`, `intra-refresh`, `optical-centre`) from its driver manifest. A host with an older driver never receives settings it cannot apply.

## Not in RC4, and why
- **Staying on one Wi-Fi access point.** NetworkManager refuses to change the access-point lock on a running connection, as it does for power saving. A permanent lock would stop a handheld from roaming elsewhere. The diagnostics report roams instead.
- **Keeping the client's SteamVR open after Stop.** Quick resume keeps the host side, the slower half of startup. Local SteamVR settings are restored on every Stop as before.
- **Waking the host from the headset.** The in-headset panel belongs to the native client, which starts after the host answers. Wake progress shows on the client page and in Game Mode.

## How to test (please report each)
1. **Install** client RC4 and host RC4. Check both pages show 1.0.0-rc4 and your settings are unchanged. The host page lists stream features including `pacing`, `gaze-foveation` and `optical-centre`.
2. **Network test:** on Ethernet and on Wi-Fi, press *Test network now* and note the result. Start a session and check the log line `Stream: … (measured); … pacing … Mbit/s`.
3. **Foveated encoding, Index:** *Subtle* (Auto), then *Off*, same scene. Look for any blur or seam towards the edges and compare detail at the centre. Try *Balanced*.
4. **Foveated encoding, Beyond 2e:** without eye tracking (*Subtle*/*Balanced*), then with calibrated eye tracking and *Eye-tracked*. Look around without moving your head: the edges should look as sharp as the centre wherever you look. Report any lag, shimmer or blur. Save a report (it includes `VROST_GAZE_FOVEATION`). Then try the *Ultra* stream size.
5. **10-bit:** dark gradients and skies on HEVC, with *10-bit colour* on and off.
6. **Pacing:** with the handheld on Ethernet (1 or 2.5 Gbit/s), compare *Pace video* on and off at a high bitrate. Look at lost frames and stutter.
7. **Gradual recovery on Wi-Fi:** turn it on and look for `Host intra refresh confirmed` in the client log. Compare how losses look (brief smear instead of a freeze).
8. **In-headset status:** stop the host companion mid-session and check the headset says what is happening. Try the quality line.
9. **Wake-on-LAN:** after one session, put the host to sleep, then Start from the client. Also try *Sleep after a session* on the host.
10. **Quick resume:** set 15 minutes, Stop, Start again. The second start should skip the host's SteamVR startup.
11. **Headset audio:** check game sound and the microphone use the headset, and your defaults return after Stop.
12. **AV1** (if the host GPU supports it): select AV1 and compare with HEVC.

Build and test evidence: [evidence/rc4-build-1.0.0-rc4.json](../evidence/rc4-build-1.0.0-rc4.json).
