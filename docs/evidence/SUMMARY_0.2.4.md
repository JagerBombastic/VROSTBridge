# Release verification — 0.2.4

- Python regression suite: 72 tests, 71 passed and one Unix-datagram test skipped
  because that socket type is unavailable in the build environment.
- Native C++ core and stream contracts passed; rebuilt Linux CLI and the
  missing-runtime background probe behaved as expected.
- Desktop DOM controls passed, including both roles, host-menu action and pending
  runtime preference restoration.
- Actual frozen Bazzite fresh install, repair, corruption detection, authenticated
  page/report, repeat launch, Stop, Quit, upgrade from the real 0.2.3 installer and
  uninstall passed. Settings, identity, diagnostics and unrelated files survived;
  managed old payloads were removed. Paths included spaces and non-ASCII text.
- Real loopback WebSocket fixture verified the vendored ALVR event reader and its
  required header. Statistics sampling and raw session/tracking exclusion passed.
- Both release ZIPs passed CRC and every payload manifest digest. The NSIS EXE
  passed archive integrity; all 534 manifest entries match the Windows payload.
  The large FFmpeg DLL was verified by streaming its full archive entry directly
  after the execution filesystem produced a truncated extracted copy.
- PE imports use Windows system/UCRT contracts; no external MinGW C++ or GCC
  runtime DLL is required. Upstream Windows runtime and bundled Python bytes
  match the verified prior payload. The new helper and current Python/UI files
  are included.

No Windows execution, physical headset/stream, privileged firewall operation or
pixel-rendered browser test was performed in this workspace. The user confirmed
first streamed Index frames on the preceding build, not this revision.
