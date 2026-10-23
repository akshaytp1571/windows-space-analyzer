# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] — 2026-10-23

Production-readiness pass. The v1.0.0 executable was built before the safety
fixes below landed, so **v1.0.0 shipped a binary that did not contain them**. Use
1.0.1.

### Added

- **Crash logging.** The app now writes to
  `%LOCALAPPDATA%\WindowsSpaceAnalyzer\space-analyzer.log` (rotating, 512 KB × 3).
  A windowed build has no console, so before this an unexpected exception vanished
  with no trace at all. Startup, elevation state and every error are recorded.
- **An uncaught-exception handler.** An unexpected error is logged, and the user
  gets a dialog naming the log file with the traceback available under *Details*,
  instead of the window simply dying.
- **An application icon**, painted at seven sizes (16–256 px), used for the window,
  the taskbar and Alt-Tab. Previously the exe and window showed the generic
  Python/Qt icon.
- **The version is visible.** The window title is now
  `Windows Space Analyzer <version>`, so a user can say which build they ran.

### Fixed

- **`ActionReport.requires_restart` is now actually set.** It was declared and
  rendered by the report dialog but no code path ever set it to `True`. Removing
  `Windows.old` genuinely needs a restart when Windows still holds handles, so that
  case now sets it and the report says so.

### Fixed in 1.0.0 (documented here for completeness)

- **`Windows.old` is now path-checked.** `CLEAN_WINDOWS_OLD` previously reached the
  registry and `cleanmgr` without validating its target. It cannot use
  `validate_target` because `Windows.old` is itself a top-level folder, which that
  function refuses by design, so it now uses `safety.validate_windows_old`: the
  path must be exactly this PC's `Windows.old`, not a link, and still a folder. The
  check runs before any system change.
- **Re-validated before the move.** `delete_to_recycle` now validates each path a
  second time immediately before sending it to the Recycle Bin, so a file that
  became a link, or that now resolves elsewhere, between sizing and the move is
  dropped and reported instead of moved. This narrows the TOCTOU window to the gap
  between the second check and the `send2trash` call.

### Changed

- Documentation no longer describes either of the above as an open gap.
  `docs/ARCHITECTURE.md` safety rules 1, 4, 9 and 11 are scoped to what the code
  actually enforces.
- The v1.0.0 release now ships `WindowsSpaceAnalyzer.exe` (49.1 MB), built and
  launched to verify it starts. Its SHA-256 is published in the README.

### Tests

- Added `tests/test_safety.py::ValidateWindowsOldTests` (7 tests) covering every
  refusal `validate_windows_old` makes.
- Added a TOCTOU regression test that swaps a path to a link after sizing and
  asserts it never reaches the recycler. Verified it fails when the re-check is
  removed, so it guards the fix rather than merely passing alongside it.
- Replaced the timing-based `_wait_idle` helper in `tests/test_suggestions_page.py`
  with one that waits on the worker itself. The old helper used a fixed idle count
  and could race a page that chains a second worker from the first one's signal,
  which made `test_accepting_the_bin_offer_empties_the_bin` fail intermittently.

## [1.0.0] — 2026-10-22

First public release.

### Added

**Scanning and analysis**

- Background scan of every fixed drive, cancellable, reporting bytes read, file
  count, folder count and elapsed time
- Honest accounting of what could not be read: unreadable system areas are shown
  separately rather than being silently excluded
- Folder tree per drive, largest first, expandable, with a share column
- Sixteen cleanup rules across seven categories, each with a risk level, an
  impact statement, a regeneration answer, and the warnings that apply
- Duplicate detection by content hash, grouped, with the oldest copy marked to
  keep and a guarantee that a group can never lose every copy
- Large-file inventory with a size filter, a text filter and sortable columns

**Safety**

- `safety.validate_target` as a single gate for every deletion path, refusing
  drive roots, top-level folders, the profile folder and seven named personal
  folders, reparse points, paths outside the item's allowed roots, and protected
  system locations
- Recycle Bin as the default destination, via `send2trash`
- Permanent actions confirmed with an explicit cannot-be-undone warning, with No
  as the default button
- Administrator-only items never pre-ticked and refused without elevation
- No recursive deletion anywhere in the package
- Six independent layers of reparse-point handling across scanner, cleaner,
  recycler, duplicates and the rules

**Interface**

- Instrument-panel design: blueprint grid backdrop, square hairline panels,
  monospace numerals, a single acid-lime accent
- Sixteen vector icons painted in code, so there are no image assets
- `TickGauge` with a tick scale and needle, and a `SegmentBar` that splits a drive
  into read, unread and free
- Scrolling page bodies, so a short window scrolls instead of crushing a panel
- Status bar showing elevation state with a restart-as-administrator action
- Reports dialog for what a cleanup did, including per-path failures

**Project**

- 254 tests covering the engine and the interface, runnable on Windows and Linux
- `run_app.bat`, `run_tests.bat` and `build_exe.bat` for a no-setup start
- Single-file `.exe` build via PyInstaller
- Complete documentation: user guide, safety model, architecture contract and
  development guide

[Unreleased]: https://github.com/akshaytp1571/windows-space-analyzer/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/akshaytp1571/windows-space-analyzer/releases/tag/v1.0.1
[1.0.0]: https://github.com/akshaytp1571/windows-space-analyzer/releases/tag/v1.0.0
