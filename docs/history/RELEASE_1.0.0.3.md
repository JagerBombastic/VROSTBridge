# VROSTBridge 1.0.0.3

1.0.0.3 fixes what the 1.0.0.2 test and its reports showed, on both programs. Everything in the [1.0.0.2](RELEASE_1.0.0.2.md) and [1.0.0.1](RELEASE_1.0.0.1.md) notes still applies.

**Install both:** `VROSTBridge-Host-Setup-1.0.0.3.exe` on Windows and `VROSTBridge-Bazzite-Installer-1.0.0.3.zip` on Bazzite. Both install over 1.0.0.2 and keep its settings, pairing, Baballonia and calibration. **Do not uninstall 1.0.0.2 first:** its uninstaller deletes the Baballonia download, and Baballonia would then have to be downloaded again (from 1.0.0.3 the uninstaller asks, see 6).

## What was wrong, and what changed

### 1. Game Mode always said "Headset display not reserved" (client)
In Game Mode the session card always said *The previous display lease is stale*, even after a fresh start, with the headset plugged in and powered. Game Mode had in fact reserved the headset display: the reports show the reservation of DP-1 at Game Mode start.

The check that the reservation still belongs to this Game Mode session was wrong on Bazzite. Bazzite's gamescope has a file capability (`cap_sys_nice`, for real-time scheduling), and Linux then refuses even the same user the link to a running program (`/proc/PID/exe`). The check needed that link, so it always failed. This was reproduced in the build environment with a program given the same capability.

- The check now uses what any user can read: the same process with the same start time, owned by you, running gamescope with the headset's `--lease-connector`. Where the program link can be read, it must still lead to gamescope.
- When the gamescope that reserved the display really has ended, the card now says so: *The gamescope that reserved the headset display is no longer running.*
- Nothing changes in how Game Mode starts. No switch to Desktop Mode and back is needed after installing.

### 2. The host's SteamVR session sometimes would not close (host)
In one session SteamVR took 61.9 seconds to start, and 3 seconds after it was ready another SteamVR server process (vrserver) appeared. VROSTBridge ended the session (*A different SteamVR process appeared*), and every Stop after that refused to close SteamVR (*SteamVR ownership changed*), so SteamVR had to be closed on the host.

- A new SteamVR server process now joins the session when it is SteamVR's own (the SteamVR installation this session started), started after this session started it, while this session's SteamVR is still running or restarting. Stop then closes it with the rest. The log says *SteamVR started another server process (PID …) while this session's SteamVR was running; it belongs to this session.*
- A SteamVR from another installation, or one started after this session's SteamVR had ended, is still left alone.
- While SteamVR starts, the log no longer says *Host runtime interrupted*; that now means a runtime that was running and stopped. After 30 seconds without the VROSTBridge driver, the log says SteamVR is still starting and that another add-on can hold it up. Your host loads these add-ons: BeyondSteamVR, VirtualDesktop, bigscreenbeyond, frame_controller, frame_hmd, indexcontroller, prism, roy and tundra_labs. If SteamVR's start is slow again, turn off the ones you don't need in *SteamVR → Settings → Startup / Shutdown → Manage Add-ons*.
- Host reports now include SteamVR's log of the previous start (`vrserver.previous.txt`) and its monitor log, so a restart like this one can be traced.

### 3. The reduced area sparkled (host and client)
With eye-tracked foveation, the area you are not looking at had *a bit of a sparkle* and pixelated edges. There were two causes:

- **The host made the reduced area without filtering.** It took one sample from up to five source pixels in each direction, so thin lines and fine detail flickered in and out as you moved. The host driver now gives the full picture a mip chain each frame and samples it anisotropically, so each reduced pixel is the average of the area it covers (new driver patch `0016-host-foveation-prefilter.patch`). The sharp region is unchanged. The host's log shows `VROST_FOVEATION_PREFILTER composition_mips=1`.
- **The headset PC enlarged the reduced area with an overshooting filter.** Catmull-Rom keeps the full-resolution centre sharp, but where the reduced picture is enlarged two to five times it drew a bright and a dark fringe beside edges, which moved with your head and eyes. The reduced area is now enlarged with a smooth B-spline that has no fringes. The two blend across the border of the sharp region, and the full-resolution centre still uses Catmull-Rom. In the tests, enlarged moving detail changes a third as much as before, and a sharp step no longer overshoots.

The reduced area now looks softer and steadier. Sharpening applies only to the sharp region, as before.

### 4. The sharp region followed quick eye movements late (client)
Two things slowed eye tracking down:

- **The eye-camera helper sent Baballonia at most one picture every 50 ms**, and only the first to arrive after each pause. Your report shows 17 samples a second. It now sends each new picture as it arrives, up to one every 15 ms (about 66 a second). The 15 ms keeps Baballonia's processor load moderate, because on a handheld the processor and the graphics share one power limit.
- **Baballonia's own eye filter smooths heavily**: a 5 to 10 degree eye movement took 235 to 350 ms to arrive in full at 17 samples a second. VROSTBridge now sets that filter once to 1 Hz and speed 10 (Baballonia's defaults are 0.5 Hz and 3), but only while it is still at Baballonia's defaults. With about 60 samples a second, the same movement takes 70 to 85 ms, and the gaze is as steady as before when you hold still (a simulation of Baballonia's filter, in the tests). Baballonia's log in a report says so once. If you change the filter in Baballonia's settings, VROSTBridge leaves your choice.

Your graphics settings add to this delay too (see *Settings worth checking*).

### 5. The eye-tracking view (both pages and Game Mode)
The view did not clearly show your eyes moving, or where the picture is sharp and where it is reduced:

