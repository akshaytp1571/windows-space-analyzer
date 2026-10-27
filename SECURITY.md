# Security Policy

## The short version

Windows Space Analyzer deletes and moves files. A bug here can destroy data. If
you find a way to make the app delete something it promised not to delete, that
is a security issue, and it should not be reported in a public issue.

## Reporting a vulnerability

**Do not open a public issue for a safety or data-loss bug.**

Report it privately through GitHub's [private vulnerability
reporting](https://github.com/akshaytp1571/windows-space-analyzer/security/advisories/new),
or by email to the address on the maintainer's GitHub profile.

Please include:

* What the app did, and what it should have done.
* The exact steps to reproduce, including the paths involved.
* Whether the app was running as administrator.
* Your Windows version and the app version (`space_analyzer.__version__`).

I aim to acknowledge a report within 7 days and to ship a fix or a mitigation
plan within 30 days. If you would like credit in the release notes, say so.

## What counts as a security issue here

In scope:

* **A safety-gate bypass.** Any path that reaches a delete, move, or system
  change without passing `safety.validate_target`, or that passes it when it
  should have been refused.
* **Deletion outside the declared scope.** Removing something outside the
  `allowed_roots` of a cleanup item.
* **Following a reparse point.** Traversing a symlink or junction into a
  location the user did not select.
* **A destructive action without confirmation.** Anything irreversible that runs
  without the user explicitly confirming it.
* **Privilege escalation.** Getting the app to perform an administrator-only
  action without administrator rights, or prompting for elevation without the
  user asking.
* **Command injection** through a path, filename, or system command argument.
* **A false SAFE classification** on an item that in practice destroys
  something a normal user would not expect to lose.

Out of scope:

* The app being unable to delete a file it should be able to delete.
* A cleanup item being absent because the folder does not exist.
* Cosmetic bugs, layout problems, or a wrong number in the interface.
* Anything that requires an attacker to already have write access to the
  application's own source files.
* The app running with the privileges the user gave it. It does not elevate
  itself; the "Restart as administrator" button asks the OS, which prompts the
  user.

## Design commitments

These are the guarantees the project makes. A report that shows one of them is
false is a valid security report. Each is enforced in code unless marked
otherwise; [`docs/SAFETY.md`](docs/SAFETY.md) gives the exact mechanism and
states the known limits honestly.

1. Every path the app deletes or moves on the user's behalf passes
   `safety.validate_target` before it runs. The four permanent actions (empty the
   Recycle Bin, turn hibernation off, delete restore points, remove `Windows.old`)
   take no path and are instead protected by an administrator gate and a fixed
   argument list; see `docs/SAFETY.md`.
2. Drive roots, the user profile folder, the seven named folders directly under
   it, and protected system locations are never deletion targets.
3. Reparse points, symlinks and junctions are never followed.
4. No folder is deleted recursively. A folder is only removed when it is empty.
5. Files the user selected go to the Recycle Bin, not to oblivion, unless the
   item is explicitly documented as permanent.
6. Every permanent action is confirmed with a warning that says it cannot be
   undone, and No is the default button. This one is a UI convention rather than
   engine-level enforcement — `cleaner.perform` cannot tell whether a
   confirmation happened. A bypass of it in the shipped UI is still a valid
   report.
7. Items that need administrator rights are never pre-ticked and are refused
   when the app is not elevated.
8. The app never requests elevation on its own.
9. The app never deletes an application's own files. Uninstalling from the Apps
   page runs the uninstaller the publisher registered, and nothing else. A
   registered command is launched only when it is an absolute local `.exe` path
   that exists, or an MsiExec product code — and MsiExec itself is resolved to its
   absolute `System32` path rather than looked up on `PATH`. Shells, file-deletion
   tools, relative names and network shares are refused, and the status line shows
   the reason. This is stated plain rather than buried because it is the one place
   the app starts a program it did not write.

## Known limitations

These are documented rather than reported as vulnerabilities. They are listed so
you can judge whether a finding is new:

* The re-check before the move narrows the TOCTOU window but does not close it: a
  local attacker who can already write to the same folders still has the gap
  between the second validation and the `send2trash` call. Closing it would need a
  handle-based delete, which `send2trash` does not offer.
* `oldwin.windows_old` is not a `DELETE_PATHS` action, so it does not use
  `validate_target`; `Windows.old` is itself a top-level folder, which that
  function refuses by design. It uses the stricter `safety.validate_windows_old`
  instead, which accepts only this PC's exact `Windows.old` folder.
* The "a misconfigured environment variable must never widen a deletion" rule is
  enforced for the two temp rules only; five other item families read their base
  folder from the environment and are bounded by `allowed_roots` instead.
* The personal-folder check depends on `%USERPROFILE%` being set, and matches a
  hardcoded list of seven names rather than the Windows known-folder API.
* `ActionReport.requires_restart` is declared and rendered but never set to
  `True` by any code path.

## Supported versions

The latest release on the `main` branch receives security fixes. This is a small
project; there are no long-term support branches.

## Safe harbour

I will not pursue legal action against anyone who reports a vulnerability in
good faith, follows this policy, and does not exploit it beyond what is needed
to demonstrate the problem.
