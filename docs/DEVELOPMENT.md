# Development guide

How the code is organised, how to work on it, and the conventions it follows.
Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for the module contract and
[`SAFETY.md`](SAFETY.md) for the rules you must not break.

## Setting up

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -r requirements-build.txt   # only for the .exe
.venv\Scripts\python.exe main.py
```

Python 3.10 or newer. The app targets Windows 10/11; the engine imports and tests
fine on Linux, which is what CI uses.

## Running the tests

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

On Linux or in CI, the UI tests need a Qt platform plugin and a font:

```bash
export QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests -t .
```

Without a font, Qt renders text as empty boxes — layout assertions still work, but
screenshots taken in that state are useless for review. Point Qt at a font
directory to fix it:

```bash
export QT_QPA_FONTDIR=/usr/share/fonts/truetype/dejavu
```

On Windows, `QT_QPA_FONTDIR=C:\Windows\Fonts` gives real text in offscreen
renders. This is how the screenshots in `assets/` were produced.

## Layout of the code

```
main.py                     launcher, 6 lines
space_analyzer/
    models.py               dataclasses and enums shared by everything
    util.py                 size, count and time formatting
    system.py               drives, admin rights, protected paths, command runner
    scanner.py              disk scanner and measure_path
    duplicates.py           duplicate file finder
    recycle.py              Recycle Bin send, empty and info
    safety.py               validate_target — the gate
    cleaner.py              delete_to_recycle and perform
    analyzer.py             build_suggestions
    rules.py                one function per cleanup item
    ui/
        app.py              run() entry point
        state.py            shared Qt state
        workers.py          background worker thread
        theme.py            colours, fonts, stylesheet
        icons.py            hand-painted vector icons
        widgets.py          reusable widgets
        main_window.py      rail navigation and status bar
        dialogs.py          confirm() and show_reports()
        page_logic.py       pure rules behind the pages
        pages/              the four pages
tests/                      unittest suite, one file per module
docs/                       this documentation
```

## The boundary that matters

`space_analyzer/` (the engine) must not import `PySide6`. The engine is pure
Python and the standard library, plus `send2trash` for the Recycle Bin. This is
what lets the engine tests run anywhere and keeps the safety-critical code
independent of the interface.

The UI may import the engine freely. The engine must never import the UI.

If you need to add a decision that both the UI and the engine must agree on, put
it in the engine or in `ui/page_logic.py` (which is pure and has no widgets), not
in a page.

## Adding a cleanup item

1. Write a rule function in `rules.py` returning `list[CleanupItem]`, and append
   it to `RULES`.
2. Refuse to walk links. Use the existing `_is_dir` / `_is_file` helpers, which
   already combine the existence check with `not system.is_link(path)`.
3. Set `risk` honestly. When unsure, `LOW` rather than `SAFE`. Write the
   trade-off in `impact` and any sharp edge in `warnings`.
4. Do not set `default_selected`. `analyzer.build_suggestions` computes it as
   `risk == SAFE and not requires_admin`.
5. Set `allowed_roots` to the narrowest root that makes sense. This is what
   bounds the deletion if anything upstream is wrong.
6. Add the item to the table in `ARCHITECTURE.md` and to `docs/USER_GUIDE.md`.
7. Add tests: the item appears when its target exists, is absent when it does
   not, is never pre-ticked when it needs admin, and every path it produces
   passes `validate_target`.

## Adding a safety refusal

Add it to `safety.validate_target` **and** to `tests/test_safety.py`. Update the
table in `SAFETY.md`. Refusals are returned strings, never exceptions — the
caller reports them per path and continues with the rest.

## The UI conventions

The interface is built around one idea: this is a measurement instrument, not a
web dashboard. Conventions that follow from that:

* **Square hairline panels, no rounded cards, no drop shadows.**
* **Monospace numerals** for every measured value. Labels stay in the UI font.
  This is what makes numbers read as readings.
* **One accent colour**, acid lime `#c8f751`. It marks the active nav item, the
  primary button, and the live gauge fill. Risk levels get amber, blue and red;
  nothing else is coloured.
* **Numbered page eyebrows** — `01 / drive telemetry`, `02 / cleanup candidates`,
  and so on.
* **Status verbs in caps** — `IDLE`, `SCANNING`, `COMPLETE`, `FAILED`, `LISTED`.
* **Icons are painted in code** (`icons.py`), so there are no image assets and no
  dependency on an icon theme.

Colours and fonts live in `theme.py` as named tokens. Do not hardcode a hex value
in a page; add a token.

### The layout rule that is easy to get wrong

Every page body is wrapped in `widgets.PageScroll`. Do not build a page as a bare
`QVBoxLayout` on the page widget. Without a scroll container, Qt takes the whole
height shortfall out of whichever widget has stretch, and it collapses to an
unusable size while the page appears to be broken.

`tests/test_layout.py` enforces this: every page must have a scroll container,
must report a small minimum height, and must never shrink content below its
minimum. If you add a page, add it to that test.

Two related traps:

* A `QLabel` holding prose needs `setWordWrap(True)`. Without it the text is
  silently clipped, which looks like a rendering bug.
* A panel that must stay usable needs a `setMinimumHeight`. Otherwise it absorbs
  the entire shortfall before the page scrollbar engages.

## Threading

Anything that touches the disk runs in a `Worker` thread (`ui/workers.py`), never
on the Qt main thread. A `Worker` takes a callable plus a `cancel` event and
emits `succeeded`, `failed`, `finished` and `progress`.

The pattern used by every page:

```python
worker = Worker(fn, cancel=event)
worker.progress.connect(self._on_progress)
worker.succeeded.connect(self._on_succeeded)
worker.failed.connect(self._on_failed)
worker.finished.connect(self._on_worker_finished)
worker.start()
```

Pages keep a reference to their worker and call `cancel()` then `wait(5000)` in
`shutdown()`, which the main window calls on close.

## Building the executable

```bat
build_exe.bat
```

Produces `dist\WindowsSpaceAnalyzer.exe` via PyInstaller. The exe does not
request elevation at start; it runs as the current user and offers the restart
button for the items that need it.

## Style

No formatter is enforced. Match the file you are editing.

* `from __future__ import annotations` at the top of every module.
* Type hints on public functions.
* A docstring on every public function and class, one line, describing what it
  does rather than restating its name.
* British spelling in prose, American spelling in identifiers where the platform
  uses it.
* Prefer a named constant over a magic value.

## Debugging

* `python -c "import space_analyzer.safety as s; print(s.validate_target(r'C:\\', ['C:\\']))"`
  — try the gate directly. This is the fastest way to understand a refusal.
* Set `QT_QPA_PLATFORM=offscreen` and call `window.grab().save(...)` to capture a
  page without a display.
* Engine functions are pure and callable from a REPL. `scanner.scan`,
  `analyzer.build_suggestions` and `duplicates.find` all take a `cancel` event and
  a `progress` callback, so you can drive them from a script.
