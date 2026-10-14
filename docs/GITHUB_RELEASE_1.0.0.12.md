# GitHub release text for 1.0.0.12

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.12`, title **VROSTBridge 1.0.0.12**.
Attach the five packages, the three source archives and `SHA256SUMS.txt`, which lists all eight:

- `VROSTBridge-Host-Setup-1.0.0.12.exe` and `VROSTBridge-Host-Portable-1.0.0.12.zip`
- `VROSTBridge-Bazzite-Installer-1.0.0.12.zip`
- `VROSTBridge-Client-Setup-1.0.0.12.exe` and `VROSTBridge-Client-Portable-1.0.0.12.zip`
- `VROSTBridge-1.0.0.12-host-source.zip`, `VROSTBridge-1.0.0.12-windows-client-source.zip` and
  `VROSTBridge-1.0.0.12-bazzite-client-source.zip`
- `SHA256SUMS.txt`

GitHub adds the whole source itself (*Source code*). If you publish before trying 1.0.0.12 on your own PCs, tick
**Set as a pre-release**. See [PUBLISHING.md](PUBLISHING.md).

---

**VR outside the box.** VROSTBridge streams SteamVR games from a Windows PC to a PC VR headset plugged into a second PC somewhere else in the house, running Bazzite Linux or Windows.

1.0.0.12 puts right what 1.0.0.11's first session on real hardware showed. It streamed well (Beat Saber at Native, eye-tracked foveation, a handheld headset PC), with three things to fix:

- **The automatic bitrate forgives.** It dropped from 65 to 30 Mbit/s in the first 80 seconds, while the headset PC's GPU sat at a low clock and the game was starting, and never came back. Now, when the headset PC's GPU clock is held higher, the bitrate goes back to where the session started; and a second in which frames never arrived at all (the rendering PC sent nothing for a moment) no longer counts against it.
- **The headset PC remembers its GPU clock** (Bazzite): the clock a session needed is held from the next session's start instead of being found again minutes in. Learned per PC, only while the GPU's level is automatic.
- **The add-on warning on the rendering PC** lists only add-ons registered from outside SteamVR. It used to call SteamVR's own drivers third-party and suggest turning them off.

### Download

| PC | File |
| --- | --- |
| Rendering PC (Windows) | `VROSTBridge-Host-Setup-1.0.0.12.exe` |
| Headset PC with Bazzite | `VROSTBridge-Bazzite-Installer-1.0.0.12.zip`: extract the whole ZIP and open **Install VROSTBridge.desktop** |
| Headset PC with Windows 10 or 11 | `VROSTBridge-Client-Setup-1.0.0.12.exe`: installs for your account only, no administrator rights needed |

Install each over 1.0.0.11: settings, pairing, the network test, Baballonia and its calibration stay. The portable ZIPs run both Windows programs without installing. `SHA256SUMS.txt` lists the SHA-256 of every file here. The Windows programs aren't code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**.

New here? Start with the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md). Your headset must already work in SteamVR on the headset PC.

### Good to know

- The Bazzite headset PC in Desktop Mode is the tested path; starting a session from Steam's Game Mode hasn't worked yet, and the Windows headset PC has only been tested in software. The [README](https://github.com/JagerBombastic/VROSTBridge#what-works-today) has the details.
- The rendering PC's occasional stalls (40–80 ms without a frame while a game runs) are not fixed yet; 1.0.0.12 only stops them from costing bitrate. The [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.12.md) have the detail and the known limits.
- Found a bug? Open an issue with the two reports (client page: *Send report to host*, then save both on the host page). They contain local network addresses and hardware names, so have a look before posting.

MIT licence, © 2026 JagerBombastic; the logo and icons CC BY-ND 4.0; third-party parts keep their own licences ([in plain words](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/LICENSING.md)). Not affiliated with Valve, Bigscreen, the ALVR project or Project Babble.
