# VROSTBridge 1.0.0-rc1.1 — Bazzite startup diagnostics and retry update

This is a targeted client update, compatible with the existing RC1 Windows host.
It fixes a premature probe-timeout abort and missing diagnostics. It is **not a
confirmed fix for the Beyond display-readiness timeout**. Physical testing is
still required; the headset readiness acceptance criteria are unchanged.

## What the client report establishes

The 2 October attempt reached local SteamVR startup, but did not reach host
connection or streaming. SteamVR identified Bigscreen Beyond, acquired the DRM
display, enabled direct mode and updated the display frequency to 90 Hz. The
client repeatedly failed its three-second display-progress probe, then stopped
the runtime at the overall 60-second deadline. An earlier attempt aborted as
soon as one probe exceeded its eight-second subprocess timeout.

SteamVR initially requested 120.019 Hz while enumerating 75 and 90 Hz modes. It
then selected mode 0 (75 Hz), despite reporting a 90 Hz headset property.
This corrects the initial reading of that log; see the RC1.2 update notes. This is a clue to inspect, not proof that an old refresh
preference caused the readiness failure. The original launcher discarded the
probe's stdout whenever SteamVR's vrenv.sh wrapper emitted stderr, so the report
cannot establish the actual vsync counters or the failing readiness predicate.
The separate earlier observation of host video followed by shutdown is not
diagnosed by this failed-start report.

## Changes

- Retry an individual slow readiness probe inside the original 60-second budget.
  The timed-out probe is killed and reaped before retry; Steam is launched at most
  once per attempt. Cancel remains effective and no host Start is sent until
  readiness succeeds.
- Preserve native probe results alongside real stderr errors. Suppress only the
  two recognised vrenv.sh informational banners that previously obscured them.
- Include probe attempts, timeouts, executable, headset model, connection state,
  reported frequency, render dimensions, vsync counters and measured cadence,
  and compositor frame-history availability in the client report. No poses or
  raw gaze are added. Sampling is limited to startup, outside the stream loop.
- Explain that a verification timeout is not proof of an undetected headset.

The dedicated background-only VROSTBridgeRuntimeProbe does not create a Scene
application, enable Home/dashboard, draw a local environment or change settings.
It uses the same readiness algorithm as RC1. The native streaming executable,
ALVR transport, decoder libraries, shader, Game Mode lease helper and Windows
host remain unchanged. Their RC1 native banners are expected. Rebuilding the
streamer from this source also includes the extra startup diagnostics.

## Install and test

1. Stop the current session. Extract the entire Bazzite installer ZIP.
2. Open **Install VROSTBridge.desktop** and allow the normal installer to finish.
   It replaces managed application files and retains settings, pairing and logs.
3. Open VROSTBridge Client. The web page should show **1.0.0-rc1.1**.
4. Connect the Beyond through the working adapter, then click **Start session**.
   Keep the existing RC1 host installed.
5. If it fails, use **Diagnostics → Save report on the Bazzite client** before
   restarting either application. The host cannot collect this pre-connection
   report. Include the exact displayed message.

This build may still reach the display-timing timeout. The new report will
distinguish a missing vsync timer, frozen counters, a cadence mismatch, missing
compositor history and incomplete headset metadata, rather than treating them
all as "headset not found". Do not disable the readiness check or force the old
Index refresh rate merely to get past this test.

## Build and validation

Build the background probe using the pinned OpenVR header and system C++ runtime:

```sh
g++ -std=c++17 -O2 -Wall -Wextra -I. -Ithird_party/openvr \
  client/platform/linux/runtime_probe.cpp -o dist/client/VROSTBridgeRuntimeProbe \
  -ldl -pthread
```

The CMake build/install also includes this helper. Freeze the launcher using the
existing packaging recipe, then use `packaging/build_client.py --work WORK
--output OUTPUT --frozen FROZEN` to assemble only the client. Existing attribution
and licences are retained; the public-alpha redistribution issues documented
in OPEN_SOURCE_CREDITS.md remain unresolved by this update.

Automated results and exact reused native-file hashes are retained in
`docs/evidence/client-startup-1.0.0-rc1.1.json`. A successful test here does not
substitute for a physical Beyond session, SteamVR rendering on Bazzite, or Game
Mode display-lease validation.
