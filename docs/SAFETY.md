# How Windows Space Analyzer keeps you safe

This document describes what the app will and will not delete, and which code
enforces it. It is deliberately precise about the difference between a **hard
guarantee** and a **UI convention**, because a disk cleanup tool that overstates
its own safety is worse than one that admits its limits.

If you would rather read code than prose, start at
[`space_analyzer/safety.py`](../space_analyzer/safety.py) — it is 63 lines.

## The gate

Most deletions pass through one function:

```python
safety.validate_target(path, allowed_roots, *, protected=None, allowlist=None) -> str | None
```

It returns `None` when the path may be removed, or a short reason when it may
not. It never raises. Checks run in order and stop at the first failure:

| # | Check | Refusal message | Applies to |
|---|---|---|---|
| 1 | Path is empty or whitespace | `No path given` | all |
| 2 | Path is a filesystem or fixed-drive root | `Drive roots cannot be removed` | all |
| 3 | Path's parent is a drive root | `Top-level folders cannot be removed` | folders |
| 4 | Path is the profile folder or one of seven named folders directly under it | `Personal folders cannot be removed` | folders |
| 5 | Path is a symlink, junction or reparse point | `Links and junctions are not followed` | all |
| 6 | Path is outside every `allowed_roots` entry | `Outside the allowed locations` | all |
| 7 | Path is a protected system location not on the allowlist | `Protected system location` | all |

The seven names in check 4 are `Desktop`, `Documents`, `Downloads`, `Pictures`,
`Videos`, `Music`, and `OneDrive`.

Important detail: `system.norm` resolves the real path, so checks 2, 3, 4, 6 and
7 evaluate where a path **actually is**, not the text you typed. A path reached
through a junction is judged by its resolved destination.

### Which callers actually use the gate

This is the part most such documents gloss over. Be clear about it:

| Operation | Goes through `validate_target`? |
|---|---|
| Deleting a cleanup item's files (`DELETE_PATHS`) | **Yes**, every path, and again before the move |
| Windows Update cache (`CLEAN_UPDATE_CACHE`) | **Yes**, via an inner `delete_to_recycle` |
| Files you tick on *Large files* / *Duplicates* | **Yes**, and again before the move |
| Empty the Recycle Bin | No — takes no path |
| Turn hibernation off | No — takes no path |
| Delete restore points | No — takes no path |
| Remove `Windows.old` | No — uses the stricter `validate_windows_old` instead |
| Uninstalling an app | No — launches a program, and is checked by `apps.uninstall_problem` instead |

The three operations that take no path have nothing for a path validator to check;
they are protected differently, described in the next section. `Windows.old` does
have a path, so it gets its own check rather than none. Uninstalling launches a
program rather than removing a path, so it is checked against the launch rules in
the section after that.

## Launching a publisher's uninstaller

The Apps page is the only place the app starts a program it did not write, so it is
guarded by its own check rather than by the path gate. `validate_target` asks "may
this path be deleted?"; an uninstaller is not being deleted, so that is the wrong
question.

`apps.uninstall_problem` answers the right one. A registered command is launched
only when it is one of two things:

1. **An absolute local `.exe` path**, which may be quoted or unquoted, with or
   without arguments — `"C:\Program Files\App\uninst.exe" /S` and
   `C:\Program Files\App\uninst.exe /S` are both accepted.
2. **An MsiExec product code**, in any spelling installers actually use: `/X`,
   `/I` or `/uninstall`. Windows' own Apps page treats all three as "uninstall this
   product", so refusing `/I` would have blocked Java, Node.js and Chrome Remote
   Desktop for no reason.

Everything else is refused, and the reason is shown in the status line:

| Refused | Why |
|---|---|
| `cmd`, `powershell`, `pwsh`, `cscript`, `wscript`, `mshta`, `rundll32`, `regsvr32` | A shell re-parses the rest of the line |
| `del`, `erase`, `rd`, `rmdir`, `format`, `shutdown` | A deletion tool, not an uninstaller |
| A bare name such as `uninst.exe` | `PATH` decides what runs, not the publisher |
| A network share such as `\\server\share\x.exe` | The app cannot vouch for a program it fetches over the network |
| An MsiExec command with no product code | Nothing to uninstall |

Two details that matter more than they look:

- **Only the product code survives.** The app does not run the registered MsiExec
  line. It extracts the code and runs `/x {code}`, so no extra switches a registry
  value carried — `/qn`, `/norestart`, a log path — reach the call.
- **MsiExec is resolved to an absolute path.** It is launched as
  `C:\Windows\System32\msiexec.exe`, not by bare name, for the same reason a
  vendor path must be absolute: a bare name is a `PATH` lookup. If `System32\msiexec.exe`
  cannot be found, the launch is refused rather than falling back to a lookup.

