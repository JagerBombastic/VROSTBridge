# Beyond display timing correction — client 1.0.0-rc1.2

Keep the existing 1.0.0-rc1 Windows host installed. This is a Bazzite client
update addressing the failed startup recorded by the RC1.1 client report.

## Confirmed cause

SteamVR selected mode 0, `5088x2544@75 Hz`, while publishing
`Prop_DisplayFrequency_Float = 90`. The client's advancing physical vsync
counter independently measured approximately 75 Hz. The headset was connected,
but VROSTBridge required measured timing to match that property, so readiness
timed out before it sent a session-start request to the host.

The previous notes incorrectly described the selected mode as 90 Hz. The
property update is not the selected display mode. This report establishes the
readiness failure; it does not diagnose the separate earlier observation of
streaming followed by a shutdown.

## Change

- For a headset whose reported model contains Beyond, accept the specific
  reported-90/measured-75 discrepancy only after at least 1.5 seconds of valid,
  advancing physical vsync evidence within 3% of 75 Hz. Counter resets, invalid
  timers, stale samples and changing render dimensions invalidate the evidence.
- Retain the existing checks for correctly reported modes and for Index startup.
  No general acceptance of mismatching rates, fixed startup delay, forced mode
  selection or edits to SteamVR display preferences are introduced.
- Put the verified effective rate into the existing headset metadata sent to
  the host. The RC1 host already uses this value for its preferred frame rate.
- Reverify timing before the native streaming process opens its scene. Reject
  changed model/rate properties between verification and scene opening. Use the
  effective rate for client transport capabilities, presentation budgets and
  input scheduling as well as the host profile.
- Include reported and effective frequency and `refresh_reconciled` in startup
  diagnostics. This measurement runs only at startup, outside the stream loop.

The client still uses SteamVR for physical tracking and presentation, with its
Home/dashboard suppressed during streaming. This update does not intentionally
switch to the local SteamVR environment as a recovery action.

Both VROSTBridgeClient and VROSTBridgeRuntimeProbe are rebuilt. The ALVR
transport, FFmpeg decoder libraries, shader, OpenVR loader, camera support and
Game Mode lease helper are reused unchanged. Existing credits and licences are
retained. This update does not resolve the public-alpha redistribution gaps
documented in OPEN_SOURCE_CREDITS.md.

## Install and check

1. Stop any bridge session and quit VROSTBridge Client.
2. Extract the complete **VROSTBridge-Bazzite-Installer-1.0.0-rc1.2.zip** and open
   **Install VROSTBridge.desktop**. The managed installer upgrades application
   files and removes its previous managed release while retaining user settings.
3. Open the client. Confirm its page shows **1.0.0-rc1.2**. Leave the existing RC1
   host running and use the working Beyond adapter arrangement.
4. Start the session from the client. For the reported configuration, diagnostics
   should show `reported_hz=90 effective_hz=75 refresh_reconciled=1`; the host
   should negotiate 75 Hz. A genuine working 90 Hz mode should remain at 90 Hz.
5. Test a cold start, Stop/Start, and a short game session. If it fails, save the
   **client** report before restarting either application. A pre-connection
   failure cannot be collected by the host. Include the on-screen error.

Automated checks cover the 75/90 discrepancy, correctly reported rates, Index's
90/120 startup mismatch, invalid/frozen/reset timers, metadata propagation and
the installer. They cannot prove a physical Beyond stream works. Hardware
acceptance on Bazzite remains necessary.

Build details and validation results are recorded in
`docs/evidence/beyond-refresh-1.0.0-rc1.2.json`.
