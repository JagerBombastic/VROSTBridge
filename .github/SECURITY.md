# Security

VROSTBridge is built for a **trusted home network**: two PCs on the same private network, never
exposed to the internet. Please don't port-forward it or run it on networks you don't trust.

## Reporting a vulnerability

Please report security problems privately, not in a public issue: open the repository's
**Security** tab and choose **Report a vulnerability**. Include the versions of both PCs, what an
attacker could do and how to reproduce it. You will get a reply as soon as the maintainer can;
this is a hobby project, so please allow a little time.

## What's in scope

- The companions' local pages and their actions, the pairing between the two PCs, the session and
  diagnostics endpoints, and the installers.
- The eye-camera helper's local stream (it listens on 127.0.0.1 only) and the gaze relay.

Problems in ALVR, SteamVR, Baballonia or VB-CABLE themselves are best reported to those projects;
tell us too if VROSTBridge makes them worse.
