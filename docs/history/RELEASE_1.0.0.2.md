# VROSTBridge 1.0.0.2

1.0.0.2 fixes Baballonia's eye calibration on Bazzite. The fix is in the Bazzite client. The Windows host has the new version number and documents, but its programs work exactly as in 1.0.0.1, so updating it is optional. Everything in the [1.0.0.1 notes](RELEASE_1.0.0.1.md) still applies.

**Install:** `VROSTBridge-Bazzite-Installer-1.0.0.2.zip` on Bazzite and, to keep the numbers matching, `VROSTBridge-Host-Setup-1.0.0.2.exe` on Windows. Both install over 1.0.0.1 and keep its settings, pairing, Baballonia and calibration. There is no need to remove 1.0.0.1 first.

## What was wrong

In 1.0.0.1, choosing **Eye** in Baballonia showed the calibration's window for a moment, then it closed. Nothing appeared in the headset, and Baballonia's calibration buttons stayed greyed out. It happened during a VROSTBridge session and with SteamVR started on its own.

The calibration is a separate program from Project Babble, made with the Godot engine. Up to the RC4.5 test build it ran through OpenXR as an application of its own in SteamVR: it showed in the headset, but during a session SteamVR then ended the stream. From 1.0.0, VROSTBridge starts it as a SteamVR overlay instead, so it shows over the stream and the stream keeps running.

SteamVR takes an overlay's picture through graphics (Vulkan) functions on the program's own graphics device: functions that share memory and synchronisation with SteamVR's compositor. Each group of those functions works only if the program switched it on when it set up its graphics device. The calibration program switches on memory sharing but not semaphore sharing. On Windows the graphics drivers hand the functions over anyway. Mesa, the graphics drivers on Bazzite, returns nothing for a function that was not switched on, and SteamVR called it regardless. The calibration crashed as soon as it showed its first picture: the crash reports point into SteamVR's `vrclient.so`, inside `SetOverlayTexture`. Baballonia then kept waiting for a calibration that was no longer running, which is why its buttons stayed greyed out. SteamVR's own Room Setup crashes on Fedora-based systems for the same reason.

## What changed

- When VROSTBridge starts the calibration as a SteamVR overlay, it now gives that program a small Vulkan layer of its own. The layer switches on the memory, semaphore and fence sharing functions SteamVR uses, where your graphics driver offers them, together with timeline semaphores and synchronization2. Nothing else about the program changes.
- It applies to that one program only. Nothing is installed system-wide, Baballonia's own files are unchanged, and games and other programs never load it. It lives in Baballonia's calibration folder, beside VROSTBridge's calibration launcher (`Calibration/Linux/Overlay/vrostbridge-vulkan`). It is put there when eye tracking starts, and *Remove Baballonia* deletes it with Baballonia.
- Each time the calibration starts, the layer writes one line to Baballonia's log, which a report from the client includes. The line says what the layer switched on and names any function SteamVR uses that your driver still lacks.
- To leave the layer out (for troubleshooting), start VROSTBridge with `VROSTBRIDGE_VULKAN_LAYER=off` in its environment.
- **Version 1.0.0.2** on both programs.

If Baballonia is still open with its calibration buttons greyed out, close it (or stop the session) before trying again. VROSTBridge starts it again with the next session.

## How to test (please report each)

1. Install 1.0.0.2 on Bazzite (and on Windows if you like). The pages show *VROSTBridge Client 1.0.0.2* (and *VROSTBridge Host 1.0.0.2*).
2. Start a session in Desktop Mode with eye tracking on, put the headset on and choose **Show Baballonia** on the *Eye tracking* card.
3. In Baballonia choose **Eye**. The calibration should appear in the headset over the stream and stay there. Follow it to the end, and keep the session running through the training.
4. Report:
   - whether it appeared in the headset and stayed;
   - whether **each eye looks right**: one dot, not doubled or swapped;
   - whether the session kept running through the calibration and the training.
5. If it still closes, send a report from the client straight away (it includes Baballonia's log with the layer's line). If it crashed again, also send `coredumpctl info` for the newest *BabbleCalibration* entry, as before.

## Known limits

- Not yet tried with SteamVR: SteamVR does not run in the build environment. The layer was tested with Baballonia's real calibration program on two software Vulkan drivers (lavapipe and SwiftShader). It loads into the program, and the program's graphics device then has every function SteamVR calls that the driver offers. The calibration ran and drew as before.
- Calibrating in the headset, the eye pictures and the training remain to be confirmed on real hardware.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.2.json](../evidence/vrostbridge-build-1.0.0.2.json).
