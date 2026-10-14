# Publishing on GitHub

A checklist for putting VROSTBridge on GitHub as a public open-source project, written for its
maintainer (JagerBombastic). Do it once; later releases only need the *Each release* part.

## 0. Is it ready? (reviewed for 1.0.0.11, 2026-10-12; done: 1.0.0.11 is published)

**Yes, as source and as a release, once you have tried 1.0.0.11 on your PCs and made the two
decisions below.** What is already in place:

- **Licences:** VROSTBridge's own code is MIT (`LICENSE`, copyright JagerBombastic), with `NOTICE`,
  `THIRD_PARTY.md` and `licenses/` in the source and in all three packages. Contributions come in
  under MIT (`.github/CONTRIBUTING.md`).
- **What it builds on:** ALVR (MIT) is changed only through `patches/`; OpenVR (BSD-3-Clause). Both
  clients decode with FFmpeg (LGPL-2.1-or-later), which ships with its unmodified source in the
  Bazzite package and in the Windows client package. The camera helper go-bsb-cams (GPL-2.0, a
  separate program, VROSTBridge's changes marked in it) ships with its source in the Bazzite package;
  the Windows client does not use it. The Windows host has carried no FFmpeg since RC4.
- **Not bundled, on purpose:** Baballonia (Babble Software Distribution License, non-commercial) and
  VB-CABLE (donationware) are downloaded by the user from their authors, after their licence is shown.
- **Clean:** every release's source and packages are scanned for secrets, e-mail addresses, home
  network addresses, personal names and the working name before delivery (for 1.0.0.11, see its
  build evidence). Publish from the public source archive, not the private history (step 3).
- **For visitors:** the README as the front page (what it is, what works today, what you need,
  getting started), a documentation home (`docs/README.md`) with troubleshooting and the licences in
  plain words (`docs/LICENSING.md`), CHANGELOG, CONTRIBUTING, SECURITY, issue forms, a pull request
  template and a checks workflow. The README carries the trademark and no-affiliation statements.
  Older records live in `docs/history/`.
- **Checked from a clean copy:** the public source passes the checks workflow's steps (the Python
  tests, the page test, the native tests), and every relative link and heading link in the
  documentation resolves (`tests/test_public_release.py`). The three source archives for the release
  page build their parts: the two client archives rebuild the released native programs byte for
  byte, and the host archive rebuilds the launcher byte for byte and carries exactly the 23 patches
  the shipped driver lists (see the build evidence).

**Still yours to decide or do:**

- [x] **The first release.** 1.0.0.11 was published as a full release on 2026-10-13 and tried on
      your PCs the same day; 1.0.0.12 answers what that session showed. For each later release,
      try it on your PCs first and update the README's *What works today*, or mark it a pre-release
      (step 6).
- [x] **The artwork licence:** CC BY-ND 4.0 for the logo, icons and other artwork (`brand/LICENSE.md`).
- [ ] **GitHub account privacy and two-factor authentication** (step 1).
- [ ] Optional: a `CODE_OF_CONDUCT.md` with a contact address you are happy to make public; code signing later (step 6).

