# VROSTBridge 0.2.6 — regenerated release review

This release was regenerated from the verified, persisted 0.2.5 source and matching host/client packages. The unfinished previous 0.2.6 workspace was not recoverable. ALVR's pinned host distribution and existing transport ABI remain unchanged; the Linux native scene, companion, installers and Windows launcher are rebuilt.

## Requested changes

1. Windows has a native notification-area icon using the shared transparent artwork. It shows session status, Open VROSTBridge and Quit. Explorer restarts and delayed tray availability are handled. The web UI controls per-user sign-in startup, always using quiet `--background`; no browser or SteamVR opens at sign-in. Upgrade retains an existing startup-off preference.
2. Local SteamVR is always stream-only during bridge use. Home/dashboard are disabled and System input enabled offline before startup, then verified. Stop closes the compositor before restoring prior preferences. Native errors request shutdown while preserving a neutral scene when possible. Old settings receipts are recovered offline. The physical boundary and runtime safety/error visuals remain active.
3. Bazzite gets a native SDL gamepad/touch/keyboard window plus real gamescope display-lease integration. One-time desktop setup records a reversible compositor wrapper, checks installed capabilities, and reserves only a recognized headset. The native helper exposes the lease to SteamVR via the standard Wayland leasing protocol. Start fails with an explanation if display access is unavailable; it does not substitute local Home. The desktop web UI remains available.

The existing client-led session broker, source-frame pacing changes, separate controller inputs, host menu routing, rectangular room transfer, working game audio, quality controls and combined diagnostic collection from 0.2.5 are retained. No new measured latency or judder improvement is claimed by this rebuild.

## Validation

Release evidence is included under `docs/evidence/`. Automated checks cover settings recovery, process identity constraints, session ordering/cancellation, ownership-preserving setup/removal, registry startup behavior, desktop controls, native SDL rendering, standard Wayland FD transfer and single-grant lifetime, native compilation and package integrity. The final install smoke report records fresh install, actual 0.2.5 upgrade, repair, shutdown and uninstall outcomes against the built frozen package.

**Not validated here:** execution on a real Windows desktop/Explorer; SteamVR and GPU-driver interoperability with the Game Mode lease; physical Index menu/input/pacing behavior in 0.2.6; Beyond 2e display or calibrated gaze. The older user-confirmed remote Index video/tracking/audio is prototype evidence, not validation of these changes. This remains an unsigned development build.

## First test

Install both matching packages over the old installations after stopping sessions. Open the Windows host, confirm its tray icon and leave it waiting. Use the desktop client first at the saved stream settings (new installs use H.264 / 80 Mbit/s), test Start/Stop twice, both controllers and host menu. Save the host report while connected. Then follow `BAZZITE_PLAY.md` for one-time Game Mode setup and a separate headset test. Headset power/display must be present when entering Game Mode. Keep Proton off for the native Steam shortcut.

Do not delete preferences or the ALVR identity to upgrade. Installers remove only recognized old application payloads. Uninstall also removes unmodified Game Mode setup, restores pending runtime preferences when SteamVR is stopped, and preserves user reports/settings. User-edited integration files are retained with an explanation.
