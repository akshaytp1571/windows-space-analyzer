# Windows Space Analyzer: Architecture Contract

This file is the contract between all modules. Every builder must implement these
signatures exactly. If a change is needed, report it to the lead instead of changing
another owner's file.

## Goals

Scan the internal drives, show what uses the space, suggest what can be removed together
with the impact of removing it, and let the user confirm each action. The app never
deletes anything the user has not confirmed.

Target: Windows 10/11, Python 3.10+, PySide6 6.x, send2trash. The engine must import and
run its tests on Linux too, so Windows-only code is guarded.

## Safety rules (non-negotiable)

1. Every path-bearing deletion goes through `cleaner.py`, which calls `safety.validate_target()` for each path before touching it. Only `cleaner.py` and `recycle.py` may delete anything. **Scope:** this covers `DELETE_PATHS` and `CLEAN_UPDATE_CACHE`, which are the only actions that carry paths. The four pathless actions (`EMPTY_RECYCLE_BIN`, `DISABLE_HIBERNATION`, `DELETE_RESTORE_POINTS`, `CLEAN_WINDOWS_OLD`) never call `validate_target`; they are bounded by rule 7 plus a fixed argument list and a scope derived from `system.system_drive()` rather than from item data. `docs/SAFETY.md` states this to users.
2. User-selected deletions go to the Recycle Bin via send2trash. Space is only freed after the bin is emptied, and the UI says so.
3. Permanent deletion or Windows-managed cleanup is used only for system items, and each such item lists it in `CleanupItem.warnings`.
4. Never follow symlinks, junctions or other reparse points while walking or deleting. Six independent layers enforce this: `system.is_link`, `validate_target` check 5, `system.norm` resolving paths before containment tests, `scanner._skip_reason`, `recycle._is_real_dir`, and `follow_symlinks=False` throughout `duplicates` and the rules. **Known gap:** there is no re-check between validation and the `send2trash` call, so a narrow TOCTOU window exists.
5. Protected system locations (see `system.protected_roots()`) are refused unless they lie inside `system.system_allowlist()`.
6. Nothing is pre-selected unless `risk == Risk.SAFE` and `requires_admin` is False. Admin-only items are never pre-selected, even when the app runs as administrator.
7. Admin-only actions never run without admin rights. The UI disables them and shows the note.
8. A drive root can never be a deletion target.
9. Actions other than recycling (`CLEAN_WINDOWS_OLD`, `DELETE_RESTORE_POINTS`, `DISABLE_HIBERNATION`, `EMPTY_RECYCLE_BIN`) are permanent. The UI must confirm them with `destructive=True` and say so in the confirmation text. **This is a UI convention, not an engine guarantee:** `cleaner.perform` has no `destructive` parameter and cannot tell whether a confirmation happened. Any new caller of `perform` must supply its own confirmation.
10. A folder that sits directly under a drive root, the user profile folder, or a well-known user folder (Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive) can never be a deletion target. Files inside those folders can be. `validate_target` enforces this. The rule applies to every path passed to `delete_to_recycle` or `safety.validate_target`. `oldwin.windows_old` is removed by `cleanmgr` and never passes through those functions.
11. Folder-children items are only created for explicitly known locations (see the temp rules below). A misconfigured environment variable must never widen what gets deleted. **Enforced for `temp.user` and `temp.system` only**, which guard with `_inside()` against `%LOCALAPPDATA%` and the Windows directory. Five other families read their base folder from the environment without that guard (browser and thumbnail caches from `%LOCALAPPDATA%`, WER reports from `%ProgramData%`, logs and update cache from the Windows directory); they are bounded by `allowed_roots` and the protected-path check instead.
12. No recursive or in-place deletion. Never call `shutil.rmtree`, `os.rmdir`, `os.remove` or `os.unlink` on user or system content. Content leaves the disk only through `recycle.send_to_recycle_bin` (called from `cleaner`) and the named system commands (`cleanmgr`, `vssadmin`, `powercfg`, and `recycle.empty_recycle_bin`). A Recycle Bin move stays on the same drive, so it does not copy or delete the contents of junction targets.

## Module map and owners

