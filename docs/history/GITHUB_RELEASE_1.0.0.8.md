# GitHub release text for 1.0.0.8

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.8`, title **VROSTBridge 1.0.0.8**.
Attach `VROSTBridge-Host-Setup-1.0.0.8.exe`, `VROSTBridge-Bazzite-Installer-1.0.0.8.zip`,
`VROSTBridge-Client-Setup-1.0.0.8.exe` and `SHA256SUMS.txt` (and the two portable ZIPs,
`VROSTBridge-Host-Portable-1.0.0.8.zip` and `VROSTBridge-Client-Portable-1.0.0.8.zip`, if you want to offer them).
See [PUBLISHING.md](../PUBLISHING.md). The links assume the repository is `JagerBombastic/VROSTBridge`; change them if you
name it differently.

---

**VR outside the box.** Stream SteamVR games from a Windows PC to a PC VR headset plugged into another PC somewhere else in the house: a Bazzite Linux PC or a Windows PC.

Tested with a Valve Index and a Bigscreen Beyond 2e, an AMD rendering PC and a Ryzen handheld running Bazzite as the headset PC, on one home network. The Windows client has not met real hardware yet, so reports from yours are very welcome.

### Download

- **Rendering PC (Windows):** `VROSTBridge-Host-Setup-1.0.0.8.exe`.
- **Headset PC, Windows 10 or 11:** `VROSTBridge-Client-Setup-1.0.0.8.exe`. Installs for your account only; no administrator rights needed.
- **Headset PC, Bazzite:** `VROSTBridge-Bazzite-Installer-1.0.0.8.zip`. Extract the whole ZIP and open **Install VROSTBridge.desktop**.
- `SHA256SUMS.txt` lists each file's SHA-256, so you can check your download.

The Windows programs are not code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**.

Install the host and one client, then follow the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md). Your headset must already work in SteamVR on the headset PC.

### New in 1.0.0.8

- **Steadier streams:** automatic bitrate asks only for what the picture needs, then steps down when decoding falls behind or frames go missing and back up as the stream runs clean, changed on the rendering PC while the stream runs. Video is paced below what the network test carried without loss.
- **Use recommended settings:** one button for the stream settings that work best.
- **Balance performance during sessions (Bazzite):** the power limit at its maximum on the charger, and the GPU clock held higher when frames run late, never at the processor's expense; all set back at Stop.
- **Base stations off when VROSTBridge closes,** and on again at Start, over the headset PC's Bluetooth (SteamVR 2.0 base stations, both clients).
- **Keep my gaze centred:** an eye-tracking calibration that sits off to one side is corrected as you play and remembered; **Use previous calibration** goes back one.
- Every package carries version 1.0.0.8; install both PCs together.

Everything since the first test build is in the [changelog](https://github.com/JagerBombastic/VROSTBridge/blob/main/CHANGELOG.md); the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.8.md) have the detail and the known limits.

### Good to know

- For a trusted home network only: please don't expose it to the internet.
- Eye tracking uses [Baballonia](https://github.com/Project-Babble/Baballonia) by Project Babble, downloaded only if you turn it on and accept its licence (personal, non-commercial use).
- Found a bug? Open an issue with the two reports (client page: *Send report to host*, then save both on the host page). They contain local network addresses and hardware names, so have a look before posting.

MIT licence. Not affiliated with Valve, Bigscreen, ALVR or Project Babble.
