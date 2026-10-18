# Installable packages — VROSTBridge 1.0.0.13

Documentation revision: 2026-10-09. See [the full specification](PROJECT_SPEC.md) and [quick start](QUICK_START.md).

Current behaviour and validation limits are in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). The client now starts SteamVR
automatically; a manual local run is only the initial hardware/room-setup check.

1.0.0.13 has three packages, one for each kind of PC, and all carry version 1.0.0.13:

- **VROSTBridge-Host-Setup-1.0.0.13.exe** for the rendering PC (Windows);
- **VROSTBridge-Bazzite-Installer-1.0.0.13.zip** for a headset PC with Bazzite;
- **VROSTBridge-Client-Setup-1.0.0.13.exe** for a headset PC with Windows 10 or 11 (64-bit), new in 1.0.0.7.

`VROSTBridge-Host-Portable-1.0.0.13.zip` and `VROSTBridge-Client-Portable-1.0.0.13.zip` hold the same
files as the two Windows setups, without an installer. `SHA256SUMS.txt` lists each file's SHA-256.

Install host 1.0.0.13 and a 1.0.0.13 client together, so both PCs carry one version (either still works with the other at 1.0.0.12, a Beyond in its 90 Hz mode then streaming 2016 per eye, as 1.0.0.12 did). On the rendering PC and on Bazzite an earlier installation must be 1.0.0.1 or later: 1.0.0.13 installs over 1.0.0.12, 1.0.0.11, 1.0.0.10, 1.0.0.9, 1.0.0.8, 1.0.0.7, 1.0.0.6, 1.0.0.5, 1.0.0.4, 1.0.0.3, 1.0.0.2 and 1.0.0.1 and keeps their state (see the [release notes](RELEASE_1.0.0.13.md)). The Windows client installs over 1.0.0.7 to 1.0.0.12, or fresh. Install over the previous version; do not uninstall it first: 1.0.0.2's and earlier uninstallers delete the Baballonia download, which would then be downloaded again. Over a test build it installs fresh: remove a test build (1.0.0 or earlier) with its own uninstaller first, as the [release notes](history/RELEASE_1.0.0.1.md#before-you-install-remove-earlier-test-builds) describe. Later versions install over 1.0.0.1. This release retains
client-led startup and private-LAN connection through the VROSTBridge pages. Normal
bridge operation does not require opening or using the separate ALVR Dashboard.
Stop the existing sessions and choose **Quit** on both pages before upgrading;
close any separately opened host ALVR Dashboard and host SteamVR as well. The
client's working local SteamVR setup and headset drivers remain in place.

From 1.0.0.1 on, install over the existing installation; an uninstall/reinstall cycle is not
required. The installers verify and self-check the new payload before replacing
the active version, close the old companion through its authenticated local
interface, then remove old files they can identify as their own.
Preferences, reports and the client's ALVR identity are preserved. If the old
companion cannot close or host SteamVR blocks driver migration, installation
stops with the old files retained. Close the reported application and rerun the
installer rather than deleting its folders manually.

## Windows rendering host

1. Install Steam and SteamVR on the rendering PC.
2. Run **VROSTBridge-Host-Setup-1.0.0.13.exe** and follow the installer. It installs
   for the current user and creates a **VROSTBridge Host** shortcut in the Start menu's
   *VROSTBridge* folder.
3. If the Microsoft x64 Visual C++ runtime is missing, select the installer's
   optional runtime installation. Microsoft's own installer supplies its normal
   UI and any Windows administrator prompt it requires.
4. Open **VROSTBridge Host** from the Start menu. It opens the local desktop
   interface in your browser and prepares the waiting host companion. SteamVR starts only when the client requests a session. Reopening the
   installed launcher brings you back to the existing interface rather than
   creating a second session.

The installer contains its own Python runtime and the matching patched ALVR
Windows host. No Python installation, compiler, source build or online package
manager is required for normal installation. SteamVR and the GPU driver remain
prerequisites. The installer and launcher are not code-signed, so Windows SmartScreen may say
*Windows protected your PC*: choose **More info**, then **Run anyway**.

This replaces the 0.2.1 self-extracting EXE. Use the installed **VROSTBridge Host**
shortcut for normal operation, not the old downloaded EXE. Close the old launcher
before installing the update. Keep a copy of the existing session report if you
need to compare settings or a failed test.

