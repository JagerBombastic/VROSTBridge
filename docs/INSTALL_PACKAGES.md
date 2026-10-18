# Installable packages — VROSTBridge 1.0.0.14

The [quick start](QUICK_START.md) is the short route; this page says what each package installs, how upgrades and removal work, and where things go. What has been confirmed on hardware is in [hardware acceptance](HARDWARE_ACCEPTANCE.md).

There is one package for each kind of PC, all carrying version 1.0.0.14:

- **VROSTBridge-Host-Setup-1.0.0.14.exe** for the rendering PC (Windows);
- **VROSTBridge-Bazzite-Installer-1.0.0.14.zip** for a headset PC with Bazzite;
- **VROSTBridge-Client-Setup-1.0.0.14.exe** for a headset PC with Windows 10 or 11 (64-bit).

`VROSTBridge-Host-Portable-1.0.0.14.zip` and `VROSTBridge-Client-Portable-1.0.0.14.zip` hold the same files as the two Windows setups, without an installer. `SHA256SUMS.txt` lists each file's SHA-256.

## Upgrading

Install the host and a client of the same version together. 1.0.0.14 installs over any release from 1.0.0.1 on (the Windows client over any from 1.0.0.7), and either PC still works with the other at 1.0.0.13. Install over the previous version rather than uninstalling first: settings, pairing, the network test result, reports, the client's ALVR identity, and Baballonia with its settings and calibration are all kept. A test build from before 1.0.0.1 is the exception: remove it with its own uninstaller first ([why](history/RELEASE_1.0.0.1.md#before-you-install-remove-earlier-test-builds)).

Stop any session and choose **Quit** on both pages before upgrading. Each installer checks the new files before replacing anything, closes the running companion through its own authenticated page, switches to the new version, then removes old files it can identify as its own. If the old companion cannot close, or the rendering PC's SteamVR blocks the driver update, installation stops with the old version intact: close what it names and run the installer again rather than deleting folders by hand. Running the same installer again repairs an installation.

After upgrading to 1.0.0.14, the host asks once for administrator permission when it first starts, to update its firewall rules so they apply on any network type.

## Windows rendering PC