- It is drawn at the screen's rate from 20 updates a second (10 a second on the host's page), and the gaze point and the sharp region glide to each new position. Before, there were 8 updates a second, from a view the headset PC updated 10 times a second (now 30).
- The gaze leaves a fading trail of its last second, so you can see where your eyes went.
- The reduced area is drawn as coarse squares, their size set by its resolution (larger for *Lightest*), and the sharp region as fine squares with a bright outline labelled *Sharp*. When your eyes move, you see the dot move first and the sharp region follow.
- Game Mode's *Eye-tracking view* page draws the same, 20 times a second.

### 6. Baballonia after an update (client)
After an update, VROSTBridge usually asked to set up Baballonia again:

- **Installing over the previous version** has always kept Baballonia and its calibration. Baballonia lives outside the version folders.
- **Uninstalling first** deleted the Baballonia download, and the uninstaller's message didn't say so. From 1.0.0.3 the uninstaller asks whether to keep it: **Keep** (the default button), **Remove**, or **Cancel**, which stops the uninstall. A kept Baballonia is used again by the next installation, with no download. 1.0.0.2's uninstaller still deletes it, so install 1.0.0.3 over 1.0.0.2.
- **A copy left by an installation before 1.0.0.1**, under the old folder name, is now found and moved into place by the installer (or at the client's next start), and nothing is downloaded. It must be the release VROSTBridge uses, with the receipt VROSTBridge wrote when it set it up. The installer then says *found where an earlier installation left it and moved into place*.
- **A newer Baballonia in a later VROSTBridge:** the one you have keeps working, with its calibration, until you choose *Accept licence and update to …* on the *Eye tracking* card (or *Update Baballonia* in Game Mode).
- The installer's last message now says what it found, for example *Eye tracking: Baballonia 1.1.1.0rc6 found and kept, calibrated 2026-10-06.*

### 7. The name in the header (both pages and Game Mode)
The header now shows the name in the logo's lettering, beside the symbol on one line: *VROST* in the logo's wide letters, then *Bridge* with a capital B and lower-case *ridge*, in the logo's light grey-blue. *VR Rendering OutSide The Box – Bridge* stays under it, unchanged. The logo's BRIDGE has capitals only, so *Bridge* is set in Montserrat, the open font closest to its letters, and kept as outlines (no font is added to either program).

### 8. Smaller things from the logs
- **Host:** when the headset PC no longer shares its report (it does so only during a session and for a minute after), the host says so once instead of logging *HTTP Error 403* at every retry.
- **Version 1.0.0.3** on both programs.

## Settings worth checking (from your reports)
- **Stream resolution:** the session ran at 3200 × 3200 per eye, and the headset PC's integrated Radeon graphics could not show it at 75 fps. SteamVR showed it at reduced frame rate (37.5 fps) for the whole session. The GPU ran at about 940 of its 2700 MHz at about 20 W, so a power limit held it back. That made the picture about 80 to 100 ms old by the time it was shown, and the sharp region followed your eyes that much later. With the charger connected, raise the TDP limit as far as it goes (Game Mode: *Quick Access → Performance*), or choose **Standard** (2016 per eye) or **High** for a steady 75 fps. The client page offers *Use Standard* when this happens.
- **SteamVR background reprojection is off** on the headset PC, so a late frame halves the frame rate instead of being smoothed. Select **Repair** where the client page says so.

## How to test (please report each)
1. **Install** both over 1.0.0.2, without uninstalling first. The pages show *VROSTBridge Host 1.0.0.3* and *VROSTBridge Client 1.0.0.3*. The Bazzite installer's last message ends with *Eye tracking: Baballonia … found and kept, calibrated …*, and the *Eye tracking* card does not ask to set it up.
2. **Game Mode:** with the Beyond plugged in and powered, switch to Game Mode and open VROSTBridge from Steam. The session card should not say *Headset display not reserved*. Start a session, and report what happens in the headset. This is the first time Game Mode gets past the display reservation on your PC, so please send a report from Game Mode whatever happens.
3. **Stop from the headset PC** several times, including after a slow SteamVR start. SteamVR should close on the host each time. If it does not, send a host report.
4. **The reduced area:** in Desktop Mode with eye-tracked foveation, look straight ahead and pay attention to the edges, ideally on something with fine detail (text, fences, foliage). Is it steadier, and does it look acceptably soft?
5. **Quick eye movements:** look quickly from one object to another. Does the sharp region keep up better? The *Eye-tracking samples* figure on the client page should be well above the 17 a second in your last report.
6. **Eye-tracking view:** on the client page (or the host's page during a session) and in Game Mode under *Eye-tracking view*. Can you see your eyes moving and the sharp region following?
7. **The header:** *VROST Bridge* beside the symbol on both pages and in Game Mode.
8. If the gaze seems less accurate than before, run **Eye (no tutorial)** in Baballonia once: the calibration was trained on pictures at 17 a second.

## Known limits
- Not yet tried on real hardware: the Game Mode session after the reservation (the display adapter that hands the headset to SteamVR), the host's filtered reduced area on your rendering PC's AMD GPU, and the camera rate: the Beyond 2e's cameras decide how many pictures a second there are, up to the 66 VROSTBridge allows.
- The host driver was built in the same cross-build as 1.0.0.1 (clang-cl 19, xwin Windows SDK). The new filtering uses standard Direct3D 11 calls. If the full picture's mip chain cannot be created, the driver falls back to sampling as before, and its log says `composition_mips=0`.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.3.json](../evidence/vrostbridge-build-1.0.0.3.json).
