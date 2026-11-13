# Changelog

Each version in a few lines, newest first. The release notes in `docs/` have the detail, the reasons and a *How to test* list. Every package shares one version (the host, the Bazzite client and the Windows client): install the host and your client of the same release together, over the previous one.

## 1.0.0.15

SteamVR's volume slider in the headset, and the faults that ended 1.0.0.14 sessions with the wrong explanation now say what happened.

- **Volume from SteamVR's dashboard** (rendering PC). The game audio is captured from the headset's audio device before the device's own volume, so the dashboard's slider changed nothing. The capture applies that volume now, ramped so a change never clicks, before the soft limit; mute stays the host's own (it mutes the device while streaming). The first new host driver since 1.0.0.9.
- **SteamVR's safe mode** (rendering PC). SteamVR turns add-on drivers off after a crash and keeps them off; the headset PC then searched for a host that never appeared. The host checks before starting SteamVR (and in SteamVR's log while it starts) and stops at once with the reason; *Turn the driver back on* fixes it from the headset PC or the host page while SteamVR is closed.
- **The headset's USB** (headset PC). A hub nested too deep for Linux (five in a chain is the most, and a headset brings its own) is named at Start on Bazzite; the headset and its controllers disconnecting together is reported as the USB dropping out (its 5 V supply) instead of *texture submission failed: 105*.
- **Controllers that can't see the base stations:** on but not tracked while the headset tracks (misted sensors, base stations off) gets a line in the headset and a note on the page.
- **The bitrate starts where it settled** (headset PC): per stream, the bitrate the last session of two minutes or more spent longest at, and whether decoding followed the picture size, instead of stepping down from the ceiling at every Start.
- **Game audio through stalls** (headset PC). The audio buffer grows only for gaps one step would have bridged; stalls of the whole stream no longer leave the sound late for the rest of the session (one 1.0.0.14 session ended 125 ms late).
- **A lower power limit than before:** a session short of frames while drawing much less power than the last smooth session says so, with both figures.
- **Smaller fixes:** the network settings check counts Wi-Fi adapters a session paused and checks again later; an unplugged Wi-Fi adapter is forgotten instead of asked for at every Stop; speakers added to a Beyond's accessory port under a name run into the Beyond's are its sound output; the host report lists the capture's skipped frames beside the encoder's send time; a second source build in one checkout goes ahead.
- Version 1.0.0.15 for every package. The host's driver is rebuilt (patch 0023), the clients' transport (patch 0022) and native programs too.
- [Release notes](docs/RELEASE_1.0.0.15.md)

## 1.0.0.14

What the first external testers and 1.0.0.13's first session asked for: a sharper picture, and hands that land where they were swung.

- **Hands predicted with their acceleration** (headset PC). The rendering PC predicts each pose a measured latency ahead from its velocity alone, so a hand speeding up or slowing down landed short or long by half its acceleration times the latency squared (about 18 mm for a brisk swing; the client's check measured up to 170 to 280 mm). The client now estimates each controller's acceleration and folds it into the pose and velocity it sends, so the host's own extrapolation lands where the hand will be. The poses on the wire are the same poses, so a 1.0.0.13 host gains it too.
- **Rendered larger than it is streamed** (rendering PC). The host renders at the size the headset PC's SteamVR recommends, at most one and a half times the stream, and scales down for the encoder, as a directly connected headset renders larger than its panels. SteamVR's resolution setting on the rendering PC changes that render size, not the stream.
- **HEVC for every headset.** *Auto* codec is HEVC, 10-bit where the headset PC decodes it; H.264 only on a PC without HEVC decoding. The Bazzite client asks VA-API what the GPU decodes and remembers it, as the Windows client asks Direct3D 11.
- **More bits per pixel.** The automatic bitrate's ceiling is 0.30 bit per encoded pixel for HEVC (0.42 H.264, 0.25 AV1), about a third more than before for the same stream, when the network test and the decoder allow it.
- **One network, two adapters** (Bazzite). A headset PC on its network by cable and Wi-Fi received the video over Wi-Fi (Linux answers for both addresses on both adapters) and stalled every five minutes, while pausing Wi-Fi instead had cut one PC's cable off from the host. *Fix this PC's network settings* (two standard settings, one password prompt, kept across restarts) keeps the video on the cable, and a wired stream then leaves Wi-Fi connected. Without it Wi-Fi is still paused, but the host's reachability over the cable is checked straight away and the pause undone at once if it fails. A session says when its video arrives on another adapter.
- **The receive buffer, judged properly** (Bazzite). The stream's socket gets twice the system limit, which on Bazzite is already enough; only a limit too small for the stream (a stock kernel's) is reported, and fixed by the same page item. The reports carry the stream and gaze sockets' own drop counters.
- **Any Windows network type.** Paired headset PCs start sessions on a network Windows calls Public; the firewall rules admit only the local subnet, and a new headset PC pairs itself only from a Private network.
- **Eye tracking:** the sharp region is predicted through the head's turn, so it stays on what you look at while turning.
- **The host keeps its last session's statistics** (`host-last-session.json`), so a report made after the host's VROSTBridge restarted still carries them (`host_last_session`).
- The menu button is confirmed: a short press opens the rendering PC's SteamVR menu.
- Version 1.0.0.14 for every package. The clients' native program is rebuilt (the acceleration, the gaze prediction, VA-API's decoder profiles); the host's driver is 1.0.0.9's.
- [Release notes](docs/history/RELEASE_1.0.0.14.md)