1. Install Steam and SteamVR.
2. Run **VROSTBridge-Host-Setup-1.0.0.14.exe**. It installs for the current user and adds **VROSTBridge Host** to the Start menu's *VROSTBridge* folder. If the Microsoft x64 Visual C++ runtime is missing, select the installer's optional runtime installation (Microsoft's own installer, with its own prompt).
3. Open **VROSTBridge Host**. It opens its page in your browser, asks once for administrator permission to add its firewall rules, and waits in the tray. SteamVR starts only when a headset PC starts a session. Opening the shortcut again brings back the same page.

The package carries its own Python and the patched ALVR host: no Python, compiler or package manager is needed. SteamVR and the GPU driver are prerequisites. The installer and launcher are not code-signed, so SmartScreen may say *Windows protected your PC*: choose **More info**, then **Run anyway**.

**Firewall and pairing.** The five inbound rules admit only the local subnet, on any network type, each limited to its program and ports. With *Allow home-network clients* on (the default), a headset PC pairs by itself the first time it connects, provided Windows classes the home network as **Private** for that first pairing; a paired headset PC then starts sessions on any network type. The pairing is what lets a headset PC start anything, so the Private rule keeps a stranger on a café or hotel network from pairing itself. If discovery fails, **Repair Windows Firewall access** in the host page's *Advanced* section runs the firewall setup again. VROSTBridge never turns the firewall off or changes Windows' network category.

**At sign-in.** *Launch at Windows sign-in* on the page (or the installer's **Start in the system tray when I sign in** component) writes this user's own sign-in entry, which starts the host quietly in the tray: no browser and no SteamVR. If no network is ready yet, it waits for one without prompting. Closing the browser leaves the host in the tray; the tray menu has *Settings* and *Exit*.

**State.** Settings, pairing, reports and logs live in `%LOCALAPPDATA%\VROSTBridge`. An upgrade backs up the host session, trust information and logs under `upgrade-backups` there, and `host-retained-session.json` keeps the host profile and client trust across an uninstall and reinstall. Do not run ALVR's own dashboard or a separate ALVR alongside VROSTBridge.

**Removing.** Use the normal Windows app removal entry. It removes the driver registration and firewall rules and keeps your settings.

### If the host does not open

Open File Explorer at `%LOCALAPPDATA%\VROSTBridge` and keep `host-native.log` (the launcher), `host-startup.log` (the companion's start) and `latest.log` (session activity). Note whether the installer, the Start-menu shortcut or **Start session** failed and the exact message, and collect these before reinstalling.

### Host provenance

The host driver is built from ALVR revision `f5023f8e78fa14ea57fb1038548d7820289a8524` plus VROSTBridge's patches. `dist/host/vrostbridge-build.json` records the native protocol, the patch hashes and the native files' hashes, and the installer assembler refuses a driver that does not match. The embedded Python and the helpers carry their provenance in `HOST_PROVENANCE.txt`.

## Bazzite headset PC

1. In Desktop Mode, download **VROSTBridge-Bazzite-Installer-1.0.0.14.zip** and extract the **whole ZIP** into a normal folder.
2. Open **Install VROSTBridge.desktop** (allow it to run if KDE asks), keeping the `payload` folder beside it.
3. Open **VROSTBridge Client** from the application menu and select **Start session**. Before the first session, check that the headset works in SteamVR on this PC.

The installer copies checked files under `~/.local/share/vrostbridge` (respecting `XDG_DATA_HOME`) and switches the active version in one step. It needs no root, does not touch Bazzite's immutable image and installs no RPM or Flatpak. The package carries its launcher runtime, the native client, the decoding libraries, the eye-camera helper, the icons and the open-source notices. The native client targets x86-64 Linux with glibc 2.39 or later and runs in the desktop session, outside any container. Preferences, reports and logs live in `~/.local/state/VROSTBridge` (respecting `XDG_STATE_HOME`); Baballonia in `~/.local/share/vrostbridge/baballonia` and its settings and calibration in `~/.config/ProjectBabble` survive updates.

The page offers two system changes, each once, each with your password, and each kept across reboots:

- **Allow home-network connection** adds firewalld rules for the session ports, limited to your home subnet. It changes no zone and never turns the firewall off.
- **Fix this PC's network settings** appears when this PC needs it: when it is on the home network by cable and Wi-Fi at once, Linux answers for both addresses on both adapters and the video can end up on Wi-Fi; on a kernel whose receive buffer limit is too small, the stream drops packets in runs. The fix writes `/etc/sysctl.d/70-vrostbridge-network.conf`. Without it, VROSTBridge disconnects the other Wi-Fi adapter for each session instead, and reconnects it if that cuts the host off.

**Network adapters.** *Network → Network adapter* chooses the adapter for the stream; *Automatic* follows the system's route. The first session on each adapter runs a 15-second network test; a wired link is then paced below the rate where the test began to lose data, and the automatic bitrate follows the stream during sessions. A test that found no clean rate is repeated before the next session.

**Reports.** **Save report** keeps one on this PC; **Send report to host** uploads it to the host page's *Reports*, where the host keeps the latest three.

**Removing.** **Uninstall VROSTBridge Client** in the application menu removes the installed versions and launchers, the Steam library entry (Steam restarts if open) and the Game Mode setup, and keeps preferences, reports, logs and the ALVR identity. It asks about Baballonia (about 840 MB): **Keep** (the default) leaves it for the next installation, **Remove** deletes it, **Cancel** stops. Without a dialog program, `VROSTBRIDGE_UNINSTALL_CONFIRMED=1` uninstalls and keeps it, and `VROSTBRIDGE_KEEP_EYE_TRACKING=0` removes it too. Uninstalling also removes VROSTBridge's own firewall rules (your password); if that is declined, the files stay for a retry.

**Game Mode.** **Add to Steam** on the page in Desktop Mode adds VROSTBridge to your Steam library with its artwork and sets up the Game Mode display handling (a connected headset is never used as the Game Mode screen); **Remove from Steam** undoes both. It replaces VROSTBridge entries added by hand, so their play time and controller layout do not carry over. See [Game Mode](BAZZITE_PLAY.md).

The menu entries are `io.vrostbridge.Client.desktop`, `io.vrostbridge.GameMode.desktop` and `io.vrostbridge.Uninstall.desktop`; an upgrade rewrites them in place, so a pinned entry stays. The icon (theme name `vrostbridge`) is installed in every size from 16 to 512 pixels.

## Windows headset PC

The Windows client has not run on Windows hardware yet. Its Python parts are covered by unit tests, its native programs by their tests under Wine, and its installer, companion and page have run under Wine. Expect first-run problems, and send reports.

1. Install Steam and SteamVR. Connect the headset, run SteamVR once, finish its room setup and check both controllers, then close SteamVR.
2. Run **VROSTBridge-Client-Setup-1.0.0.14.exe**. It needs 64-bit Windows 10 or 11, installs for the current user without administrator rights, and may need **More info → Run anyway**. The optional **Start when I sign in** component stays off unless you tick it.
3. Open **VROSTBridge Client** from the Start menu. It opens its page and waits in the system tray. Select **Allow home-network connection** (one administrator prompt), then **Start session**.

The program goes to `%LOCALAPPDATA%\VROSTBridge\Client\1.0.0.14` (about 63 MB), with **VROSTBridge Client**, *Client startup logs*, *Client open-source credits* and *Uninstall VROSTBridge Client* in the Start menu's *VROSTBridge* folder. It carries its own Python, the native client with its libraries (the transport built on ALVR's client core, FFmpeg's decoders with their source, OpenVR's), the companion and the notices. The native programs link the C runtime statically, so no Visual C++ runtime is needed. Settings, pairing and logs live in `%LOCALAPPDATA%\VROSTBridge`.

Close SteamVR before installing. Setup runs the new launcher's self-test against the package's `SHA256SUMS` first; if it fails, the installed version stays and `client-native.log` says why. A failed copy puts the previous files back.

**Firewall.** **Allow home-network connection** adds five inbound rules named `VROSTBridge.Client.…`: TCP 9943, UDP 9944 and UDP 5353 for the native client, and TCP 9945 and UDP 9944 and 9946 for the companion, the same ports the Bazzite client opens, for the local subnet only, on any network type. The page also warns when a declined firewall prompt left a rule that blocks VROSTBridge (block rules win over allow rules: remove it in *Windows Defender Firewall → Inbound Rules*).

**At sign-in.** *Start when I sign in* (the installer component, or *Start VROSTBridge Client when I sign in* under *This PC*) writes this user's sign-in entry `VROSTBridgeClient`, which starts the client quietly in the tray: no browser and no SteamVR. It never replaces an entry of that name that another program wrote.

**Silent install.** `VROSTBridge-Client-Setup-1.0.0.14.exe /S`, with `/STARTUP` to turn the sign-in start on. `Uninstall.exe /S` in the program folder uninstalls and keeps the downloaded Baballonia; add `/REMOVEEYETRACKING` to remove it.

**Removing.** Close SteamVR and use **Uninstall VROSTBridge Client** in the Start menu or *Settings → Apps*. It asks about a Baballonia it downloaded (**Yes** keeps it, the default; **No** removes it; **Cancel** stops), restores SteamVR settings a session changed if they are still held, and removes its firewall rules (one administrator prompt; declining stops the uninstall). Settings, pairing and logs, Baballonia's settings and calibration in `%APPDATA%\ProjectBabble`, and a Baballonia you installed yourself stay.

**Portable.** `VROSTBridge-Client-Portable-1.0.0.14.zip` holds the same files in a `VROSTBridge-Client` folder: extract it whole and open `VROSTBridge Client.exe`. It has no Start menu entries and no uninstaller: turn off its sign-in start on the page, quit it and delete the folder; remove its `VROSTBridge.Client.…` firewall rules by hand.

**Host and client on one PC.** Both can be installed side by side, each with its own program folder (`%LOCALAPPDATA%\VROSTBridge\Host\<version>` and `…\Client\<version>`), *Settings → Apps* entry, sign-in entry, firewall rules and files in the shared settings folder. Uninstalling one leaves the other.

**Eye tracking.** Baballonia's Windows installer (1.1.1.0rc6, about 423 MB) is downloaded only when you set it up on the page, checked against its exact size and SHA-256, and installed silently into `%LOCALAPPDATA%\VROSTBridge\Baballonia` (about 0.88 GB) without Start menu or *Settings → Apps* entries. A Baballonia of the same version you installed yourself is used and never removed. How it works: [technical reference, section 9](TECHNICAL_REFERENCE.md#9-eye-tracking-bigscreen-beyond-2e).

### If the client does not open

Choose *Client startup logs* in the Start menu (or open `%LOCALAPPDATA%\VROSTBridge`) and keep `client-native.log` (the launcher), `client-startup.log` (the companion's start) and `client-latest.log` (session activity). Note the step that failed and the exact message. Closing the browser leaves the client in its tray: **Open VROSTBridge** reopens the page and **Quit** ends it.

## What installation proves

The installers' integrity checks and the launchers' self-tests prove the files, the bundled runtime and the start-up. They do not prove SteamVR's compositor, hardware decoding, tracking, input, audio or latency: those need a session on real hardware ([hardware acceptance](HARDWARE_ACCEPTANCE.md)). Checks run under Wine or from a cross-build are not a substitute for Windows hardware.