| Path | Owner | Purpose |
|---|---|---|
| space_analyzer/models.py | lead (done) | Shared dataclasses and enums |
| space_analyzer/util.py | lead (done) | format_bytes, format_count, format_timestamp, format_age |
| space_analyzer/system.py | A1 | Windows environment, admin, drives, protected paths, command runner |
| space_analyzer/scanner.py | A2 | Disk scanner and measure_path |
| space_analyzer/duplicates.py | A1 | Duplicate file finder |
| space_analyzer/recycle.py | B1 | Recycle Bin send, empty and info |
| space_analyzer/safety.py | B1 | validate_target |
| space_analyzer/cleaner.py | B1 | delete_to_recycle and perform (dispatch by ActionKind) |
| space_analyzer/analyzer.py | B2 | build_suggestions (rules, measuring, sorting) |
| space_analyzer/rules.py | B2 | Rule implementations used by analyzer |
| space_analyzer/ui/state.py | C1 | AppState (shared Qt state) |
| space_analyzer/ui/workers.py | C1 | Worker thread wrapper |
| space_analyzer/ui/theme.py | C1 | Colors, labels, stylesheet |
| space_analyzer/ui/app.py | C1 | run() entry point |
| space_analyzer/ui/main_window.py | C2 | MainWindow with navigation and status bar |
| space_analyzer/ui/pages/overview.py | C2 | Drives, folder breakdown, scan controls |
| space_analyzer/ui/dialogs.py | C3 | confirm() and show_reports() |
| space_analyzer/ui/pages/suggestions.py | C3 | Suggestions list and cleanup flow |
| space_analyzer/ui/pages/large_files.py | C4 | Large files list and Recycle Bin move |
| space_analyzer/ui/pages/duplicates.py | C4 | Duplicate groups and Recycle Bin move |
| main.py | C1 | Launcher: `from space_analyzer.ui.app import run` |
| requirements.txt, build_exe.bat, run_app.bat, README.md, .gitignore | D | Packaging and docs. The exe must not request admin at launch, so no --uac-admin. The app runs as a normal user and offers Restart as administrator. |
| tests/test_*.py | each owner tests their own modules | unittest based |

## Public API (exact signatures)

### models.py (lead, done)
Enums `Risk` (SAFE, LOW, ADVANCED, INFO), `Category`, `ActionKind`. Dataclasses `DriveInfo`, `ScanNode`, `FileEntry`, `SkippedPath`, `ScanResult`, `ScanProgress`, `DuplicateGroup`, `CleanupItem`, `ActionFailure`, `ActionReport`. `CATEGORY_ORDER` tuple. Do not rename fields.

### util.py (lead, done)
- `format_bytes(n: int | float | None) -> str` (None gives "unknown")
- `format_count(n: int) -> str`
- `format_timestamp(ts: float | None) -> str`
- `format_age(ts: float | None, now: float | None = None) -> str`

