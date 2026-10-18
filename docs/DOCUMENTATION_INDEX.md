# Documentation index

Every document in the project, current and historical, with a line on each. For a guided start, see the [documentation home](README.md).

Current baseline: VROSTBridge Windows host, Bazzite client and Windows client **1.0.0.14**. Documentation revision **2026-10-18 / 1.0.0.14**.

## Start here

| If you want to… | Read |
| --- | --- |
| Find out what it is and try it | [README](../README.md), then [QUICK_START.md](QUICK_START.md) |
| Use a Windows PC as the headset PC | [QUICK_START.md](QUICK_START.md), then [INSTALL_PACKAGES.md](INSTALL_PACKAGES.md) |
| Install, upgrade or remove it | [INSTALL_PACKAGES.md](INSTALL_PACKAGES.md) |
| Get the smoothest session (recommended settings, the bitrate, the network settings, base stations) | [QUICK_START.md](QUICK_START.md) and [TROUBLESHOOTING.md](TROUBLESHOOTING.md), then the [technical reference](TECHNICAL_REFERENCE.md) |
| Choose an Index's refresh rate, or know how the stream follows a Beyond's mode | [BEYOND_DISPLAY_OPTIONS.md](BEYOND_DISPLAY_OPTIONS.md) |
| Play from Steam's Game Mode (Bazzite) | [BAZZITE_PLAY.md](BAZZITE_PLAY.md) |
| Use a Beyond 2e's eye tracking | [BEYOND_EYE_TRACKING.md](BEYOND_EYE_TRACKING.md) |
| Fix a problem | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Know what changed | The [1.0.0.14 release notes](RELEASE_1.0.0.14.md), then the [CHANGELOG](../CHANGELOG.md) |
| Know what hardware it has met | [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md) |
| Report a bug or send a fix | [CONTRIBUTING](../.github/CONTRIBUTING.md), [SECURITY](../.github/SECURITY.md) |
| Know what the licences allow, and what isn't included on purpose | [LICENSING.md](LICENSING.md) |
| Understand how it works, or how it differs from ALVR, Baballonia and SteamVR | [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) ([PDF](TECHNICAL_REFERENCE.pdf)) |
| Read or change the code | [SOURCE_OVERVIEW.md](SOURCE_OVERVIEW.md), [BUILDING.md](BUILDING.md), [DEVELOPMENT_CHECKPOINT.md](DEVELOPMENT_CHECKPOINT.md), [PROJECT_SPEC.md](PROJECT_SPEC.md) |

## Current entry points

