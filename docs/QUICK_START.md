# Quick start — VROSTBridge 1.0.0.14

These steps use the installable release packages; the source is for developers ([BUILDING.md](BUILDING.md)). Install the same version on both PCs. A new version installs over the previous one and keeps your settings, pairing, network test, and Baballonia with its calibration: install over it rather than uninstalling first ([installing and upgrading](INSTALL_PACKAGES.md)).

The headset PC runs Bazzite or Windows 10 or 11 (64-bit). The Windows client has not run on real hardware yet: expect first-run problems, and send reports (see [If it fails](#if-it-fails)).

## Prepare once

### Rendering PC (Windows)

1. Install Steam, SteamVR and the graphics driver. No headset is needed on this PC. To wake it from the headset PC, connect it by Ethernet and enable *Wake on Magic Packet* for that adapter (the host page says what to change).
2. Install **VROSTBridge-Host-Setup-1.0.0.14.exe**. It is not code-signed, so Windows may say *Windows protected your PC*: choose **More info**, then **Run anyway**.
3. Open **VROSTBridge Host** and accept the one administrator prompt for its firewall rules (they admit only your local subnet). Leave it waiting in the tray; turn on *Launch at Windows sign-in* if it should be ready after a restart.
4. For the headset microphone in Discord or games, open the host page's **Headset microphone** card and select **Set up headset microphone**: VROSTBridge downloads VB-CABLE from VB-Audio (donationware) and opens its setup; select *Install Driver* and restart Windows if asked. Then choose **CABLE Output (VB-Audio Virtual Cable)** as the microphone in your apps.

### Headset PC with Bazzite

1. On the Bazzite desktop, check that the headset shows SteamVR and that both controllers work. Finish SteamVR's room setup before room-scale play. The headset, its USB and DisplayPort cables and its drivers belong on this PC.
2. Extract the whole **VROSTBridge-Bazzite-Installer-1.0.0.14.zip** and open **Install VROSTBridge.desktop**, keeping the payload folder beside it.
3. Open **VROSTBridge Client** and select **Allow home-network connection** if the page offers it (your password).
4. If the page offers **Fix this PC's network settings**, use it once (your password). It appears when this PC is on the home network by cable and Wi-Fi at once, and keeps the video on the cable with Wi-Fi left connected.
5. If the page offers **Fix SteamVR reprojection**, use it once with SteamVR closed (again after a SteamVR update).
6. For Steam Game Mode, select **Add to Steam** under *This PC → Steam Game Mode* in Desktop Mode ([Game Mode](BAZZITE_PLAY.md)).

### Headset PC with Windows

1. Install Steam, SteamVR and the graphics driver. Connect the headset, run SteamVR once, finish its room setup and check both controllers, then close SteamVR. Until SteamVR has run once, **Start session** says it is not set up yet.
2. Run **VROSTBridge-Client-Setup-1.0.0.14.exe**. It installs for your Windows account only, without administrator rights. Choose **More info → Run anyway** if Windows asks. Tick *Start when I sign in* if the client should wait in the tray.
3. Open **VROSTBridge Client** from the Start menu and select **Allow home-network connection** (one administrator prompt). The rules admit only your local subnet.

### Pairing

The client finds the host on the home network and pairs by itself while the host's *Allow home-network clients* is on (the default). For that first pairing only, Windows on the rendering PC must class the home network as **Private** (*Settings → Network & internet*); a headset PC already paired works on any network type. With *Allow home-network clients* off, choose **Allow & connect** for the client on the host page once.

## Start a session

On the client choose **Use recommended settings** on the *Headset & stream* card, then **Start session**. Recommended means Auto codec (HEVC, 10-bit where this PC decodes it), the headset's own display mode as the stream size (2560 per eye on a Beyond at 75 Hz, 1920 at 90 Hz, 2016 × 2240 on an Index), Auto foveated encoding, automatic bitrate, and the timing and motion settings that keep the stream steady.

The first start on each network adapter tests the network for about 15 seconds; the bitrate then asks only for what the picture needs and follows the stream during the session. A sleeping rendering PC is woken, both SteamVRs start through VROSTBridge, and the rendering PC's SteamVR appears in your headset. Do not open ALVR's own dashboard.

While it runs, the page shows the detected headset, its refresh rate, the stream size, the foveation, the network path and the frame rate. Worth knowing:

- **Handhelds:** keep the charger in, so *Balance performance during sessions* can give the stream the power it needs. If the page says this PC's graphics cannot keep up, it shows the picture a little smaller first and then offers a lighter mode.
- **Wi-Fi:** *Stay on one access point* stops Wi-Fi's background scans for the session, and *Turn off Wi-Fi power saving* removes regular pauses. A cable is always steadier.
- **Base stations:** SteamVR 2.0 base stations wake as the session starts and sleep when VROSTBridge closes, over the headset PC's Bluetooth.
- **Alignment:** once video runs, *Motion & timing → Check alignment* asks one question that decides whether VROSTBridge re-aims frames itself.

**Stop session** on the client ends the stream and closes both SteamVRs (or, with *Quick resume*, keeps the rendering PC's open for the chosen minutes). On Windows the client's tray icon has **Start session** and **Stop session** too, with a notification when streaming starts or a session stops. Closing the browser tab does not stop the companion.

## Beyond 2e eye tracking

With eye tracking set up and calibrated ([eye tracking](BEYOND_EYE_TRACKING.md)), Auto foveated encoding becomes *Eye-tracked*: the sharp region follows your gaze. Without it, the sharp region sits straight ahead of each eye. *Sharp region* (Small, Medium, Large) and *Outside the sharp region* (⅓, ¼ or ⅕ resolution) tune it, and *Sharpening* brings back fine detail and changes at once. The **Eye-tracked foveation** panel shows the sharp region and where each eye looks, live; it is never saved or reported.

On a Windows headset PC, close Bigscreen's own eye-tracking app first: only one program at a time can read the eye cameras. Turn on *Eye tracking* and select **Accept licence and set up**: VROSTBridge downloads Baballonia's installer from Project Babble, checks it and installs it for VROSTBridge only. Start a session, select **Show Baballonia** and calibrate as the card's *Calibrate once* steps describe, then stop and start the session.

## If it fails

The headset shows what is happening when video stops. On the client page select **Send report to host**, then save it on the host page under *Reports* together with the host's own report (**Save report**). Note the stage the page showed and the exact message, and capture the reports before reinstalling or deleting anything. To rule out the stream features, set Foveated encoding to Off and turn off Automatic bitrate and 10-bit colour, then compare.

On a Windows headset PC some causes differ:

- **SteamVR not set up:** start SteamVR once with the headset connected, then start the session again.
- **Block rules:** if the page says Windows Firewall has rules that block this program, a declined firewall prompt usually left them. Block rules win over allow rules, so remove them in *Windows Defender Firewall → Inbound Rules*.
- **Wi-Fi network name:** on Windows 11 24H2 and later, Windows tells programs the Wi-Fi network's name only with location permission (*Settings → Privacy & security → Location → Let desktop apps access your location*). Without it the page says so; streaming is unaffected.
- **Client does not open:** its startup logs are in `%LOCALAPPDATA%\VROSTBridge` (*Client startup logs* in the Start menu).

More in [troubleshooting](TROUBLESHOOTING.md). For upgrades, repair and removal see [installing and upgrading](INSTALL_PACKAGES.md); the checks for a new setup are in [hardware acceptance](HARDWARE_ACCEPTANCE.md).
