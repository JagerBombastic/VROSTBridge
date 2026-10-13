# VROSTBridge 0.2.14 — stability, native input and display update

Update **both PCs together**. This release changes the native tracking protocol;
0.2.13 and earlier cannot be mixed with 0.2.14. A mismatched companion is rejected
before it can start an incompatible stream. Your saved quality settings and
connection identity remain in place.

The previous Index test is the baseline: approximately 120 Hz, H.264,
2016 × 2240 per eye and an 80 Mbit/s target. This update preserves that pipeline
and those saved choices. It addresses concrete defects and adds evidence for
remaining hitches; a compiled release is not proof of a measured hardware speedup.

## Install

1. Stop the session from the client, close SteamVR and quit both old companions.
2. On Windows, run `VROSTBridge-Host-Setup-0.2.14.exe` over the existing installation.
3. On Bazzite, extract the entire `VROSTBridge-Bazzite-Installer-0.2.14.zip` and open
   **Install VROSTBridge.desktop**. Keep the extracted folder together until the
   installer finishes. No separate Python installation or terminal is needed.
4. Open VROSTBridge Host. It waits in the system tray; Settings opens its web UI.
   Windows sign-in startup remains silent, with no browser or SteamVR launch.
5. Open VROSTBridge Client and select **Start session**. Start and Stop remain
   client-controlled. Desktop uses the web UI; the existing Game Mode entry uses
   the controller-friendly native screen.

The installers replace owned program files and retain preferences. They do not
require deleting SteamVR configuration or repeating valid room setup. Keep the
previous matching pair if you need to return to the prior working build.

## What changed

### Startup and normal shutdown

The physical client must demonstrate advancing display timing at its reported
refresh rate before the host is configured. This addresses the observed startup
sequence where an early 90 Hz property preceded the actual 120 Hz compositor,
causing an avoidable host restart. A fixed startup sleep is not the readiness test.
The native probe uses measured vsync progress, with an advancing compositor-timing
fallback when the vsync API is unavailable.

The native helper and streaming process use the active SteamVR installation's
`vrenv.sh` when available. That lets Valve's own environment setup supply helper
library paths. A missing wrapper is recorded explicitly. Stop can cancel discovery
and startup without subsequently launching the remote session.

A new, current-session Steam quit-all request is classified as a normal external
shutdown. Old log entries cannot make a later crash look intentional. Unexplained
runtime exits remain errors, and the existing verified process-ownership checks
remain in use during cleanup.

### Connection stability and adapter selection

The native TCP control path now has a bounded write deadline. A partially sent
control message closes the damaged connection instead of allowing later messages
to continue in a corrupted frame. EOF is recognized immediately. This removes an
indefinite-send condition that could block heartbeats and cancellation. Discovery
also uses a stable service instance and explicitly shuts down each mDNS daemon
when it is no longer needed, preventing reconnect attempts from accumulating
background discovery services.

The normal two-second heartbeat limit remains. Recent media/tracking progress can
grant a bounded grace period for a delayed control heartbeat, with an absolute
six-second ceiling. A dead media link does not receive that extension. Explicit
Stop, restart and closed-socket errors remain immediate. These changes cannot
prevent a physical Wi-Fi outage, but distinguish useful ongoing streaming from a
lost connection.

On the desktop client, expand **Headset display & stream → Host connection** for
**Client network adapter**. In Game Mode, open the **Host** page and use **Adapter**.
Automatic follows the route to the host; an explicit selection uses an observed
physical adapter. The chosen source address and outgoing interface are applied to
VROSTBridge's sockets without changing system routes or disconnecting other apps.
The selection is locked during an active session.

For the handheld test, select the intended Wi-Fi 7 adapter if both wireless adapters are
connected. Use the addresses shown by the app to identify it rather than assuming
an interface name. The report records the route, owned socket endpoints/queues,
link state and available Wi-Fi power-save information. Two adapters alone do not
prove that route switching caused the earlier drops.

### Host capture and AMF

The host waits for the encoder before touching the shared D3D11 immediate context
or acquiring SteamVR's compositor texture. Only successfully acquired mutexes are
released, including on composition exceptions. Encoder completion is a persistent
readiness condition, so a
failed capture cannot consume the only wake-up and hang the following frame.

AMF input admission is checked, accepted surfaces remain owned until their output
completes, and intermediate frames survive downstream backpressure. The output
query ceiling is bounded by the refresh interval rather than the former one-second
maximum. A stalled pipeline has checked recovery and requests a fresh IDR. This
retains codec, resolution and bitrate; it does not remove texture synchronization.

