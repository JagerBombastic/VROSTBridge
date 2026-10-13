# VROSTBridge 1.0.0.10

1.0.0.10 follows the 1.0.0.9 test. Game audio came through clean, and in a session of more than twenty minutes on Wi-Fi not one video frame was lost: packet repair rebuilt the 48 that would have frozen. Three things still needed work:
- **The menu button opened the rendering PC's SteamVR menu only sometimes.** While the system button is held, the headset PC's own SteamVR takes input focus from the client (it shows its recentre indicator, ready for a long press), and without focus every control reads as released. The client then released them all on the rendering PC, so the rendering PC saw the menu press for a few milliseconds, or not at all.
- **Game Mode did not start a session.** SteamVR started, but its compositor reached a display server that cannot lend it the headset display and failed with SteamVR error 496, six times in a row, while VROSTBridge waited out its minute. Back in Desktop Mode the same SteamVR got the headset display from the desktop at once. The report could not tell whether SteamVR ever reached the headset display VROSTBridge serves in Game Mode.
- **The automatic bitrate stepped from 40 down to 20 Mbit/s** because frames arrived unevenly. On Wi-Fi, short pauses of the radio (most about 100 ms, the beacon interval Wi-Fi power saving keeps) made frames arrive unevenly at any bitrate: at 20 Mbit/s they arrived no more evenly than at 40, so the steps only cost picture detail.

One base station also refused both tries to wake it again, the same station as in the 1.0.0.8 test.

So 1.0.0.10:
- forwards the system button as one clean press and keeps the other controls through a short loss of focus (both clients);
- starts SteamVR in Game Mode with the headset display as the only Wayland display it is given, explains a failure at once, and keeps what happened for the reports (Bazzite);
- undoes a bitrate step taken for uneven arrival when it did not help (both clients);
- gives a base station three tries and, on Bazzite, searches again before each retry and names the signal strength of a station it could not reach.

Every package carries **1.0.0.10**. Everything in the [1.0.0.9](RELEASE_1.0.0.9.md) and earlier notes still applies.

**Install:**
- **Rendering PC:** install `VROSTBridge-Host-Setup-1.0.0.10.exe` over 1.0.0.9. Its driver is 1.0.0.9's, unchanged.
- **Headset PC:** install `VROSTBridge-Bazzite-Installer-1.0.0.10.zip` or `VROSTBridge-Client-Setup-1.0.0.10.exe` over 1.0.0.9. Settings, pairing, Baballonia and its calibration stay.

Nothing on the network changed, so either PC still works with the other at 1.0.0.9. Installing both keeps them on one version.

## The menu button (both clients)
- **One clean press.** A press reaches the rendering PC for at least 0.12 s and ends with a release the client has seen, read with focus or from SteamVR's own button event, which the client now reads as well. While the button cannot be read, the press ends after those 0.12 s, as a tap, and the same press is never sent twice. The rendering PC's SteamVR then does what it does with a headset plugged into it: its menu on a short press, and a recentre on a long one whenever the client can follow the press (with focus, or through SteamVR's button events).
- **The other controls stay put.** A loss of focus shorter than half a second keeps every other button, trigger and stick as it was, so a short menu press does not drop what you are holding. A longer one (a long press, or a window of the headset PC's SteamVR that wants the controls) releases them as before.
- **Where to see it:** a `SYSTEM_BUTTON` line in the client log for each press: how long it was forwarded, whether the headset PC's SteamVR took focus meanwhile (`focus=lost`), and whether the button went unread for a moment, with neither focus nor a button event from SteamVR (`unread=1`; such a press may have reached the rendering PC as a tap). The `INPUT` lines count the losses of focus and SteamVR's own button events.

## Game Mode (Bazzite)
In Game Mode, gamescope reserves the headset's connector, and VROSTBridge's display adapter hands it to SteamVR on a private Wayland display of its own (see [BAZZITE_PLAY.md](../BAZZITE_PLAY.md)). SteamVR's compositor must reach that display, not gamescope's.
- **SteamVR's environment.** SteamVR now starts without gamescope's display marker (`GAMESCOPE_WAYLAND_DISPLAY`), so the only Wayland display it is given is the headset display. That display has a plain name in the runtime directory (`WAYLAND_DISPLAY=vrostbridge-headset-<number>`), the usual form, which libraries and container runtimes that accept only a name can find; it was a path to a socket in a folder of its own.
- **A clear failure.** If SteamVR's compositor still cannot take the headset display, Start stops within seconds and says so, with the display SteamVR's server was given when that could be read. Retrying could not help. The same check stops a Start in Desktop Mode on a desktop that cannot lend the headset to SteamVR.
- **The evidence.** The log says which display SteamVR's server was started with, which programs reached the headset display and bound its lease interface (`LEASE_CLIENT`), whether the headset was leased (`LEASE_GRANTED`), and the compositor's own lines about the headset display. The lines of the last Start in Game Mode are kept in `game-mode-last-start.json` and go into every client report, so a report saved later in Desktop Mode still has them.

Game Mode's handover of the headset display has not yet been seen working on hardware. If it still fails, the report shows at which step.

## Automatic bitrate on Wi-Fi (both clients)
A step down taken for uneven arrival alone (no lost frames or slow decoding at the time) is now checked, as a step for slow decoding already was. VROSTBridge compares how evenly frames arrive at the lower bitrate with how they arrived at the previous one, over the seconds before the step (at least 20 of them), not only the troubled seconds that triggered it, which would look better afterwards in any case. When frames arrive no more evenly, the bitrate goes back up and uneven arrival stops counting for that session. Seconds in which too few frames arrived at all, as while a game loads, no longer count as uneven arrival. Lost frames and slow decoding still step the bitrate down.

The pauses themselves come from the Wi-Fi link. On Bazzite, NetworkManager cannot turn power saving off while connected, so the page offers **Turn off Wi-Fi power saving** for your home network between sessions (it reconnects Wi-Fi once). A network test after that gives the bitrate its real ceiling on that link.

## Base stations
A station that did not answer gets three tries, two seconds apart. On Bazzite each retry is preceded by a short search, so the next try starts from the station's latest advertisement, and when a station still cannot be reached, the message gives the signal strength the headset PC heard from it during those searches (for example `signal -91 dBm`): a weak signal means it is too far from the headset PC's Bluetooth, or something is in the way. On Windows the tries are the same, without the search or the signal.

## How to test
Install 1.0.0.10 on both PCs; on Wi-Fi, turn off Wi-Fi power saving on the client page first and run the network test again. Then please report whether the menu opens on every press (short and long), whether Game Mode starts a session (with the headset connected before entering Game Mode), whether the bitrate holds on Wi-Fi, and whether all base stations wake at Start, and send reports from both PCs, also when something does not work.

## Known limits
- **Nothing new here has run on hardware yet.** The menu-button press and the focus handling are tested in code against the sequences the 1.0.0.9 report showed. Game Mode's new environment and checks are tested with a stand-in display adapter and SteamVR's logged lines.
- **SteamVR's button events while it holds focus are not confirmed yet.** If they do not reach the client, a press the client reads before SteamVR takes focus still reaches the rendering PC, but as a tap, and a press hidden from the start is still missed. The `SYSTEM_BUTTON` and `INPUT` lines will show which happens.
- If SteamVR turns out to start without the environment VROSTBridge gives it, Game Mode cannot hand it the headset this way. The report will say so, and the handover will be changed.
- The Windows client has still not run on real hardware (see 1.0.0.7).

Build and test evidence: [evidence/vrostbridge-build-1.0.0.10.json](../evidence/vrostbridge-build-1.0.0.10.json).
