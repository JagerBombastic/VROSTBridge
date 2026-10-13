# Contributing

Thanks for wanting to help! Bug reports, test results on hardware we don't have, and pull requests
are all welcome.

## Reporting a problem

Use the **Bug report** issue form. The most useful things to attach are the two reports: on the
client page choose **Send report to host**, then save both on the host page. Reports contain your
local network addresses and hardware names: check them before posting publicly, or say you'd rather
share them privately.

## Changing the code

- Read the [source overview](../docs/SOURCE_OVERVIEW.md) first. The [technical reference](../docs/TECHNICAL_REFERENCE.md)
  explains how the parts work together, and the [specification](../docs/PROJECT_SPEC.md) explains why things
  are the way they are.
- Run the checks a fresh clone can run before opening a pull request:

  ```sh
  python3 -m unittest discover -s tests -p 'test_*.py'
  node tests/test_desktop_ui.js
  cmake -S . -B build-tests -DVROST_BUILD_LINUX_CLIENT=OFF && cmake --build build-tests && ctest --test-dir build-tests
  ```

- Add a test for what you fix. Keep changes to ALVR as patches in `patches/`, and keep third-party
  notices: a new dependency needs its licence in `licenses/` and a line in `THIRD_PARTY.md`.
- Say in the pull request what you tested on real hardware, if anything.
- Use the name VROSTBridge, spelled that way, for the project, its programs and anything people see.

By contributing you agree that your contribution is licensed under the project's
[MIT licence](../LICENSE).
