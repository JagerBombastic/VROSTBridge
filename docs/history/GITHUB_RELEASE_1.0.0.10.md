# GitHub release text for 1.0.0.10

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.10`, title **VROSTBridge 1.0.0.10**.
Attach `VROSTBridge-Host-Setup-1.0.0.10.exe`, `VROSTBridge-Bazzite-Installer-1.0.0.10.zip`,
`VROSTBridge-Client-Setup-1.0.0.10.exe` and `SHA256SUMS.txt` (and the two portable ZIPs,
`VROSTBridge-Host-Portable-1.0.0.10.zip` and `VROSTBridge-Client-Portable-1.0.0.10.zip`, if you want to offer them).
See [PUBLISHING.md](../PUBLISHING.md). The links assume the repository is `JagerBombastic/VROSTBridge`; change them if you
name it differently.

---

**VR outside the box.** Stream SteamVR games from a Windows PC to a PC VR headset plugged into another PC somewhere else in the house: a Bazzite Linux PC or a Windows PC.

Tested with a Valve Index and a Bigscreen Beyond 2e, an AMD rendering PC and a Ryzen handheld running Bazzite as the headset PC, on one home network. The Windows client has not met real hardware yet, so reports from yours are very welcome.

### Download

- **Rendering PC (Windows):** `VROSTBridge-Host-Setup-1.0.0.10.exe`.
- **Headset PC, Windows 10 or 11:** `VROSTBridge-Client-Setup-1.0.0.10.exe`. Installs for your account only; no administrator rights needed.
- **Headset PC, Bazzite:** `VROSTBridge-Bazzite-Installer-1.0.0.10.zip`. Extract the whole ZIP and open **Install VROSTBridge.desktop**.
- `SHA256SUMS.txt` lists each file's SHA-256, so you can check your download.

The Windows programs are not code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**.

Install the host and one client, then follow the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md). Your headset must already work in SteamVR on the headset PC.

### New in 1.0.0.10

- **The menu button:** a press now reaches the rendering PC as one clean press, although the headset PC's own SteamVR takes input focus while the button is held, and a short loss of focus no longer lets go of what you are holding.
- **Game Mode (Bazzite, still being tested):** SteamVR starts with the headset display as the only Wayland display it is given, a failure is explained at once, and the last Game Mode start goes into every report.
- **Automatic bitrate on Wi-Fi:** a step down for unevenly arriving frames is undone when frames arrive no more evenly at the lower bitrate, as on Wi-Fi with power saving.
- **Base stations:** three tries; on Bazzite a fresh search before each retry, and the signal strength of a station that could not be reached.
- Every package carries version 1.0.0.10; the rendering PC's driver is 1.0.0.9's.

Everything since the first test build is in the [changelog](https://github.com/JagerBombastic/VROSTBridge/blob/main/CHANGELOG.md); the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.10.md) have the detail and the known limits.

### Good to know

- For a trusted home network only: please don't expose it to the internet.
- Eye tracking uses [Baballonia](https://github.com/Project-Babble/Baballonia) by Project Babble, downloaded only if you turn it on and accept its licence (personal, non-commercial use).
- Found a bug? Open an issue with the two reports (client page: *Send report to host*, then save both on the host page). They contain local network addresses and hardware names, so have a look before posting.

MIT licence. Not affiliated with Valve, Bigscreen, ALVR or Project Babble.
