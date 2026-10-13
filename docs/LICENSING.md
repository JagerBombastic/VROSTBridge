# Licences, in plain words

VROSTBridge's own code is free and open source under the MIT licence. Its packages also carry other people's work,
each under its own licence, and two programs it can work with are deliberately *not* included. This page explains
what that means for you as a user, if you share or fork VROSTBridge, and when contributing. The exact list of every
component, file by file, is [THIRD_PARTY.md](../THIRD_PARTY.md); the licence texts are in [`licenses/`](../licenses/).

*This is practical guidance, not legal advice.*

## VROSTBridge itself: MIT

[`LICENSE`](../LICENSE): Copyright (c) 2026 JagerBombastic, MIT licence. In short:

- **You may** use it, copy it, change it, share it and build on it, privately or commercially.
- **You must** keep the copyright line and the licence text with any copy or substantial part of the code.
- **No warranty:** it comes as it is. It's a hobby project that drives VR hardware, so read the README's safety note.

Contributions are accepted under the same MIT licence ([CONTRIBUTING.md](../.github/CONTRIBUTING.md)), so the whole project
stays under one licence.

## What the packages include from others

Every package carries `LICENSE`, `NOTICE`, `THIRD_PARTY.md` and `licenses/`, and the source code wherever a licence
asks for it. The ones that ask for something beyond keeping a notice:

| Component | Licence | Where | What it asks |
| --- | --- | --- | --- |
| [FFmpeg](https://ffmpeg.org/) 6.1.4 | LGPL 2.1 or later | Both headset-PC packages (video decoding) | Its exact source travels with the binaries (`third_party/ffmpeg/`, also in the source code), and the shared libraries can be swapped for compatible rebuilt ones. Built with decoders only and without FFmpeg's GPL or non-free options. |
| [go-bsb-cams](https://github.com/LilliaElaine/go-bsb-cams) | GPL 2 | Bazzite package (the Beyond 2e eye-camera helper, a separate program) | Its complete source, with VROSTBridge's changes marked, travels with it (`third_party/go-bsb-cams/`). |
| NVIDIA Video Codec SDK samples | NVIDIA's SDK licence | Compiled into the rendering PC's driver (from ALVR) | The notice that the software contains source code provided by NVIDIA Corporation. |
| Rust crates of ALVR and the transport | MIT, Apache-2.0, a few MPL-2.0 | The driver and both headset-PC packages | Notices for each (`licenses/rust/`); the MPL crates are used unmodified and their source is on crates.io. |

Everything else is under permissive licences that only ask you to keep their notices: ALVR (MIT), OpenVR (BSD
3-Clause), SDL2 (zlib), Wayland (MIT), stb (public domain or MIT), the Inter and Outfit fonts (SIL Open Font Licence
1.1), Python (PSF), websockets (BSD 3-Clause), NSIS (zlib) and the runtime parts built into the helper programs. The
Bazzite companion is frozen with PyInstaller, whose bootloader is GPL with an exception that lets the frozen program
keep its own licence. Microsoft's C runtime (linked into the Windows programs) and the Visual C++ Redistributable (in
the rendering PC's package) are Microsoft's code under Microsoft's own terms, not open source.

## Not included, on purpose

- **Baballonia** (Project Babble), the eye-tracking software. Its licence, the Babble Software Distribution License
  1.0, allows personal, non-commercial use only. VROSTBridge never bundles it: when you choose *Set up eye tracking*
  it shows you that licence and, only if you accept, downloads Project Babble's own unmodified release and checks it.
  So VROSTBridge's code stays MIT, but eye-tracked foveation depends on software you may use only non-commercially.
- **VB-CABLE** (VB-Audio Software), the virtual microphone on the rendering PC. Downloaded from VB-Audio's own website
  when you ask for it, under its own licence. It's donationware: if you use it, consider supporting its author.
- **SteamVR, Steam, GPU drivers and the operating systems** are prerequisites with their own terms.

## If you share or fork VROSTBridge

Sharing the official packages unchanged is fine: everything a licence requires is already inside them. If you build
and share your own (a fork, a mirror, a Linux distribution package):

- Keep `LICENSE`, `NOTICE`, `THIRD_PARTY.md` and `licenses/` with what you ship.
- Ship FFmpeg's and go-bsb-cams' source with the binaries built from them, as the official packages do. If you change
  either, include your changes.
- Don't bundle Baballonia or VB-CABLE. Let users fetch them from their authors, as VROSTBridge does.
- Give a modified version its own name and logo, so people can tell it apart from the original (see below).
- Keep the "not affiliated" statements, and don't suggest an endorsement by any hardware or software maker.

## The name and logo

The MIT licence covers the code. The logo, icons and other artwork (`brand/` and the artwork built from it) are
© 2026 JagerBombastic under Creative Commons Attribution-NoDerivatives 4.0 ([`brand/LICENSE.md`](../brand/LICENSE.md)):

- **You may** share them unchanged, for any purpose, with credit to JagerBombastic: in an article, a video, a list of
  VR tools, or a package of the unchanged app.
- **You may not** share altered versions of them.
- A modified version or fork of the software should use its own name and logo, so people can tell it apart from the
  original.

## Trademarks

VROSTBridge is an independent project. It isn't affiliated with, sponsored by or endorsed by Valve, Bigscreen, any
other hardware or platform maker, the ALVR project, Project Babble, VB-Audio or Universal Blue (Bazzite). Names such
as Valve Index, SteamVR, Steam and Bigscreen Beyond are their owners' trademarks; VROSTBridge uses them only to say
what it works with.

## Questions

If you think something here is wrong or missing, or a notice is out of date, please open an issue. Getting
licences right matters to the project.
