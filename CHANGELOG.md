# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/akshaytp1571/windows-space-analyzer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/akshaytp1571/windows-space-analyzer/releases/tag/v1.0.0
