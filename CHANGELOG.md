# Changelog

Each version in a few lines, newest first. The release notes in `docs/` have the detail, the reasons and a *How to test* list. Every package always shares one version (the host, the Bazzite client and, from 1.0.0.7, the Windows client): install the host and your client of the same release together, over the previous one.

## Unreleased

- **A tidier top level** in the repository: the scripts that build or start each part from the source and the two desktop shortcuts are in `scripts/` (the root's `Build-Client-Linux.sh` wrapper is gone: `scripts/Build-Client-Linux.sh` is the script itself), the Windows client's CMake toolchain is in `scripts/cross/`, the client's runtime assets are in `client/assets/`, `CONTRIBUTING.md` and `SECURITY.md` are in `.github/` and the source's start page is in `docs/`. The programs and the packages are unchanged.

## 1.0.0.11

- **A lighter frame pass.** The headset PC's one-pass frame shader works out the foveation inverse's terms once per frame instead of for every pixel and gives each eye its own workgroups: about a third less work per pixel by the GPU compiler's own estimate, the same picture. One shader for every GPU.
- **Only what the lenses show.** Pixels SteamVR says never reach the eye (the headset's visible-area mesh and the lens distortion's outline, with a margin) are not computed.
- **Keeping the frame rate:** while the headset PC's graphics hold the stream below refresh, each eye is shown at 90 %, then 80 % of its size instead of SteamVR halving the frame rate, and at full size again once there is room.
- **Measured on the GPU:** the frame pass's GPU time (on the page and in the reports' session timeline) and that of copying each decoded frame into the pass's images (on the client's `TIMING` lines, which the reports carry).
- The Beyond's 2560 size is called **Native** (it was High).
- **Ready to share:** the first public release. A documentation home (`docs/README.md`), troubleshooting, the licences in plain words, the older records in `docs/history/`, and the source offered split by part (rendering PC, Windows headset PC, Bazzite headset PC) as well as whole. The logo and icons are licensed CC BY-ND 4.0.
- Version 1.0.0.11 for every package; the host's driver is 1.0.0.9's.
- [Release notes](docs/RELEASE_1.0.0.11.md)

## 1.0.0.10

- **The menu button, as one clean press.** The headset PC's own SteamVR takes input focus while the system button is held, which cut the press short on the rendering PC or lost it. The press is now forwarded for at least 0.12 s, also read from SteamVR's own button event, and a short loss of focus keeps the other controls as they were.
- **Game Mode (Bazzite):** SteamVR starts without gamescope's display marker and finds the headset display under a plain name; a compositor that cannot take it stops Start at once with the reason; the log and every report say which display SteamVR was given and which programs reached the headset display.
- **Automatic bitrate on Wi-Fi:** a step down for uneven arrival alone is undone when frames arrive no more evenly at the lower bitrate, and seconds in which too few frames arrived at all no longer count as uneven.
- **Base stations:** three tries; on Bazzite a fresh search before each retry and the signal strength of a station not reached.
- Version 1.0.0.10 for every package; the host's driver is 1.0.0.9's.
- [Release notes](docs/history/RELEASE_1.0.0.10.md)

## 1.0.0.9

- **Clean game audio.** On the headset PC a new player opens the sound device at the stream's own rate (converting it properly where it must), keeps a steady buffer that rides out clock drift, bridges lost packets and fades rather than clicks when it runs dry. On the rendering PC the capture reads Windows' loopback stream itself: silence sent as silence, the audio engine kept running through quiet moments, audio priority, and surround folded to stereo by speaker position.
- **Lost packets rebuilt on the way:** parity for video frames and game audio, so a lost piece no longer freezes a frame until the next keyframe, and the automatic bitrate steps down less often.
- **Eye tracking yields on a busy processor (Bazzite):** Baballonia gets a quarter of the normal CPU weight, so the stream goes first when time is short.
- **Base stations:** one that did not answer gets a second try, and on Bazzite they are reached one at a time.
- **The host keeps only the 3 newest client reports.**
- Version 1.0.0.9 for every package, with a new host driver (patches 0021 to 0023).
- [Release notes](docs/history/RELEASE_1.0.0.9.md)

## 1.0.0.8

