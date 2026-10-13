# GitHub release text for 1.0.0.11

Paste the part below the line into GitHub's *Draft a new release* form: tag `v1.0.0.11`, title **VROSTBridge 1.0.0.11**.
Attach the five packages, the three source archives and `SHA256SUMS.txt`, which lists all eight:

- `VROSTBridge-Host-Setup-1.0.0.11.exe` and `VROSTBridge-Host-Portable-1.0.0.11.zip`
- `VROSTBridge-Bazzite-Installer-1.0.0.11.zip`
- `VROSTBridge-Client-Setup-1.0.0.11.exe` and `VROSTBridge-Client-Portable-1.0.0.11.zip`
- `VROSTBridge-1.0.0.11-host-source.zip`, `VROSTBridge-1.0.0.11-windows-client-source.zip` and
  `VROSTBridge-1.0.0.11-bazzite-client-source.zip`
- `SHA256SUMS.txt`

GitHub adds the whole source itself (*Source code*). If you publish before trying 1.0.0.11 on your own PCs, tick
**Set as a pre-release**. See [PUBLISHING.md](PUBLISHING.md). The links assume the repository is
`JagerBombastic/VROSTBridge`; change them if you name it differently.

---

**VR outside the box.** VROSTBridge streams SteamVR games from a Windows PC to a PC VR headset plugged into a second PC somewhere else in the house, running Bazzite Linux or Windows. The big PC renders; the small one tracks you and shows the picture. One button starts and stops both.

This is the **first public release**. It works in the house it was built in (a Valve Index and a Bigscreen Beyond 2e, an AMD rendering PC and a Ryzen handheld running Bazzite as the headset PC), it has a great many automated tests, and yours would be the second set of hardware it meets. Reports are very welcome, whether it works or not.

### Download

| PC | File |
| --- | --- |
| Rendering PC (Windows) | `VROSTBridge-Host-Setup-1.0.0.11.exe` |
| Headset PC with Bazzite | `VROSTBridge-Bazzite-Installer-1.0.0.11.zip`: extract the whole ZIP and open **Install VROSTBridge.desktop** |
| Headset PC with Windows 10 or 11 | `VROSTBridge-Client-Setup-1.0.0.11.exe`: installs for your account only, no administrator rights needed |

The portable ZIPs run both Windows programs without installing. `SHA256SUMS.txt` lists the SHA-256 of every file here. The Windows programs aren't code-signed yet: if Windows says *Windows protected your PC*, choose **More info → Run anyway**.

Then follow the [quick start](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/QUICK_START.md). Your headset must already work in SteamVR on the headset PC: if it doesn't work there, it won't work streamed.

### What works today

- **Bazzite headset PC, Desktop Mode:** used with a Valve Index and a Bigscreen Beyond 2e: picture, tracking, Index controllers, game audio, eye-tracked foveation and the headset microphone. The newest changes (1.0.0.10 and 1.0.0.11) have passed their tests in software and await their first session.
- **Steam Game Mode on Bazzite:** included, but starting a session from Game Mode hasn't worked yet on the tested setup. Use Desktop Mode for now.
- **Windows headset PC:** built and tested in software only (its installer, page and frame path run in the tests under Wine). Brave volunteers wanted.
- **Rendering PC:** tested with an AMD Radeon. NVIDIA and Intel encoders should work through ALVR but haven't been tried.

### What's in it

- **One button:** *Start session* on the headset PC starts SteamVR on both PCs and the stream; *Stop session* ends both. The two PCs find each other on your home network, pair once, and the headset PC can wake the rendering PC.
- **A sharp picture where it counts:** foveated encoding (eye-tracked on a Beyond 2e, with [Baballonia](https://github.com/Project-Babble/Baballonia)), 10-bit colour, AV1, HEVC or H.264, a bitrate that asks only for what the picture needs, and lost packets rebuilt on the way.
- **Light on the headset PC:** decoding on its GPU and one shader pass per frame that skips what the lenses never show. While its graphics fall behind, each eye is shown a little smaller instead of SteamVR halving the frame rate.
- **The fiddly bits handled:** Wi-Fi tuning during sessions, the handheld's power balanced while streaming, base stations switched on and off, game audio and the headset's microphone, a Steam Game Mode screen on Bazzite and a tray icon on Windows.

New since the last test build (1.0.0.10): the lighter frame pass (about a third less work per pixel by the GPU compiler's own estimate, the same picture), the lens area skipped, the smaller picture instead of half the frame rate, the frame pass's GPU time on the page and in the reports, and the Beyond's 2560 size called **Native**. The [changelog](https://github.com/JagerBombastic/VROSTBridge/blob/main/CHANGELOG.md) has every version; the [release notes](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/RELEASE_1.0.0.11.md) have the detail and the known limits.

### Source code

*Source code* below is the whole source. The three `*-source.zip` files hold one part each: the rendering PC, the Windows headset PC or the Bazzite headset PC, with a `SOURCE.md` that says what is in it and how to build it.

### Good to know

- For a trusted home network only: please don't expose it to the internet.
- Eye tracking uses Baballonia by Project Babble, downloaded only if you turn it on and accept its licence (personal, non-commercial use). It isn't bundled.
- Found a bug? Open an issue with the two reports (client page: *Send report to host*, then save both on the host page). They contain local network addresses and hardware names, so have a look before posting.
- The [documentation](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/README.md) has the guides, [troubleshooting](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/TROUBLESHOOTING.md) and how it all works.

MIT licence, © 2026 JagerBombastic; the logo and icons CC BY-ND 4.0; third-party parts keep their own licences ([in plain words](https://github.com/JagerBombastic/VROSTBridge/blob/main/docs/LICENSING.md)). Not affiliated with Valve, Bigscreen, the ALVR project or Project Babble.
