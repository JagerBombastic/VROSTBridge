# VROSTBridge 0.2.7 — host startup timing fix

## Finding

A timing bug in the 0.2.5/0.2.6 host controller can close the client session while SteamVR is legitimately restarting to apply the headset settings. This was reproduced against the original code with a local HTTP driver fixture: a momentarily incomplete session file plus driver exit immediately produced “SteamVR exited. Save a report…” and no relaunch. The user's new failing-session report was not supplied, so this is a confirmed code defect consistent with the symptom, not proof of the exact cause on that PC.

The pinned ALVR implementation writes negotiated display settings and a restart hash, signals the client to reconnect, and shuts down its driver. VROSTBridge owns the subsequent SteamVR restart because it does not run ALVR Dashboard. An incomplete file read or an exit before VROSTBridge's first process observation previously bypassed that restart path. A second problem marked the host session running when the asynchronous launch call returned, before driver readiness.

## Changes

- A missing host API/server gets a bounded **10-second recovery window** to read the negotiated restart record or recover the service. Missing processes alone no longer end a session immediately. A genuine exit without a restart record still fails with a useful error; it does not loop through arbitrary restarts.
- A changed restart hash is honored even if the first driver lifetime finished before the first process sample. Settings are reread after shutdown, with a short bounded retry for an incomplete write.
- Relaunch waits for the owned **startup, server, compositor and monitor** processes to exit, including late-arriving processes, and a **one-second quiet period**. An accepted shutdown HTTP response is not treated as process exit. Recovery still requires matching executable path, PID and creation time; unrelated runtimes are not killed by name.
- Cold startup allows **90 seconds** for the host driver. This is a startup ceiling, not a delay added to each successful launch or to streaming. Negotiated restarts remain capped at three.
- The companion lease stays active in **starting** state during startup/reconfiguration. It becomes **running** only when the driver reports ready and available. Client heartbeats continue while either SteamVR runtime is starting.
- A failed heartbeat from an old lease cannot overwrite the state of a newer session. Stop during the negotiation recovery window prevents a relaunch.
- Host reports include runtime phase, launch generation, elapsed launch time, recovery events, shutdown acceptance and completion. This makes any remaining failure easier to locate.

The 0.2.6 tray icon, quiet Windows sign-in option, stream-only local runtime, desktop web UI, Game Mode controls, installers and open-source credits are retained. Video quality, codec defaults, transport ABI, tracking and audio paths are unchanged. The native Linux streaming/display-lease binaries are reused byte-for-byte from verified 0.2.6; their native-core diagnostic banner remains 0.2.6, while the installed application reports 0.2.7. This is an unsigned development release.

## Validation

The regression checks cover the reproduced incomplete-file/driver-exit race, negotiation before first process observation, real-exit timeout, Stop during recovery, readiness versus launch acceptance, preserving the lease through reconfiguration, quick startup failure, late monitor exit and a stale heartbeat against a new lease. Existing ownership, client runtime, tray, Game Mode, session and installer checks are also run. Results: **159 Python cases run; 157 passed and 2 environment-dependent cases skipped** (procfs identity visibility and Unix datagram restrictions). The desktop JavaScript check passed. **All 12 packaged Bazzite install/lifecycle groups passed**, including fresh install, real 0.2.6 upgrade, repair, malformed-package rejection, authenticated HTTP, native SDL controls, orderly quit and uninstall preservation. Evidence is under `docs/evidence/`.

Automated checks use fixtures for the Windows runtime and a real local HTTP service. Package CRC/SHA checks, native PE inspection, and Linux install tests do **not** establish Windows execution, GPU/SteamVR compatibility or physical headset streaming. Those remain the focused acceptance test below. There is no claim that this fix resolves earlier judder or controller issues.

## Focused retest

1. Stop the current bridge session and close SteamVR on both PCs. Quit the old companions, then install **both 0.2.7 packages** over the existing installations. Settings and pairing identity are preserved; no manual deletion is required. If coming from 0.2.5, this also includes the regenerated 0.2.6 features.
2. Open **VROSTBridge Host** on Windows, confirm the web UI says **0.2.7**, and leave it waiting in the tray. Do not start SteamVR separately on the host.
3. Use the Bazzite **desktop client** first, confirm **0.2.7**, and keep the same working headset, refresh rate, codec and bitrate. Select **Start session once on the client**.
4. Watch the host status. Starting, applying client display settings, restarting, then ready/connected is expected if the headset configuration changed. The client session should remain active through this transition. Do not repeatedly press Start. A cold launch may take up to 90 seconds per launch attempt.
5. Once the picture appears, stream for one minute, select **Stop on the client**, and confirm SteamVR exits on both ends. Start again twice to check a cold start, a restart with saved configuration, and repeated session ownership.
6. If startup still closes the connection, use **Save report on the host immediately**, before restarting the companion. Record whether SteamVR remained open on Windows or Bazzite and the time you pressed Start. The report should include launch generation, phase and elapsed time; client detail is included when reachable. Then save the client report too if it was unavailable to the host.

An existing compatible saved headset configuration may need no restart; that is normal. After the desktop test passes, test Game Mode separately using `BAZZITE_PLAY.md`. Local SteamVR still supplies tracking/compositing on Bazzite, but the bridge session suppresses local Home/dashboard rather than falling back to them.

## Upstream audit

ALVR remains pinned to `f5023f8e78fa14ea57fb1038548d7820289a8524`:

- [Negotiation and restart signal](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_core/src/connection.rs)
- [Driver lifecycle and restart notification](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/server_core/src/server.rs)
- [Upstream SteamVR launcher](https://github.com/alvr-org/ALVR/blob/f5023f8e78fa14ea57fb1038548d7820289a8524/alvr/dashboard/src/steamvr_launcher/mod.rs)

Existing upstream licenses, source pins and attribution are retained in `OPEN_SOURCE_CREDITS.md`, `licenses/` and the packaged host notices.
