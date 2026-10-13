# VROSTBridge 1.0.0.1

**VR outside the box.** *Think outside the box. Render there too.*

1.0.0.1 is the first release of VROSTBridge under its new name, for the Windows rendering PC (host) and the Bazzite headset PC (client). It keeps everything [1.0.0](RELEASE_1.0.0.md) brought (Baballonia's calibration over the stream, Baballonia kept out of the way, one page tab, the logo, the licence and notices) and adds three things:

- **What the name stands for, in full.** Under *VROSTBridge* at the top of both pages and the Game Mode screen: *VR Rendering OutSide The Box – Bridge*. V, R (of Rendering), O, S, T and B (of Box) are picked out in the logo's colours, and every letter of *Bridge* runs through the colours from cyan to magenta. The badge on the right still says which PC you are on.
- **One name throughout.** Every program, folder, setting, Windows registration, protocol name and file now carries the VROSTBridge name, down to the files the streaming driver reads and the name the two PCs check before they stream. Both PCs therefore need 1.0.0.1.
- **Version 1.0.0.1** on both programs.

**Install both:** `VROSTBridge-Host-Setup-1.0.0.1.exe` on Windows and `VROSTBridge-Bazzite-Installer-1.0.0.1.zip` on Bazzite.

## Before you install: remove earlier test builds

1.0.0.1 installs fresh. It does not take over an earlier test build (1.0.0 or before): their files, settings and registrations used other names. Remove them first, on both PCs, with their own uninstallers, so they also undo what they set up (the streaming driver's registration in SteamVR, firewall rules, sign-in startup, SteamVR settings, the Steam library entry):

1. **Windows:** *Settings → Apps*: uninstall the earlier *VROSTBridge Host*. If you turned on *Wi-Fi priority for video* on its page, turn that off there first.
2. **Bazzite:** open *Uninstall VROSTBridge Client* from the application menu.
3. **Steam (Bazzite):** if the library still has the earlier VROSTBridge entry, remove it.
4. Their settings and log folders stay behind; 1.0.0.1 does not read them, and you can delete them.

Remove them **before** installing 1.0.0.1: an earlier build's uninstaller also removes the Start menu shortcuts and the menu icons that 1.0.0.1 uses under the same names. If you installed 1.0.0.1 first, run its installer again after removing the earlier build; that puts them back.

Then install 1.0.0.1 on both PCs. On first start it is a new installation: approve the home-network firewall prompts again, the two PCs pair again by themselves on your home network, set your preferences again and, for the Beyond 2e, let VROSTBridge download Baballonia again. Baballonia keeps its own settings and calibration in `~/.config/ProjectBabble`, so a calibration you made earlier is still there. SteamVR on the rendering PC sees the headset under a new serial number, so settings SteamVR keeps per headset there start from their defaults.

## Calibrate (Beyond 2e)
1. Desktop Mode: turn on *Eye tracking* and start a session with the Beyond 2e.
2. Choose **Show Baballonia** on the *Eye tracking* card. Put the headset on.
3. In Baballonia's window choose **Eye** and follow it in the headset, where it shows over the stream.
   - Hold your gaze on the dot while you move your head slowly around it, as it asks; then close your eyes when it asks (about 20 seconds).
   - It then trains a model for your eyes: 5 to 10 minutes by Project Babble's figure, longer on a handheld's processor. **Keep the session running** until it has finished; the log says *Baballonia saved its calibration*.
4. Stop and start the session. The sharp region now follows your eyes, in Desktop Mode and Game Mode.

## How to test (please report each)
1. **Install** both fresh, as above. Both pages show *VROSTBridge Host 1.0.0.1* and *VROSTBridge Client 1.0.0.1*, and the client finds and pairs with the host by itself.
2. **Under the name:** *VR Rendering OutSide The Box – Bridge* on both pages and in Game Mode, the R of *VR* grey, the R of *Rendering* and the B of *Box* coloured, and *Bridge* in colour from cyan to magenta.
3. **Names and icons:**
   - Windows: the Start menu has *VROSTBridge → VROSTBridge Host*; *Settings → Apps* lists *VROSTBridge Host 1.0.0.1*; the firewall setup and, if you use it, *Wi-Fi priority for video* work again; the tray icon is clear on your taskbar (try light and dark mode in *Settings → Personalisation → Colours* if you like) and its tooltip says *VROSTBridge Host*; *Start in the system tray when I sign in* works after a restart.
   - Bazzite: the application menu and a pinned taskbar entry show *VROSTBridge Client* with its icon; the browser tab shows the icon.
   - Steam: **Add to Steam** adds *VROSTBridge* with its artwork, and it starts the Game Mode screen.
   - Headset: the status cards say *VROSTBRIDGE* with the logo.
4. **A whole session:** picture, head and controller tracking, buttons and haptics, game sound and the headset microphone on Windows, as before. The stream settings (bitrate, foveated encoding, 10-bit colour, the codec) still reach the host: the client page's quality line and the host page show them.
5. **Eye pictures:** start a session in Desktop Mode. Baballonia should start minimised; *Show Baballonia* should open it, with both eye pictures.
6. **Calibration:** choose **Eye** and follow it. Report:
   - whether it shows in the headset, over the stream;
   - whether **each eye looks right**: one dot, not doubled or swapped. If it looks wrong, stop the calibration and send a report;
   - whether the session keeps running through the calibration and the training;
   - how long the training took.
7. **After calibrating:** stop and start the session; *Stream & headset → Eye-tracking view* should show the sharp region following your eyes.
8. **One tab:** with the page open, open it again (tray icon on Windows, taskbar pin on Bazzite). You should end up with one tab, also after quitting and reopening the companion.
9. If anything goes wrong, send a report from the client straight afterwards. It includes the end of Baballonia's log, with its calibration program's output.

## Known limits
- Not yet tried on real hardware: everything above. The streaming driver and the client were rebuilt for the new names only (the driver is built from the same sources, with only names changed), but the two PCs meet only in a real session.
- The Windows programs are not code-signed, so Windows may say *Windows protected your PC* (choose **More info**, then **Run anyway**).
- Baballonia's calibration and training run on the processor, beside the stream, on the handheld's shared power limit.
- Baballonia's licence allows personal, non-commercial use only; VROSTBridge downloads it only when you accept that.

Build and test evidence: [evidence/vrostbridge-build-1.0.0.1.json](../evidence/vrostbridge-build-1.0.0.1.json).
