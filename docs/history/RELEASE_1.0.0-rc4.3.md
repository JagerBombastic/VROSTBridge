# VROSTBridge 1.0.0-rc4.3 — host microphone, Game Mode that starts, a sharper picture in one pass, eye-tracked foveation you can see

RC4.3 follows the RC4.2 tests of 3–4 October 2026. Four things needed fixing:
- the headset microphone did not appear on the host PC until a virtual audio cable was installed by hand;
- Steam Game Mode could not start a session, and its screen lacked most settings;
- B on the Game Mode main screen closed the app;
- a session at the High stream size felt worse than earlier ones.

You also asked for a picture as sharp as a direct connection, less GPU work on the headset PC so higher stream
sizes fit, better eye-tracked foveation with settings for the sharp and reduced parts, and a live view of where
your eyes look. Sections 8 and 9 cover those.

RC4.3 also fixes two stream-recovery restarts seen in the host logs, and the Wi-Fi 7 multi-link summary.

**Install:** Bazzite client `VROSTBridge-Bazzite-Installer-1.0.0-rc4.3.zip` and Windows host `VROSTBridge-Host-Setup-1.0.0-rc4.3.exe`. Settings, pairing and logs are kept.
- **Host:** a companion update. Its SteamVR driver is the RC4.1 build (patches 0001–0015), unchanged.
- **Client:** the native client (new frame pass), runtime probe and Game Mode lease helper are rebuilt.

Install both. After installing the client, switch to Desktop Mode and back to Game Mode once, so Game Mode starts with the new display handling.

## What changed

### 1. The headset microphone appears on the host (host)
**Why it needed software:** Windows lists a microphone only when an audio driver provides one, and a driver must be signed by Microsoft. VROSTBridge cannot create a microphone by itself. The headset microphone reaches Discord and games through a *virtual audio cable*: the streaming driver plays the microphone into the cable's playback side ("CABLE Input"), and apps record from its recording side.