| Document | Use |
| --- | --- |
| [README](../README.md) | What VROSTBridge is, what you need, getting started |
| [Documentation home](README.md) | The guides, technical references and records, grouped by what you want to do |
| [QUICK_START.md](QUICK_START.md) | First install and a normal desktop session, with a Bazzite or a Windows headset PC |
| [INSTALL_PACKAGES.md](INSTALL_PACKAGES.md) | The three packages: install, upgrade, repair, uninstall and what each keeps |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | What the page's messages mean and what to do about them |
| [BAZZITE_PLAY.md](BAZZITE_PLAY.md) | Steam Game Mode set-up, display handling and limits |
| [BEYOND_DISPLAY_OPTIONS.md](BEYOND_DISPLAY_OPTIONS.md) | Headset detection, stream size, refresh and projection |
| [BEYOND_EYE_TRACKING.md](BEYOND_EYE_TRACKING.md) | Beyond 2e eye tracking: set-up, calibration, the gaze reach, what runs, limits |
| [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) / [PDF](TECHNICAL_REFERENCE.pdf) | How it works end to end on the rendering PC and on a Bazzite or Windows headset PC, the minimum and recommended specs, and where it differs from ALVR, a directly connected headset, SteamVR's defaults and Baballonia |
| [PROJECT_SPEC.md](PROJECT_SPEC.md) / [PDF](PROJECT_SPEC.pdf) | The full project specification and requirements |
| [SOURCE_OVERVIEW.md](SOURCE_OVERVIEW.md) | The source layout, where things stand, release history in short |
| [BUILDING.md](BUILDING.md) | Native host and client builds and tests, including the Windows client, cross-built on Linux |
| [Packaging guide](../packaging/README.md) | Frozen launchers, verified native inputs and installer assembly for the three packages |
| [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | Area by area: what is implemented, what has been seen on hardware, what is pending |
| [DEVELOPMENT_CHECKPOINT.md](DEVELOPMENT_CHECKPOINT.md) | Developer handoff: where things stand, how the parts fit, each release's code changes since 1.0.0.11, what comes next |
| [BUILD_VERIFICATION.md](BUILD_VERIFICATION.md) | Evidence and limits of the current build |
| [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md) | The physical acceptance checks, including the Windows client's first run on hardware |
| [evidence/vrostbridge-build-1.0.0.14.json](evidence/vrostbridge-build-1.0.0.14.json) | 1.0.0.14's build and test record: package hashes, tools and test results |
| [evidence/vrostbridge-build-1.0.0.13.json](evidence/vrostbridge-build-1.0.0.13.json), [1.0.0.12](evidence/vrostbridge-build-1.0.0.12.json), [1.0.0.11](evidence/vrostbridge-build-1.0.0.11.json) | The build and test records of the earlier public releases |
| [evidence/vrostbridge-public-release-1.0.0.11.json](evidence/vrostbridge-public-release-1.0.0.11.json) | The first public release's record: the source split by part and each part built from it, the checks from a clean copy, the scans |
| [OPEN_SOURCE_CREDITS.md](OPEN_SOURCE_CREDITS.md) | Upstream authors, pins, licences and notices |
| [LICENSE](../LICENSE), [NOTICE](../NOTICE), [THIRD_PARTY.md](../THIRD_PARTY.md) | VROSTBridge's MIT licence, its key attributions, and what each package ships under which licence |
| [LICENSING.md](LICENSING.md) | The licences in plain words: for users, for anyone sharing or forking VROSTBridge, and for contributors |
| [PUBLISHING.md](PUBLISHING.md) | Putting a release on GitHub: the checks, the source, the tag and the release page |
| [CHANGELOG](../CHANGELOG.md) | Each version in a few lines, newest first; the release notes have the detail |
| [RELEASE_1.0.0.14.md](RELEASE_1.0.0.14.md) | 1.0.0.14's release notes: what changed, how to test it, known limits |
| [GITHUB_RELEASE_1.0.0.14.md](GITHUB_RELEASE_1.0.0.14.md) | The text for 1.0.0.14's GitHub release page, ready to paste, with the files to attach |
| [brand/README.md](../brand/README.md) | The name and logo: spelling, which artwork goes where, how the app's images are built |

## Current change records

- [1.0.0.14](RELEASE_1.0.0.14.md), from the first external testers and 1.0.0.13's first session: the hands predicted with their acceleration, the host rendering at the headset PC's recommended size, HEVC as the automatic codec, a bitrate ceiling about a third higher, a Bazzite headset PC on its network twice kept on the cable (*Fix this PC's network settings*), the receive buffer judged properly, paired headset PCs starting on any Windows network type, the sharp region predicted through the head's turn, and the host's last session kept for its report.
- [1.0.0.13](history/RELEASE_1.0.0.13.md), published, from 1.0.0.12's first session: the stream chosen before a session, more parity on a wired link, a quick stop while SteamVR starts, and the adapters' drop counters in the reports.
- [1.0.0.12](history/RELEASE_1.0.0.12.md), published, from 1.0.0.11's first session: the bitrate's fresh start once the GPU clock is held, the GPU clock remembered, and the add-on warning limited to add-ons from outside SteamVR.
- [1.0.0.11](history/RELEASE_1.0.0.11.md), the first public release: a lighter picture on the headset PC, so a headset's full resolution fits lighter headset PCs.

## Historical material

These live in [`history/`](history/). They may contain old versions, planned features or limitations later resolved, and are kept for traceability, not as installation instructions; where they differ from the current documents, the current documents hold.

- **Earlier detail of the current pages:** [CHANGELOG_EARLIER.md](history/CHANGELOG_EARLIER.md) (the changelog to 1.0.0.10), [IMPLEMENTATION_STATUS_EARLIER.md](history/IMPLEMENTATION_STATUS_EARLIER.md) (the status per release to 1.0.0.10, and the Windows client's part-by-part table) and [DEVELOPMENT_CHECKPOINT_EARLIER.md](history/DEVELOPMENT_CHECKPOINT_EARLIER.md) (the handoff's sections A to L and earlier builds) and [BUILD_VERIFICATION_EARLIER.md](history/BUILD_VERIFICATION_EARLIER.md) (the build summaries from 1.0.0.13 back to RC3).
- **Release notes, 1.0.0 to 1.0.0.10:** [1.0.0.10](history/RELEASE_1.0.0.10.md) (the menu button, Game Mode's display handover), [1.0.0.9](history/RELEASE_1.0.0.9.md) (game audio, packet repair), [1.0.0.8](history/RELEASE_1.0.0.8.md) (steadier streams, performance balance, base stations), [1.0.0.7](history/RELEASE_1.0.0.7.md) (the Windows client), [RELEASE_1.0.0.6.md](history/RELEASE_1.0.0.6.md) (the gaze reach, the network test's lateness), [RELEASE_1.0.0.5.md](history/RELEASE_1.0.0.5.md) (exact 10-bit colour, the eye cameras' rate), [RELEASE_1.0.0.4.md](history/RELEASE_1.0.0.4.md) (the gaze hold, the processor warning), [1.0.0.3](history/RELEASE_1.0.0.3.md) (Game Mode's reservation, the filtered reduced area), [RELEASE_1.0.0.2.md](history/RELEASE_1.0.0.2.md) (the calibration overlay's Vulkan layer), [1.0.0.1](history/RELEASE_1.0.0.1.md) (one name throughout) and [1.0.0](history/RELEASE_1.0.0.md) (the first release build).
- **Release candidates:** [RC4.5](history/RELEASE_1.0.0-rc4.5.md) (eye tracking run by VROSTBridge), [RC4.4](history/RELEASE_1.0.0-rc4.4.md), [RC4.3](history/RELEASE_1.0.0-rc4.3.md), [RC4.2](history/RELEASE_1.0.0-rc4.2.md), [RC4.1](history/RELEASE_1.0.0-rc4.1.md), [RC4](history/RELEASE_1.0.0-rc4.md) (foveated encoding, 10-bit, AV1, the network test), [RC3](history/RELEASE_1.0.0-rc3.md), [RC2](history/RELEASE_1.0.0-rc2.md), [RC1.3 projection](history/PROJECTION_1.0.0-rc1.3.md), [RC1.2 refresh](history/BEYOND_REFRESH_1.0.0-rc1.2.md), [RC1.1 startup](history/CLIENT_STARTUP_1.0.0-rc1.1.md), [RC1 review](history/RELEASE_REVIEW_1.0.0-rc1.md) and the [native hand protocol](history/NATIVE_HAND_PROTOCOL.md).
- **Release page texts:** [1.0.0.13](history/GITHUB_RELEASE_1.0.0.13.md), [1.0.0.12](history/GITHUB_RELEASE_1.0.0.12.md) and [1.0.0.11](history/GITHUB_RELEASE_1.0.0.11.md), as published (1.0.0.12's shortened), and the unpublished ones for [1.0.0.10](history/GITHUB_RELEASE_1.0.0.10.md), [1.0.0.9](history/GITHUB_RELEASE_1.0.0.9.md), [1.0.0.8](history/GITHUB_RELEASE_1.0.0.8.md), [1.0.0.7](history/GITHUB_RELEASE_1.0.0.7.md) and [1.0.0.6](history/GITHUB_RELEASE_1.0.0.6.md).
- **Earlier reviews and records:** the versioned `RELEASE_REVIEW_*`, `HOST_NATIVE_0.2.14.md`, `SESSION_NETWORK_0.2.5.md`, `PERFORMANCE_RESEARCH_0.2.5.md`, `PACKAGING_HISTORY_0.2.md`, `BUILD_VERIFICATION_0.1.md`, the dated files under `evidence/`, and the focused test and audit notes `INDEX_FIRST_TEST.md`, `HARDWARE_CHECKLIST.md`, `CONTROLLER_RECOVERY_1.0.md` and `UPSTREAM_AUDIT.md`. `PROJECT_DIRECTION.md` is a short direction summary, and `STREAMING_NEXT_STEPS.md` and `RUNTIME_REVIEW.md` the priorities and risks as they stood at RC1.

The current documents in `docs/` are authoritative; all three packages carry the same `docs/` folder.
