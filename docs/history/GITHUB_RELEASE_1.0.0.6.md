# GitHub release text for 1.0.0.6

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.6`, title **VROSTBridge 1.0.0.6**.
Attach `VROSTBridge-Host-Setup-1.0.0.6.exe`, `VROSTBridge-Bazzite-Installer-1.0.0.6.zip` and `SHA256SUMS.txt`
(and `VROSTBridge-Host-Portable-1.0.0.6.zip` if you want to offer it). See [PUBLISHING.md](../PUBLISHING.md).
The links assume the repository is `JagerBombastic/VROSTBridge`; change them if you name it differently.

---

**VR outside the box.** The first public release of VROSTBridge: stream SteamVR games from a Windows PC to a PC VR headset plugged into a Bazzite Linux PC somewhere else in the house.

Tested with a Valve Index and a Bigscreen Beyond 2e, an AMD rendering PC and a Ryzen handheld as the headset PC, on one home network. It has met exactly one set of hardware so far, so reports from yours are very welcome.

### Download

- **Windows (rendering PC):** `VROSTBridge-Host-Setup-1.0.0.6.exe`. Not code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**.
- **Bazzite (headset PC):** `VROSTBridge-Bazzite-Installer-1.0.0.6.zip`. Extract the whole ZIP and open **Install VROSTBridge.desktop**.
- `SHA256SUMS.txt` lists each file's SHA-256, so you can check your download.

Install both, then follow the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md). Your headset must already work in SteamVR on the Bazzite PC.

### New in 1.0.0.6

- **Gaze reach** for Beyond 2e eye tracking: how far your gaze went each way, with *Measure again*, and calibration steps that turn your head far enough for the sharp region to follow you toward the edges.
- **10-bit colour, second attempt:** the host tries a plain SDR conversion first and checks it with a test picture; if it can't confirm exact colour it streams 8-bit, so blacks stay black.
- **A fairer network test:** the rendering PC's own sending time no longer counts as network delay, so automatic bitrate is no longer needlessly low.
- **Controllers, measured:** reports show the stream's prediction miss and the distance between the controllers as tracked and as used, to chase a small overlap seen when they touch.

Everything since the first test build is in the [changelog](https://github.com/JagerBombastic/VROSTBridge/blob/main/CHANGELOG.md); the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.6.md) have the detail.

### Good to know

- For a trusted home network only: please don't expose it to the internet.
- Eye tracking uses [Baballonia](https://github.com/Project-Babble/Baballonia) by Project Babble, downloaded only if you turn it on and accept its licence (personal, non-commercial use).
- Found a bug? Open an issue with the two reports (client page: *Send report to host*, then save both on the host page). They contain local network addresses and hardware names, so have a look before posting.

MIT licence. Not affiliated with Valve, Bigscreen, ALVR or Project Babble.
