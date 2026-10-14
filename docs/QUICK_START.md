# Quick start — VROSTBridge 1.0.0.12

These steps use the installable release packages. The source ZIP is for developers; see [BUILDING.md](BUILDING.md) to build it. Coming from a test build (1.0.0 or earlier)? Remove it first: from 1.0.0.1 VROSTBridge installs fresh over test builds ([release notes](history/RELEASE_1.0.0.1.md#before-you-install-remove-earlier-test-builds)). On the rendering PC and on Bazzite, 1.0.0.12 installs over 1.0.0.11, 1.0.0.10, 1.0.0.9, 1.0.0.8, 1.0.0.7, 1.0.0.6, 1.0.0.5, 1.0.0.4, 1.0.0.3, 1.0.0.2 and 1.0.0.1 ([release notes](RELEASE_1.0.0.12.md)), and on Windows headset PCs over 1.0.0.7 to 1.0.0.11; install it over the previous version rather than uninstalling first, which keeps Baballonia and its calibration.

The headset PC runs Bazzite or, new in 1.0.0.7, Windows 10 or 11 (64-bit). Install the same version on both PCs. The Windows client has not run on real hardware yet: expect first-run problems, and send reports (see [If it fails](#if-it-fails)).

## Prepare once

### Rendering PC (Windows)

1. Install Steam/SteamVR and the graphics driver. No physical headset is required on the rendering host. The trusted home connection must be a Windows **Private** network. For waking the host from the headset PC, connect it by Ethernet and enable *Wake on Magic Packet* for that adapter (the host page says what to change).
2. Install **VROSTBridge-Host-Setup-1.0.0.12.exe** (a later version installs over it). It is not code-signed, so Windows may say *Windows protected your PC*: choose **More info**, then **Run anyway**.
3. Open **VROSTBridge Host**. Approve the scoped home-network firewall setup if requested. Leave the host waiting in its tray, and turn on *Launch at Windows sign-in* if the host should be ready after a restart.
4. For the headset microphone in Discord or games on Windows, open the host page's **Headset microphone** card. If it says *Not set up*, select **Set up headset microphone**: VROSTBridge downloads VB-CABLE (a free virtual audio cable by VB-Audio Software, donationware) from VB-Audio and opens its setup; select *Install Driver* and restart Windows if asked. Then choose **CABLE Output (VB-Audio Virtual Cable)** as the microphone in your apps.

### Headset PC with Bazzite

1. On the Bazzite desktop, confirm the powered headset displays local SteamVR correctly and that both controllers work. Complete local room setup before room-scale use. The physical HMD, USB, DisplayPort and drivers belong on this computer.
2. Extract the whole **VROSTBridge-Bazzite-Installer-1.0.0.12.zip** and open **Install VROSTBridge.desktop**, keeping its adjacent payload folder. Later versions install over it; do not manually delete its state.
3. Open **VROSTBridge Client**. Approve the scoped home-network firewall setup if requested (**Allow home-network connection**; Bazzite may ask for your password).
4. If the client page offers **Fix SteamVR reprojection**, use it once with SteamVR closed (it may ask for your password; again after a SteamVR update).
5. For Steam Game Mode, select **Add to Steam** under *This PC → Steam Game Mode* in Desktop Mode. Steam restarts and VROSTBridge appears in your library with its artwork.

### Headset PC with Windows (new in 1.0.0.7)

1. Install Steam, SteamVR and the graphics driver. Connect the headset, run SteamVR once, complete its room setup and check that both controllers work, then close SteamVR. SteamVR records where it is installed when it first runs; until then **Start session** says SteamVR is not set up on this PC yet.
2. Run **VROSTBridge-Client-Setup-1.0.0.12.exe**. It installs for your Windows account only and needs no administrator rights. It is not code-signed, so Windows may say *Windows protected your PC*: choose **More info**, then **Run anyway**. Tick *Start when I sign in* if the client should wait in the tray after every sign-in.
3. Open **VROSTBridge Client** from the Start menu. Its page says *VROSTBridge Client 1.0.0.12* at the bottom, and the client also waits in the system tray.
4. Select **Allow home-network connection** on the page and accept Windows' administrator (UAC) prompt once. It adds firewall rules for the ports the client uses, for private networks and the local subnet only. Windows must treat the home network as **Private**; the page warns when it does not.
5. Pairing needs nothing more: the client finds the host on the home network, and the host accepts it while *Allow home-network clients* is on (the default). If you turned that off, choose **Allow & connect** for this client on the host page once.
6. Optionally select **Test network now** on the *Headset & stream* card to run the network test (about 15 seconds) before the first session; otherwise the first **Start session** runs it.

Game Mode, *Add to Steam* and *Fix SteamVR reprojection* belong to Bazzite and are not shown on Windows. Day to day, the tray icon's **Start session** and **Stop session** work without opening the page, with a notification when streaming starts, when a session stops on its own and when one does not start (with the reason). *Start VROSTBridge Client when I sign in* under *This PC* turns the sign-in start on or off later; it opens neither a browser nor SteamVR.

## Start a session

On the client choose **Use recommended settings** on the *Headset & stream* card (1.0.0.8): Auto codec, Automatic headset resolution (Standard, 2016 per eye, for a Beyond from RC4.3), Auto foveated encoding and Automatic bitrate, with the timing and motion settings that keep the stream steady. Then **Start session**. Automatic bitrate asks only for what the picture needs and follows the stream during the session; on a Bazzite handheld, keep the charger in so *Balance performance during sessions* can give the stream the power it needs; with SteamVR 2.0 base stations and Bluetooth on the headset PC, the base stations wake as the session starts and sleep when VROSTBridge closes. Once the stream runs, the *Game audio* line under *Use the headset's speakers and microphone* says how the game's sound is playing (1.0.0.9). The first start on each network adapter also tests the network (about 15 seconds); a wired link is then paced below the rate where the test began to lose data, and a test that found no clean rate is repeated at the next start. The headset is probed locally, a sleeping host is woken if its adapter is known, and both SteamVR sessions start through VROSTBridge. Do not open a second ALVR Dashboard.

Inspect the detected headset, effective refresh, negotiated dimensions and the foveation note. The *Network* section shows whether this PC uses Wi-Fi or Ethernet and warns about a slow link. On Wi-Fi, *Stay on one access point* reconnects Wi-Fi once as the session starts (on Windows it turns off Windows' background Wi-Fi scans for the session instead). Once video runs, use *Motion & timing → Check alignment* once and answer the question: it decides whether VROSTBridge re-aims frames itself. For a consistent comparison, keep codec, bitrate, resolution, foveation and refresh fixed. Higher bitrate can improve compression quality while also increasing load. If the page says this PC's graphics cannot keep up, use its smaller size (*Standard* on a Beyond): a handheld with integrated Radeon graphics held 75 fps there but fell to half rate at 2560 with RC4.2. RC4.3 shows each frame in one GPU pass instead of two, and 1.0.0.11 makes that pass lighter and shows the picture a little smaller while the graphics fall behind, so try **Native** (2560 on a Beyond, called High before 1.0.0.11) with eye-tracked foveation; the page shows what the frame pass costs on the GPU beside the frame rate. On a handheld or other PC whose processor and graphics share one power limit, the session card says when the processor is held back while streaming (usually because the GPU clock is fixed at its maximum) and what to change; where Steam's Performance settings come from steamos-manager, as on Bazzite, *Set the GPU clock to automatic* does it at once.

On a Windows headset PC the video details show the decoder in use (*decoded with D3D11VA* when the graphics decode the stream). During a session on Wi-Fi, Windows' media streaming mode is on, and with *Keep this PC awake during sessions* the PC stays awake in the *Best performance* power mode; your own settings come back at Stop.

Use **Stop session on the client** to end the stream and close both managed SteamVR sessions (or, with Quick resume, keep the host's open for the chosen minutes); on a Windows headset PC the tray icon's **Stop session** does the same. Closing a browser tab does not stop the companion. Host tray **Settings** reopens its interface; **Exit** quits. The Windows client's tray menu reopens its page with **Open VROSTBridge**; **Quit** quits. Optional Windows sign-in startup opens neither browser nor SteamVR.

## Beyond 2e eye tracking

With calibrated eye tracking forwarding (see [BEYOND_EYE_TRACKING.md](BEYOND_EYE_TRACKING.md)), Auto foveated encoding becomes *Eye-tracked*: the full-resolution region follows your gaze. Without it, a fixed region centred on each eye's straight-ahead view is used.

From RC4.3, *Sharp region* (Small, Medium, Large) and *Outside the sharp region* (⅓, ¼ or ⅕ resolution) tune it, and the **Eye-tracked foveation** panel on the client page, the host page and in Game Mode shows both eyes live: the sharp region and where each eye looks. That view is never saved or included in reports. *Sharpening* (Off to High, default Medium) brings back fine detail in every mode and changes at once, so you can compare levels during a session.

On a Windows headset PC, close Bigscreen's own eye-tracking app first: only one program at a time can read the eye camera, and Baballonia reads it directly. Turn on *Eye tracking* and select **Accept licence and set up**: VROSTBridge downloads Baballonia's Windows installer from Project Babble (about 423 MB), checks it and installs it for VROSTBridge only. Start a session, select **Show Baballonia** and calibrate in Baballonia's window as the card's *Calibrate once* steps describe: the calibration shows in the headset over the stream. Then stop and start the session. How this works on Windows is in the [technical reference](TECHNICAL_REFERENCE.md#9-eye-tracking-bigscreen-beyond-2e).

## If it fails

The headset shows what is happening when video stops. On the client page select **Send report to host**, then download it on the host page under *Reports* together with the host's own report (**Save report**). Each file name says which PC saved it, the version and the time. Report the visible startup stage, exact error and local-versus-bridge behaviour. Do not repeatedly reinstall or delete settings before capturing the evidence. To rule out the new stream features, set Foveated encoding to Off and turn off Automatic bitrate and 10-bit colour, then compare.

On a Windows headset PC some causes differ:

- **SteamVR not set up:** if Start says SteamVR is not set up on this PC yet, start SteamVR once with the headset connected, then start the session again.
- **Public network:** if the page says Windows treats your network as Public, set it to Private in *Settings → Network & internet*. The client's firewall rules apply to private networks only.
- **Block rules:** if the page says Windows Firewall has rules that block this program, they were usually left by a declined Windows Firewall prompt. Block rules win over allow rules, so remove them in *Windows Defender Firewall → Inbound Rules*.
- **Wi-Fi network name:** on Windows 11 24H2 and later, Windows tells programs the Wi-Fi network's name only with location permission (*Settings → Privacy & security → Location → Let desktop apps access your location*). Without it the page says so: VROSTBridge then cannot notice roaming between access points or pause a second Wi-Fi adapter, and streaming is unaffected.
- **Client does not open:** its startup logs are in `%LOCALAPPDATA%\VROSTBridge` (*Client startup logs* in the Start menu): `client-native.log`, `client-startup.log` and `client-latest.log`.

For Game Mode use [BAZZITE_PLAY.md](BAZZITE_PLAY.md) after the desktop route works. For upgrades, repair and removal see [INSTALL_PACKAGES.md](INSTALL_PACKAGES.md). Physical release checks are in [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md).
