# Bazzite Game Mode — VROSTBridge Client 1.0.0.13

Updated 2026-10-15. Physical Game Mode acceptance remains pending; see [PROJECT_SPEC.md](PROJECT_SPEC.md).

VROSTBridge has a controller-friendly screen for Bazzite's Steam Game Mode and a headset display adapter for its gamescope session. The **VROSTBridge Client** application entry still opens the desktop web page. Both interfaces use the same saved preferences and session controller. Opening either one leaves SteamVR stopped until you select **Start session**.

## Set up once in Desktop Mode

1. Install the client and the Windows host. Confirm a desktop session works and grant the home-network firewall permission there.
2. On the client page, open **This PC → Steam Game Mode** and select **Add to Steam**. Steam closes, VROSTBridge is added to your library as **VROSTBridge** with its cover, wide capsule, background and title logo, and Steam opens again. The same step sets up the Game Mode display handling (below). Do this in Desktop Mode: in Game Mode, Steam is the session itself and cannot restart to apply it.
3. *Add to Steam* also replaces a VROSTBridge entry you added to Steam by hand (one that runs the client for Game Mode). Steam treats the new entry as a new game, so play time and a controller layout made for the old entry do not carry over. Your other shortcuts are kept exactly.
4. Connect and power the headset **before entering Game Mode**: gamescope chooses its screen and reserves the headset when the session starts. After installing or updating VROSTBridge, switch to Desktop Mode and back to Game Mode once. Open **VROSTBridge** from the Steam library and select **Start session**. The Windows host must be waiting in its tray. If the headset display is not reserved, the session card says why (for example *Game Mode started without the headset (no supported headset was plugged in and powered)*) and Start explains it again.