**Good to know:** the release notes in `docs/` are written to you as the tester ("your report
shows…"). They are fine as the project's history, but for a GitHub release use the version's
`CHANGELOG.md` entry, written for everyone, and link the notes for detail.

## 1. Before you start

- [x] **Name and logo.** Done: **VROSTBridge**, with the logo in `brand/`. No trademark search or
      registration has been made; if that matters to you, the UK and EU trade mark registers are free to search. Don't use
      Valve's, Bigscreen's or ALVR's logos anywhere.
- [x] **Artwork licence.** Done: the code is MIT, and the logo, icons and other artwork are
      CC BY-ND 4.0 (`brand/LICENSE.md`): anyone may share them unchanged, with credit (articles, videos,
      packages of the unchanged app), but not alter them, and a fork uses its own name and logo. The
      README, `brand/README.md`, `THIRD_PARTY.md`, `NOTICE` and the licence page say so.
- [ ] **GitHub account privacy.** In GitHub *Settings → Emails*, turn on **Keep my email addresses
      private** and **Block command line pushes that expose my email**. Note the private address
      GitHub shows there (it looks like `12345678+JagerBombastic@users.noreply.github.com`); your
      commits will use it instead of your real email.
- [ ] **Two-factor authentication** on the account (*Settings → Password and authentication*).

## 2. Create the repository

On GitHub: **New repository** → owner JagerBombastic, a name such as `VROSTBridge`, **Public**.
Leave *Add a README*, *.gitignore* and *licence* **unticked**: the source already has them.

## 3. The first commit: a fresh start

Publish from the clean public source archive (`VROSTBridge-1.0.0.11-public-source.zip`), not from the
private development history. The private history has working notes in its commit messages and was
committed under another author; a fresh repository starts with one commit by you.

With Git installed (on Windows, *Git for Windows* gives you *Git Bash*):

```sh
cd path/to/the/extracted/folder
git init -b main
git config user.name "JagerBombastic"
git config user.email "12345678+JagerBombastic@users.noreply.github.com"   # your private address from step 1
git add .
git commit -m "First public release: VROSTBridge 1.0.0.11"
git remote add origin https://github.com/JagerBombastic/REPOSITORY.git
git push -u origin main
```

Prefer clicking? **GitHub Desktop** does the same: *File → Add local repository* → choose the
folder → *create a repository* → commit → *Publish repository* (untick *Keep this code private*).

## 4. Last look before the push

The archive was checked for secrets, personal details and licence gaps when it was made (see the
release's build evidence). If you change anything first, check again from the folder:

```sh
git grep -n -I -i -E "password|secret|api[_-]?key|token=" -- . ':!licenses' ':!third_party'
git grep -n -E "(^|[^0-9.])(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+" -- . ':!third_party' ':!licenses'
```

Expected: only code that handles tokens (the pages' request tokens), and only the documentation's and tests'
example addresses (such as `192.168.1.20`), never your own network's. The archive script can also refuse your own
network's prefix without it ever being written into the source: set `VROSTBRIDGE_PRIVATE_PATTERNS` to a regular
expression for it (one per line) before running `scripts/package_public_source.py`. Optional,
more thorough: [gitleaks](https://github.com/gitleaks/gitleaks) (`gitleaks detect --source . --no-git`).

## 5. Repository settings

- **About** (the cog on the repository page): the description *An open-source remote PCVR
  bridge.*, and topics such as
  `vr`, `steamvr`, `pcvr`, `valve-index`, `bigscreen-beyond`, `alvr`, `bazzite`, `linux`,
  `windows`, `streaming`, `foveated-rendering`, `eye-tracking`.
- **Settings → General:** Issues on; Wiki and Projects off unless you want them; Discussions
  optional (good for "does it work with my hardware?" questions). Upload
  `brand/social-preview.png` (1280 × 640) as the **Social preview** so links look nice when shared.
- **Settings → Code security:** turn on **Private vulnerability reporting** (`.github/SECURITY.md` points
  people there), **Secret scanning** and **Push protection**, and **Dependabot alerts**.
- **Settings → Branches** (optional for a solo project): protect `main` and require the
  **Checks** workflow to pass before merging pull requests.
- **Actions:** `.github/workflows/checks.yml` runs the Python, page and native tests on every
  push and pull request. It builds no installers and uploads no binaries.

## 6. Each release

1. Build and check the packages (see [packaging/README.md](../packaging/README.md)), then try
   them on your own PCs with the release notes' *How to test*. For 1.0.0.12 that is the bitrate
   after a slow start, the GPU clock held from the start and the add-on warning, all tested in
   software only. The Windows client has only been tested in software too (its tests, and its
   installer, companion and page under Wine), so try it on a Windows headset PC first. The source
   goes to the repository as ordinary commits by you (the private address from step 1), before the
   release is drafted, so its tag points at the code it was built from.
2. On GitHub: **Releases → Draft a new release** → tag `v1.0.0.12` (create it on publish), title
   *VROSTBridge 1.0.0.12*. For the text, paste [GITHUB_RELEASE_1.0.0.12.md](GITHUB_RELEASE_1.0.0.12.md)
   (ready-made for this release; 1.0.0.11's is in `history/`). Without a ready-made text, use the
   version's entry in [CHANGELOG.md](../CHANGELOG.md) (written for everyone) and link its release notes.
   Tick **Set as a pre-release** only if you want to mark it as not yet tested on your PCs.
3. Attach the five packages (`VROSTBridge-Host-Setup-1.0.0.12.exe`,
   `VROSTBridge-Host-Portable-1.0.0.12.zip`, `VROSTBridge-Bazzite-Installer-1.0.0.12.zip`,
   `VROSTBridge-Client-Setup-1.0.0.12.exe`, `VROSTBridge-Client-Portable-1.0.0.12.zip`), the source
   split by part (`VROSTBridge-1.0.0.12-host-source.zip`, `VROSTBridge-1.0.0.12-windows-client-source.zip`,
   `VROSTBridge-1.0.0.12-bazzite-client-source.zip`) and `SHA256SUMS.txt`, which must list every
   file you attach. The release kit's `upload` folder holds exactly these nine. For a later release,
   make the split source with `python3 scripts/package_component_sources.py` and the checksums in
   the folder with the files: `sha256sum VROSTBridge-* > SHA256SUMS.txt`. GitHub adds the whole
   source itself (*Source code*), so the public source archive is not attached.
4. Publish.

**Never upload host packages RC1 to RC3:** they contained a GPL FFmpeg build without its source.
Release 1.0.0.11 was the first published; publish later releases only, never the earlier builds
(the release candidates and 1.0.0 to 1.0.0.10).

**What the packages must keep** (already done by the build): `LICENSE`, `NOTICE`,
`THIRD_PARTY.md` and `licenses/` in all three; FFmpeg's source in both client packages (the
Bazzite package's `payload/third_party/ffmpeg/`; the Windows client's `third_party/ffmpeg/`, which
holds `ffmpeg-n6.1.4-source.tar.xz`, FFmpeg's `LICENSE.md` and the Windows build notes
`BUILD-Windows.txt`), and go-bsb-cams' source in the Bazzite package (`payload/third_party/`),
because their licences require the source to travel with the binaries.

**Windows warning:** the Windows setups aren't code-signed, so Windows SmartScreen shows *Windows
protected your PC* (the README explains *More info → Run anyway*). Signing certificates cost money;
[SignPath Foundation](https://signpath.org/) signs open-source projects for free if you want to
apply later.

## 7. Good habits

- Ask people to check their reports before attaching them: they contain local network addresses
  and hardware names (the bug report form says so).
- Baballonia's licence is non-commercial. VROSTBridge only downloads it when a user accepts that
  licence; never bundle it in a release, and don't offer VROSTBridge's eye tracking as part of a
  paid product or service.
- VB-CABLE is downloaded from VB-Audio by the user's own click; never bundle it either.
- Keep third-party notices up to date: a new dependency needs its licence in `licenses/` and a line
  in `THIRD_PARTY.md`.
- Optional: say in the README that parts of the code were written with an AI assistant, as many
  projects do. It doesn't change the licence.
- Optional: a `CODE_OF_CONDUCT.md` (GitHub offers a template under *Insights → Community
  standards*), with a contact address you are happy to make public.

This guide is practical advice, not legal advice.
