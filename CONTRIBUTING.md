# Contributing to Windows Space Analyzer

Thanks for taking the time to help. This document tells you how to set up, what
the code expects of you, and how to get a change accepted.

## Quick start

```bat
git clone https://github.com/akshaytp1571/windows-space-analyzer.git
cd windows-space-analyzer
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Run the tests with `run_tests.bat`, or directly:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

On Linux or macOS the engine tests run, but the app itself does not start. The UI
tests skip themselves when PySide6 is missing.

## Before you write code

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). It is the module contract:
the exact public signatures, the safety rules that may not be broken, and the
definition of every cleanup item. A change that contradicts it will be asked to
change or to update the document in the same pull request.

## The one rule that is not negotiable

**Nothing may delete, move, or modify a file without passing
`safety.validate_target` first, or being one of the documented exceptions.**

If your change adds a code path that touches the disk, it must either go through
`cleaner.delete_to_recycle` / `cleaner.perform`, or be added to the allowlist in
`docs/ARCHITECTURE.md` with a written justification. Pull requests that bypass
the safety gate are rejected regardless of how useful they are.

Two related invariants:

* Reparse points, symlinks and junctions are never followed.
* No folder is ever deleted recursively. Folders are only removed when empty.

## What makes a good pull request

* **One concern per pull request.** A bug fix and a refactor in the same branch
  is two pull requests wearing one coat.
* **A test that fails before your fix.** For a bug, add the regression test in
  the same commit. For a feature, add tests that describe the behaviour.
* **Small diffs.** If the diff is large, say why in the description.
* **No new dependency** unless the standard library genuinely cannot do it. If
  you do add one, explain why in the description and pin it in
  `requirements.txt`.
* **Match the surrounding style.** No formatter is enforced; just read a
  neighbouring file and follow it. Type hints on public functions, docstrings on
  every public function, `from __future__ import annotations` at the top.
* **Windows-first.** This is a Windows tool. Do not break the ability of the
  engine to import and test on Linux — that is how CI checks it.

## Tests

The suite is `unittest`, one file per module under `tests/`.

* Engine tests use temporary directories and a fake recycler. **No test may
  touch a real file outside a temporary directory.**
* UI tests set `QT_QPA_PLATFORM=offscreen` and skip when PySide6 is absent.
* If you add a cleanup rule, add it to `tests/test_rules.py` and to the item
  table in `docs/ARCHITECTURE.md`.
* If you add a safety refusal, add it to `tests/test_safety.py`.

## Adding a cleanup item

1. Add a rule function in `space_analyzer/rules.py` and append it to `RULES`.
2. Set `risk`, `requires_admin`, `regenerates`, `impact` and `warnings`
   honestly. `default_selected` is computed for you: it is `True` only when the
   risk is `SAFE` **and** the item does not require administrator rights.
3. Add the item to the table in `docs/ARCHITECTURE.md`.
4. Add tests covering: the item appears when its target exists, it is absent
   when it does not, and it is never pre-ticked if it needs administrator
   rights.

Be conservative. When you are unsure whether something is safe, mark it `LOW`
rather than `SAFE`, and write the trade-off in `impact`.

## Commit messages

Short imperative subject line, then a body explaining *why* when it is not
obvious:

```
Refuse deletions inside the profile folder

validate_target allowed a path equal to the profile folder itself, so a
cleanup item pointing at it would have removed the user's home directory.
Compare the resolved path, not the parent.
```

## Reporting a bug

Open an issue with:

* What you did, what you expected, what happened.
* Your Windows version and whether the app ran as administrator.
* The exact text of any error, or a screenshot.

**If the bug is data loss or a safety-gate bypass, do not open a public issue.**
Follow [`SECURITY.md`](SECURITY.md) instead.

## Code of conduct

Participation is covered by [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Licence

By contributing you agree that your contribution is licensed under the MIT
licence in [`LICENSE`](LICENSE).
