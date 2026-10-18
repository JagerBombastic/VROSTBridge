# Troubleshooting

When something goes wrong, VROSTBridge usually says so in plain words on the headset PC's page (or the Game Mode
screen, the tray, and where it matters the headset itself), often with a button that fixes it. This page collects
the common cases in one place, with the reasoning behind each fix. The words in *italics* are what the page shows.

**Before you change anything, save the reports.** On the headset PC's page choose **Send report to host**, then on
the rendering PC's page save both reports (*Reports*). The reports hold the session's timeline, the logs of both PCs
and what each check found. They are far more useful from the session that went wrong than from one after a
reinstall. They contain local network addresses and hardware names, so look through them before posting them
publicly.

## Before a session

**The installer is blocked: *Windows protected your PC*.** The Windows setups aren't code-signed yet (certificates
cost money; it's on the list). Choose **More info → Run anyway**. Each setup is listed with its SHA-256 in the
release's `SHA256SUMS.txt` if you want to check your download first.

**The page doesn't open.** VROSTBridge's page is served by the companion on your own PC and opens in your browser
(the address starts with `127.0.0.1`). If it doesn't appear, open VROSTBridge again from the Start menu or the app
menu: opening it again opens the running one's page, and if no browser can be opened it shows the address to copy. Startup logs live in `%LOCALAPPDATA%\VROSTBridge` on Windows (*Client startup logs* in
the Start menu for the Windows client) and in `~/.local/state/VROSTBridge` on Bazzite. Installing the same version
again repairs missing files and keeps your settings ([installing and upgrading](INSTALL_PACKAGES.md)).

***SteamVR is not set up on this PC yet.*** The headset PC's SteamVR has never run with the headset. Start SteamVR
there once with the headset connected (on Windows, finish its room setup), then start the session again. VROSTBridge
uses the headset PC's own SteamVR for tracking and the displays, so it has to work there first: if the headset
doesn't work in SteamVR on the headset PC, it won't work streamed either.

***Companion unavailable.*** The page lost its connection to the companion, usually because VROSTBridge was closed.
Reopen VROSTBridge to reconnect.

## Finding each other

***Finding your host*** **for a long time.** The headset PC looks for the rendering PC on your home network.

- Open VROSTBridge on the rendering PC too. It waits in the system tray.
- Both PCs must be on the same home network and the same subnet, with private addresses (192.168.x.x, 10.x.x.x or
  172.16–31.x.x). Guest networks, VLANs and "AP isolation" settings that keep devices apart stop discovery.
- On the headset PC, choose **Allow home-network connection** if the page offers it. On Bazzite it adds firewall rules
  for your home subnet only (your password); on Windows it asks for administrator permission once.
- On a Windows PC, Windows' Private or Public label no longer matters for a headset PC that is already paired; only
  pairing a **new** one automatically needs the network set to Private once (*Settings → Network & internet*). If the page mentions **block rules**, a
  declined Windows Firewall prompt left them behind; block rules win over allow rules, so remove them in *Windows
  Defender Firewall → Inbound Rules*. On the rendering PC, **Repair Windows Firewall access** puts VROSTBridge's own
  rules back.
- If discovery still fails, type the rendering PC's address under *Host address* on the headset PC's page.

***Client approval needed*** **(rendering PC).** With *Allow home-network clients* on (the rendering PC's Preferences,
on by default), a new headset PC pairs by itself, provided Windows classes the rendering PC's network as Private for
that first pairing; once paired, it connects on any network type. With the setting off, or on a Public network the
first time, choose **Allow & connect** beside the right device on the rendering PC's page.

**The rendering PC is asleep.** If it doesn't answer at Start, the headset PC offers **Wake host now**. Wake-on-LAN
becomes available after one session, once the headset PC knows the rendering PC's network adapter. The adapter and
the BIOS must allow waking.

## Starting the stream

***Starting video*** **or** ***Video not arriving.*** The PCs are connected but no picture has arrived yet.

- Give it a few seconds: the first start runs a 15-second network test, and the rendering PC's SteamVR has to start.
- If it stays this way, the rendering PC's SteamVR may be showing a prompt or an error on its own screen. Check there.
- Save the reports if it continues: they show which side stopped.

