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

### What's new

- **The automatic bitrate recovers.** When the headset PC's GPU clock is raised, it goes back to where the session started, and moments when the rendering PC sent no frames no longer count against it. In 1.0.0.11 it could drop to 30 Mbit/s early on and stay there.
- **The headset PC remembers its GPU clock** (Bazzite): the clock one session needed is held from the start of the next.
- **The add-on warning** on the rendering PC names only add-ons installed separately from SteamVR, not SteamVR's own drivers.

### Download

| PC | File |
| --- | --- |
| Rendering PC (Windows) | `VROSTBridge-Host-Setup-1.0.0.12.exe` |
| Headset PC with Bazzite | `VROSTBridge-Bazzite-Installer-1.0.0.12.zip`: extract the whole ZIP and open **Install VROSTBridge.desktop** |
| Headset PC with Windows 10 or 11 | `VROSTBridge-Client-Setup-1.0.0.12.exe` |

Install over 1.0.0.11; settings, pairing and calibration stay. The portable ZIPs run without installing, each `*-source.zip` holds one part's source, and `SHA256SUMS.txt` lists the checksums. The Windows programs aren't code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**. New here? Start with the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md).

### Known limits

Starting a session from Steam's Game Mode on Bazzite doesn't work yet (use Desktop Mode), and the Windows headset PC has only been tested in software. More in the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.12.md). Found a bug? Open an issue with both session reports, after checking them: they include local network addresses.

MIT licence, © 2026 JagerBombastic; the logo and icons CC BY-ND 4.0; third-party parts keep their own licences ([in plain words](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/LICENSING.md)). Not affiliated with Valve, Bigscreen, the ALVR project or Project Babble.
