# VROSTBridge 1.0.0-rc4.4 — a held-back GPU named, SteamVR crash recovery, a lighter frame pass, sharper headset cards, the Game Mode display kept

RC4.4 follows the RC4.3 test of 4 October 2026 (17:27–17:35). The logs showed four things:
- **Jittery head and controller tracking.** The Bazzite PC's integrated GPU (rated up to 2700 MHz) stayed at 800 MHz in every sample of the session while 58 % busy, with the chip drawing about 15 W, plugged in and charging. Both RC4.2 tests show the same. At High (2560) SteamVR showed the stream at 37.5 fps for 407 of the 412 streaming seconds, so latency doubled and the host predicted head and controller poses about 96 ms ahead: that is the jitter. A power limit (TDP) or a fixed GPU clock holds the GPU back, not the GPU itself.
- **No reconnecting screen.** SteamVR's own compositor on the Bazzite PC crashed at 17:35:12 (SteamVR's server log). The compositor draws everything in the headset, VROSTBridge's screens included, so nothing could be shown. Nine seconds later the VROSTBridge client was killed by a broken pipe (exit −13), which ended the session.
- **No eye-tracked settings or eye view.** *Calibrated eye tracking* was off, so *Eye-tracked* fell back to the fixed *Balanced* region, and RC4.3 hid the eye-tracked settings until the region followed the eyes. Foveated streaming itself worked: the host sent 1792 × 1728 per eye instead of 2560 × 2560.
- **A pixelated quality pill.** It was drawn at about one image pixel per headset pixel, and the lens correction enlarged it.

