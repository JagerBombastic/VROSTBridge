# VROSTBridge 1.0.0-rc4.1 — fixes from the first RC4 test

RC4.1 fixes what the RC4 test session (3 October 2026) showed: choppy head and controller motion, an AMD encoder that fell two frames behind, a Wi-Fi dropout the stream never recovered from, slow starts and several checks that reported the wrong thing. It changes no stream format and keeps every RC4 setting.

**Install:** Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc4.1.zip` and Windows host `VROSTBridge-Host-Setup-1.0.0-rc4.1.exe` (SteamVR driver rebuilt from patches 0001–0015). Install both; most fixes need both sides. Settings, pairing and logs are kept.

## What changed

### 1. Head and controller motion is predicted once (client + host patch 0014)
**Found:** every pose was predicted twice. Since ALVR 4a463af5 the host extrapolates each pose it receives by the measured motion-to-photon delay (about 37 ms in the test). The VROSTBridge client, since RC2, also asked SteamVR for poses at that future time. Motion was therefore projected about twice as far ahead as it should be, so small hand and head movements overshot and jittered: the choppiness you felt.

**Now:** the client reads the current pose and only labels it with the time it will be shown, exactly like ALVR's own clients. The host predicts once.
- *Frame pacing → Motion prediction* (on) keeps its meaning: frames show where your head and hands will be. Turning it off now turns prediction off on an RC4.1 host too (patch 0014), for comparison.
- Head rotation is still corrected at the last moment by SteamVR on the headset PC.

### 2. The AMD encoder no longer falls behind (host patch 0013)
**Found:** after a slow frame (the game launch at 13:05) the AMF encoder stayed two frames behind for the rest of the session: encode time rose from about 5 ms to 27 ms, adding about 22 ms of latency and making frames come out in pairs (one repeated, one skipped).

**Now:** the host collects every finished frame each time, oldest first, and waits only for the frame it just submitted, at most one frame's time per frame. A slow frame costs one frame, never the rest of the session. The encoder is never asked for output nobody waits for, the driver keeps Windows' timer at 1 ms while encoding (a 1 ms wait could otherwise last 15.6 ms), and a frame lost inside the encoder for 250 ms resets it. The host log line `VROST_HOST_AMF` adds `late_frames`, `backlog_cleared` and `max_backlog`.

### 3. The stream comes back after a Wi-Fi dropout (host + client)
**Found:** at 13:08:18 the Wi-Fi link dropped for a few seconds. The companions stayed connected, but ALVR never reconnected the video: it only looked for the headset PC by multicast discovery, which the dropout and the second Wi-Fi adapter (see 4) defeated.
- **Known address:** the client tells the host its ALVR name; the host adds the address the session uses to ALVR's list, so ALVR retries that address directly every second. It follows the address if it changes during a session. Your own manual addresses are kept.
- **Automatic recovery:** if the headset still has no video, the host restarts its SteamVR session to reconnect: after 8 s when ALVR is stuck in *Disconnecting*, 20 s when ALVR still claims to stream, or 45 s otherwise. At most twice per session, three minutes apart, and only while the headset PC confirms over the companion link that its video is gone (a slow start never counts). The headset shows what is happening. This restart closes a running VR game on the host, which is why it waits that long.
- The client log records heartbeat failures and recoveries; the host log records every ALVR connection-state change.

### 4. A second Wi-Fi adapter is paused during sessions (client)
**Found:** the handheld had two Wi-Fi adapters on the same home network. They share one radio channel, and ALVR's discovery could see the address the stream was not using.

**Now:** *This PC → Pause other Wi-Fi adapters on the same network* (on) disconnects other Wi-Fi adapters on the stream's network for the session and reconnects them on Stop. If the companion ends unexpectedly they are reconnected the next time it starts. Wired adapters and other networks are never touched. Turn the option off to get a warning instead.

### 5. The host's ALVR log is in reports (host)
**Found:** the report had no ALVR driver log (`session_log.txt`), so host-side events around the dropout were missing.

**Now:** the driver writes its log for every session (tracking, button and haptic logging stay off). The previous SteamVR start's log is kept as `session_log.previous.txt`, and both are in host reports.

### 6. Faster starts (host patch 0015 + both companions)
**Found:** the session took about 75 s to start. Host SteamVR restarted once to apply the headset's display settings and that restart hung for 20 s; local SteamVR's compositor also crashed once and took 33 s to come back.
- **No host restart for the headset (patch 0015):** the host companion knows the headset's display before SteamVR starts, so the driver now starts the virtual headset with the configuration the client is about to negotiate. The log says *Host headset configuration prepared for the client display at SteamVR start; no restart needed.*
- **Shorter fallback:** if a restart is still needed, SteamVR processes that do not exit are stopped after 6 s instead of 20 s.
- **Compositor crash:** when local SteamVR's compositor reports a watchdog abort, the client stops it at once. SteamVR starts a new one immediately instead of waiting about 30 s for the crash report.
- **Timeline:** the client log now marks `STARTUP …` steps (local display, host accepted, client started, first video) with their time since Start.

### 7. Wi-Fi pacing uses the measured rate (client)
**Found:** Wi-Fi pacing used 60 % of the radio's link rate (1729 Mbit/s here), far above what the air link really carries, so frame bursts still flooded the access point.

**Now:** on Wi-Fi the host paces at the last network test's clean rate on that path; without a test result Wi-Fi stays unpaced. Pacing is used down to 1.25 x the bitrate (was 2 x). The network test has finer steps (80, 150, 200, 250, 300, 400, 500, 600, 750 and 900 Mbit/s; about 15 s) so the clean rate is closer to the truth.

### 8. Microphone check (host)
**Found:** the host's audio check helper was blocked by Windows (*Access is denied*), and the page then blamed a missing virtual cable.

**Now:** if the helper cannot run, the device list is read from Windows' device manager instead. The message says which step failed, or that no virtual audio cable (VB-CABLE, VoiceMeeter, Virtual Audio Cable) with both sides is active.

### 9. Asynchronous reprojection check (client)
**Found:** the check said reprojection was ready, but SteamVR's own log said *Async support disabled by user setting or a direct mode driver* at every start since 2 October.

**Now:** the check also reads SteamVR's `enableLinuxVulkanAsync` setting and its last compositor start. If the setting is off (or SteamVR reported it off), *Fix SteamVR reprojection* turns it on while SteamVR is closed; it applies at the next start. If SteamVR still reports it off with the setting on, the headset's display driver disables it, and the page says so.

### 10. Wake-on-LAN detection (host)
**Found:** *Ethernet 2 does not report Wake-on-LAN support*, although its driver may have the setting.

**Now:** the check also reads the standard *Wake on Magic Packet* advanced property and whether Windows lets the adapter wake the PC. Advice names the adapter the session actually uses (with its device name) and says what to change, including the *Allow this device to wake the computer* tick box. USB Ethernet adapters often cannot wake a PC.

### 11. Foveated encoding is visible (client)
**Found:** the page said *Foveation following your eyes* whenever the sharp region was centred per eye, without eye tracking.

**Now:**
- The client page's foveation line shows what the running stream does: the preset; whether the sharp region follows your eyes (with the share of frames that had gaze), is centred on each eye's view, or is fixed; and how many fewer pixels are encoded.
- The headset quality line (*Show stream quality in the headset*) adds the same in short, for example `fov balanced centred -51% px`.

### 12. Fewer repeated frames
Most repeats in the test came from the encoder backlog (2), Wi-Fi bursts (7) and the dropout (3). In addition, *Catch late frames* now waits up to 3 ms (a quarter of a frame at 75–90 Hz; was 1.5 ms) for a frame that is just late, still keeping 2 ms for SteamVR to show it. In the test more than half of these waits ran out at 1.5 ms.

## Compatibility
- RC4.1 adds one host capability (`quality-4`) for the motion-prediction switch, the client's ALVR name and the stream state in heartbeats. The client sends them only to hosts that advertise it; an RC4 host works as before.
- Driver features `prediction-switch` and `init-config` come from the driver manifest; `stream-recovery` from the companion.
- An RC4 client with an RC4.1 host works as RC4 (no recovery: it sends no stream state).

## How to test (please report each)
1. **Install** both. Both pages show 1.0.0-rc4.1; the host page lists `prediction-switch`, `init-config` and `stream-recovery`.
2. **Motion:** play Beat Saber or similar and move your hands quickly. Is the choppiness gone? Compare *Motion prediction* on and off.
3. **Start time:** time from Start to picture; check the client log's `STARTUP` lines and that the host log says *no restart needed*.
4. **Encoder:** play for 10 minutes including a game launch. Save a host report: `VROST_HOST_AMF … max_backlog` should stay 0 or 1 and encode time near 5 ms.
5. **Dropout:** during a session, switch the Wi-Fi access point off for 5–10 s (or walk out of range). The video should come back by itself.
6. **Second adapter:** the client log should say the other Wi-Fi adapter was paused, and it should reconnect after Stop.
7. **Foveation:** check the client page line and the headset quality line for each preset.
8. **Reprojection:** if the page offers *Fix SteamVR reprojection*, use it (SteamVR closed), start a session and check SteamVR's log no longer says *Async support disabled*.
9. **Microphone and Wake-on-LAN:** read the host page messages.

Build and test evidence: [evidence/rc4.1-build-1.0.0-rc4.1.json](../evidence/rc4.1-build-1.0.0-rc4.1.json).
