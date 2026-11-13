# GitHub release text for 1.0.0.15

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.15`, title **VROSTBridge 1.0.0.15**.
Attach the five packages, the three source archives and `SHA256SUMS.txt`, which lists all eight:

- `VROSTBridge-Host-Setup-1.0.0.15.exe` and `VROSTBridge-Host-Portable-1.0.0.15.zip`
- `VROSTBridge-Bazzite-Installer-1.0.0.15.zip`
- `VROSTBridge-Client-Setup-1.0.0.15.exe` and `VROSTBridge-Client-Portable-1.0.0.15.zip`
- `VROSTBridge-1.0.0.15-host-source.zip`, `VROSTBridge-1.0.0.15-windows-client-source.zip` and
  `VROSTBridge-1.0.0.15-bazzite-client-source.zip`
- `SHA256SUMS.txt`

Push the source first: the tag has to point at the commit the source archives were made from. See [PUBLISHING.md](PUBLISHING.md).

---

### What's new

- **SteamVR's volume slider works in the headset.** The rendering PC applies the volume of the headset's audio device to the game audio it captures, smoothly, so the dashboard's slider (and Windows' own) changes what you hear.
- **SteamVR's safe mode no longer leaves you searching for the host.** If SteamVR on the rendering PC turned VROSTBridge's driver off after a crash, Start says so at once, and **Turn the driver back on** fixes it from the headset PC.
- **Headset USB problems explained:** a hub nested too deep (Bazzite), the headset's USB dropping out (instead of *texture submission failed: 105*), and controllers that are on but can't see the base stations, each with what to do.
- **The bitrate starts where your last session settled,** instead of stepping down for the first minute of every session.
- **Game audio stays in time:** a stall of the whole stream no longer leaves the sound late for the rest of the session.
- **A lower power limit than before** is pointed out when the headset PC is short of frames.
- Smaller fixes: the network settings check counts a paused Wi-Fi adapter, an unplugged one is forgotten, and audio mods on a Beyond's accessory port are used for its sound.

### Download

| PC | File |
| --- | --- |
| Rendering PC (Windows) | `VROSTBridge-Host-Setup-1.0.0.15.exe` |
| Headset PC with Bazzite | `VROSTBridge-Bazzite-Installer-1.0.0.15.zip`: extract the whole ZIP and open **Install VROSTBridge.desktop** |
| Headset PC with Windows 10 or 11 | `VROSTBridge-Client-Setup-1.0.0.15.exe` |

Install over 1.0.0.14; settings, pairing and calibration stay. If the headset sounds quieter than before, the rendering PC's playback device was turned down: turn it up in SteamVR's dashboard. The portable ZIPs run without installing, each `*-source.zip` holds one part's source, and `SHA256SUMS.txt` lists the checksums. The Windows programs aren't code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**. New here? Start with the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md).

### Known limits

Starting a session from Steam's Game Mode on Bazzite doesn't work yet (use Desktop Mode), and the Windows headset PC has only been tested in software. More in the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.15.md). Found a bug? Open an issue with both session reports, after checking them: they include local network addresses.

MIT licence, © 2026 JagerBombastic; the logo and icons CC BY-ND 4.0; third-party parts keep their own licences ([in plain words](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/LICENSING.md)). Not affiliated with Valve, Bigscreen, the ALVR project or Project Babble.
