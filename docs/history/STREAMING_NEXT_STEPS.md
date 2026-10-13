# Streaming and release priorities — 2026-10-02

The existing ALVR/OpenVR path now has user-confirmed Index operation and observed Beyond connection. Keep it as the baseline. See [PROJECT_SPEC.md](../PROJECT_SPEC.md) for the complete scope.

1. **Confirm optics first.** Test RC1.3's corrected raw-tangent conversion on Beyond, retain its new `view_geometry` report and regress Index. No guessed FOV constants or resolution changes should mask a geometry fault.
2. **Explain half-rate presentation.** The latest Beyond session sometimes presents near 37 unique frames/s with a roughly 75 Hz host. Correlate receive, decoder readiness, conversion, GPU completion, compositor deadlines and source-pose history. The report's frame-age summary is not motion-to-photon measurement.
3. **Establish repeatable comparisons.** Fixed game scene, refresh, codec, resolution and bitrate; compare Ethernet/Wi-Fi with route verified. Record startup as well as steady-state. Do not trade away quality silently to claim lower delay.
4. **Close input/lifecycle acceptance.** Both controllers, skeletal accuracy versus local baseline, haptics, menu, sleep/reconnect, cold/warm starts and clean runtime stop.
5. **Qualify remaining platform paths.** Real Game Mode DRM lease and calibrated Beyond 2e gaze workflow; then fresh Windows installer qualification and licence/source closure.

Apollo/Sunshine-style capture, pacing and congestion ideas may inform experiments, but no alternate transport is shipped. A VR replacement must retain correct stereo geometry, time/pose association, physical compositor integration, skeletons and bidirectional input. Switching whole stacks without stage-level evidence would make the current fault harder to isolate. AV1, HDR, adaptive resolution, Windows client and dynamic foveated rendering need separately scoped implementation and tests.