**Install:** Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc4.4.zip` and Windows host `VROSTBridge-Host-Setup-1.0.0-rc4.4.exe`. Settings, pairing and logs are kept.
- **Host:** a companion update (the shared page and version); its SteamVR driver is the RC4.1 build, unchanged.
- **Client:** the native client and runtime probe are rebuilt, `fused.spv` is new.

## First, before anything else: let the GPU clock up
In Game Mode, open the Quick Access menu → **Performance** (or Handheld Daemon's menu), with the charger connected:
1. raise the **TDP limit** as far as it goes;
2. if there is a **GPU clock** setting, leave it on automatic (not a fixed value).

Then start at High and watch the new GPU readout (*GPU 800 of 2700 MHz* on the Game Mode session card and the client page): during a session it should climb well above 800 MHz. If SteamVR still falls behind, use Standard (2016).

## What changed

### 1. A held-back GPU, named and shown (client)
VROSTBridge now reads the GPU's clock, its maximum and its load every 5 seconds during a session.
- **Live readout:** the client page (*GPU 800 of 2700 MHz, 58% busy, 15 W* on the fresh-video line), the Game Mode session card (*GPU 800 of 2700 MHz*) and the headset's quality line (*GPU 800 MHz*), so a TDP change can be watched as it is made.
- **Warning:** when SteamVR falls behind while the GPU is busy but held at or below half its maximum clock, the warning names that first, for example: *Its GPU ran at 800 of its 2700 MHz while busy, at about 15 W: a power limit (TDP) or a fixed GPU clock is holding it back. With the charger connected, raise the TDP limit as far as it goes and leave the GPU clock on automatic (Game Mode: Quick Access menu > Performance, or Handheld Daemon), then start again.*
- The headset shows *GPU held at 800 MHz · raise the TDP limit* for 12 seconds, and the Game Mode session card says the same.
- A smaller stream size is still offered, after that.

### 2. A lighter frame pass (client)
RC4.3's one pass ran on every frame's critical path with 9 brightness samples per pixel (13 in the sharp region with sharpening) and, with sharpening on, three evaluations of the foveation formula. RC4.4:
- **Catmull-Rom from 5 samples** instead of 9 (the 4 corner samples carry under 2 % of the weight);
- **how many sent pixels each shown pixel spans** comes from the foveation formula's slope, one evaluation instead of three;
- **sharpening samples** only where the picture was sent at full resolution.

Per pixel that is 5 brightness samples (9 with sharpening, in the sharp region only) and one foveation evaluation. On the software Vulkan device the tests use, the pass takes about a fifth less time than RC4.3's (median of 11 runs: 65.6 against 84.4 ms, or 75.3 against 96.7 ms with sharpening; a real GPU will differ). Detail is kept: in the re-aim test, a detailed picture differs from the original by 0.64 levels, against 0.72 for RC4.3 and 2.21 for RC4.2's two passes.

### 3. When SteamVR on this PC crashes (client)
- **The VROSTBridge client survives** a closed pipe instead of being killed by it, and logs when SteamVR's compositor frames stop and start again. It stops by itself if the VROSTBridge companion is gone, instead of running on.
- **The companion notices**, during a session, SteamVR's compositor gone for 2 seconds while its server runs (the RC4.3 crash), SteamVR gone as a whole, or a compositor SteamVR restarted itself while this session's frames stopped. It says so (*Restarting the session… SteamVR's compositor on this PC closed unexpectedly at 17:35:12, so the headset could not show anything…*) and **restarts the session once** on both PCs, as if you had pressed Stop and then Start. The same happens when the client ends with an error because of it.
- A second crash within ten minutes stops the session with the explanation instead.
- Not treated as a crash: SteamVR closing during a Stop, quitting SteamVR yourself (it asks the client to quit) and a clean exit. A *Stop* or *Start* by hand during the restart wins over the automatic one.
- The client log (and so the report) gets what Bazzite recorded about the crash: the compositor's core dump entry and the kernel's graphics messages from the last minutes, when this user may read them. RC4.3's crash left nothing in SteamVR's own logs.

VROSTBridge cannot stop SteamVR's compositor from crashing, and nothing can be shown in the headset while it is down. Please send a report if it happens again.

### 4. Eye-tracked settings shown, with what is missing (client)
- *Sharp region* and *Outside the sharp region* now appear whenever *Eye-tracked* is chosen.
- When the region does not follow your eyes, the note under them says why (page), and Game Mode shows a row for it:
  - **Eye tracking off on this PC** (your RC4.3 test): the fixed *Balanced* region is in use. To use Eye-tracked:
    1. start the Beyond 2e eye cameras on the client page;
    2. calibrate in Baballonia and send its eye output to 127.0.0.1, port 9005;
    3. turn on *Calibrated eye tracking* on both PCs.
  - **On here, but the host keeps the region centred** (during a session): turn on *Calibrated eye tracking* on the host PC too.
  - **The host follows gaze, but none arrives**: check the eye cameras and Baballonia's output to port 9005.
  - **CPU decoding** is selected: foveated encoding is off.

### 5. Sharper headset cards (client)
The quality pill and the status cards are drawn at twice the pixels per degree, so SteamVR shrinks them instead of enlarging them. A large card keeps within the 2 MiB image limit the cards have always used (about 1.35 times for a reconnecting card), and a card is drawn once per change: the next one starts from the density the last card of its kind allowed.

### 6. Sharpening, explained
Sharpening is separate from foveated encoding. Foveated encoding decides how much of the picture is sent; sharpening brings back fine detail when it is shown. The setting's text says so on both pages and in Game Mode.

### 7. Game Mode keeps the headset display for the whole session (client)
In Game Mode, VROSTBridge's display adapter (`VROSTBridgeLease`) holds the headset display for SteamVR. It is set to end when the companion ends, and Linux ties that to the *thread* that started it, not the program: it was started from the Start request's own thread, so it would have been ended as soon as Start finished, taking the headset display with it. Since RC4.3 recognised the Beyond 2e, this would have been the next thing to stop Game Mode sessions. It is now started from a thread that lasts as long as the companion. A test shows both: started the old way, the adapter is terminated when the starting thread ends; started the new way, it keeps running.

## Compatibility
- Works with RC4.2 and RC4.3 hosts; the host page's live eye view still needs RC4.3 or later on both PCs.
- Game Mode's display reservation (the gamescope wrapper) is unchanged: no switch to Desktop Mode and back is needed after installing.

## How to test (please report each)
1. **Install** both. Both pages show 1.0.0-rc4.4.
2. **GPU clock:** raise the TDP limit as far as it goes and leave any GPU clock setting on automatic (charger connected), then start at High.
   - The GPU readout should show well above 800 MHz, the fps tile should hold 75 and the tracking should feel steady.
   - If SteamVR still falls behind, the warning says what the GPU did. Note the MHz it reports, and the TDP you set.
3. **Overlay:** turn on *Stream quality in the headset*. The text should look crisp.
4. **Eye-tracked:** with eye tracking off, choose *Eye-tracked*: the two settings and the note appear (page and Game Mode). With Baballonia set up and eye tracking on at both ends, the eye view appears.
5. **Picture:** the client log says `Frame pass: fused`. Compare *Sharpening* Off and Medium on text.
6. **Game Mode:** start a session from Game Mode with the Beyond 2e. It should keep running after Start finishes (send a report from Game Mode if it ends).
7. **If SteamVR crashes on the Bazzite PC:** the page and Game Mode say so, and the session restarts by itself.

Build and test evidence: [evidence/rc4.4-build-1.0.0-rc4.4.json](../evidence/rc4.4-build-1.0.0-rc4.4.json).
