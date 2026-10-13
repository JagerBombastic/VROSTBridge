# VROSTBridge 1.0.0-rc4.5 — eye tracking set up and run by VROSTBridge

RC4.5 integrates eye tracking. Until RC4.4 eye-tracked foveation needed four things done by hand: install Baballonia, start the Beyond 2e eye cameras, point Baballonia at them and at port 9005, and turn on *Calibrated eye tracking* on both PCs. Now there is one switch.
- **Eye tracking** (client page → *Eye tracking* card, or Game Mode → *This PC* → *Eye tracking*): VROSTBridge downloads Baballonia once, then starts and stops it and the eye cameras with each session.
- It sets up Baballonia's cameras, eye areas and output, and turns eye tracking on at the host for the session.
- You calibrate once, in Desktop Mode.

Baballonia itself (Project Babble's eye-tracking program, which turns the camera pictures into where you look and calibrates for your eyes) is not part of VROSTBridge. It stays Project Babble's program, under its own licence, and is downloaded from Project Babble's GitHub release only when you ask.

**Install:** Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc4.5.zip` and Windows host `VROSTBridge-Host-Setup-1.0.0-rc4.5.exe`. Settings, pairing and logs are kept.
- **Host:** a companion update (session eye tracking, shared page and version); its SteamVR driver is the RC4.1 build, unchanged.
- **Client:** the companion has the new eye tracking; the native client is RC4.4's with the new version number.

## Before you start
- **The RC4.4 test is still open.** Raise the TDP limit first (Quick Access menu → *Performance*, charger connected, any GPU clock setting on automatic), then check that the GPU readout climbs above 800 MHz.
- **Baballonia uses about one CPU core** while it runs. On a handheld the processor and graphics share one power limit, so a higher TDP limit matters even more with eye tracking on. The GPU readout on the session card shows whether the graphics still get enough.

## What changed

### 1. One switch, and a set-up you start (client)
The client page has a new **Eye tracking** card (the old *Calibrated eye tracking* switch and the *Beyond 2e eye cameras* buttons are gone from the client page).
- **Eye tracking:** on or off. It is saved at once and applies at the next Start.
- **Accept licence and set up:** downloads **Baballonia 1.1.1.0rc6** from Project Babble's GitHub release.
  - About 517 MB to download and 841 MB once unpacked; 1.6 GB free is needed while it unpacks.
  - It is checked against the release's pinned size and SHA-256 before anything is unpacked. Only plain files and folders inside its own folder are unpacked.
  - It goes to `~/.local/share/vrostbridge/baballonia`. A failed or cancelled set-up keeps nothing.
  - The card names the licence before you start. The *Babble Software Distribution License 1.0* allows personal, non-commercial use only; setting up accepts it.
- **Open Baballonia / Close Baballonia:** run it without a session (for example to look at the eye pictures), or close it.
- **Allow eye camera access:** shown only if this PC does not yet let you open the eye cameras. It installs VROSTBridge's camera rule (`70-vrostbridge-bigeye.rules`) after the system's own password prompt, and applies it without reconnecting the headset. If no password prompt can be shown (Game Mode), use the page in Desktop Mode.
- **Remove Baballonia:** deletes the download; Baballonia's own settings and calibration stay. Uninstalling VROSTBridge removes the download too.

Game Mode has the same: *This PC → Eye tracking* (switch, *Set up eye tracking* with the licence in its description, progress, Open/Close, camera access, Remove).

### 2. What happens at Start and Stop (client)
With Eye tracking on and a Beyond (or a headset not seen before):
1. At Start, before SteamVR is up, VROSTBridge opens its gaze relay on 127.0.0.1:9005 and starts the eye-camera helper (go-bsb-cams, on 127.0.0.1:8880).
2. It waits for the first camera picture, writes Baballonia's settings (see 5) while Baballonia is closed, and starts Baballonia.
   - Baballonia starts as its own user service, outside Steam's game, so in Game Mode its window never takes the screen from VROSTBridge.
   - A small watcher ends Baballonia and the camera helper if VROSTBridge itself is closed abruptly (crash or kill), so they never keep running on battery.
3. In the sandbox, samples arrived about 4 seconds after Baballonia started, about 30 a second. They pass to the stream for as long as the session runs.
4. At Stop, Baballonia and the cameras stop (a Baballonia you opened yourself stays). If a calibration is running, Baballonia is left to finish it and save it (up to 15 minutes), then closes.

If the session's headset turns out to have no eye cameras (an Index), eye tracking stops for that session and the host is not asked to follow your eyes.

### 3. Calibrate once, in Desktop Mode
Baballonia's general model does not know your eyes, so **until Baballonia is calibrated the fixed *Balanced* region is used** (as with eye tracking off). Baballonia still runs, so it can be calibrated.

Calibration needs Baballonia's window, which only Desktop Mode shows:
1. In Desktop Mode, turn on Eye tracking and start a session with the Beyond 2e. Baballonia's window opens beside it.
2. Put the headset on. In Baballonia's window, start its eye calibration and follow it in the headset. It then trains a model for your eyes. Keep the session running until it has finished (the VROSTBridge log says *Baballonia saved its calibration*).
3. Stop and start the session. The sharp region now follows your eyes, in Desktop Mode and in Game Mode.

In Game Mode an uncalibrated Baballonia is not started at all: it would only cost a CPU core. The note says to calibrate in Desktop Mode.

### 4. The host turns eye tracking on for the session (host and client)
An RC4.5 host lists a new session feature, `session-gaze`. When the client's eye tracking follows your eyes (Beyond, Baballonia calibrated), the client asks the host to turn its eye tracking on for that session. That is ALVR's VRChat eye output on the host PC: VRChat, with OSC on, receives where you look.
- The host's own switch is now *Eye tracking in VRChat* and is no longer needed for this; it still turns it on always.
- A session-only setting is never read back as the host's own preference, also across host updates.
- Quick resume restarts the host's SteamVR when this changes.

Foveation itself never needed the host's switch: the RC4.4 note *turn it on on the host PC too* was wrong and is gone.

### 5. What VROSTBridge sets in Baballonia, and what stays yours
Baballonia keeps its settings in `~/.config/ProjectBabble`. VROSTBridge sets only these, before each start, while Baballonia is closed:
- **Cameras:** both eyes read `http://127.0.0.1:8880/stream` with Baballonia's own MJPEG reader, started automatically. Its OpenCV path waited 13 s and crashed in the sandbox runs.
- **Eye areas, once:** the left and right halves of the Beyond 2e's side-by-side picture, as Baballonia's own *select the entire frame* chooses them. Areas you already chose, and later changes, are kept unless they no longer fit the picture.
- **Output:** VRChat native eye tracking to 127.0.0.1, port 9005. OSCQuery stays off: it would follow VRChat on the host PC instead.
- **No SteamVR auto-start, update check or GPU attempt** (the Linux release runs on the processor). Its first-run introduction is skipped once.

The first time it changes an existing settings file, a copy is kept as `LocalSettings.json.before-vrostbridge`.

Everything else stays yours: calibration, the choice to share eye data with Project Babble (off unless you opt in, in Baballonia), filters and the other settings.

### 6. When something is missing, the page says what (client)
The note under *Eye-tracked* and the Game Mode row now name what is missing:
- eye tracking off;
- Baballonia not set up, or not calibrated;
- a headset without eye cameras;
- *Start again* (eye tracking was not ready when this session started);
- eye tracking starting;
- the eye cameras (permission, not found, no picture);
- Baballonia closed;
- the host keeping the region centred (only an older host's driver can);
- no gaze reaching the host.

The *Eye tracking* card shows Baballonia's state, the samples a second, and the relay's counts. The relay counts Baballonia's other messages (eyelids and expressions, about seven per gaze sample) separately: RC4.4 counted them as invalid.

### 7. When something goes wrong (client)
- **The camera helper ends:** Baballonia is stopped at once and both start again. Its camera reader never reconnects, and spun a CPU core in the sandbox once the stream ended.
- **No samples for 25 seconds while everything runs:** the cameras and Baballonia restart, at most once in five minutes, never during or just after a calibration.
- **Baballonia closed:** the note says so; *Open Baballonia* starts it again.
- **Another Baballonia is open:** it is named, and VROSTBridge does not start its own. Only one can run, and a running one writes its settings back over VROSTBridge's.
- **A Baballonia started before this VROSTBridge:** restarted, because its output may have stopped while nothing listened. Baballonia's output stops for good after one refused send.
- **Port 9005 taken by another program:** said on the card.

Reports include the eye tracking state (no gaze), the relay's counts and the end of Baballonia's log. Where you look is never logged, saved or reported.

### 8. Privacy note
While eye tracking runs, the eye-camera pictures and gaze stay on this PC: 127.0.0.1 only, as with RC4.4's camera helper. Other programs running as you on this PC could read them there; nothing is sent to the host but the combined gaze direction, inside the stream.

## Compatibility
- The client works with RC4.2–RC4.4 hosts. The region follows your eyes as before; VRChat on such a host still needs its own switch (*Calibrated eye tracking* there).
- A Baballonia you installed yourself is not used. Close it before a session with eye tracking: VROSTBridge names it if it is open.

## How to test (please report each)
1. **Install** both. Both pages show 1.0.0-rc4.5.
2. **Set up:** on the client page, open the *Eye tracking* card, turn on *Eye tracking* and choose *Accept licence and set up*. It should end with *Baballonia 1.1.1.0rc6 is set up · not calibrated yet* (note the time it took).
3. **First session, Desktop Mode:** start a session with the Beyond 2e. Within about 10 seconds the card should say *Eye tracking is running: about 30 samples a second*, and Baballonia's window should show both eye pictures, left eye left.
   - If *Allow eye camera access* appears, use it, then report whether the cameras started without reconnecting.
4. **Calibrate:** in Baballonia's window, start the eye calibration and follow it in the headset until it has saved. Then stop and start the session.
5. **Eye-tracked:** with *Foveated encoding* on Auto or Eye-tracked, *Stream & headset → Eye-tracking view* should show the sharp region following your eyes. Look at the edges without moving your head: they should be sharp where you look.
6. **Game Mode:** start a session there. *This PC → Eye tracking* should say *On · running*, and the region should follow your eyes. No Baballonia window should appear.
7. **Load:** note the fps and the GPU readout with Eye tracking on and off (same TDP).
8. **Recovery:** close Baballonia's window during a session in Desktop Mode. The note should say so; *Open Baballonia* should bring it back.
9. **VRChat (optional):** with OSC on in VRChat on the host, your avatar's eyes should follow yours.

Build and test evidence: [evidence/rc4.5-build-1.0.0-rc4.5.json](../evidence/rc4.5-build-1.0.0-rc4.5.json).
