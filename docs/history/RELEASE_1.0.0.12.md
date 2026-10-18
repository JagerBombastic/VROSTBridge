# VROSTBridge 1.0.0.12

Fixes from 1.0.0.11's first session on real hardware. Every package carries **1.0.0.12**, and the [1.0.0.11 notes](RELEASE_1.0.0.11.md) still apply.

**Install** over 1.0.0.11 on each PC: `VROSTBridge-Host-Setup-1.0.0.12.exe` on the rendering PC, and `VROSTBridge-Bazzite-Installer-1.0.0.12.zip` or `VROSTBridge-Client-Setup-1.0.0.12.exe` on the headset PC. Settings, pairing, the network test, Baballonia and its calibration stay. Nothing on the network changed, so either PC still works with the other at 1.0.0.11.

## What changed
- **The bitrate recovers** (both clients). When the headset PC's GPU clock is raised, the automatic bitrate goes back to where the session started. A second in which frames never arrived at all (the rendering PC sent nothing) no longer counts against it; late and lost frames still do. In 1.0.0.11 it could fall to 30 Mbit/s in the first minute and stay there.
- **The GPU clock is remembered** (Bazzite). The clock a session needed is held from the start of the next, on the same GPU and power source, and only while the GPU's level is automatic.
- **The add-on warning** on the rendering PC lists only add-ons registered from outside SteamVR, no longer SteamVR's own drivers.

## How to test
Play with *Use recommended settings*. The bitrate line on the headset PC's page should climb back after a slow start, a second session should say *GPU held at … MHz from the start*, and the add-on warning should name only add-ons you installed. Send both reports.

## Known limits
- Tested on one setup: the fresh start, the rule for frames that never came and the add-on warning worked there; holding the remembered clock from the start awaits a second session.
- The rendering PC's occasional stalls are not fixed; they just no longer cost bitrate.
- Game Mode on Bazzite doesn't start sessions yet, and the Windows client hasn't run on real hardware.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.12.json](../evidence/vrostbridge-build-1.0.0.12.json).