**Now:** the host page has a **Headset microphone** card that checks this PC's audio devices:
- **Ready:** a cable is installed. Apps can use **CABLE Output (VB-Audio Virtual Cable)** as their microphone.
- **Not set up:** select **Set up headset microphone**. VROSTBridge then:
  1. downloads VB-CABLE, the free virtual audio cable by VB-Audio Software (donationware, [vb-cable.com](https://vb-cable.com)), from VB-Audio's own server;
  2. extracts it to a new folder of its own and holds the files open so nothing can change them, then checks them: unchanged since the download, no other file in the folder, and either the VB-CABLE release this version knows, or, for a newer release, a setup program validly signed by VB-Audio's author (exactly *BUREL VINCENT Entrepreneur individuel*, French registration 423 734 177);
  3. opens VB-Audio's setup program itself. The Windows prompt names VB-Audio as the verified publisher. Select **Install Driver** and allow Windows to install it;
  4. deletes the extracted files when the setup closes, and checks the audio devices again.
- **Restart needed:** VB-CABLE is installed but Windows has not finished. Restart Windows when the card says so.
- **Enable both sides:** VB-CABLE is installed, but Windows lists only *CABLE Input* or only *CABLE Output* (the other is disabled in Windows Sound settings). Enable both, then select **Check again**.
- The setup button shows only while no cable is installed. A check from the page while the setup runs never replaces its progress. If a VB-CABLE setup window from an earlier attempt is still open, the card asks you to finish or close it instead of opening another.

VB-CABLE's licence does not allow building it into another program's installation. VROSTBridge never runs its setup silently, and does not include or redistribute it.

Other places now point to this card:
- When the client asks for the microphone and the host cannot provide it, the session continues without it, and the message says which step is missing:
  - no cable yet: use *Set up headset microphone* on the host;
  - VB-CABLE installed but Windows not finished: restart the host PC;
  - only one of *CABLE Input* and *CABLE Output* is active: enable both in Windows Sound settings.
- The client page's *Headset microphone* option and the Game Mode *This PC* page say the same.

### 2. Game Mode starts a session (client)
**Found:** in Game Mode the headset's display has to be reserved for SteamVR. gamescope reserves it at Game Mode start, through VROSTBridge's wrapper.

RC4.2 recognised a headset only by fixed product codes: the Valve Index's, and one Bigscreen code. The RC4.2 report shows the Bigscreen Beyond 2e on DP-1 was not recognised (`game_mode_setup.connectors` was empty), so:
- it was never reserved;
- Start stayed greyed out;
- the session card showed "Ready to start a session." instead of the reason.

**Now:**
- **Any headset is recognised** by the head-mounted markers its display identity (EDID) carries. These are the markers Linux itself uses to keep a display off the desktop: a Microsoft HMD block or a DisplayID "head-mounted" use, read exactly as the kernel reads them. The Index's product codes still work, and so does the kernel's own *non-desktop* flag on the display (for headsets only the kernel's list knows). The Python check (`launcher/game_mode.py`) and the native lease helper (`core/headset_edid.hpp`) apply the same rules. Desktop monitors are never reserved.
- **gamescope always starts.** If anything in VROSTBridge's wrapper fails, Game Mode starts plain gamescope instead of not starting.
- **Start stays available.** If the headset display is not reserved, the session card says why, and Start explains it if pressed. Examples:
  - "Game Mode started without the headset (no supported headset was plugged in and powered)"
  - "This Game Mode session started without VROSTBridge's display reservation"

  Both end with: *switch to Desktop and back to Game Mode with the headset plugged in and powered*.
- **The wrapper records what it did** at each Game Mode start (`$XDG_RUNTIME_DIR/vrostbridge/gamescope-wrapper.json`). The status lists every connected display with its maker, product code and markers, and reports include both.

### 3. Every setting in Game Mode (client)
The Game Mode screen has the desktop page's settings, on pages that scroll with the D-pad. The focused row explains what it does above the list.

| Page | Settings |
|---|---|
| Stream & headset | stream resolution, codec, bitrate (Auto or fixed), foveated encoding, sharp region and outside resolution (with eye tracking), sharpening, 10-bit colour, encoder priority, quick resume, network test, eye-tracking view (while it runs) |
| Motion & timing | re-aim (switchable during a session), alignment check with its question, motion prediction, frame timing lock, late-frame catch-up, pacing, gradual loss recovery |
| Network & host | this PC's connection, host address, network adapter, stay on one access point (Wi-Fi), pause other Wi-Fi, Wi-Fi power fix (when offered), wake the host and wake now |
| This PC | keep awake, headset speakers and microphone, send the microphone to the host, quality line in the headset, eye tracking, CPU decoding, one-pass frame path, SteamVR reprojection repair (when offered), resend play area, restore saved SteamVR settings |
| Reports, About | as in RC4.2 |

As on the desktop page, stop the session to change the settings that apply at the next Start. Re-aim and sharpening apply at once. The main screen has buttons for Stream & headset, Motion & timing, Network & host, This PC, Reports, About and Exit VROSTBridge. The focused setting stays focused when a row above it appears or goes, and rows scrolled out of view ignore the pointer.

### 4. B does not close Game Mode (client)
**B** (and Escape) goes back one page and does nothing on the main screen. **Exit VROSTBridge** closes the app and asks first during a session, as before. Steam's own *Exit game* works as before.

### 5. When this PC's graphics cannot keep up (client)
**Found:** the session in the latest reports ran at **High (2560 × 2560 per eye, HEVC 10-bit)**. Frames arrived from the host at a steady 75 per second, with one lost frame in four minutes, so the network was fine.

The headset PC's integrated Radeon graphics could not hold that stream size:
- **What happened:** SteamVR on that PC kept switching the stream to half rate, 37.5 fps, for seconds at a time. Latency rose from about 42 ms to 70–107 ms, and the host logged "Latency is too high" about 3,900 times.
- **Its share of the work:** the client's GPU work per frame was 7–11 ms of the 13.3 ms frame budget.
- **For comparison:** earlier that evening, Standard (2016 per eye) held 75 fps with about 42 ms latency.

**Now:**
- **The client GPU does less work per frame** (section 8): one pass instead of two.
- **The client notices.** When SteamVR runs the stream below the headset refresh for 8 of the last 30 streaming seconds:
  - **Only seconds that say something about the graphics count:** the first 5 seconds of video are ignored, and so are seconds when frames did not reach this PC at the refresh rate (a Wi-Fi gap) or the headset was not tracked.
  - **On the two-pass path, re-aim pauses for the rest of the session** (it costs a full-resolution GPU pass per frame there; your saved setting is unchanged). With the one-pass path it costs nothing extra and stays on.
  - **The headset shows a short line for 12 seconds**, the first time in a session: for example *PC graphics at their limit · Standard 2016 for 75 fps*.
  - **Both pages show a warning with the advice that applies:**
    - **Use** this headset's smaller size (one tap; it applies at the next Start): *Standard · 2016 per eye* on a Beyond, *High* on an Index;
    - **Repair background reprojection** when SteamVR has it off (*Fix SteamVR reprojection*), so a late frame is smoothed instead of halving the frame rate;
    - otherwise: close other programs and check the PC is not overheating.
  - **The client log records** a `GRAPHICS` line, and reports carry a `graphics` section.
- **Auto starts the Beyond at Standard (2016).** High (2560) and Ultra (3200) remain one choice away. A setting you chose is never changed. With eye-tracked foveation (section 9), High and Ultra send fewer pixels than Standard without it.

### 6. Stream recovery that restarted SteamVR for no reason (both)
- **At Start:** the client's first heartbeats of a session reported the previous session's last video time. The host then saw "no video for 20 s" within seconds of streaming, and restarted its SteamVR. This was *Stream recovery 1* at 22:41:37, 49 s into a 2016 session.
  - **Now** a new session starts with no downtime.
  - **The host** also counts downtime only from the start of the current session.
- **While tracking was lost:** the client reported video as down whenever the native client's state was not *streaming*, even while frames kept decoding (for example with tracking lost).
  - **Now** video counts as arriving while decoded frames keep advancing.

### 7. Wi-Fi 7 multi-link (client)
RC4.2 named only the first link's band of a multi-link connection, and warned "Wi-Fi is on 2.4 GHz" for a 2.4 + 5 + 6 GHz connection.
- **The Network section** now shows, for example, *Wi-Fi · multi-link 2.4 + 5 + 6 GHz · −26 dBm · 2.9 Gbit/s link*.
- **The 2.4 GHz warning** appears only when every link is on 2.4 GHz.
- **The Game Mode header** shows *Wi-Fi 7 · 2.4+5+6 GHz*.

*Stay on one access point* is still skipped on multi-link connections. Their address belongs to the access point, not to one radio, and there is no evidence yet that a lock helps.

### 8. A sharper picture in one pass, and less GPU work on the headset PC (client)
**Before:** each frame took two full-resolution GPU passes on the headset PC:
1. when it was decoded, a conversion to a colour image at the full stream size, expanding the foveated edges with bilinear filtering;
2. when it was shown, a re-aim pass that read that image and wrote another one for SteamVR.

So the picture was resampled twice (each resampling softens it), and every frame wrote and read an extra full-size image.

**Now:** the decoded picture is kept as it arrives (its brightness and colour planes, at the smaller foveated size), and **one pass** runs when the frame is shown. It re-aims, expands the foveated edges, converts the colours and sharpens, straight into the image SteamVR shows:
- **Sharper:** brightness is resampled once, with a sharp (Catmull-Rom) filter, from the decoded picture. In the tests, a detailed picture shown during head movement differs from the original by about a third as much as with RC4.2's two passes (0.72 against 2.21 levels).
- **Lighter:** per frame the headset PC writes one full-size image instead of two, and reads the small decoded planes instead of a full-size image. Repeated frames cost the same one pass.
- **Sharpening** (new setting: *Off, Low, Medium, High*; default *Medium*) brings back fine detail lost to compression and to SteamVR's lens correction. It is contrast-adaptive: strongest on fine texture, gentle on strong edges, never on flat areas, and not applied in the reduced foveation edges. It applies at once, during a session too.
- **Where it runs:** with hardware decoding on a GPU whose driver allows 208 bytes of shader constants. AMD's Linux driver (RADV) allows 256. A GPU that allows less keeps RC4.2's two passes, and CPU decoding keeps its own path. The client log says which, for example `STATUS Frame pass: fused (one pass per shown frame)`.
- **If the picture looks wrong** (wrong colours, blocks, flicker or black) with the new pass: turn off **One-pass frame path** (client page *Preferences*, or Game Mode *This PC*) and Start again. That uses RC4.2's two passes, without sharpening, and keeps everything else in RC4.3. The new pass has so far been tested only on a software GPU, so please report it if you need the switch.

### 9. Eye-tracked foveation: tuning and a live view (both)
With eye tracking on and *Foveated encoding* on *Eye-tracked* (or *Auto* on a Beyond 2e with eye tracking), the full-resolution region follows your gaze. RC4.3 adds two settings for it, shown only then:

- **Sharp region:** *Small* (30% of each eye), *Medium* (40%, as in RC4.2) or *Large* (55%).
- **Outside the sharp region:** *More detail* (⅓ resolution), *Balanced* (¼, as in RC4.2) or *Lightest* (⅕).

A smaller region or a lighter outside sends fewer pixels. That is less work for the host's encoder, the network and this PC's decoder, and room for a higher stream size. Pixels sent per eye:

| Stream size | Small + Lightest | Medium + Balanced (default) | Large + More detail |
|---|---|---|---|
| Standard 2016 | 896 × 896 (80% fewer) | 1120 × 1120 (69% fewer) | 1408 × 1408 (51% fewer) |
| High 2560 | 1120 × 1120 (81% fewer) | 1408 × 1408 (70% fewer) | 1792 × 1792 (51% fewer) |
| Ultra 3200 | 1408 × 1408 (81% fewer) | 1760 × 1760 (70% fewer) | 2240 × 2240 (51% fewer) |

(Four sizes corrected in 1.0.0.6 to ALVR's own rounding; the companion's estimate had rounded them up one 32-pixel step.)

They apply at the next Start, and work with an RC4.2 host too.

**The live view.** While eye-tracked foveation runs, an **Eye-tracked foveation** panel appears:
- on the client page;
- on the host page;
- in Game Mode, under *Stream & headset → Eye-tracking view*.

It shows two pictures, one per eye, eight times a second:
- the sharp region (blue to purple), where the host keeps full resolution for the frame being shown;
- where that eye is looking (pink dot), from the latest eye-tracking sample.

Below them are the numbers:
- the sharp region's size and the outside resolution;
- the pixels sent per eye, and how many fewer that is;
- the share of frames centred on your gaze;
- eye-tracking samples per second;
- the frame pass and the sharpening level.

**Where you look is private.** The live view is kept in memory only. It is never written to logs or reports, and is shared only with the host you are streaming from (its page keeps it in memory too). The host's streaming driver (unchanged since RC4.1) still writes a foveation summary every 5 seconds to its own log on the host PC, including the region centres at that moment. Reports and the host's event journal now leave those centres out and keep only the counts.

## Compatibility
- One new session-link feature, `fovea-view`: the host page's live view needs RC4.3 on both PCs. An RC4.3 client works with an RC4.2 host (without that view), and an RC4.2 client with an RC4.3 host.
- The microphone setup is host-only.
- The Game Mode, graphics-limit, frame-pass, sharpening and recovery changes on the client work with any host since RC4.1. The tuned eye-tracked foveation values are within what RC4.2 hosts accept.
- The host's stricter downtime count needs no client change.

## How to test (please report each)
1. **Install** both, then switch to Desktop Mode and back to Game Mode once. Both pages show 1.0.0-rc4.3.
2. **Host microphone:** on the host page, *Headset microphone* should say **Ready**, since VB-CABLE is already installed on your PC.
   - If it says *Not set up*, select *Set up headset microphone*, follow VB-CABLE's setup and restart if asked.
   - In Discord, choose **CABLE Output (VB-Audio Virtual Cable)** and speak into the headset.
3. **Game Mode start:** with the Beyond plugged in and powered, open VROSTBridge from Steam in Game Mode. The session card should not say *Headset display not reserved*. Start a session.
   - If it does not start, the card or the message says why.
   - Send a report from *Reports → Send report to host*. It includes the display list and what the wrapper did.
4. **B button:** B on the main screen does nothing; B on every other page goes back; *Exit VROSTBridge* closes the app.
5. **Game Mode settings:** open each page and check that the settings match the desktop page.
6. **Frame rate:** play for 10 minutes or more at Standard (Auto now picks it). The fps tile should stay at 75.
   - Then try High: with the one-pass frame path it may now hold 75. If the PC falls behind, the headset shows the short line, and both pages offer *Use Standard*.
   - With SteamVR closed, try *Fix SteamVR reprojection* on the client page, then compare High again.
7. **Sharpness:** in the client log, look for `Frame pass: fused`. Compare *Sharpening* Off, Low, Medium and High on text and fine textures (it changes during a session). Tell us which looks closest to the direct connection.
   - If the picture looks wrong, turn off *One-pass frame path*, Start again and tell us what you saw.
8. **Eye-tracked foveation:** with eye tracking on, the client page, the host page and Game Mode's *Eye-tracking view* show both eyes. Look around: the pink dot and the region should follow your eyes.
   - Try *Small + Lightest* at High or Ultra, then *Large + More detail*. Notice where the edge of the sharp region becomes visible, if at all.
9. **Wi-Fi:** the Network section shows your multi-link bands, with no 2.4 GHz warning.

Build and test evidence: [evidence/rc4.3-build-1.0.0-rc4.3.json](../evidence/rc4.3-build-1.0.0-rc4.3.json).