### system.py (A)
- `IS_WINDOWS: bool`
- `run_command(args: list[str], timeout: float = 120) -> tuple[int, str, str]`. Never raises. Returns (-1, "", message) on timeout or missing executable. Hides the console window on Windows (creationflags 0x08000000).
- `is_admin() -> bool`. Uses `ctypes.windll.shell32.IsUserAnAdmin()`; False when not Windows.
- `relaunch_as_admin() -> bool`. ShellExecuteW with verb "runas" and file = `sys.executable`. Parameters are empty when `sys.frozen` is set. Otherwise they are the quoted absolute path of `os.path.abspath(sys.argv[0])`. Set the working directory to the project folder. Returns True when the new process started. The caller then exits.
- `fixed_drives() -> list[str]`. Windows: GetLogicalDrives plus GetDriveTypeW == 3 (DRIVE_FIXED), e.g. ["C:\\", "D:\\"]. Non-Windows: ["/"].
- `drive_info(root: str) -> DriveInfo`
- `system_drive() -> str`. %SystemDrive% plus "\\", for example "C:\\".
- `windows_dir() -> str`. %SystemRoot%.
- `user_profile() -> str | None`, `local_app_data() -> str | None`, `roaming_app_data() -> str | None`, `program_data() -> str | None`, `user_temp_dir() -> str | None` (%TEMP%), `system_temp_dir() -> str | None` (windows_dir()\Temp)
- `protected_roots() -> list[str]`. Built from `system_drive()`, `fixed_drives()` and the list below.
- `system_allowlist() -> list[str]`. Built from `system_drive()`.
- `norm(path: str) -> str`. abspath + realpath + normcase.
- `is_within(path: str, root: str) -> bool`. Inclusive: a path equals its root or lies inside it. Both sides are normalized.
- `is_link(path: str) -> bool`. True for symlinks, junctions and any reparse point (FILE_ATTRIBUTE_REPARSE_POINT 0x400 on Windows).
- `is_protected_path(path: str, roots: list[str] | None = None, allowlist: list[str] | None = None) -> bool`. True when the path is inside a protected root and not inside an allowlist entry. Pass precomputed lists to avoid repeated work.
- `open_in_explorer(path: str) -> None`. Windows: for a folder, `os.startfile(folder)`. For a file, run `explorer /select,<path>` so the file is shown but never launched. Other OS: no-op.
- `count_restore_points(drive_root: str) -> int | None`. Strips the trailing backslash from `drive_root` (so `C:\` becomes `C:`), then runs `vssadmin list shadows /for=<drive>` through run_command with timeout 300 and counts the "Shadow Copy ID" lines. Returns None when the command fails or the output cannot be read. Other OS: None.

Protected roots, where `sd` = system_drive(): `sd\Windows`, `sd\Program Files`, `sd\Program Files (x86)`, `sd\ProgramData`, `sd\Windows.old`, `sd\Recovery`, `sd\Boot`, `sd\EFI`, `sd\$WINDOWS.~BT`, `sd\$WinREAgent`, `sd\pagefile.sys`, `sd\hiberfil.sys`, `sd\swapfile.sys`. For every fixed drive: `<drive>\System Volume Information` and `<drive>\$Recycle.Bin`.

System allowlist (cleanup allowed inside protected areas): `sd\Windows\Temp`, `sd\Windows\SoftwareDistribution\Download`, `sd\Windows\Logs`, `sd\Windows\Minidump`, `sd\Windows\MEMORY.DMP`, `sd\ProgramData\Microsoft\Windows\WER\ReportArchive`, `sd\ProgramData\Microsoft\Windows\WER\ReportQueue`.

Non-Windows fallbacks: `system_drive()` returns "/" and `windows_dir()` returns "/Windows". All functions read environment variables at call time (no caching). Other modules must call these as `system.x()`, never `from space_analyzer.system import x`, so tests can patch them.

### scanner.py (A)

The scanner imports only models and util. It does its own reparse-point check, so it does not depend on system.py.
- `scan(root: str, *, max_depth: int = 8, max_children: int = 200, top_files: int = 500, progress: Callable[[ScanProgress], None] | None = None, cancel: threading.Event | None = None) -> ScanResult`
  - Uses a thread pool with os.scandir. Uses st_size. Cloud-only files (OFFLINE 0x1000 or RECALL_ON_DATA_ACCESS 0x400000) count as 0 bytes but still count as files.
  - Does not follow links. Links are recorded in `skipped` with reason "link or junction".
  - Unreadable entries are recorded in `skipped` with a reason. `error_count` counts all of them. `skipped` is capped at 5000 entries.
  - `root_node` holds nodes down to `max_depth`. Each node keeps at most `max_children` children, largest first. Directories deeper than `max_depth` are folded into their depth-`max_depth` ancestor while scanning, so memory stays bounded. The rest are rolled into one synthetic node with path "" and name "N smaller folders", so totals stay exact.
  - `largest_files` holds the top `top_files` files by size, largest first.
  - `progress` is throttled to at most 4 calls per second. `cancel` is checked between directories; on cancel return partial results with `cancelled=True`.
- `measure_path(path: str, *, cancel: threading.Event | None = None) -> tuple[int, int]`. Returns (bytes, file_count). A file gives its own size. A directory is walked recursively. Links are not followed. Missing paths give (0, 0). Errors are ignored.

### duplicates.py (A)
- `find_duplicates(root: str, *, min_size: int = 1_048_576, max_groups: int = 500, progress: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> list[DuplicateGroup]`
  - Walk the tree, skipping links, cloud-only files and protected paths. Group by size, then by SHA-256 of the first 64 KB, then by full SHA-256.
  - `paths` are sorted oldest-modified first. Result is sorted by `reclaimable_bytes` descending.

### recycle.py (B)
- `send_to_recycle_bin(paths: list[str]) -> tuple[list[str], list[ActionFailure]]`. Returns (sent, failed). Imports send2trash lazily. Sends in batches, and falls back to one path at a time when a batch fails.
- `empty_recycle_bin() -> bool`. Windows: `SHEmptyRecycleBinW(None, None, 0x7)` through ctypes. Returns True when the bin is empty afterwards.
- `recycle_bin_info() -> tuple[int, int]`. Returns (bytes, item count) across fixed drives, from `<drive>\$Recycle.Bin`. Counts `$R` files as items. Errors are ignored.

### safety.py (B)
- `validate_target(path: str, allowed_roots: list[str], *, protected: list[str] | None = None, allowlist: list[str] | None = None) -> str | None`. `cleaner` computes `protected` (system.protected_roots()) and `allowlist` (system.system_allowlist()) once per call and passes them in; when omitted they are computed. Returns None when the target is allowed, otherwise a short reason. Checks in order: non-empty; not a drive root; not a folder directly under a drive root; not the user profile folder or a well-known user folder (the folder itself, not its contents); not `system.is_link`; within or equal to an allowed root; not `system.is_protected_path`. The folder checks use `os.path.isdir`, so single files are not affected.

### cleaner.py (B)
- `delete_to_recycle(paths: list[str], allowed_roots: list[str], *, dry_run: bool = False, recycler: Callable[[list[str]], tuple[list[str], list[ActionFailure]]] | None = None, progress: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> ActionReport`
  - `action="recycle"`. For each path: skip with a message if `os.path.lexists` is False (already gone). Validate. Measure the size with `scanner.measure_path`. Send the valid paths through `recycler`, which defaults to `recycle.send_to_recycle_bin`.
  - `dry_run=True` runs validation and measuring only, and reports `dry_run=True`.
- `perform(item: CleanupItem, *, admin: bool, dry_run: bool = False, progress: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> ActionReport`
  - Dispatches on `item.action`:
    - `NONE`: no-op report.
    - `DELETE_PATHS`: `delete_to_recycle(item.paths, item.allowed_roots)`.
    - `EMPTY_RECYCLE_BIN`: read `recycle.recycle_bin_info()` before and after `recycle.empty_recycle_bin()`, and report the difference as `bytes_removed`.
    - `DISABLE_HIBERNATION`: `powercfg /hibernate off`.
    - `DELETE_RESTORE_POINTS`: keeps the newest `action_params.get("keep_newest", 1)`, only on the system drive. Loop: while `system.count_restore_points(system_drive())` is greater than `keep_newest` and fewer than 50 deletions have run, run `vssadmin delete shadows /for=<system drive> /oldest /quiet` through `system.run_command`, then recount. Stop when the count is at or below `keep_newest`, when a deletion fails, or when the count is None. Never delete more copies than that.
    - `CLEAN_WINDOWS_OLD`: set `StateFlags0065` to 0 on every subkey of `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches`, then set it to 2 on `Previous Installations` only, then run `cleanmgr /sagerun:65` with timeout 3600. This way only the Windows.old category runs.
    - `CLEAN_UPDATE_CACHE`: stop `wuauserv` and `bits`, move the Download folder's children to the Recycle Bin through `delete_to_recycle` (allowed_roots = [Download folder]), then restart the services in a `finally` block.
  - Any item with `requires_admin=True` returns a failure "Requires administrator" when `admin` is False, whatever its action kind. This includes temp.system and logs.wer, which use DELETE_PATHS.
  - ActionReport.action uses the ActionKind value.

### analyzer.py (B)
- `build_suggestions(*, admin: bool | None = None, progress: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> list[CleanupItem]`
  - `admin=None` means `system.is_admin()`.
  - Runs every rule in rules.py. Measures sizes. Drops items with 0 bytes, except restore points, which may have unknown size. For DELETE_PATHS items only, every path is checked with `safety.validate_target`. Failing paths are removed and a warning is added, and items left with no valid paths are dropped. Size-only items (hiberfil.sys, pagefile.sys) keep their paths for display and are never deleted through them.
  - Items that require admin when `admin` is False keep `note="Requires administrator"` and `default_selected=False`.
  - `default_selected = risk == SAFE and not requires_admin`. This gives exactly five pre-selected items: temp.user, cache.chrome, cache.edge, cache.firefox and cache.thumbnails.
  - Sorted by `CATEGORY_ORDER`, then by size descending.

### Rules (B, inside rules.py)
Item ids and definitions. `paths` are the deletion targets. `allowed_roots` is the folder whose children are targeted (folder-children items), the exact path itself (single-target items), or the drive root for user-chosen files on the Large files and Duplicates pages. A target must lie inside one of its allowed roots. Safety rule 10 applies to the target path, not to `allowed_roots`.

| item_id | Title | Category | Paths | Risk | Admin | Action |
|---|---|---|---|---|---|---|
| temp.user | Your temporary files | TEMP | Each child of user_temp_dir() | SAFE | no | DELETE_PATHS |
| temp.system | Windows temporary files | TEMP | Each child of system_temp_dir() | SAFE | yes | DELETE_PATHS |
| cache.chrome | Chrome cache | CACHE | Cache, Code Cache and GPUCache folders in each profile under %LOCALAPPDATA%\Google\Chrome\User Data | SAFE | no | DELETE_PATHS |
| cache.edge | Edge cache | CACHE | Same folders under %LOCALAPPDATA%\Microsoft\Edge\User Data | SAFE | no | DELETE_PATHS |
| cache.firefox | Firefox cache | CACHE | cache2 folder in each profile under %LOCALAPPDATA%\Mozilla\Firefox\Profiles | SAFE | no | DELETE_PATHS |
| cache.thumbnails | Thumbnail and icon cache | CACHE | thumbcache_*.db and iconcache_*.db in %LOCALAPPDATA%\Microsoft\Windows\Explorer | SAFE | no | DELETE_PATHS |
| logs.crashdumps | Your crash dumps and error reports | LOGS | %LOCALAPPDATA%\CrashDumps and %LOCALAPPDATA%\Microsoft\Windows\WER | LOW | no | DELETE_PATHS |
| logs.wer | System error reports | LOGS | ReportArchive and ReportQueue under %ProgramData%\Microsoft\Windows\WER | LOW | yes | DELETE_PATHS |
| logs.windows | Windows log files | LOGS | %SystemRoot%\Logs | LOW | yes | DELETE_PATHS |
| logs.memdump | Memory dump and minidumps | LOGS | %SystemRoot%\MEMORY.DMP and %SystemRoot%\Minidump | LOW | yes | DELETE_PATHS |
| updates.download | Windows Update download cache | OLD_WINDOWS | Children of %SystemRoot%\SoftwareDistribution\Download | SAFE | yes | CLEAN_UPDATE_CACHE |
| oldwin.windows_old | Previous Windows installation | OLD_WINDOWS | %SystemDrive%\Windows.old | ADVANCED | yes | CLEAN_WINDOWS_OLD |
| recycle.bin | Recycle Bin | RECYCLE | none | LOW | no | EMPTY_RECYCLE_BIN |
| hibernate.file | Hibernation file (hiberfil.sys) | HIBERNATION_RESTORE | %SystemDrive%\hiberfil.sys (size only) | ADVANCED | yes | DISABLE_HIBERNATION |
| restore.points | System Restore points | HIBERNATION_RESTORE | none (size unknown) | ADVANCED | yes | DELETE_RESTORE_POINTS, action_params {"keep_newest": 1} |
| system.pagefile | Page file (pagefile.sys) | SYSTEM_FILES | %SystemDrive%\pagefile.sys (size only) | INFO | no | NONE |

Rules:
- Every item sets `description` (one plain sentence saying what the files are) and `regenerates`. Use "Yes, new ones are created as needed" for temp files, logs and error reports (old copies are not recovered), "Yes, rebuilt automatically" for caches, "Yes, Windows Update downloads them again when needed" for the update cache, "Yes, when hibernation is turned back on" for hiberfil.sys, "Recreated automatically" for pagefile.sys, and "No" for Windows.old, restore points and the Recycle Bin. `size_known` is False only when the size could not be measured.
- Browser cache and thumbnail items are only created when the folder or file exists. Browser cache items carry the warning "Close the browser first. If it is still open, Windows may remove only part of its cache."
- Folder-based log and report items use the immediate children of the folder as `paths`, never the folder itself. `logs.memdump` uses the MEMORY.DMP file plus the children of Minidump.
- `temp.user` and `temp.system` use the immediate children of the folder, not the folder itself. `temp.user` is only created when `user_temp_dir()` lies inside `local_app_data()` and is not equal to it. `temp.system` is only created when `system_temp_dir()` lies inside `windows_dir()` and is not equal to it. Otherwise the item is skipped.
- `default_selected` follows the analyzer rule above. The only pre-selected items are temp.user, cache.chrome, cache.edge, cache.firefox and cache.thumbnails.
- `updates.download` and `oldwin.windows_old` are shown only when the folder exists. `restore.points` is shown when admin is True only if `system.count_restore_points(system_drive())` returns a number greater than 1 (the newest copy is always kept). When it returns None, the item is not shown. When admin is False the count is not attempted, and the item is shown with note "Requires administrator to check restore points".
- `hibernate.file` is shown only when hiberfil.sys exists.
- `system.pagefile` is shown only when pagefile.sys exists. It is information only: no checkbox and no action.
- The `warnings` list for `updates.download` must say the files go to the Recycle Bin, that the bin must be emptied to free space, and that Windows Update is paused briefly. For `oldwin.windows_old` it must say Windows removes them permanently with its own tool, not the Recycle Bin. For `restore.points` it must say the copies are removed permanently, you lose the ability to roll back to them, and shadow copies made by backup software on the same drive may also be removed.

Impact text must be plain English and must say what the user loses. Required wording for two items: `hibernate.file` says "Hibernate and Fast Startup stop working. Normal sleep still works, and you can turn hibernation back on with powercfg /hibernate on." Every `logs.*` item says "Only old log and report files are removed. Windows and apps create new ones when needed." For example, `oldwin.windows_old`: "You can no longer roll back to the previous Windows version. Windows also deletes this folder by itself after about 10 days."

### UI (C1 to C4)
- `ui/state.py` (C1): `AppState(admin: bool = False)`, a QObject. Signals `scans_changed()` and `suggestions_changed()`. Attributes `admin: bool`, `scans: dict[str, ScanResult]` keyed by drive root, `suggestions: list[CleanupItem]`. Methods `set_scan(result: ScanResult)`, `clear_scans()`, `set_suggestions(items)`.
- `ui/workers.py` (C1): `Worker(QThread)` with signals `progressed(object)`, `succeeded(object)`, `errored(str)`. Constructor `Worker(func, *args, **kwargs)`. The worker passes `progress=` and `cancel=` keyword arguments only when `func` accepts them (checked with inspect). Method `cancel()`.
- `ui/theme.py` (C1): `RISK_LABEL: dict[Risk, str]` ("Safe", "Low risk", "Advanced", "Info"), `RISK_COLOR: dict[Risk, str]`, `apply_theme(app)`.
- `ui/app.py` (C1): `run() -> int`. `main.py` calls it. run() applies the theme, creates `AppState(admin=system.is_admin())`, and passes it to `MainWindow(state)`.
- `ui/main_window.py` (C2): `MainWindow(state: AppState, parent=None)`, a QMainWindow. `app.run()` constructs it as `MainWindow(state)`. Navigation pages Overview, Suggestions, Large files, Duplicates. Status bar shows admin status and an "Restart as administrator" button.
- `ui/pages/overview.py` (C2): `OverviewPage(state: AppState, parent=None)`. Scans all fixed drives with one Worker (`scanner.scan` per drive). Shows drive usage bars and an expandable folder tree built from `root_node` (largest first) for the selected drive, with an Open in Explorer action. Shows "Not accounted for" = used - total_size. Nodes with an empty path (the summary row) are shown but cannot be opened. The scanner progress callback receives `ScanProgress` objects, so convert them to text before updating the status bar.
- `ui/dialogs.py` (C3): `confirm(parent, title: str, text: str, details: list[str] | None = None, *, destructive: bool = False) -> bool` and `show_reports(parent, title: str, reports: list[ActionReport]) -> None`.
- `ui/pages/suggestions.py` (C3): `SuggestionsPage(state: AppState, parent=None)`. Analyze button runs `analyzer.build_suggestions` in a Worker. Tree grouped by category with checkboxes. Shows risk badge, size, impact text, note. Items with `requires_admin and not state.admin` cannot be checked. Items whose action is NONE have no checkbox. Sizes are shown with `format_bytes(item.size_bytes if item.size_known else None)`. Clean button confirms, then runs `cleaner.perform` for each item (EMPTY_RECYCLE_BIN items always run last), then shows reports. If anything went to the Recycle Bin, it offers to empty the bin. The confirmation lists every selected item with its size and warnings, and passes `destructive=True` when any selected item uses EMPTY_RECYCLE_BIN, DISABLE_HIBERNATION, DELETE_RESTORE_POINTS or CLEAN_WINDOWS_OLD. The EMPTY_RECYCLE_BIN confirmation must say: "This permanently deletes everything in the Recycle Bin, including files you put there yourself."
- `ui/pages/large_files.py` (C4): `LargeFilesPage(state: AppState, parent=None)`. Uses `state.scans[drive].largest_files`, hides `system.is_protected_path` entries, and has a minimum-size filter. Files under any `AppData` folder add this warning to the confirmation: "Some apps keep data here. Deleting it can reset those apps." Moves selected files with `cleaner.delete_to_recycle(paths, allowed_roots=[drive_root])` in a Worker. Opens the folder with `system.open_in_explorer`. A drive with no scan shows a message that says to run a scan on the Overview page first.
- `ui/pages/duplicates.py` (C4): `DuplicatesPage(state: AppState, parent=None)`. A drive combo box (default: system drive) selects the root. Runs `duplicates.find_duplicates` in a Worker with a Cancel button, and warns that a whole-drive search can take several minutes. Groups show size and reclaimable space. "Keep oldest" checks every copy except paths[0]. The UI must never allow every copy in one group to be checked: if a click would check the last unchecked copy, refuse it and show a message. Moves selected copies to the Recycle Bin with `cleaner.delete_to_recycle`.

## Conventions

- `from __future__ import annotations` in every module. Python 3.10-compatible syntax. Type hints on public functions.
- Docstrings are short and say what the function does and what it returns.
- Windows-only code (ctypes.windll, winreg, msvcrt, os.startfile) lives inside functions or behind `IS_WINDOWS`. Modules must import on Linux. Mark each Windows-only block with the comment `# Windows-only: not runnable in this sandbox`.
- Use only the standard library plus PySide6 and send2trash. No other dependencies.
- PySide6 6.x requires fully qualified enums, for example `Qt.ItemDataRole.UserRole`, `Qt.CheckState.Checked`, `Qt.AlignmentFlag.AlignLeft`, `QMessageBox.StandardButton.Yes`. Never use the bare short forms.
- Engine modules use absolute imports such as `from space_analyzer import system`, and call functions as `system.is_link(...)`. Never write `from space_analyzer.system import is_link`, so tests can patch `system.*`.
- Logging and errors: functions return failures in their result objects. They do not raise for expected conditions such as access denied or a missing file.
- Human-readable sizes come only from `space_analyzer.util`.
- Tests: `tests/test_<module>.py`, using unittest. Patch `system.*` functions to point at temp directories. Use a fake recycler instead of send2trash. Engine tests must run without PySide6 or send2trash installed; to test send2trash batching, inject a fake `send2trash` module into `sys.modules`. UI tests use `@unittest.skipUnless(PySide6 importable)` and set QT_QPA_PLATFORM=offscreen.
- Run tests with: `python -m unittest discover -s tests -t . -v` from the project root.

## Data flow

1. Overview: `scanner.scan` per fixed drive, then `AppState.set_scan`.
2. Suggestions: `analyzer.build_suggestions`, then `AppState.set_suggestions`. Then `cleaner.perform` for each selected item, then `show_reports`.
3. Large files and duplicates: `cleaner.delete_to_recycle(paths, allowed_roots=[drive_root])`. Protected paths are refused by `safety`.

## Addenda (v1.1, binding)

These supersede earlier text where they differ.

1. `ActionReport` has a new field `title: str = ""`. `perform()` sets `report.title = item.title` and `report.action = item.action.value` on every report it returns.
2. `perform(dry_run=True)`: DELETE_PATHS passes `dry_run` to `delete_to_recycle`. Every other action except NONE returns a report with `dry_run=True` and one message "Would run: <short description>". No command runs and nothing changes. NONE returns an empty report.
3. The admin refusal runs before anything else: `ActionFailure(path=item.title, reason="Requires administrator")`.
4. `perform()` checks `cancel` before each step. If it is set, return what was done so far with `cancelled=True`.
5. CLEAN_UPDATE_CACHE: run `["net", "stop", "wuauserv"]`, then `["net", "stop", "bits"]`. Exit code 2 with "not started" in the output means the service was already stopped, which is fine. Any other failure aborts the action before the Download folder is touched. In a `finally`, restart only the services this action stopped, with `["net", "start", <name>]`. Then call `delete_to_recycle(item.paths, item.allowed_roots)`.
6. CLEAN_WINDOWS_OLD: `["reg", "query", KEY]` lists subkeys as lines that start with `KEY + "\\"`. For each subkey run `["reg", "add", subkey, "/v", "StateFlags0065", "/t", "REG_DWORD", "/d", "0", "/f"]`. For the subkey named `Previous Installations`, use `/d 2`. If that subkey is missing, return a failure "Windows.old cleanup is not available on this PC". Then run `["cleanmgr", "/sagerun:65"]` with timeout 3600. Afterwards, if the Windows.old folder still exists, add the message "Some files remain. Restart the PC and run this action again if they are still there."
7. DELETE_RESTORE_POINTS: `drive = system.system_drive().rstrip("\\/")`, for example "C:". Loop at most 50 times. Each time, `count = system.count_restore_points(system.system_drive())`. Stop when count is None or count <= keep_newest. Otherwise run `["vssadmin", "delete", "shadows", f"/for={drive}", "/oldest", "/quiet"]` and stop on a non-zero exit code. Report the number of copies removed.
8. DISABLE_HIBERNATION: run `["powercfg", "/hibernate", "off"]`. Exit code 0 is success. Message: "Hibernation is off. Windows removes hiberfil.sys."
9. EMPTY_RECYCLE_BIN: `before = recycle.recycle_bin_info()[0]`, then `ok = recycle.empty_recycle_bin()`, then `after = recycle.recycle_bin_info()[0]`. `bytes_removed = max(0, before - after)`. If `ok` is False, add the failure `ActionFailure("Recycle Bin", "Some items could not be removed")`.
10. `delete_to_recycle([])` returns an empty report without calling the recycler.
11. `find_duplicates` computes all groups, sorts them by `reclaimable_bytes` descending, and returns the first `max_groups`.
12. Every page class has `shutdown() -> None`. It cancels the page's running Worker and waits up to 5 seconds. `MainWindow.closeEvent` calls `shutdown()` on every page, then accepts the event. Pages keep their Worker in an attribute such as `self._worker`, so Python does not garbage-collect it. A page disables its start button while its Worker runs.
13. `dialogs.show_reports` shows, for each report, its `title`, `removed_count`, `format_bytes(bytes_removed)`, a dry-run note when `dry_run` is set, and its failures (the first 20, then "and N more").
14. The browser cache warning text is "Close the browser first. If it is still open, Windows may remove only part of its cache."
15. The UI converts `ScanProgress` objects to text itself. The engine does not format them.
16. Permanent actions confirm with `destructive=True`. Recycle Bin moves do not need it, but a move that includes AppData paths adds the AppData warning to the confirmation.