***Headset not tracked.*** VROSTBridge only gets a picture rendered for a head position the headset PC sends, so with
no tracking there's nothing to show. The message names the likely cause: the headset asleep or off your head, the
base stations off or out of sight, or SteamVR on the headset PC not running. Base stations can be switched on from the
*This PC* card (SteamVR 2.0 base stations, over the headset PC's Bluetooth).

***Waiting for the headset’s position*** **(rendering PC).** The same problem seen from the other end: frames are
rendered, but the headset PC isn't sending a head position. Look at the headset PC.

**SteamVR doesn't start at 144 Hz (Index).** 144 Hz is experimental, and not every graphics card or driver drives
it: the start fails after a minute with a message naming the rate VROSTBridge asked for. Choose *Native · 120 Hz*.

**The Beyond is in the wrong mode (75 or 90 Hz).** VROSTBridge doesn't switch it, on Bazzite or Windows: the stream
follows the mode the headset is in, and the page shows which. Set the mode with Bigscreen's Beyond Utility on a
Windows PC, then start again.

**Steam's Game Mode on Bazzite doesn't start a session.** Starting a session from Game Mode hasn't yet been seen to
work on the tested setup: the headset PC's SteamVR could not take over the headset display there. Use Desktop Mode
until a release says otherwise, and send the reports if you try it (they record the Game Mode start). More:
[BAZZITE_PLAY.md](BAZZITE_PLAY.md).

## During a session

**Stutter, or the picture runs at half its usual smoothness.** Look at the *Fresh video* line on the page and any
message under it.

- *This PC's graphics can't keep up with the stream* means the headset PC's own GPU is the limit. VROSTBridge first
  shows the picture a little smaller (*Shown at 90 %*, then 80 %) instead of SteamVR halving the frame rate, and offers
  a lighter choice if it keeps happening: an Index's next lower refresh rate, a Beyond's 90 Hz mode (set with
  Bigscreen's Beyond Utility), or a smaller stream on other headsets. On a handheld, keep it on the charger and raise its power limit (TDP):
  decoding, SteamVR and eye tracking share one power budget. If the page says *a power limit (TDP) or a fixed GPU clock
  is holding it back*, **Set the GPU clock to automatic** (Bazzite) fixes the usual cause.
- *Fewer frames are arriving from the host* means the rendering PC or the network is the limit: a heavy game, or a
  busy link. Try a lower bitrate or a smaller stream size.
- Frames arriving in uneven bursts usually point at Wi-Fi. See below.
- A stall of a few seconds every five minutes or so, on a cable, with Wi-Fi connected too: the page says *the video is
  arriving over* your Wi-Fi adapter. A Linux PC on one network twice answers for both addresses on both adapters.
  **Fix this PC's network settings** (one password prompt; it stays set) keeps the video on the cable, and Wi-Fi can
  stay connected.

**Wi-Fi drops, stalls or hiccups every few minutes.** VR video is a lot of data that has to arrive on time, and Wi-Fi
is shared radio.

- Use 5 or 6 GHz, in the same room as the access point. 2.4 GHz isn't supported, and the page warns about a weak or
  2.4 GHz link.
- **Turn off Wi-Fi power saving** when the page offers it. Power saving makes the adapter sleep between beacons,
  which shows up as regular pauses of about a tenth of a second.
- Keep *Stay on one access point* on. It stops the background scans that pause the stream every few minutes.
- A second Wi-Fi adapter on the same network shares the radio: *Pause other Wi-Fi adapters on this network* handles
  it.
- An Ethernet cable is always the most reliable link. A 1 Gbit/s USB adapter is fine; the page warns about a slow or
  half-duplex link.

**Blurry picture.** Choose **Use recommended settings** first: *Auto* codec is HEVC on every headset, and the
rendering PC renders the game larger than the stream and scales it down, which is where fine detail comes from. Then
check the *Headset & stream* card: a larger stream size, HEVC or AV1, and a higher bitrate each help, as long as both
PCs and the network keep up. SteamVR's resolution setting on the rendering PC changes how large the game renders, not the
stream; a very low SteamVR resolution on the headset PC lowers it too. Foveated
encoding sends the edges of each eye at reduced resolution, which is hard to see in the headset by design; set it to
*Off* to compare. Sharpening is separate and can be changed live.

**Colours look wrong or banded.** 10-bit colour removes banding in dark gradients. With AMD encoders the rendering PC
checks its colour at every stream start and falls back to 8-bit when the check fails; the page says so.

**No game sound, or crackling.** Game audio plays on the headset PC through its default sound device or the headset's
own, as the *This PC* card chooses. If it crackles, save the reports: they record the game audio on both PCs, including
dropouts and buffer underruns.

**The headset microphone doesn't reach the game.** Windows has no virtual microphone by default. **Set up headset
microphone** on the rendering PC's page downloads VB-CABLE from VB-Audio (donationware) and opens its setup.

**Hands land short in fast games.** The rendering PC predicts every pose a stream's latency ahead, and the
headset PC folds each controller's acceleration into what it sends, so a swing lands where it is going. A change of
direction within that latency (30 to 40 ms on a cable) cannot be predicted by either PC: a rhythm game at its hardest is
the worst case for any streamed headset. Wired beats Wi-Fi here more than anywhere.

**Controllers.** VROSTBridge forwards Valve Index controllers, with finger tracking. Other controllers aren't
supported yet. The system (menu) button opens the rendering PC's SteamVR menu. Holding it is the headset PC's own SteamVR
recentre.

## Eye tracking (Bigscreen Beyond 2e)

- **Set up eye tracking** downloads Baballonia from Project Babble after you accept its licence (personal,
  non-commercial use). It isn't bundled with VROSTBridge.
- On Bazzite, *This PC does not let VROSTBridge open the eye cameras yet* is fixed by **Allow eye camera access**
  (your password once). Calibration runs over the stream in Desktop Mode.
- On Windows, close Bigscreen's own eye-tracking app first: only one program can read the eye cameras.
- If the sharp region doesn't reach the edges of your view, recalibrate while turning your head well round as you
  follow the dot, then **Measure again**.

More: [BEYOND_EYE_TRACKING.md](BEYOND_EYE_TRACKING.md).

## Still stuck?

Rule out the newer stream features: set Foveated encoding to *Off* and turn off Automatic bitrate and 10-bit colour,
then compare. If that doesn't explain it, open an issue with the two reports and what you saw. The
[technical reference](TECHNICAL_REFERENCE.md#27-ease-of-use-and-setup) lists every message with a fix button, and
its [known limits](TECHNICAL_REFERENCE.md#203-known-limits) say what VROSTBridge doesn't do (yet).
