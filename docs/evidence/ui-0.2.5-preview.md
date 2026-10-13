# Desktop UI validation — 0.2.5

The four `ui-0.2.5-*-desktop.png` / `ui-0.2.5-*-mobile.png` screenshots were rendered
with a local headless Chromium browser using **synthetic status fixtures**.
They are layout/control evidence, not a real headset, network or timing result.

Checked client streaming and host waiting states at 1280×940 and 390×844:

- No JavaScript page errors or horizontal page overflow.
- Main Start session / Stop session controls visible only on the client.
- Host waiting copy explicitly says the client starts SteamVR on demand.
- Transparent icon renders without text or an opaque background.
- Open-source credits remain accessible in About.

`node tests/test_desktop_ui.js` separately checks dynamic action payloads, typed
bitrate, per-role controls, permission actions, outage recovery, client identity,
remote host metrics, pending preference recovery and cancellation of a still
pending startup request.
