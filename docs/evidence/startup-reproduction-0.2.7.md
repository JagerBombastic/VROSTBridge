# Startup regression reproduction

Baseline: verified saved 0.2.6 source, with the same immediate-exit branch as 0.2.5. A real loopback HTTP fixture supplies the pinned ALVR API shape; process discovery is injected.

1. Start and wait for driver readiness.
2. Replace session.json temporarily with `{` and report the driver inactive.
3. Let one host monitor iteration run.
4. Write valid negotiated settings with a changed restart hash.

Before: `SteamVR exited. Save a report to inspect the host failure, then start again.`; launch count 1, terminal error before the valid restart record is available.

After: recovery phase keeps the session pending; the new hash triggers exactly one orderly shutdown/relaunch; launch count 2, driver ready, empty error. Automated regression: tests/test_startup_027.py, test_partial_negotiation_write_and_exit_wait_for_restart.

This reproduces a code race. It does not execute Windows SteamVR or establish the exact cause of a user session without its report.
