# VROSTBridge 1.0.0.15

SteamVR's volume slider now works in the headset, and the faults that ended 1.0.0.14 sessions with the wrong explanation (or none) now say what happened and what to do. Every package carries **1.0.0.15**, and the [1.0.0.14 notes](history/RELEASE_1.0.0.14.md) still apply.

**Install** over 1.0.0.14 on each PC: `VROSTBridge-Host-Setup-1.0.0.15.exe` on the rendering PC, and `VROSTBridge-Bazzite-Installer-1.0.0.15.zip` or `VROSTBridge-Client-Setup-1.0.0.15.exe` on the headset PC. Settings, pairing, the network test, Baballonia and its calibration stay. The stream is unchanged, so either PC still works with the other at 1.0.0.14; turning the rendering PC's driver back on from the headset PC needs 1.0.0.15 on both.

## What changed

- **Volume from SteamVR's dashboard** (rendering PC). The headset's game audio is captured on the rendering PC from the playback device SteamVR uses for the headset, and Windows hands that capture the device's mix *before* the device's own volume. The dashboard's slider sets exactly that volume, so it changed nothing. The capture now reads the device's volume and applies it, ramped over each 10 ms packet so a change never clicks, before the soft limit that keeps loud mixes from clipping. Windows' own volume control for that device works the same way. Mute is left alone: the host mutes the device itself while streaming so the PC's speakers stay quiet. This is the first new host driver since 1.0.0.9. One thing to know: the headset now follows that device's volume, so if it was turned down for the PC's speakers, turn it up in the dashboard.
- **SteamVR's safe mode on the rendering PC.** After a crash SteamVR can start in safe mode, which turns add-on drivers off and keeps them off afterwards. VROSTBridge starts SteamVR on the rendering PC without a window, so nobody saw SteamVR's notice: the driver never loaded and the headset PC looked for a host that never appeared. The host now checks SteamVR's settings before starting it (and SteamVR's log while it starts) and stops at once with the reason. **Turn the driver back on**, on the headset PC's page or in Game Mode, or on the host's page, fixes it while SteamVR is closed. A driver switched off by hand in SteamVR's add-ons gets the same message and button.
- **USB hubs nested too deep** (Bazzite). USB allows five hubs in a chain, and Linux turns off a hub any deeper with everything on it. A headset brings hubs of its own (a Beyond has three), so a dock with two of its own, or a PC whose ports already sit behind a hub, is enough to lose the controllers' radios while the headset half-appears. The client reads the PC's USB tree when a session starts, and the page and Game Mode say to plug the headset into a port on the PC itself, or a dock plugged straight in.
- **The headset's USB dropping out.** When the headset's USB loses power for a moment, the headset and its controllers (whose radios are in the headset) disconnect together, and SteamVR refuses the next frame: the session ended with *SteamVR texture submission failed: 105*. The client now notices the headset and a controller disconnecting within a second of each other and says the USB dropped out, pointing at its 5 V supply: a port on the PC itself, or a dock or hub with a stronger power supply.
- **Controllers that can't see the base stations.** A controller that is on and connected but not tracked while the headset tracks has its sensors blinded: misted up after coming in from the cold, covered, or with the base stations off or out of view. It now gets a line in the headset and a note on the page and in Game Mode.
- **The bitrate starts where it settled** (headset PC). Every 1.0.0.14 session started at the ceiling and spent its first minute stepping down to where the session before had settled, often finding again that a lower bitrate decoded no faster. The headset PC now remembers, for each stream (codec, size, refresh rate, cable or Wi-Fi), the bitrate the last session of at least two minutes spent longest at, and whether its decoding followed the picture size rather than the bitrate. The next session starts there and steps up as usual while the stream runs clean.
- **Game audio through stream stalls** (headset PC). On a wired link the game audio ran dry almost only when the whole stream stalled for 70 to 220 ms (the video stalled at the same moments), and each time the audio buffer grew by 10 ms: one session ended with the sound 125 ms late, which a rhythm game feels. The buffer now grows only for a gap one more step would have bridged. A stall still fades the sound for a moment, but no longer leaves it late for the rest of the session.
- **A lower power limit than before** (headset PC). When a session is short of frames while the PC draws much less power than in its last smooth session (three minutes or more near the refresh rate), the page, Game Mode and the headset say so with both figures, and where the power limit (TDP) is raised. Reading the power drawn works where the power limit itself can't be read.
- **The network settings check** (Bazzite). A check made while a session had Wi-Fi paused saw one adapter and said all was well, and the page kept saying so, hiding *Fix this PC's network settings*. Adapters a session paused now count, and the check is made again after a session and whenever it is more than a minute old.
- **An unplugged Wi-Fi adapter** that a session had paused is said once to be unplugged and then forgotten, instead of being asked for at every Stop.
- **Speakers on a Beyond's accessory port.** An audio mod whose device name runs the Beyond's name into one word was missed by the whole-word match and left the sound on the PC's own speakers; it is now picked as the Beyond's sound output.
- **The rendering PC's stalls, recorded.** The capture's skipped frames in the 1.0.0.14 reports came in the same seconds as the encoder's network send stalling for half a second or more. The host report now lists each second with skipped frames beside the send time (`host_events.capture_stalls`), so the next report shows whether that holds mid-session too.
- **The source builds twice.** `scripts/fetch_upstream.py` records the patch set it applied, so a second build in the same checkout goes ahead instead of failing on a patch that later patches overlap.

## How to test

Play as before and send both reports.

- **Volume:** in the headset, open SteamVR's dashboard and move the volume slider: the game gets quieter and louder, smoothly. The host log's `VROST_AUDIO_CAPTURE volume_db=` lines follow it. If the headset starts quieter than before, the device's volume was low: turn it up there.
- **Safe mode:** if SteamVR on the rendering PC ever crashes, the next Start says *turned VROSTBridge's driver off after an earlier crash (safe mode)* within seconds; *Turn the driver back on*, then Start again.
- **USB:** on Bazzite with a dock, the page says nothing about hubs when the headset works; a *nested too deep* note means a port nearer the PC. If the headset's USB drops out mid-session, the card says so instead of error 105.
- **Controllers:** a controller brought in from the cold (or with its sensors covered) gets *not tracked: wipe the sensors* in the headset within about ten seconds.
- **Bitrate:** the second session of the same stream starts where the first settled (the log's *Bitrate: starting at* line) and doesn't open with three steps down.
- **Audio:** the page's game-audio line keeps its aim near the starting value through a long session even when it reports a few dropouts.
- **Network settings:** with cable and Wi-Fi both on the home network, *Fix this PC's network settings* stays offered until it is used, during and after sessions.

## Known limits

- The volume is the captured device's. If SteamVR's audio settings on the rendering PC override the headset's playback device with another one, the slider moves that other device and the headset doesn't follow.
- The hub check reads Linux's USB tree; on Windows a hub too deep shows only as a device that doesn't start.
- The power comparison needs one smooth session first, and a reading of the power drawn (the GPU's sensor on most PCs with integrated graphics).
- The rendering PC's stalls are recorded, not fixed.
- Starting a session from Game Mode on Bazzite is unconfirmed (use Desktop Mode), and the Windows client hasn't run on real hardware.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.15.json](evidence/vrostbridge-build-1.0.0.15.json).
