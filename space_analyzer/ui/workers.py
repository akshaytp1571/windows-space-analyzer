"""Background worker: runs an engine function on a thread and reports back to the UI."""

from __future__ import annotations

import inspect
import threading
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    """Runs func(*args, **kwargs) off the UI thread.

    progress= and cancel= are passed only when func accepts them. Signals:
    progressed(object) for status text, succeeded(object) with the return value,
    errored(str) with the error message. Call cancel() to stop a cooperative function.
    """

    progressed = Signal(object)
    succeeded = Signal(object)
    errored = Signal(str)

    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = dict(kwargs)
        self._cancel_event = threading.Event()
        parameters = inspect.signature(func).parameters
        if "progress" in parameters:
            self._kwargs["progress"] = self._emit_progress
        if "cancel" in parameters:
            self._kwargs["cancel"] = self._cancel_event

    def cancel(self) -> None:
        """Ask the running function to stop at its next check."""
        self._cancel_event.set()

    def _emit_progress(self, message: object) -> None:
        self.progressed.emit(message)

    def run(self) -> None:
        try:
            result = self._func(*self._args, **self._kwargs)
        except Exception as exc:  # reported to the UI instead of failing silently
            self.errored.emit(str(exc) or type(exc).__name__)
            return
        self.succeeded.emit(result)