## 1.0.0.13

What 1.0.0.12's first session on real hardware showed: its three fixes worked, and what held the picture back was the headset PC's decoder and packets lost in clumps on a wired link.

- **Choose the stream before a session.** The choices come from the last headset seen (on Bazzite also the one plugged in), so they no longer wait for Start. An Index offers *Native* (2016 × 2240, the size SteamVR renders for its lenses) at 80, 90, 120 or 144 Hz (experimental): the rate is set in SteamVR on the headset PC for the session and put back afterwards, so no SteamVR menus. A Beyond offers no choice: the stream follows the mode it is in, 2560 per eye at 75 Hz or 1920 at 90 Hz (which the headset upscales), and the mode is switched with Bigscreen's Beyond Utility on a Windows PC, on Bazzite and Windows alike. *Auto* follows the headset as it is set.
- **More parity on a wired link.** One parity packet per five video packets instead of ten when the headset PC is wired, so a run of lost packets up to a fifth of a frame is rebuilt. Wi-Fi resends lost packets itself and keeps one per ten.
- **Stopping while SteamVR starts** no longer waits out the whole 90 s start on the rendering PC: ALVR can ask SteamVR to quit only once a headset has streamed, so SteamVR's own *Exit VR* follows.
- **Reports:** both carry the network adapters' drop counters for the session, and the Bazzite client's the remembered GPU clock and whether the session held it from the start. A Game Mode start records which process started SteamVR's server, whether it runs in a container, and which display it really reaches.
- The transport's stream-counter checks know all seven counters (two were added in 1.0.0.9).
- Version 1.0.0.13 for every package. The native programs are 1.0.0.12's with the new version label; the host's driver is 1.0.0.9's.
- [Release notes](docs/history/RELEASE_1.0.0.13.md)

## 1.0.0.12

What 1.0.0.11's first session on real hardware showed (Beat Saber at Native with eye-tracked foveation, a handheld headset PC on Bazzite): it streamed well, with three things to put right.

- **The automatic bitrate forgives.** It went from 65 to 30 Mbit/s in the first 80 seconds, while the headset PC's GPU sat at a low clock and the game was starting, and never came back. When the companion holds the headset PC's GPU clock higher, the bitrate now goes back to where the session started; and a second in which frames never arrived at all (the rendering PC sent nothing for a moment) no longer counts against it, since a lower bitrate would not have sent them sooner.
- **The headset PC remembers its GPU clock.** The clock a session ended up holding is held from the next session's start, instead of being found again minutes in. It is learned per PC, used only while the GPU's level is automatic, and forgotten when a session has to set it back down.
- **The host's add-on warning** lists only add-ons registered from outside SteamVR. It used to call SteamVR's own drivers (the Index controllers' among them) third-party and suggest turning them off.
- **A tidier top level** in the repository: the build and start scripts and the two desktop shortcuts are in `scripts/` (the root's `Build-Client-Linux.sh` wrapper is gone), the Windows client's CMake toolchain in `scripts/cross/`, the client's runtime assets in `client/assets/`, `CONTRIBUTING.md` and `SECURITY.md` in `.github/` and the source's start page in `docs/`.
- Version 1.0.0.12 for every package. The native programs are 1.0.0.11's with the new version label; the host's driver is 1.0.0.9's.
- [Release notes](docs/history/RELEASE_1.0.0.12.md)

## 1.0.0.11

- **A lighter frame pass.** The headset PC's one-pass frame shader works out the foveation inverse's terms once per frame instead of for every pixel and gives each eye its own workgroups: about a third less work per pixel by the GPU compiler's own estimate, the same picture. One shader for every GPU.
- **Only what the lenses show.** Pixels SteamVR says never reach the eye (the headset's visible-area mesh and the lens distortion's outline, with a margin) are not computed.
- **Keeping the frame rate:** while the headset PC's graphics hold the stream below refresh, each eye is shown at 90 %, then 80 % of its size instead of SteamVR halving the frame rate, and at full size again once there is room.
- **Measured on the GPU:** the frame pass's GPU time (on the page and in the reports' session timeline) and that of copying each decoded frame into the pass's images (on the client's `TIMING` lines, which the reports carry).
- The Beyond's 2560 size is called **Native** (it was High).
- **Ready to share:** the first public release. A documentation home (`docs/README.md`), troubleshooting, the licences in plain words, the older records in `docs/history/`, and the source offered split by part (rendering PC, Windows headset PC, Bazzite headset PC) as well as whole. The logo and icons are licensed CC BY-ND 4.0.
- Version 1.0.0.11 for every package; the host's driver is 1.0.0.9's.
- [Release notes](docs/history/RELEASE_1.0.0.11.md)

## Earlier versions

1.0.0.10 and earlier, back to 1.0.0: [docs/history/CHANGELOG_EARLIER.md](docs/history/CHANGELOG_EARLIER.md).
