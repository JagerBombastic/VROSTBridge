# Publishing on GitHub

How VROSTBridge is published on GitHub, written for its maintainer (JagerBombastic). The repository was set up for the first public release, 1.0.0.11; 1.0.0.12 and 1.0.0.13 followed the same way. Each new release only needs [Each release](#6-each-release), after the checks in [Last look before the push](#4-last-look-before-the-push).

## 0. What is in place

- **Licences:** VROSTBridge's own code is MIT (`LICENSE`), the logo, icons and other artwork CC BY-ND 4.0 (`brand/LICENSE.md`), with `NOTICE`, `THIRD_PARTY.md` and `licenses/` in the source and in all three packages. Contributions come in under MIT (`.github/CONTRIBUTING.md`).
- **What it builds on:** ALVR (MIT), changed only through `patches/`; OpenVR (BSD-3-Clause); FFmpeg (LGPL-2.1-or-later) in both clients, shipped with its unmodified source; the camera helper go-bsb-cams (GPL-2.0, a separate program) with its source in the Bazzite package. The Windows host carries no FFmpeg.
- **Not bundled, on purpose:** Baballonia (non-commercial licence) and VB-CABLE (donationware) are downloaded by the user from their authors, after their licence is shown.
- **For visitors:** the README as the front page, a documentation home with troubleshooting and the licences in plain words, the CHANGELOG, CONTRIBUTING, SECURITY, issue forms, a pull request template and a checks workflow; older records in `docs/history/`.
- **Checked from a clean copy:** every release's source and packages are scanned for secrets, e-mail addresses, home network addresses, personal names and the working name before delivery; the public source passes the checks workflow's tests and the documentation's link check (`tests/test_public_release.py`); the source split by part rebuilds its part (the clients' archives their native programs byte for byte).

**Still open:** a trademark search, if that matters to you (the UK and EU registers are free to search); optionally a `CODE_OF_CONDUCT.md` and, later, code signing.

**Release text:** the release notes in `docs/` are written to you as the tester. For the GitHub page use the ready-made `GITHUB_RELEASE_<version>.md`, or the version's `CHANGELOG.md` entry, written for everyone, with a link to the notes.

## 1. Account and name (done)

- **Name and logo:** **VROSTBridge**, with the logo in `brand/`. Don't use Valve's, Bigscreen's or ALVR's logos anywhere.
- **GitHub account privacy:** *Settings → Emails* → **Keep my email addresses private** and **Block command line pushes that expose my email**; commits use the private `…@users.noreply.github.com` address GitHub shows there.
- **Two-factor authentication** on the account (*Settings → Password and authentication*).

## 2. The repository (done)

`JagerBombastic/VROSTBridge`, public, created empty (no README, `.gitignore` or licence from GitHub: the source has them).

## 3. The first commit (done)

The repository started from the clean public source archive of 1.0.0.11 as one fresh commit by you, not from the private development history (whose commit messages hold working notes and another author). Later releases go in as ordinary commits by you, from the same clean source: `python3 scripts/package_public_source.py` makes it, and the repository should hold exactly its files.

## 4. Last look before the push

