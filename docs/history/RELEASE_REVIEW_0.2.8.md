# VROSTBridge 0.2.8 — connection audio fix and Windows tray controls

## What the supplied report establishes

The Windows host was running 0.2.7. During its latest attempt, the driver became ready in 1.18 seconds. At 16:30:49 local time on 27 September, its current ALVR event stream and crash log both recorded **“Handshake error … No microphones found.”** The client had reached the host's transport handshake, so this attempt was not blocked at discovery alone.

In the pinned ALVR code, that specific error means the Windows host could not find a matching virtual microphone playback/capture pair. It does not mean the physical Index microphone is absent. Microphone forwarding was optional in the UI, but two integration mistakes let it block video: the inherited default ALVR session enabled it, and host startup used the host's saved flag instead of the client's current selection.

SteamVR's monitor requested a shutdown later, at about 16:31:35; VROSTBridge then observed the runtime exit. The report does not establish who initiated that monitor shutdown. Two earlier starts were stopped soon after becoming ready; their exact cause cannot be recovered from this report. The embedded client report is an old cached **0.2.4** report from 24 September, not evidence of the client version or local-runtime behavior in the new attempt. The compositor log is older too. The confirmed microphone-handshake blocker is fixed below; fresh client diagnostics will help if a second problem remains.

## Changes

- Microphone forwarding defaults off in the bundled host profile and is selected from the **client** for each session. A saved microphone-on flag on the host cannot override a client-off request. Older clients that omit the optional field default to off; install both matching 0.2.8 packages.
- If microphone forwarding is requested, a small read-only Windows Core Audio helper checks active playback/capture endpoint names against the pinned ALVR routing rules. If the required route is missing or cannot be verified, the host turns off microphone forwarding for that session and shows a warning. Video, tracking and game-audio configuration continue unchanged. No audio driver is installed and no system audio preference is changed.
- Automatic, named virtual-cable modes and custom name-substring routes are checked. Custom numeric device indices are not assumed equivalent across enumeration APIs; an unverified route is disabled with a warning. Endpoint detection is not a physical microphone delivery test, and removal after the preflight can still cause a runtime error.
- The notification-area icon has a right-click menu with **Settings — open web UI** and **Exit**, plus status, relevant connection details, bitrate/codec while active, and microphone fallback information. Exit uses the existing orderly companion/session shutdown. The standard hover tooltip is enabled for Windows' version-4 notification API. Keyboard context-menu activation is also handled; Explorer-restart recovery remains.
- Startup, connecting, streaming, stopping and failure are distinguished in the tray. An ended session's error is retained for attention instead of being displayed as ordinary idle standby. Settings opens the web UI without starting SteamVR. Quiet sign-in startup is unchanged.
- The host can collect read-only client reports from the actively paired companion during startup, before the native video socket exists. Access remains limited to that chosen host or the established native control peer; browser-origin requests and control actions are rejected. The existing 60-second disconnect collection grace remains.
- Cached client reports are explicitly marked as not current. Client startup errors are logged before rollback asks the host to stop, so the host can collect the actual reason. Host stop reasons and live handshake errors are more visible. Runtime phase returns to idle after cleanup.

The 0.2.7 startup timing fixes, stream-only local SteamVR handling, desktop web UI, Game Mode integration, working prototype game audio and open-source credits are retained. The native Linux streaming, transport and display-lease binaries remain byte-for-byte 0.2.6; their native-core banner still says 0.2.6. The companion and installer identify as **0.2.8**. No codec, resolution, bitrate or pacing change is included.

## Install and test

1. Stop the bridge, close SteamVR, and quit both old companions. Install **VROSTBridge-Host-Setup-0.2.8.exe** on Windows and **VROSTBridge-Bazzite-Installer-0.2.8.zip** on Bazzite over the previous installations. Managed old program files are replaced; pairing/settings are retained.
2. Confirm **0.2.8** on both web pages. For this first test, leave **Headset microphone off on the client** and keep the previously working video settings. The host no longer has a separate microphone enable checkbox.
3. Open the Windows host and leave it waiting. Start the session **once from the client**, first in Bazzite desktop mode. Let any automatic display-configuration restart finish. Do not manually launch SteamVR on the host.
4. Check image, tracking and game audio. Stop from the client, confirm SteamVR closes on both ends, and start again.
5. Beside the Windows clock (including its hidden-icons arrow), right-click the VROSTBridge icon. Check Settings opens the web UI; check the hover/status text while waiting, starting and streaming. After testing the session, choose Exit and confirm the companion closes. Windows sign-in should still leave it quietly waiting without a browser or SteamVR.
6. If the link still fails, save the **host report immediately**. It should now contain a current client report or clearly identify why one was unavailable. Save the client report too if it could not be collected. Do not rely on an older cached client's streaming numbers.

For a temporary test on **0.2.7**, stop the session, turn off **Headset microphone on the host**, click **Save preferences**, leave it off on the client, and start again from the client. This removes the specific reported microphone blocker, but does not include the other 0.2.8 corrections.

Microphone forwarding is not needed to test video, tracking or game audio. Enable it later only when a compatible Windows virtual audio route is configured. Physical end-to-end microphone acceptance remains separate.

## Validation and limits

**176 Python cases completed: 174 passed, 2 environment-dependent skips.** Tests include actual host-start profile rewriting with an inherited microphone-on profile, strict client preference transport, absent/partial audio routes, optional-probe failures, client error logging before cleanup, startup diagnostic authorization, cached-report freshness, tray status and the registered window-procedure popup dispatch through a simulated Win32 adapter. Desktop JavaScript checks pass on both roles.

All 12 packaged Bazzite installer/lifecycle groups passed, including fresh install, an actual 0.2.7 upgrade, repair, background/UI lifecycle and uninstall preservation. Release evidence is under `docs/evidence/`. The Windows audio helper is cross-compiled against Windows Core Audio headers; native PE/import and archive integrity checks are performed. These checks do not execute Windows Explorer, the Core Audio APIs, SteamVR or a physical headset. Actual connection, tray rendering and microphone routing still need the target PCs. This is an unsigned development build.

## Source references and attribution

ALVR remains pinned to `f5023f8e78fa14ea57fb1038548d7820289a8524`. The virtual microphone selection and error text were checked against [alvr/audio/src/lib.rs](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/audio/src/lib.rs) and handshake flow against [connection.rs](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_core/src/connection.rs).

The Windows probe uses Microsoft's [IMMDeviceEnumerator::EnumAudioEndpoints](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nf-mmdeviceapi-immdeviceenumerator-enumaudioendpoints) and [PKEY_Device_FriendlyName](https://learn.microsoft.com/en-us/windows/win32/coreaudio/pkey-device-friendlyname). The tooltip correction follows [NOTIFYICONDATAW / NIF_SHOWTIP](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-notifyicondataw).

Existing upstream authors, source pins and license notices remain in `OPEN_SOURCE_CREDITS.md`, `licenses/` and the host payload's notices. This source distribution does not embed the user's full diagnostic report.