The app never deletes an application's files itself. The uninstaller does that,
shows its own prompts, and reports its own errors. The confirmation dialog shows
the exact registered command before anything starts.

## How the permanent actions are protected

Three of them — emptying the Recycle Bin, turning hibernation off, and deleting
restore points — do not accept a user-supplied path, so there is nothing for a path
validator to check. `Windows.old` does have a path and is checked separately (see
below). For the three, the protection is:

* **An administrator gate.** Each is refused before it runs when the app is not
  elevated, with the failure `Requires administrator`.
* **A fixed argument list.** No path is ever interpolated into a command.
  `powercfg /hibernate off`, and `vssadmin` with a drive letter taken from
  `%SystemDrive%` (validated against the pattern `[A-Za-z]:`), never from item
  data.
* **A scope derived from the system, not from data.** The Recycle Bin is emptied
  through the Windows API across fixed drives; restore points are capped at
  `count - keep` with a hard iteration ceiling of 50.
* **`Windows.old`** is handed to Windows' own `cleanmgr`, with every other
  cleanup category explicitly disabled first so only *Previous Installations*
  runs.

### `Windows.old` has its own check

`oldwin.windows_old` cannot use `validate_target`, because `Windows.old` **is** a
top-level folder on the system drive, and rule 3 refuses every top-level folder by
design. That rule exists to stop a bad cleanup item naming an arbitrary folder, so
weakening it for this one action would be the wrong fix.

Instead the action has a stricter check, `safety.validate_windows_old`, which
requires that the path be **exactly this PC's `Windows.old` folder** and nothing
else. It refuses a different top-level folder, any folder inside `Windows.old`, a
path that is no longer a folder, and a link. It runs before the registry is touched
or `cleanmgr` is invoked, so a malformed item cannot reach either.

## Protected locations, and the allowlist

These roots are refused by check 7:

```
<system>\Windows                     <system>\Program Files
<system>\Program Files (x86)         <system>\ProgramData
<system>\Windows.old                 <system>\Recovery
<system>\Boot                        <system>\EFI
<system>\$WINDOWS.~BT                <system>\$WinREAgent
<system>\pagefile.sys                <system>\hiberfil.sys
<system>\swapfile.sys
<drive>\System Volume Information    (every fixed drive)
<drive>\$Recycle.Bin                 (every fixed drive)
```

A short allowlist overrides those roots for specific cleanable folders. These
are the only exceptions:

```
<system>\Windows\Temp
<system>\Windows\SoftwareDistribution\Download
<system>\Windows\Logs
<system>\Windows\Minidump
<system>\Windows\MEMORY.DMP
<system>\ProgramData\Microsoft\Windows\WER\ReportArchive
<system>\ProgramData\Microsoft\Windows\WER\ReportQueue
```

The allowlist is checked **before** the protected roots, so an allowlisted path
wins. Anything else under `Windows` or `ProgramData` is refused. Adding to this
list is a deliberate act and requires justification in the pull request.

## Links are never followed

Reparse points are handled in six independent layers, all keyed on
`FILE_ATTRIBUTE_REPARSE_POINT`:

1. `system.is_link` — used by the gate and by the rules.
2. `validate_target` check 5.
3. `system.norm` resolves paths, so containment is judged on the real location.
4. `scanner._skip_reason` recognises symlinks, junction mount points and WSL
   symlinks by their reparse tag, and refuses to walk any other reparse
   directory.
5. `recycle._is_real_dir` never descends into a link while surveying the bin.
6. `duplicates._walk` and the rules use `follow_symlinks=False` throughout.

`system.is_link` returns `True` when a path cannot be inspected at all, so an
unreadable target is refused rather than allowed.

**Re-checked before the move.** `delete_to_recycle` validates each path twice: once
while measuring it, and again immediately before sending, so a path that became a
link — or that now resolves somewhere else — in between is dropped and reported
rather than moved. That narrows the window to the gap between the second check and
the `send2trash` call itself. It is narrowed, not eliminated: closing it completely
would need a handle-based delete, which `send2trash` does not offer.

## Nothing is deleted recursively

There is no `os.remove`, `os.unlink`, `os.rmdir`, or `shutil.rmtree` anywhere in
`space_analyzer/`. `shutil` is imported only for `disk_usage`.

Folders are removed only when empty. A cleanup item that targets a folder deletes
the files inside it individually — each through the gate — and then removes the
folder only if nothing is left.

## The Recycle Bin is the default destination