The archive is checked for secrets, personal details and licence gaps when it is made (see the release's build evidence). If you change anything first, check again from the folder:

```sh
git grep -n -I -i -E "password|secret|api[_-]?key|token=" -- . ':!licenses' ':!third_party'
git grep -n -E "(^|[^0-9.])(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+" -- . ':!third_party' ':!licenses'
```

Expected: only code that handles tokens (the pages' request tokens), and only the documentation's and tests' example addresses (such as `192.168.1.20`), never your own network's. The archive script can also refuse your own network's prefix without it ever being written into the source: set `VROSTBRIDGE_PRIVATE_PATTERNS` to a regular expression for it (one per line) before running `scripts/package_public_source.py`. Optional, more thorough: [gitleaks](https://github.com/gitleaks/gitleaks) (`gitleaks detect --source . --no-git`).

## 5. Repository settings

- **About** (the cog on the repository page): the description *An open-source remote PCVR bridge.*, and topics such as `vr`, `steamvr`, `pcvr`, `valve-index`, `bigscreen-beyond`, `alvr`, `bazzite`, `linux`, `windows`, `streaming`, `foveated-rendering`, `eye-tracking`.
- **Settings → General:** Issues on; Wiki and Projects off unless you want them; Discussions optional. Upload `brand/social-preview.png` (1280 × 640) as the **Social preview**.
- **Settings → Code security:** **Private vulnerability reporting** (`.github/SECURITY.md` points people there), **Secret scanning** and **Push protection**, and **Dependabot alerts**.
- **Settings → Branches** (optional for a solo project): protect `main` and require the **Checks** workflow before merging pull requests.
- **Actions:** `.github/workflows/checks.yml` runs the Python, page and native tests on every push and pull request. It builds no installers and uploads no binaries.

## 6. Each release

1. **Build and try it.** Build and check the packages ([packaging/README.md](../packaging/README.md)), then try them on your own PCs with the release notes' *How to test*. For 1.0.0.14 the far field at the same stream, the sharp region during head turns, a Public network on the rendering PC and the host's last session after a restart are still tested in software only, and the Windows client has run only under Wine, so try it on a Windows headset PC before calling it tested. Update the README's *What works today*, or mark the release a pre-release.
2. **Push the source first.** Commit the release's source to `main` as ordinary commits by you (the private address from step 1) and push it, **before** creating the release. A tag created on publish points at whatever the repository holds at that moment: if the source isn't there yet, the tag names the previous release's code and has to be moved afterwards (as happened with 1.0.0.12 and 1.0.0.13). Check that `main` holds the new version before going on.
3. **Draft the release.** **Releases → Draft a new release** → tag `v1.0.0.14` on the pushed commit, title *VROSTBridge 1.0.0.14*. Paste [GITHUB_RELEASE_1.0.0.14.md](GITHUB_RELEASE_1.0.0.14.md) as the text (earlier ones are in `history/`); without a ready-made text, use the version's [CHANGELOG](../CHANGELOG.md) entry and link its release notes. Tick **Set as a pre-release** only if you want to mark it as not yet tested on your PCs.
4. **Attach the files:** the five packages (`VROSTBridge-Host-Setup-1.0.0.14.exe`, `VROSTBridge-Host-Portable-1.0.0.14.zip`, `VROSTBridge-Bazzite-Installer-1.0.0.14.zip`, `VROSTBridge-Client-Setup-1.0.0.14.exe`, `VROSTBridge-Client-Portable-1.0.0.14.zip`), the source split by part (`VROSTBridge-1.0.0.14-host-source.zip`, `VROSTBridge-1.0.0.14-windows-client-source.zip`, `VROSTBridge-1.0.0.14-bazzite-client-source.zip`) and `SHA256SUMS.txt`, which must list every file you attach. The release kit's `upload` folder holds exactly these nine. To make them yourself: `python3 scripts/package_component_sources.py` for the split source, and `sha256sum VROSTBridge-* > SHA256SUMS.txt` in the folder with the files. GitHub adds the whole source itself (*Source code*), so the public source archive is not attached.
5. **Publish**, then open the release page and check that the tag's commit is the one you pushed and every file downloads.

**Publish releases from 1.0.0.11 on only.** Never upload the earlier builds (the release candidates and 1.0.0 to 1.0.0.10), and above all not host packages RC1 to RC3, which contained a GPL FFmpeg build without its source.

**What the packages must keep** (already done by the build): `LICENSE`, `NOTICE`, `THIRD_PARTY.md` and `licenses/` in all three; FFmpeg's source in both client packages (the Bazzite package's `payload/third_party/ffmpeg/`; the Windows client's `third_party/ffmpeg/`, with `ffmpeg-n6.1.4-source.tar.xz`, FFmpeg's `LICENSE.md` and `BUILD-Windows.txt`), and go-bsb-cams' source in the Bazzite package (`payload/third_party/`), because their licences require the source to travel with the binaries.

**Windows warning:** the Windows setups aren't code-signed, so Windows SmartScreen shows *Windows protected your PC* (the README explains *More info → Run anyway*). [SignPath Foundation](https://signpath.org/) signs open-source projects for free if you want to apply later.

## 7. Good habits

- Ask people to check their reports before attaching them: they contain local network addresses and hardware names (the bug report form says so).
- Baballonia's licence is non-commercial. VROSTBridge only downloads it when a user accepts that licence; never bundle it in a release, and don't offer VROSTBridge's eye tracking as part of a paid product or service.
- VB-CABLE is downloaded from VB-Audio by the user's own click; never bundle it either.
- Keep third-party notices up to date: a new dependency needs its licence in `licenses/` and a line in `THIRD_PARTY.md`.
- Optional: say in the README that parts of the code were written with an AI assistant, as many projects do. It doesn't change the licence.

This guide is practical advice, not legal advice.
