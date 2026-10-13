# VROSTBridge 0.2.14.1 — Windows host startup hotfix

**Update the Windows host. Keep the existing Bazzite 0.2.14 client.** This hotfix
preserves the native and companion connection protocols introduced in 0.2.14.
Clients from 0.2.13 and earlier still need the 0.2.14 client update.

The reported failure is a host software regression. The screenshot's message,
“The running host version changed. Its runtime was not stopped,” is the cleanup
failure after an earlier version check rejected the newly installed driver.
It does not establish a Wi-Fi, headset, dock or Bazzite display fault.

## Recover and install using the desktop

1. On the Bazzite client, select **Stop session** if it is still running.
2. On Windows, manually **Exit SteamVR** and wait for its window to close. The
   0.2.14 host's incorrect version check can prevent automatic Stop from completing,
   so this one-time manual close is needed before the update.
3. Right-click the Windows **VROSTBridge tray icon** and select **Exit**.
4. Run **VROSTBridge-Host-Setup-0.2.14.1.exe** over the existing installation.
5. Open **VROSTBridge Host** and leave it waiting for the headset client.
6. Open the existing **VROSTBridge Client 0.2.14** and select **Start session**.

No terminal, profile reset or client reinstall is needed for this hotfix. Keep the
saved connection, quality and room-setup preferences. The installer backs up and
preserves the host profile while replacing owned program files. Do not launch the
separate ALVR Dashboard as part of this workflow.

## Why the host rejected its own driver

The host report records the following sequence, with times shown in UTC:

| Time | Reported event | Meaning |
| --- | --- | --- |
| 01:21:29 | Client display readiness passed at approximately 120 Hz | The second client startup reached a working local compositor. |
| 01:21:32 | SteamVR loaded the driver from the installed `Host/0.2.14` directory | The new package's driver was found and loaded. |
| 01:21:33 | Expected `21.0.0-dev14+nightly.2026.09.18`, received `21.0.0-dev14` | The companion compared the driver against a retained old profile version. |
| 01:21:34–35 | Native connection succeeded and the client received H.264 decoder configuration | Discovery and the native handshake progressed; no video frames arrived before the session ended. |
| 01:21:36 | Host cleanup refused the apparent changed version; the client ended its session | The stale version expectation affected Stop as well as startup. |

The installer correctly retained `session.json` to preserve user settings. That
file also contains ALVR's writable `server_version`, which can retain the old
nightly build suffix. The host incorrectly treated that saved value as the
identity of the newly installed binary.

The hotfix reads the expected API version from the installed native build manifest
instead. It still requires **exact equality** with the running driver; it does not
ignore arbitrary version suffixes or allow incompatible drivers. Missing or
invalid packaged metadata blocks startup with a repair message. The mutable
session profile no longer defines the expected API identity, and there is no need
to discard its preferences to fix the problem.

The same expected version is used for startup and graceful shutdown. Existing
process-ownership checks remain: a replacement or unrelated SteamVR process is
not treated as a session that VROSTBridge may terminate.

## AMD encoder initialization

The AMF encoder's codec configuration switch allowed an H.264 case to continue
into HEVC and AV1 configuration. The stricter error checks introduced in 0.2.14
could then reject unsupported properties intended for another codec. This is a
separate host regression that can prevent the intended AMD hardware encoder from
starting even after the version issue is fixed.

The hotfix separates the codec cases so each encoder receives only its intended
configuration. Encoder selection and initialization failures are also visible in
diagnostics. This preserves the existing H.264 streaming target and 0.2.14 client
pipeline rather than requiring lower quality to bypass a startup defect.

A regression harness exercised the actual encoder configuration method against
strict SDK-property test doubles: **192 of 288 cases failed before the fix; all
288 passed after it**. This checks configuration behavior, not GPU execution.

The report's VPL `SetHandle` error is an encoder-attempt failure. The host tries
several encoders in sequence and catches initialization failures; that line alone
does **not** prove a SteamVR crash. It shows why reporting which encoder was
selected, and why preceding attempts failed, matters.

## What to check on the next run

Keep the same Index baseline: **120 Hz, Sharper, H.264 and 80 Mbit/s**. Start from
the client and check that the host becomes ready, frames arrive and the client
leaves its waiting screen. Then select **Stop session** on the client and confirm
that SteamVR closes on both PCs.

Once that works, test controller inputs and fingers, then the same scene used for
the earlier smoothness comparison. **Save report on the host while connected**
after a noticeable hitch. This hotfix targets startup and the correct encoder
path; it does not claim a measured latency improvement or that every remaining
hitch is resolved.

## Other messages and limits

- **Microphone unavailable:** this remains nonfatal. Video, tracking and game
  audio can continue. Microphone forwarding requires a matching Windows virtual
  audio endpoint; that warning caused neither the proven version mismatch nor
  the screenshot's failed cleanup.
- **First client startup timeout:** the report also contains an earlier native
  readiness probe that exceeded eight seconds. The next attempt passed physical
  display readiness. This host hotfix does not change that client probe; repeated
  timeouts need a new client report.
- **Local room setup unavailable:** the report had no transferable client play
  area. Valid client room setup is still needed for boundary transfer; this is
  separate from the version failure.
- **Physical validation:** Windows SteamVR/GPU execution and on-headset results
  cannot be verified in the build environment. Automated lifecycle tests include
  the exact stale-nightly upgrade case, successful graceful Stop and rejection of
  a genuinely different driver. The Python suite passes 247 tests with two
  platform skips, and desktop DOM checks pass. The corrected Windows native
  driver has been rebuilt and its source/patch correspondence and PE dependencies
  verified. Final package checks are recorded in the checkpoint and evidence;
  neither compilation nor automated checks substitute for a headset test.

The 0.2.14 finger, transport, display and presentation changes remain in place.
Their broader details and limits are preserved in the
[0.2.14 release review](RELEASE_REVIEW_0.2.14.md). The
[open-source credits](../OPEN_SOURCE_CREDITS.md) and bundled licences remain included.