The screen is drawn at your display's own resolution with the pages' fonts and colours. D-pad, left stick or arrow keys move the focus to the nearest control in that direction; A/Enter activates; B/Escape goes back one page and does nothing on the main page (RC4.3: it used to close the app). Touch and mouse also work. From 1.0.0.4, on a settings list up and down step through the rows and left and right change the focused setting (the previous or next value; switches off with left and on with right) instead of jumping to *Back*; rows scrolled out of view are reached only by stepping through the list, and a list stops at its ends. Holding a direction repeats it (after 0.38 s, then about eight steps a second; the stick moves past half way and lets go below a third), and one press reported twice, by Steam's virtual controller and the physical one or by a button and a key, moves once. The main page has Start/Stop, the session status and **Open host menu**; it leads to the settings pages, which have every setting of the desktop page and scroll with the D-pad; the focused row explains what it does:
- **Stream & headset:** stream resolution (an Index's rates, choosable before Start; on a Beyond the mode it is in), codec, bitrate, foveated encoding (with eye tracking also the sharp region and the outside resolution), sharpening (applies at once), 10-bit colour, encoder priority, quick resume and the network test; the headset's lighter choice (a lower rate on an Index, *High* on other headsets) when this PC's graphics fell behind; **Eye-tracking view** while eye-tracked foveation runs: both eyes with the sharp region and where each eye looks, live, with the numbers beside them (never saved);
- **Motion & timing:** re-aim (switchable during a session), the alignment check and its question, motion prediction, frame timing lock, late-frame catch-up, pacing and gradual loss recovery;
- **Network & host:** this PC's connection, host address, network adapter, Wi-Fi options, Wake-on-LAN and *Wake host now*;
- **This PC:** keep awake, headset audio, sending the microphone to the host, the quality line, eye tracking, CPU decoding, the SteamVR reprojection repair, *Resend play area* and *Restore saved SteamVR settings* when offered, and (1.0.0.4) *Set the GPU clock to automatic* while a fixed GPU clock holds the processor below its own minimum (the session card says so too); from 1.0.0.11 *Keep the frame rate when this PC falls behind* (applies at once) and *Skip what the lenses never show*;
- **Reports** (send to the host or save on this PC) and **About**.

From 1.0.0.5 the session card says *Headset not tracked* when SteamVR on this PC has had no position for the headset for 2 seconds (the host cannot render without one) and what to check: the base stations, and that the headset is awake; and the graphics warning offers *Set the GPU clock to automatic* when a fixed GPU clock holds the GPU back. From 1.0.0.6 *This PC → Eye tracking* has a *Gaze reach* row while eye tracking runs: how far your gaze went each way this session; choose it to measure again.

Settings that apply at the next Start are locked during a session, as on the desktop page. **Exit VROSTBridge** closes the app. Stop keeps the screen open and closes both SteamVR sessions. Exit asks whether to end an active session; Steam's Stop/SIGTERM also requests orderly shutdown. Steam's force-kill after its own timeout can interrupt cleanup; reopen the desktop client to recover retained settings if that happens.

## The handheld's screen stays on

Bazzite starts gamescope with `--prefer-output *,eDP-1`: any external output is preferred over the built-in screen. A connected VR headset is an external DisplayPort output, so it became the Game Mode screen and the handheld's own display went dark.

VROSTBridge's gamescope wrapper rewrites that preference so headset outputs come after every other output, including the built-in screen. gamescope gives a listed output its position and every unlisted one the position of `*`. Ranked last are:
- outputs with a connected headset, recognised by their display identity (EDID: the Valve Index's product codes, or the head-mounted markers below);
- remembered headset ports that are empty now, so a headset plugged in later does not take over.

A port where a headset was once connected but that now has an ordinary monitor keeps its normal place. External monitors still come before the built-in screen as before.

The wrapper never prevents Game Mode from starting. Any failure in its additions runs plain `/usr/bin/gamescope` with the original arguments. If VROSTBridge was removed without undoing the setup, the wrapper also runs plain gamescope. *Add to Steam* brings a wrapper written by an earlier release up to date.

## Headset display reservation

The setup also asks gamescope to reserve the headset for SteamVR (`--lease-connector`). This needs a gamescope that advertises `--lease-connector` and a gamescope-session-plus that supports `GAMESCOPE_BIN`; the page reports a missing capability. It does not replace your graphics stack. Automatic selection supports one recognised headset; with several, choose a connector with the setup action. A display adapter on another GPU with the same connected DP name is rejected to avoid reserving an unrelated screen. If the headset was absent or off when Game Mode started, connect it and re-enter Game Mode.

At Game Mode start the wrapper records its own process and start time, then becomes gamescope. Before each Start the companion checks that this gamescope still runs: the same process and start time, your user, gamescope with the headset's `--lease-connector`. 1.0.0.3: Bazzite's gamescope has a file capability (`cap_sys_nice`), which makes Linux refuse even the same user the link to its program (`/proc/PID/exe`). Up to 1.0.0.2 the check needed that link, so the card always said *The previous display lease is stale*. The check now uses only what the user can read, and also checks the program link where it can be read. When that gamescope has ended, the card says *The gamescope that reserved the headset display is no longer running*.

A small native helper receives gamescope's DRM lease over its private Unix socket. It verifies the card, connector, EDID and leased resources, then exposes the standard `wp_drm_lease_device_v1` protocol on a Wayland socket of its own, `vrostbridge-headset-<number>` in your runtime directory (from 1.0.0.10 a plain name, the usual form, which libraries and container runtimes that accept only a name can find; it was a path to a socket in a folder of its own). Only SteamVR receives that display environment; the headset PC's own screen continues on gamescope. SteamVR is launched through its registered startup script so an already-running Steam client cannot discard the lease environment, and from 1.0.0.10 without gamescope's own display marker (`GAMESCOPE_WAYLAND_DISPLAY`), so the only Wayland display it is given is the helper's: gamescope's cannot lend it the headset. It keeps gamescope's X display for its windows.

If SteamVR's compositor still cannot take the headset display (SteamVR error 496, as in the 1.0.0.9 test), Start stops within seconds and says so, instead of waiting out a minute: retrying cannot help. The log says which display SteamVR's server was started with, which programs reached the adapter and bound its lease interface (`LEASE_CLIENT` lines), whether the headset was leased (`LEASE_GRANTED`), and what SteamVR's compositor said about the headset display. The lines of the last Start in Game Mode are kept in `game-mode-last-start.json` in the client's state folder and go into every client report, so a report saved later in Desktop Mode still has them.

The adapter grants one lease per runtime lifetime. Stop closes SteamVR before closing the adapter. gamescope owns the original kernel lease; closing a duplicate FD is not revocation and the implementation does not pretend otherwise. The adapter neither subleases nor drops master on gamescope's FD.

The setup recognises a headset by its EDID (RC4.3, `core/headset_edid.hpp` and `launcher/game_mode.py`): the Valve Index's product codes (VLV 0x91a8 / 0x91b0–0x91bf) and Beyond BIG 0x1234, and any display carrying the head-mounted markers Linux itself uses to keep it off the desktop: a Microsoft HMD vendor block (version 1 or 2, or 3 without desktop use) or a DisplayID 2.0 head-mounted VR/AR primary use. RC4.2 knew only the fixed codes and missed the Beyond 2e. Ordinary monitors carry neither marker and are never reserved. Each Game Mode start records what the wrapper did in `$XDG_RUNTIME_DIR/vrostbridge/gamescope-wrapper.json` (time, reserved output or the reason none was, and every connected display with its maker, product code and markers); the page status and reports include it. An idle companion from a previous desktop session is closed and reopened in the current graphical environment; an active session is not taken over.

## The Steam library entry

Steam keeps non-Steam games in `userdata/<account>/config/shortcuts.vdf` (binary KeyValues) and their artwork in `config/grid/`. A running Steam rewrites that file when it exits, so VROSTBridge closes Steam (`steam -shutdown`), changes the file and starts Steam again (`steam -silent`), with Steam's own environment rather than the frozen companion's library path.
- The file is parsed and written back; unless that reproduces the original bytes exactly, nothing is changed.
- A backup (`shortcuts.vdf.vrostbridge-backup`) is kept before the first change, and the new file replaces the old one atomically.
- Only entries that run `VROSTBridge-Client` (the client's program) are replaced or removed; other shortcuts keep their order, fields and values.
- Artwork is written under the shortcut's own id. Removal deletes only files whose contents are still the ones VROSTBridge wrote; artwork you replaced is kept.
- From 1.0.0, when an update brings new artwork, the companion replaces the library images it wrote (only those still unchanged, only while the entry is in Steam) when it starts; Steam shows them after it next starts.
- The entry runs `VROSTBridge-Client --role client --game-mode` from the installed `current` folder, so it survives updates. Only the most recently signed-in Steam account is changed.

## Stream-only client

Before SteamVR starts, VROSTBridge saves the affected preferences, disables Home and the local dashboard, and enables System-button delivery to applications. A pre-existing local SteamVR session is explicitly restarted with these preferences. Settings are verified before the native scene starts. During connection loss the native scene presents a neutral image instead of launching local Home, and the headset shows a status card. Fatal native errors trigger session shutdown while retaining the neutral scene when graphics remain usable.

The SteamVR compositor, tracking, reprojection and physical boundary remain necessary and active. SteamVR or GPU failures can still produce runtime-owned safety/error visuals; this release does not suppress those or promise that a driver crash can be hidden. The controller menu path continues to target the host. Left B + left thumbstick-click and **Open host menu** remain available if a SteamVR binding consumes the physical System button.

Preferences are restored **after local SteamVR has fully stopped**. Crash receipts survive until restoration succeeds. Unrelated settings and intervening user changes are preserved.

## Recovery and removal

**Remove from Steam** removes the library entry and its artwork and the Game Mode setup files (unmodified ones only). Re-enter Game Mode afterward to release the compositor's startup reservation. Uninstall does the same. User-edited setup files are retained with an explanation rather than deleted. Reports include the detected session, setup/lease state, requested SteamVR display environment, adapter output and, from 1.0.0.10, the last Start in Game Mode. Use **Reports → Send report to host** in Game Mode and download it on the host page.

## Validation boundary

The screen, standard Wayland protocol FD transfer, display ranking, Steam shortcut handling, settings/lifecycle logic and installer paths have automated checks. **Physical acceptance of the built-in screen staying on, the gamescope lease, Game Mode Index streaming and Beyond 2e operation remains pending.** A capability check makes the path eligible to attempt; it is not proof of working VR. SteamVR's environment in Game Mode and the early stop on error 496 (1.0.0.10) are tested with a stand-in adapter and SteamVR's logged lines. First test in Desktop Mode, then separately collect a Game Mode report.

Implementation references: [OGC gamescope DRM backend](https://github.com/OpenGamingCollective/gamescope/blob/ogc/src/Backends/DRMBackend.cpp), [gamescope-session-plus](https://github.com/OpenGamingCollective/gamescope-session/blob/main/usr/share/gamescope-session-plus/gamescope-session-plus), and [Wayland DRM lease protocol](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/staging/drm-lease/drm-lease-v1.xml). gamescope is an existing system dependency, not bundled or patched by this installer.
