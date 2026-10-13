# Runtime review — current baseline

Updated 2026-10-02 for host RC1 / client RC1.3. Full contracts are in [PROJECT_SPEC.md](../PROJECT_SPEC.md).

The architecture remains appropriate to the demonstrated prototype: a patched ALVR virtual host, native ALVR client transport, FFmpeg hardware decode, Vulkan conversion and physical SteamVR presentation. Reusing these working pieces preserves the driver/input/audio stack while VROSTBridge owns session usability and the physical Linux receiver.

The strongest safeguards are bounded queues, source-pose/geometry association, native protocol/build identity, client-owned session leases, staged installer integrity and reversible local-runtime settings. Keep these constraints when tuning latency; do not bypass them to hide startup or pacing failures.

The immediate confirmed code defect was vertical optical projection conversion, corrected in RC1.3 with asymmetric tests. Physical confirmation is still missing. Half-rate client presentation is a separate observed issue and needs correlated stage timing. A healthy host encode rate alone does not prove the receiver meets display deadlines.

Input and skeleton forwarding are implemented and user-reported improved, but a controller surviving a short test does not close the prior long-run loss report. Runtime restart/driver registration have ownership checks, but repeated cold/warm starts and actual OS process failures remain physical acceptance work.

Local Home/dashboard suppression is implemented without removing SteamVR's compositor, tracking or physical safety boundary. A fatal runtime/GPU failure can still expose runtime-owned visuals. Game Mode capability checks and a successful Wayland protocol test do not prove physical lease acceptance.

No present evidence supports a wholesale transport replacement as a guaranteed improvement. Prioritise [the measured work list](STREAMING_NEXT_STEPS.md) and [physical acceptance](../HARDWARE_ACCEPTANCE.md), while retaining current attribution and pending public-release licence/source tasks.