During a recognised Windows upgrade, the installer backs up the old host session,
trust information and logs under `%LOCALAPPDATA%\VROSTBridge\upgrade-backups`,
migrates the active host profile and owned driver registration, and removes old
owned version files and obsolete per-version Windows Apps entries. **VROSTBridge
Host** has a stable Windows Apps entry and an uninstall shortcut. Unrelated ALVR
installations and user data outside the owned installation are not removed.
The retained `host-retained-session.json` in the state directory (`%LOCALAPPDATA%\VROSTBridge`) also
keeps the host profile and client trust available after uninstall/reinstall.

The host companion prepares scoped private-network firewall rules; a client Start request manages driver registration and host SteamVR launch. Open the client app on the headset PC (Bazzite or Windows); with automatic connection
enabled, headset clients on the trusted private LAN are allowed automatically.
Windows needs an active **Private** network and no active **Public** network.
If the Private network is not ready at sign-in, RC1 keeps read-only checks running
with a capped backoff without opening a browser or SteamVR. Once ready, firewall
setup is attempted once; a failed permission request needs an explicit settings
retry. First-time firewall setup may request Windows administrator consent. Automatic
first pairing is enabled for the trusted home LAN. Known paired companions can
start subsequent sessions; individual ALVR client permissions remain in Advanced.
Do not launch a separate ALVR instance alongside VROSTBridge. Select codec and
bitrate on the client before Start. Client Stop closes SteamVR on both PCs; the
host returns to waiting. Closing a browser tab leaves the companion running.
The optional **Start the waiting host with Windows** installer component creates
an ordinary per-user sign-in entry; it does not start SteamVR or a browser.

If discovery/manual connection fails, the host page's **Advanced**
section includes **Repair Windows Firewall access** to retry the firewall setup.
That action asks Windows for administrator consent to add scoped private-network
rules. It does not disable the firewall or change Windows network profiles.

### If the host does not open

Open File Explorer and enter `%LOCALAPPDATA%\VROSTBridge`. Keep these files if
present:

- `host-native.log` — native installed launcher startup.
- `host-startup.log` — Python desktop launcher startup and exceptions.
- `latest.log` — session activity after the launcher has started.

Record whether the **installer**, the **Start-menu shortcut**, or **Start session**
failed. Include the exact visible error and those logs when reporting a problem.
Do not delete the installation or repeatedly reinstall before collecting this
information. If no browser appears, launching the shortcut again should reopen
the running interface; the startup logs explain failures before that point.

Use the normal Windows app removal entry for uninstalling. The installer removes the owned driver registration and firewall rules while retaining user state.

### Host provenance

The native driver is built from ALVR revision
`f5023f8e78fa14ea57fb1038548d7820289a8524` plus this release's source patches.
`dist/host/vrostbridge-build.json` records the native protocol, complete patch-set
hashes and native-file hashes. The existing embedded Python runtime and unchanged
supporting host dependencies retain their earlier provenance in
`HOST_PROVENANCE.txt`. The installer assembler rejects a mismatched native driver.
Windows cross-compilation and package checks do not establish physical GPU/headset
interoperability. See the release evidence for the exact validation boundary.

## Bazzite headset client

1. In desktop mode, download **VROSTBridge-Bazzite-Installer-1.0.0.13.zip**.
2. Extract the **whole ZIP** into a normal folder.
3. Open **Install VROSTBridge.desktop**. Allow that launcher to execute if KDE
   asks. Keep its adjacent `payload` folder in place during installation.
4. Open **VROSTBridge Client** from the application menu. It opens its desktop
   page. Select **Start session** there to start both SteamVR runtimes and the connection. For the first run, verify the headset
   works locally in SteamVR before diagnosing a remote session.

