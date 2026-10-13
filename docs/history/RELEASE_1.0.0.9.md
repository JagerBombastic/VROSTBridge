# VROSTBridge 1.0.0.9

1.0.0.9 fixes **game audio** and gets a little more out of the video. The 1.0.0.8 test was smooth in motion and picture, with a few short stutters, but the game audio lagged, broke up and crackled now and then. VROSTBridge played and captured game audio with ALVR's own code, unchanged, and the causes were at both ends:
- **On the headset PC** the sound device was opened with the audio library's default settings. On Bazzite that is 44.1 kHz, while the stream is 48 kHz, and the library converted the rate without a filter, which turns high frequencies into grit. Every lost packet also emptied the buffer, so the sound broke off and restarted. And because the two PCs' sound clocks never run at exactly the same speed, the buffer drifted between empty and twice its size: a growing delay, then a skip.
- **On the rendering PC** the capture ignored Windows' mark for silent packets, whose contents can be stale sound or noise. It sent nothing at all while the PC was quiet, so every sound after a pause started with an empty buffer on the headset PC. It ran at normal priority, behind a busy game, and folded 5.1 sound to stereo one channel out of place.

The stutters were mostly lost video frames. Each one meant a short freeze while a keyframe was fetched, and they took the automatic bitrate down from 65 to 40 Mbit/s during the test.

So 1.0.0.9:
- plays game audio through a new player on the headset PC (both clients);
- captures game audio straight from Windows on the rendering PC;
- rebuilds lost packets on the headset PC from a little extra data the rendering PC sends;
- gives eye tracking a smaller share of a busy processor (Bazzite);
- gives a base station that did not answer a second try (and on Bazzite reaches them one at a time);
- keeps only the 3 newest client reports on the host.

Every package carries **1.0.0.9**. Everything in the [1.0.0.8](RELEASE_1.0.0.8.md) and earlier notes still applies.

**Install:**
- **Rendering PC:** install `VROSTBridge-Host-Setup-1.0.0.9.exe` over 1.0.0.8. Its driver is new this time.
- **Headset PC:** install `VROSTBridge-Bazzite-Installer-1.0.0.9.zip` or `VROSTBridge-Client-Setup-1.0.0.9.exe` over 1.0.0.8. Settings, pairing, Baballonia and its calibration stay.

Install both PCs together. Each still works with the other at 1.0.0.8, but then only half of the audio fix applies, and lost packets are not rebuilt.

## Game audio

### Playing it on the headset PC (both clients)
- **At the stream's own rate.** The player opens the sound device at 48 kHz when the device allows it, and PipeWire on Bazzite does, so nothing needs converting. Where the rate must be converted, a proper filter (windowed sinc) does it, without the grit.
- **A steady buffer.** The player keeps the host's buffer setting in hand, as before: 30 ms on a wired link, 50 ms on Wi-Fi. It nudges its speed by at most 0.5 %, too little to hear, to stay there. That absorbs any drift between the two PCs' sound clocks (real ones differ by far less), so the buffer never reaches an edge: no growing delay and no skip.
- **A lost packet is bridged** with a short cross-fade instead of emptying the buffer.
- **If the buffer still runs dry,** because packets came very late, the sound fades out rather than clicking. From then on the player keeps 10 ms more in hand, up to 150 ms, and gives back 5 ms after every calm minute. A pause in the game's own sound is not a dropout.
- **The device buffer:** on Bazzite the sound device now holds 40 ms instead of 100, so the sound comes sooner. On Windows 40 ms is asked for too, and the player's thread runs in Windows' audio scheduling class.
- **Where to see it:** under *Use the headset's speakers and microphone* the page says *Game audio: 48 kHz, 31 ms buffered (aim 30 ms); no dropouts.*, or how often it ran dry. The client log's `TIMING` lines carry the same every second (`audio_stream_hz`, `audio_device_hz`, `audio_buffer_ms`, `audio_underruns` and the others), and the reports count dropouts and gaps in the audio stream for every 10 seconds of the session.

### Capturing it on the rendering PC
The host now reads Windows' loopback capture itself:
- **Silent packets are sent as true silence.**
- **A silent playback stream keeps Windows' audio engine running**, so quiet moments are sent as silence too and the headset PC's buffer stays full.
- **The capture thread runs in Windows' *Pro Audio* class**, so a game busy on every core does not hold it back.
- **Surround sound is folded to stereo by speaker position**, with a soft limit instead of hard clipping.
- If the sound device cannot be opened this way, the host uses the old capture, and its log says why (`VROST_AUDIO_CAPTURE fallback=cpal`). The `VROST_AUDIO_CAPTURE` lines name the format and, every 10 seconds, give the running totals of packets, silent packets and capture gaps, with the longest gap of those 10 seconds.

## Lost packets rebuilt on the way
- **How it works.** For each video frame the rendering PC adds one parity piece for every 10 pieces of the frame, and it sends each game-audio packet twice. When a piece goes missing, the headset PC rebuilds it, so the frame plays instead of freezing until a keyframe comes. The parity groups are interleaved, each covering pieces spread across the whole frame, so a short run of pieces lost in a row (up to a tenth of the frame) can be rebuilt as well.
- **Why.** On a healthy wired network single pieces still go missing now and then, and before 1.0.0.9 each one cost a whole frame. Fewer lost frames also means the automatic bitrate steps down less often, and High needs the bitrate to hold.
- **Cost:** about a tenth more video data (5 Mbit/s at 50 Mbit/s), paced along with each frame, plus about 2.3 Mbit/s for the audio's second copy.
- **Where to see it:** the page's bitrate line adds *Rebuilt on the way: 3 frames that would have been lost.*, and the reports count rebuilt frames for every 10 seconds.
- A host or client at 1.0.0.8 simply goes without: the older client ignores the parity, and the older host sends none.

## Eye tracking yields on a busy processor (Bazzite)
Baballonia runs as its own service, where its lower process priority only ranks its own threads against each other. It now runs with a quarter of the normal CPU weight. When the processor is short, the stream goes first; when there is time to spare, Baballonia runs at full speed as before. Frames that reached the headset PC two or three at a time in the 1.0.0.8 test are what a briefly overloaded processor looks like.

## Base stations: a second try
In the 1.0.0.8 test, only one of two base stations woke: Bluetooth dropped one of two connections made at the same moment. On Bazzite the headset PC now reaches the stations one after another, as the Windows client already did. Both clients now try a station that did not answer once more, a second later.

## Client reports on the host
The host keeps the 3 newest reports sent from a headset PC (it kept 20), and trims the folder as soon as it starts.

## How to test
Install 1.0.0.9 on both PCs and stream for a few minutes. Please report:
1. **Game audio:** music or dialogue for a few minutes. Is it clean, without crackle, lag or breakups? Does the *Game audio* line stay at *no dropouts*?
2. **High** with eye-tracked foveation: does the bitrate hold, and how many frames does *Rebuilt on the way* count?
3. Do both base stations wake at Start and sleep when VROSTBridge closes?

Send reports from both PCs.

## Known limits
- **Nothing new here has run on hardware yet.** The player is tested in code against drifting clocks, bunched and late packets, stalls and losses. The packet repair is tested with lost and reordered pieces. The Windows capture's conversion is tested in code, but the capture itself has only been built for Windows, not run there.
- Packet repair rebuilds one missing piece per group of 10. Heavier loss still costs a frame and a keyframe, as before.
- The Windows client has still not run on real hardware (see 1.0.0.7).

Build and test evidence: [evidence/vrostbridge-build-1.0.0.9.json](../evidence/vrostbridge-build-1.0.0.9.json).
