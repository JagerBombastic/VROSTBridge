# GitHub release text for 1.0.0.14

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.14`, title **VROSTBridge 1.0.0.14**.
Attach the five packages, the three source archives and `SHA256SUMS.txt`, which lists all eight:

- `VROSTBridge-Host-Setup-1.0.0.14.exe` and `VROSTBridge-Host-Portable-1.0.0.14.zip`
- `VROSTBridge-Bazzite-Installer-1.0.0.14.zip`
- `VROSTBridge-Client-Setup-1.0.0.14.exe` and `VROSTBridge-Client-Portable-1.0.0.14.zip`
- `VROSTBridge-1.0.0.14-host-source.zip`, `VROSTBridge-1.0.0.14-windows-client-source.zip` and
  `VROSTBridge-1.0.0.14-bazzite-client-source.zip`
- `SHA256SUMS.txt`

Push the source first: the tag has to point at the commit the source archives were made from. See [PUBLISHING.md](PUBLISHING.md).

---

**VR outside the box.** VROSTBridge streams SteamVR games from a Windows PC to a PC VR headset plugged into a second PC somewhere else in the house, running Bazzite Linux or Windows.

### What's new

For the first testers, who found the latency good and asked for a sharper picture and hands that land where they were swung:

- **Hands predicted with their acceleration:** the headset PC folds each controller's acceleration into the poses it sends, so the rendering PC's prediction lands where a swinging hand will be instead of short of it.
- **A sharper picture at the same stream:** the rendering PC renders at the headset PC's recommended size (up to one and a half times the stream) and scales down for the encoder; *Auto* codec is HEVC on every headset, 10-bit where the headset PC decodes it; the automatic bitrate's ceiling is a third higher.
- **Cable and Wi-Fi on one PC:** on Bazzite, *Fix this PC's network settings* (once) keeps the video on the cable with Wi-Fi left connected; the stall every few minutes some saw with both connected is gone.
- **No more "set your network to Private"** for a headset PC that is already paired.
- **Eye tracking:** the sharp region is predicted through the head's turn, so it stays on what you look at.
- **The host keeps its last session's statistics** for a report made after a restart.

### Download

| PC | File |
| --- | --- |
| Rendering PC (Windows) | `VROSTBridge-Host-Setup-1.0.0.14.exe` |
| Headset PC with Bazzite | `VROSTBridge-Bazzite-Installer-1.0.0.14.zip`: extract the whole ZIP and open **Install VROSTBridge.desktop** |
| Headset PC with Windows 10 or 11 | `VROSTBridge-Client-Setup-1.0.0.14.exe` |

Install over 1.0.0.13; settings, pairing and calibration stay. The portable ZIPs run without installing, each `*-source.zip` holds one part's source, and `SHA256SUMS.txt` lists the checksums. The Windows programs aren't code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**. New here? Start with the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md).

### Known limits

Starting a session from Steam's Game Mode on Bazzite doesn't work yet (use Desktop Mode), and the Windows headset PC has only been tested in software. More in the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.14.md). Found a bug? Open an issue with both session reports, after checking them: they include local network addresses.

MIT licence, © 2026 JagerBombastic; the logo and icons CC BY-ND 4.0; third-party parts keep their own licences ([in plain words](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/LICENSING.md)). Not affiliated with Valve, Bigscreen, the ALVR project or Project Babble.