The installer copies verified files under `~/.local/share/vrostbridge` by default,
respecting `XDG_DATA_HOME`. It does not modify Bazzite's immutable base image,
layer an RPM, require root or ask you to install Python/compiler tools. The package
includes its launcher runtime, native client, decoder libraries, camera helper,
the logo in the icon sizes the desktop uses, and the open-source notices. The installer checks the staged payload,
switches the active version atomically and removes only old version folders with
the expected VROSTBridge ownership marker. Reinstall repairs modified files of
the same release. The separate preferences/log folder and ALVR client identity
are retained, and so are Baballonia (`~/.local/share/vrostbridge/baballonia`), its
settings and its calibration (`~/.config/ProjectBabble`): an update uses them as
they are. Its last message says so (*Eye tracking: Baballonia … found and kept*).
From 1.0.0.3 a Baballonia that an installation before 1.0.0.1 left under the old
folder name is found and moved into place by the installer (or at the client's next
start), without a download.

SteamVR, GPU drivers and normal desktop graphics/audio libraries must already work.
The native runtime targets x86-64 Linux with glibc 2.39 or later. It runs directly
in the Bazzite desktop session, outside a container and outside Proton. The
package is neither an RPM nor a Flatpak.

On the client page, **Allow home-network connection** appears when the Bazzite
firewall needs the inbound connection rules. Approve the authentication prompt
to allow the required traffic from the current home subnet. The rules take effect
immediately and persist across reboots. The page reports if access is needed
again after a network or firewall change. This does not layer an RPM, change the
firewall zone, disable the firewall or modify the immutable base image.

Logs normally live in `~/.local/state/VROSTBridge`, respecting `XDG_STATE_HOME`.
Use **Save report** in the desktop interface after a test, or **Send report to host**
to download it from the host page under *Reports* (the host keeps the latest 20). Optional Beyond eye
inference/calibration uses separately installed Baballonia; see
[BEYOND_EYE_TRACKING.md](BEYOND_EYE_TRACKING.md).

To remove the client, use **Uninstall VROSTBridge Client** from the application
menu. It removes owned installed versions and launchers, the VROSTBridge entry in
Steam (Steam restarts if it is open) and the Game Mode setup files, while retaining
preferences, reports, logs and the ALVR client identity. From 1.0.0.3 it asks
about Baballonia, the eye-tracking program VROSTBridge downloaded (about 840 MB):
**Keep** (the default button) leaves it for the next installation, which uses it
without downloading it again; **Remove** deletes it; **Cancel** stops the
uninstall. Its settings and calibration in `~/.config/ProjectBabble` stay either
way. Without a dialog program, `VROSTBRIDGE_UNINSTALL_CONFIRMED=1` uninstalls and
keeps it, and `VROSTBRIDGE_KEEP_EYE_TRACKING=0` removes it too. A stale uninstaller does
not remove a newer installation's launchers. Apply the corresponding XDG paths
when locating preserved state if you customised them.

Uninstall also removes the firewall exceptions created by VROSTBridge on the
current installation. Windows or Bazzite may request administrator authentication.
Existing unrelated rules are retained. If cleanup cannot be verified or permission
is declined, the application files and ownership records are kept for a retry.

For an Ethernet comparison, stop the session, select the wired adapter under
**Network → Network adapter** and start again at unchanged resolution, codec, bitrate and
refresh. The *Network* section shows which kind of link is in use and its speed. Automatic follows the OS-selected route; an explicit Wi-Fi choice remains
Wi-Fi. New firewall zones may need the usual client home-network allowance.
From 1.0.0.4 a wired link is paced at or below the highest rate its network test
carried without loss, when the test found a rate it could not carry; a wired path tested before 1.0.0.4 is tested once more before
its next session (about 15 seconds). From 1.0.0.5 that rate is the step below the first one that lost data steadily (1 % of its
packets or more), a wired automatic bitrate stays at or below 40 % of the pace, and a test that found no clean rate (or received
nothing) is repeated before the next session, up to three times in a row, while an earlier clean result stays in use. From 1.0.0.6 a step counts as late only for the delay the network added to its frames, not for the time the host PC itself took to send them; a result that lateness limited before 1.0.0.6 is tested again before the next session. From 1.0.0.8 the pace is 70 % of that loss-free rate, automatic bitrate stops at what the stream's picture needs, and during a session it steps down when decoding falls behind or frames go missing and back up as the stream runs clean (both PCs on 1.0.0.8).

## Windows headset client (new in 1.0.0.7)

