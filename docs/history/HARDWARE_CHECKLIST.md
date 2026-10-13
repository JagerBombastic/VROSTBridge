# Local integration and regression checks

Hardware support is a planning assumption. These checks establish observed
behaviour and diagnose a deployment; they do not block general software work.
Complete one record per headset / client / physical refresh mode. The application
does not mark these results as passed automatically.

| Item | Result or observation |
| --- | --- |
| Client model / APU / RAM | |
| Bazzite/Linux and GPU driver versions; Windows host version | |
| Dock / adapter / DP cable / USB topology | |
| Headset and physical refresh mode | |
| SteamVR and Beyond utility versions | |
| Native SteamVR works before VROSTBridge | |
| Native client starts and ALVR connects | |
| Correct stereo, scale, floor height and eye order | |
| Remote controller poses follow correctly | |
| Trigger, grip force, stick and A/B read correctly | |
| Remote left/right haptics affect the correct hand | |
| System menu and local boundary remain available | |
| Remote source-pose submission remains stable during head rotation | |
| 500 ms stall reaches neutral view and recovers | |
| Tracking loss / reacquisition recovers | |
| Stop and headset swap / restart succeeds | |
| Beyond calibrated Linux provider sends fresh gaze through host consumer | |
| Report filename and any visible error | |

Begin with a short stationary session; longer pacing/soak and room-scale tests
follow after these checks. If a deployment cannot run native SteamVR, record and isolate its
display/driver issue while general receiver implementation continues. This app cannot infer DSC functionality
from an ordinary monitor or from successful desktop preview.