The report includes separate capture, mutex, encoder submission and output
measurements. See `docs/HOST_NATIVE_0.2.14.md` for ownership and recovery details.

### Finger fidelity

The native stream carries the controller's measured curl values and all 31 OpenVR
bones for both **WithController** and **WithoutController** motion ranges. The
host uses those source curls for the individual finger channels, rather than
recalculating them from approximate joint rotations. Auxiliary bones are preserved.

Every native hand sample has a timestamp, handedness and validity. Stale, future,
missing or invalid samples cannot remain indefinitely active. The prior skeletal
approximation remains a fallback when valid native data is unavailable. Buttons,
force, haptics and the separate controller poses are retained.

Full hand samples are sent at the skeletal sampling cadence; intermediate tracking
packets refer to the latest sample. Head/controller tracking remains independent
of presentation; skeletal acquisition runs at display cadence. The larger full sample uses more UDP fragments than an ordinary pose packet, so
its effect on a real wireless link remains part of the hardware comparison.

**Diagnostics → Live timing & controller inputs** displays source curl values.
On the host, current native-driver evidence also shows the exact values applied
from the same received sample. It stops claiming current host acceptance once
that evidence becomes stale. The displayed four values are index, middle, ring
and little finger, from 0 (open) to 1 (curled).

Index controllers still provide partial skeletal tracking: matching the source
values does not give them independent measurement of every finger joint. Compare
slow individual-finger movements locally and through the bridge if physical
finger correspondence remains uncertain.

### Presentation and retained incident evidence

Client timing now separates event polling, geometry refresh, `WaitGetPoses`,
focus checks, submission and handoff. Long presentation gaps preserve nearby
frame records and compositor frame indices. Event processing and failed geometry
retries are bounded; tracking is not tied to that work or smoothed with extra delay.

Connection incidents and presentation incidents have separate bounded retention.
Repeated short hitches cannot evict the saved connection timeout. Pre/post-failure
evidence survives the ordinary rolling logs and a companion restart. Detailed logs
stay local; saving a host report collects the client evidence without continuously
streaming the full trace during play.

### Beyond display sizing

Automatic Beyond uses HEVC with a 2560-pixel-wide per-eye stream target; Beyond
Detail uses 3200. These targets are no longer capped by a local recommendation
scaled down for the handheld's GPU. Aspect ratio and codec bounds still apply.
The explicitly selected **Client application recommendation** mode continues to
use that recommendation, with codec limits.

The UI distinguishes the local application recommendation from the encoded stream
size. Physical refresh is detected, not changed by VROSTBridge. Set a Beyond
physical mode or brightness using supported headset controls before starting.
The Beyond 2e eye-tracking provider/calibration remains a separate path and still
needs physical validation.

## Focused hardware comparison

For the first Index run, keep the known-working **120 Hz**, **Sharper**, **H.264**
and **80 Mbit/s** settings. Do not change several quality controls together.

- Start from the client. Check that the host starts once at the final physical Hz.
- Run the same scene for 10–15 minutes, with slow rotations and normal movement.
  Note the approximate time of any visible hitch or reconnect.
- In Alyx, open/close each hand and move individual fingers slowly. Check both
  controllers' buttons, grips and menu access as well as the curl diagnostics.
- Save a report **on the host while connected** after a noticeable hitch. The
  retained incident windows are included; save a separate client report if the
  host says it could not retrieve the client evidence.
- Stop on the client and confirm both SteamVR sessions close while the companions
  remain ready. Test Game Mode after the desktop comparison.

Frame age is not motion-to-photon latency. A higher bitrate can preserve detail,
but it cannot by itself fix a missed compositor deadline or control-channel stall.
The earlier left-eye fault was reproduced in local SteamVR without the bridge;
this update does not establish a fix for that separate local display behavior.

## Validation and attribution

The exact build/test results are recorded under `docs/evidence/` and summarized in
`CHECKPOINT.md`. The Windows driver and Linux transport must be built from the
same patched ALVR revision. The installer assembler checks native-file hashes and
the complete source-patch set, and rejects an old or mismatched native overlay.

This environment cannot run the user's Windows SteamVR/GPU or physical headsets.
No measured latency improvement or complete dropout cure is claimed before that
comparison. All existing upstream notices remain included, and the modified ALVR
source is reproducible from the pinned revision plus the supplied patches. See
`docs/OPEN_SOURCE_CREDITS.md` for ALVR, OpenVR, FFmpeg and other component credits.