The Windows client has not run on Windows hardware yet. It was built and tested here:
its Python parts by unit tests, its native programs by their tests under Wine, and its
installer, companion and page under Wine. Expect first-run problems, and send reports.

1. Install Steam and SteamVR on the headset PC. Connect the headset, run SteamVR once,
   complete its room setup and check both controllers, then close SteamVR.
2. Run **VROSTBridge-Client-Setup-1.0.0.13.exe**. It needs 64-bit Windows 10 or 11 (it
   refuses older or 32-bit Windows), installs for the current user and needs no
   administrator rights. It is not code-signed, so SmartScreen may say *Windows
   protected your PC*: choose **More info**, then **Run anyway**. The optional
   **Start when I sign in** component stays off unless you tick it.
3. Open **VROSTBridge Client** from the Start menu. It opens its page in your browser
   and waits in the system tray. Select **Allow home-network connection** and accept
   the administrator prompt once, then **Start session**.

The installer copies the program to `%LOCALAPPDATA%\VROSTBridge\Client\1.0.0.13` (about
63 MB). It adds **VROSTBridge Client**, *Client startup logs*, *Client open-source
credits* and *Uninstall VROSTBridge Client* to the Start menu's *VROSTBridge* folder,
and *VROSTBridge Client* to *Settings → Apps*. The package carries its own Python, the
native client with its libraries (the transport built on ALVR's client core, FFmpeg's
decoding libraries with their source, and OpenVR's), the companion and the open-source
notices. The native programs link the C runtime statically, so no Visual C++ runtime
is installed or needed. Settings, pairing and logs live in `%LOCALAPPDATA%\VROSTBridge`,
outside the program folder.

Close SteamVR on the headset PC before installing. Before anything is replaced, setup
runs the new launcher's self-test, which checks every file against the package's
`SHA256SUMS`; if it fails, the existing installation stays unchanged and
`client-native.log` says why. Setup then closes an open VROSTBridge Client through its
own authenticated page, copies the new version and only then removes the earlier
version folder. Running the same setup again repairs the installation; if that copy
fails, the previous files are put back. Later versions install over earlier ones the same
way, without uninstalling first, and keep settings, pairing, logs, the downloaded
Baballonia, and Baballonia's settings and calibration (`%APPDATA%\ProjectBabble`).

**Start when I sign in** (the installer's component, or *Start VROSTBridge Client when
I sign in* under *This PC* on the page) writes this user's sign-in entry
`VROSTBridgeClient`. It starts the client quietly in the tray (`--background`): no
browser and no SteamVR. An update keeps the choice as it was, and never replaces a
sign-in entry of that name that another program wrote.

For a silent installation run `VROSTBridge-Client-Setup-1.0.0.13.exe /S`, and add
`/STARTUP` to turn the sign-in start on. `Uninstall.exe /S`, in the program folder,
uninstalls without questions and keeps the downloaded Baballonia; add
`/REMOVEEYETRACKING` to remove it too. Windows still asks for administrator permission
if the client's firewall rules have to be removed.

**Allow home-network connection** on the client page adds five inbound Windows Firewall
rules named `VROSTBridge.Client.…` after one administrator (UAC) prompt: TCP 9943, UDP
9944 and UDP 5353 for the native client, and TCP 9945 and UDP 9944 and 9946 for the
companion, the same ports the Bazzite client opens. They apply to private networks and
the local subnet only. The button does not turn the firewall off or change the
network's category. The page warns when Windows treats your network as Public, where
the rules do not apply (set it to Private in *Settings → Network & internet*), and when
a declined Windows Firewall prompt left a rule that blocks VROSTBridge (block rules win
over allow rules: remove it in *Windows Defender Firewall → Inbound Rules*).

To remove the client, close SteamVR and use **Uninstall VROSTBridge Client** in the
Start menu or *VROSTBridge Client* in *Settings → Apps*. If VROSTBridge downloaded
Baballonia, the uninstaller first asks about it: **Yes** keeps it (the default) for the
next installation, which uses it without downloading it again; **No** removes it;
**Cancel** leaves VROSTBridge Client installed. It then closes the client, restores
SteamVR settings that a session changed if they are still held, and removes the
client's own firewall rules: Windows asks for administrator permission once, only if
any exist. If you decline, the uninstall stops and the client stays installed; run the
uninstaller again and approve. Last, it removes the program folder, its Start menu
entries, its *Settings → Apps* entry and the sign-in entry if it is the client's own.
Settings, pairing and logs in `%LOCALAPPDATA%\VROSTBridge`, Baballonia's settings and
calibration in `%APPDATA%\ProjectBabble`, and a Baballonia you installed yourself stay.

The portable ZIP, `VROSTBridge-Client-Portable-1.0.0.13.zip`, holds the same files in a
`VROSTBridge-Client` folder: extract it whole and open `VROSTBridge Client.exe`. It has
no Start menu entries and no uninstaller. To remove it, turn off its sign-in start on
the page, quit it and delete the folder; its firewall rules stay until you remove the
`VROSTBridge.Client.…` rules in *Windows Defender Firewall → Inbound Rules*.

**Host and client on one PC.** VROSTBridge Host and VROSTBridge Client can be installed
side by side on one Windows PC. Each has its own program folder
(`%LOCALAPPDATA%\VROSTBridge\Host\<version>` and `…\Client\<version>`), its own
*Settings → Apps* entry, sign-in entry and firewall rules, its own Start menu entries
in the shared *VROSTBridge* folder, and its own files in the shared settings folder.
Uninstalling one leaves the other in place.

For eye tracking, Baballonia's Windows installer (1.1.1.0rc6, about 423 MB) is
downloaded only when you set it up on the client page, checked against its exact size
and SHA-256, and installed silently into `%LOCALAPPDATA%\VROSTBridge\Baballonia`
(about 0.88 GB), without Start menu or *Settings → Apps* entries of its own. If you
installed the same Baballonia version yourself, VROSTBridge uses yours and never
removes it. How it works on Windows: [technical reference, section 9](TECHNICAL_REFERENCE.md#9-eye-tracking-bigscreen-beyond-2e).

### If the client does not open

Open File Explorer and enter `%LOCALAPPDATA%\VROSTBridge`, or choose *Client startup
logs* in the Start menu. Keep these files if present:

- `client-native.log` — native installed launcher startup.
- `client-startup.log` — Python companion startup and exceptions.
- `client-latest.log` — session activity after the companion has started.

As for the host, record which step failed and the exact visible error, and do not
delete the installation or reinstall repeatedly before collecting them. Closing the
browser leaves the client in its tray: **Open VROSTBridge** reopens the page and
**Quit** ends it.

## What installation proves

Package integrity checks and launcher smoke tests check extraction, bundled
runtime loading, resource lookup and application startup. They do not validate
SteamVR's compositor, hardware decoding, tracking, input, audio or latency.

The hardware VROSTBridge has been used with, and what remains to be checked on it, is in [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md). Any Wine or cross-build check is not physical Windows SteamVR/GPU validation: the Windows client's installer, companion and page have run only under Wine, and its native programs only in their tests there, with a software Vulkan driver.

For Steam Game Mode, select **Add to Steam** on the client page in Desktop Mode. Steam closes, VROSTBridge is added to your library as **VROSTBridge** with its artwork, the Game Mode display handling is set up (a connected headset is never used as the Game Mode screen) and Steam opens again. **Remove from Steam** undoes both. Connect and power the headset before entering Game Mode. See [BAZZITE_PLAY.md](BAZZITE_PLAY.md) for capability requirements and the physical validation boundary. *Add to Steam* replaces VROSTBridge entries you added by hand (Steam treats the new entry as a new game, so play time and a controller layout made for the old one do not carry over).

The client's menu entries are `io.vrostbridge.Client.desktop`, `io.vrostbridge.GameMode.desktop` and `io.vrostbridge.Uninstall.desktop`; an upgrade rewrites them in place, so a pinned taskbar or menu entry stays. The installer also installs the icon (theme name `vrostbridge`) in every size from 16 to 512 pixels.

On the Windows rendering PC, closing the web browser leaves the host idle in its tray. Use the tray menu to open Settings or Exit. The web preference **Launch at Windows sign-in** writes only this user's owned startup entry. Sign-in launches `--background`: no browser and no SteamVR. The installer retains an existing disabled startup preference and does not override a foreign startup command. If Task Manager has disabled the entry, the web UI explains how to re-enable it in Windows settings.
