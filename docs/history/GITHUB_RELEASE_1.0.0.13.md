# GitHub release text for 1.0.0.13

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.13`, title **VROSTBridge 1.0.0.13**.
Attach the five packages, the three source archives and `SHA256SUMS.txt`, which lists all eight:

- `VROSTBridge-Host-Setup-1.0.0.13.exe` and `VROSTBridge-Host-Portable-1.0.0.13.zip`
- `VROSTBridge-Bazzite-Installer-1.0.0.13.zip`
- `VROSTBridge-Client-Setup-1.0.0.13.exe` and `VROSTBridge-Client-Portable-1.0.0.13.zip`
- `VROSTBridge-1.0.0.13-host-source.zip`, `VROSTBridge-1.0.0.13-windows-client-source.zip` and
  `VROSTBridge-1.0.0.13-bazzite-client-source.zip`
- `SHA256SUMS.txt`

Push the source first: the tag has to point at the commit the source archives were made from. See [PUBLISHING.md](../PUBLISHING.md).

---

**VR outside the box.** VROSTBridge streams SteamVR games from a Windows PC to a PC VR headset plugged into a second PC somewhere else in the house, running Bazzite Linux or Windows.

### What's new

- **Pick an Index's refresh rate before you start:** Native at 80, 90, 120 or 144 Hz, set in SteamVR for the session; no SteamVR menus. A Beyond needs no choosing: the stream follows the mode it is in (2560 at 75 Hz, 1920 at 90 Hz), set with Bigscreen's Beyond Utility.
- **More parity on a wired link:** one parity packet per five video packets instead of ten, so a clump of lost packets up to a fifth of a frame is rebuilt. Wi-Fi keeps one per ten.
- **Stopping while SteamVR is still starting** takes seconds, not up to 90.
- **Both reports carry the network adapters' drop counters,** to show where lost packets go missing, and the Bazzite report the remembered GPU clock.

### Download

| PC | File |
| --- | --- |
| Rendering PC (Windows) | `VROSTBridge-Host-Setup-1.0.0.13.exe` |
| Headset PC with Bazzite | `VROSTBridge-Bazzite-Installer-1.0.0.13.zip`: extract the whole ZIP and open **Install VROSTBridge.desktop** |
| Headset PC with Windows 10 or 11 | `VROSTBridge-Client-Setup-1.0.0.13.exe` |

Install over 1.0.0.12; settings, pairing and calibration stay. The portable ZIPs run without installing, each `*-source.zip` holds one part's source, and `SHA256SUMS.txt` lists the checksums. The Windows programs aren't code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**. New here? Start with the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md).

### Known limits

Starting a session from Steam's Game Mode on Bazzite doesn't work yet (use Desktop Mode), and the Windows headset PC has only been tested in software. More in the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.13.md). Found a bug? Open an issue with both session reports, after checking them: they include local network addresses.

MIT licence, © 2026 JagerBombastic; the logo and icons CC BY-ND 4.0; third-party parts keep their own licences ([in plain words](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/LICENSING.md)). Not affiliated with Valve, Bigscreen, the ALVR project or Project Babble.