Files you select go to the Recycle Bin through
[`send2trash`](https://pypi.org/project/send2trash/). An accidental cleanup is
therefore recoverable.

The consequence is stated in the interface: **space is not freed until the bin is
emptied.** The app says so next to the numbers so nobody is surprised when their
free space does not change.

Note that `recycle.send_to_recycle_bin` is a public function that performs no
validation of its own. `cleaner.delete_to_recycle` is its only production caller,
and that is where the gate lives.

## Risk levels

Four levels, defined in `models.Risk`:

| Level | Meaning | Pre-ticked? |
|---|---|---|
| `SAFE` | Regenerated automatically; no meaningful loss | Yes, unless it needs admin |
| `LOW` | Harmless, but may cost time or convenience later | No |
| `ADVANCED` | A system-level change with a real trade-off | No |
| `INFO` | Awareness only; no action is offered | N/A |

The pre-tick rule is one line in `analyzer.build_suggestions`:

```python
item.default_selected = item.risk == Risk.SAFE and not item.requires_admin
```

The UI applies a second gate, so an administrator-only item can never be ticked
by a standard user even if a future change got the pre-selection wrong.

Five items are pre-ticked: your temporary files, and the Chrome, Edge, Firefox
and thumbnail caches. *Windows temporary files* and the *Windows Update download
cache* are `SAFE` but need administrator rights, so they are never pre-ticked.

## Confirmation before anything happens

Before a cleanup runs, a dialog lists what will be removed, the total size, and
the warnings that apply — for example *"This permanently deletes everything in
the Recycle Bin, including files you put there yourself."* Declining runs
nothing.

Permanent actions pass `destructive=True`, which changes the icon to a warning,
relabels the accept button *"Yes, continue"*, styles it as a danger action, and
**makes No the default button**.

**Honest caveat:** `destructive` is a UI flag. `cleaner.perform` has no parameter
for it and cannot tell whether a confirmation happened. The guarantee is a
convention honoured by every page in this app, not something the engine enforces
against a different caller.

## When something goes wrong

Cleanup runs in a background thread and reports what happened. A partial failure
produces an `ActionReport` with:

* how many items were removed and how many bytes that freed
* a `failed` list of `(path, reason)` for everything that did not work
* any messages the operation wants to surface

The UI shows this. A file that cannot be deleted because it is in use appears in
that list with its reason, and the rest of the operation still completes. On
*Large files* and *Duplicates*, rows whose paths failed stay in the list so you
can retry them.

If the recycler itself raises an unexpected exception, no report is produced and
the page shows `FAILED — <message>`. That is deliberate: swallowing it would hide
a real problem.

If a scan cannot read a folder, the Overview page reports how many items were
skipped and notes that some need administrator rights. It never silently pretends
the drive is smaller than it is — the unread portion is shown separately as
*System areas it could not read*.

One measured consequence worth knowing: a cloud-only file (a OneDrive
placeholder that is not downloaded) measures as **0 bytes**, so a cleanup that
moves one reports 0 bytes freed.

## Cancelling

Scans can be cancelled. A cancelled scan keeps its partial figures and says
`CANCELLED — the figures shown are partial`. Cancelling a cleanup stops the loop;
paths already validated are still sent, and a path whose size measurement was cut
short is dropped rather than half-reported.

## Where the "never widen" promise holds, and where it does not

An environment variable that is misconfigured should not widen what gets deleted.
That is enforced for exactly two rules:

* `temp.user` must resolve strictly inside `%LOCALAPPDATA%`
* `temp.system` must resolve strictly inside the Windows directory

Five other families read their base folder from the environment directly without
that guard: the browser and thumbnail caches (from `%LOCALAPPDATA%`), the WER
report rules (from `%ProgramData%`), and the log and update-cache rules (from the
Windows directory). `validate_target` still bounds every resulting path to the
item's `allowed_roots` and still refuses protected locations, so a hostile
environment variable cannot reach outside the drive, but the stronger "cannot
widen at all" claim is only true for the two temp rules.

## Two more limits worth stating

* If `%USERPROFILE%` is unset or empty, check 4 silently stops applying. The
  remaining checks still run.
* "Well-known folders" means a hardcoded list of seven names matched against
  `%USERPROFILE%\<name>`. It is not the Windows known-folder API. A `Desktop`
  redirected to `D:\Desktop`, or `OneDrive\Desktop`, is **not** refused by that
  rule — though `D:\Desktop` would still be refused if it sat directly under a
  drive root, and any path must still fall inside the item's `allowed_roots`.

## Verifying these claims yourself

```bat
.venv\Scripts\python.exe -m unittest tests.test_safety tests.test_cleaner -v
```

`tests/test_safety.py` asserts each refusal above.
`tests/test_cleaner.py` asserts that the cleaner refuses what the gate refuses
and that no real file outside a temporary directory is touched.

## Reporting a hole in this

If you find a way around any rule on this page, please follow
[`SECURITY.md`](../SECURITY.md) and report it privately rather than opening a
public issue.