- **Steadier streams.** Automatic bitrate stops at what the picture needs (about 49 Mbit/s for a Beyond 2e at Standard with eye-tracked foveation, where 1.0.0.7 asked for 195), steps down when decoding falls behind or frames go missing and back up as the stream runs clean, and the rendering PC applies each change while the stream runs. Video is paced at 70 % of what the network test carried without loss.
- **Use recommended settings:** one button for the stream settings that work best.
- **Balance performance during sessions (Bazzite):** only while video streams, the power limit at its maximum on the charger and the GPU clock held higher when frames run late with the GPU at a low clock, never at the processor's or eye tracking's expense; set back at Stop.
- **Base stations off when VROSTBridge closes,** and on again at each Start, over the headset PC's Bluetooth (SteamVR 2.0 base stations; both clients), with buttons to do it now.
- **Keep my gaze centred:** a calibration whose straight ahead sits off to one side is corrected as you play and remembered for that calibration; **Use previous calibration** goes back one.
- Version 1.0.0.8 for every package; the host's driver is unchanged.
- [Release notes](docs/history/RELEASE_1.0.0.8.md)

## 1.0.0.7

- **Windows client.** The headset PC can now run Windows 10 or 11 as well as Bazzite: the same client, page and stream, decoding on the GPU SteamVR uses, with eye tracking through Baballonia. A per-user installer (no administrator rights) or a portable ZIP.
- **Made for Windows:** a tray icon with *Start session* and *Stop session* and notifications, start at sign-in, firewall rules in one step (private networks only), Wi-Fi media streaming mode and no background scans during sessions, *Best performance* while streaming with your own power mode restored afterwards, and plain messages when SteamVR is not set up yet. Game Mode stays with Bazzite.
- **One-pass frame path on more GPUs:** drivers with only the minimum push-constant space get a variant of the frame shader instead of the older two passes.
- Version 1.0.0.7 for every package. The host's driver is unchanged; its companion lists network adapters through Windows' own interface, shared with the Windows client.
- [Release notes](docs/history/RELEASE_1.0.0.7.md)

## 1.0.0.6

- **Gaze reach.** The Eye tracking card (and Game Mode) shows how far your gaze went each way this session, with **Measure again**. If the sharp region stops short of where you look, the usual cause is Baballonia's calibration: it only learns as far as your eyes went, so turn your head further while you follow the dot. Baballonia reports up to 45° from straight ahead.
- **10-bit colour, second attempt.** The host asks AMD's converter for plain SDR first and checks the result with a test picture, as before; if neither way checks out it streams 8-bit, so blacks stay black either way.
- **A fairer network test.** The rendering PC's own sending time no longer counts as network delay, so automatic bitrate stops being needlessly modest. Results limited the old way are tested again once.
- **Controllers, measured.** Reports now show the stream's prediction miss for each controller, and the distance between the controllers when held still as the headset PC tracks it and as the rendering PC receives and uses it, to find why the models can overlap more than on a directly connected headset.
- [Release notes](docs/history/RELEASE_1.0.0.6.md)

## 1.0.0.5

- True blacks with 10-bit colour: the host converts and labels the colour itself, checks it at every start, and falls back to 8-bit rather than show grey blacks.
- Faster, steadier eye tracking: the eye cameras at their fastest rate with their pictures passed on as sent, a quicker filter, and a sharp region that keeps still for small eye movements.
- *Headset not tracked* when SteamVR on the headset PC has lost the headset, instead of a silent wait.
- Ultra shown at SteamVR's own size on the headset PC, and a network test that measures the network rather than how busy the PC was.
- [Release notes](docs/history/RELEASE_1.0.0.5.md)

## 1.0.0.4

- The sharp region stays where you last looked through blinks and pauses in the gaze.
- A processor starved by a shared power limit is noticed, explained, and fixed in one step where Steam's performance settings allow.
- Video paced below what the network path carried without loss; no grey flashes for short stalls; Game Mode's D-pad and stick go where you expect.
- Reports cover the whole session in 10-second steps.
- [Release notes](docs/history/RELEASE_1.0.0.4.md)

## 1.0.0.3

- Game Mode recognises the headset display reservation; the rendering PC's SteamVR closes reliably.
- A calmer foveated picture: the reduced area is filtered on the host and enlarged smoothly on the headset PC.
- Eye tracking about four times as fast, a clearer eye-tracking view, and Baballonia kept across updates.
- [Release notes](docs/history/RELEASE_1.0.0.3.md)

## 1.0.0.2

- Baballonia's eye calibration no longer crashes as a SteamVR overlay on Bazzite.
- [Release notes](docs/history/RELEASE_1.0.0.2.md)

## 1.0.0.1

- One name everywhere, and the line under it.
- [Release notes](docs/history/RELEASE_1.0.0.1.md)

## 1.0.0

- The first release build: Baballonia's calibration over the stream, the logo, the licence and notices for publishing the source.
- [Release notes](docs/history/RELEASE_1.0.0.md)
