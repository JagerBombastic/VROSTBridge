# VROSTBridge documentation

New here? Start with the project's [front page](../README.md): what VROSTBridge is, what you need and how to get going.
This folder holds everything behind it, from the first install to how every frame finds its way down the hall.

If you only read one page, make it the [quick start](QUICK_START.md). If you read all of them, you have earned a
cup of tea.

## Using VROSTBridge

| Guide | What's in it |
| --- | --- |
| [Quick start](QUICK_START.md) | Prepare both PCs once, then start and stop a session |
| [Installing, upgrading and removing](INSTALL_PACKAGES.md) | The three packages (rendering PC, Bazzite headset PC, Windows headset PC): what each installs, upgrades that keep your settings, repair and clean removal |
| [Troubleshooting](TROUBLESHOOTING.md) | What the page's messages mean and what to do, from finding the host to Wi-Fi hiccups |
| [Steam Game Mode on Bazzite](BAZZITE_PLAY.md) | The Game Mode screen, the Steam library entry and the headset display |
| [Eye tracking](BEYOND_EYE_TRACKING.md) | Eye-tracked foveation on the Bigscreen Beyond 2e: set-up, calibration and gaze centring |
| [Beyond display options](BEYOND_DISPLAY_OPTIONS.md) | How the stream follows a Bigscreen Beyond 2 or 2e's display mode, refresh and projection |
| [Hardware tested so far](HARDWARE_ACCEPTANCE.md) | What has run on real hardware, and the checks for a new setup |

## How it works

| Guide | What's in it |
| --- | --- |
| [Technical reference](TECHNICAL_REFERENCE.md) ([PDF](TECHNICAL_REFERENCE.pdf)) | The whole system, end to end, with the reasons behind each choice |
| ↳ [The system in one page](TECHNICAL_REFERENCE.md#1-the-system-in-one-page) | The short version, with a diagram |
| ↳ [Networking, ports and trust](TECHNICAL_REFERENCE.md#4-networking-ports-protocols-and-trust) | Which PC listens where, discovery, pairing and the firewall rules |
| ↳ [The video pipeline](TECHNICAL_REFERENCE.md#6-the-video-pipeline) | Capture, encoding, decoding and the one-pass frame shader on the headset PC |
| ↳ [Timing and latency](TECHNICAL_REFERENCE.md#7-timing-and-latency) | Where the milliseconds go, prediction and re-aiming the picture to your head |
| ↳ [Foveated encoding](TECHNICAL_REFERENCE.md#8-foveated-encoding) and [eye tracking](TECHNICAL_REFERENCE.md#9-eye-tracking-bigscreen-beyond-2e) | Sending the sharp detail where you look |
| ↳ [Security and privacy](TECHNICAL_REFERENCE.md#17-security-and-privacy) | What is protected, what isn't, and what happens to eye data |
| ↳ [Where VROSTBridge differs](TECHNICAL_REFERENCE.md#part-2--where-vrostbridge-differs) | From ALVR, a directly connected headset, SteamVR's defaults and Baballonia's usual setup |
| ↳ [Settings](TECHNICAL_REFERENCE.md#28-settings-and-their-defaults), [files](TECHNICAL_REFERENCE.md#29-files-and-folders) and [log lines](TECHNICAL_REFERENCE.md#30-log-lines) | Reference tables |
| [Project specification](PROJECT_SPEC.md) ([PDF](PROJECT_SPEC.pdf)) | Every requirement and decision, release by release |
| [Implementation status](IMPLEMENTATION_STATUS.md) | What is implemented, what has been confirmed on hardware and what hasn't |

## Building, contributing and licences

| Guide | What's in it |
| --- | --- |
| [Building from source](BUILDING.md) | The native clients for Linux and Windows, the rendering PC's driver from ALVR plus VROSTBridge's patches, the companions, and the tests |
| [Source overview](SOURCE_OVERVIEW.md) | Where everything lives in the source |
| [Packaging](../packaging/README.md) | How the installers are assembled and checked |
| [Contributing](../.github/CONTRIBUTING.md) and [security](../.github/SECURITY.md) | How to report a bug, send a fix or report a vulnerability privately |
| [Licences in plain words](LICENSING.md) | What the MIT licence lets you do, what the packages carry from others, and what isn't included on purpose |
| [Third-party notices](../THIRD_PARTY.md) and [open-source credits](OPEN_SOURCE_CREDITS.md) | Every component, its licence and where its notice is |

## Releases

| Document | What's in it |
| --- | --- |
| [Changelog](../CHANGELOG.md) | Each version in a few lines, newest first |
| [Release notes for 1.0.0.13](RELEASE_1.0.0.13.md) | The current release in detail, with how to test it and its known limits |
| [Build verification](BUILD_VERIFICATION.md) and its [evidence](evidence/) | How each release was built and checked, with hashes and test results |
| [Development checkpoint](DEVELOPMENT_CHECKPOINT.md) | The developer's handoff: what each release changed in the code, and what comes next |
| [Publishing on GitHub](PUBLISHING.md) | The maintainer's checklist for a release |
| [The 1.0.0.13 release page](GITHUB_RELEASE_1.0.0.13.md) | The text for the GitHub release page, with the files to attach |

## History

[`history/`](history/) keeps the project's earlier records: release notes from the first release candidates to
1.0.0.10, release reviews, test reports and research notes. They are kept for the record. Where they differ from the
documents above, the documents above are right. The [complete index](DOCUMENTATION_INDEX.md) lists every document,
old and new, with a line on each.
