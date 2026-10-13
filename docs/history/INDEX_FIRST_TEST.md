# Valve Index release-candidate test — 1.0.0-rc1

Update **both** packages to RC1. Install over the existing versions after stopping
the session and quitting the companions; preferences and pairing are retained.
No new room setup is needed if the client's existing local setup is correct.

Keep the comparison fixed at **120 Hz, High, H.264 and 80 Mbit/s**. High is the
former Sharper setting: the reported Index mode uses 2016 × 2240 stream pixels per
eye. Confirm the detected refresh and actual video dimensions in the client page.

1. Open the host and leave it waiting in the tray. Open the client in desktop mode.
   Start only from the client.
2. Run **two cold launches**: after rebooting both PCs and allowing the normal
   desktop/network session to settle, start the bridge and enter the same Alyx
   area. Note whether the first launch differs from later launches.
3. Run **two warm Start/Stop cycles** without rebooting. Each Stop should close
   SteamVR on both ends; the companions should remain ready for another Start.
4. Play Alyx continuously for **at least 15 minutes**. Check both triggers, sticks,
   grips, fingers, haptics and host menus. If a controller loses tracking, compare
   the new **Tracking lost** and **Disconnected** indicators, and verify inputs
   recover when tracking returns.
5. After a hitch or controller problem, note the approximate time and save a host
   report while connected. The host collects the client's report when reachable.
   If collection fails, save the client report too.

For a controlled Ethernet comparison: Stop, connect Ethernet, select its adapter
under client **Host connection**, then restart at the same quality. If Wi-Fi remains
connected, explicit adapter selection makes the comparison unambiguous. Switching
adapters during a stream is intentionally unsupported. A new firewall zone may
require the existing **Allow home-network connection** action once.

RC1 transfers and verifies the host's rectangular play area. The client's physical
boundary remains active; it is not replaced by the remote rectangle. Local Home
and dashboard stay suppressed during streaming.

After desktop acceptance, test Game Mode separately using
[BAZZITE_PLAY.md](../BAZZITE_PLAY.md). Beyond display/gaze also require their own
physical tests. Successful builds and package checks do not replace these checks.

[Release guide](RELEASE_REVIEW_1.0.0-rc1.md)
