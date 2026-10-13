# Project direction

Updated 2026-10-02. The complete requirements now live in [PROJECT_SPEC.md](../PROJECT_SPEC.md).

Build on the working ALVR/OpenVR streaming path: Windows renders games; Bazzite decodes/presents to a physically attached Index or Beyond headset using local SteamVR tracking and compositor services. Keep hardware requirements general. The handheld, Wi-Fi standard and adapters used for testing are test equipment, not required product identities.

Normal operation is client-led. The host waits in its Windows tray, optional sign-in startup stays quiet, and client Start/Stop controls both runtime sessions. Desktop mode uses local web interfaces; Game Mode uses the native SDL interface and the implemented gamescope lease integration.

The Index is the regression baseline. Beyond connection is now observed, while the RC1.3 projection fix needs a physical check. The next performance work is measured investigation of intermittent half-rate presentation, then controller/lifecycle soak and Game Mode/gaze validation. Preserve image quality and optical correctness while reducing avoidable delays.

Do not expand scope to a Windows client, replace the VR transport with a desktop streaming system, or claim stable/public release readiness before current acceptance and licence/source tasks are complete. See [STREAMING_NEXT_STEPS.md](STREAMING_NEXT_STEPS.md).
