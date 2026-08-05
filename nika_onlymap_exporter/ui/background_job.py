"""Long work off the GUI thread, with progress and a way out.

Reading a project means reading every feature of every layer, and writing an
artifact means serialising all of it again. On a half-million-point layer both
take tens of seconds, and run on the GUI thread they freeze the dialog - which a
user cannot tell apart from a crash, and so reports as one.

**Why a thread is safe here.** PyQGIS objects are not generally thread-safe, but
the pipeline this runs is the same one the Processing algorithm runs, and a
Processing algorithm without `FlagNoThreading` already executes on a QGIS
background thread in production. The work is therefore already written to be
run off the main thread; what was missing was a way for the *dialog* to do it.

`QThread` is subclassed rather than the `moveToThread` pattern, because there is
exactly one job shape here - run a callable, report progress, hand back a
result - and the object-plus-thread plumbing would be more code saying less.
Signals emitted from `run()` cross to the GUI thread as queued connections, so
every slot on the far side runs where Qt widgets are legal to touch.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from qgis.PyQt.QtCore import QThread, pyqtSignal


class JobCancelledError(Exception):
    """Raised inside the worker when the user pressed Cancel.

    Not an error: it unwinds the work and is reported as a cancellation, never
    as a failure the user should report.
    """


class Progress:
    """The worker's handle on the progress bar, and on being cancelled.

    Passed into the work function so it can report where it is without knowing
    anything about Qt or about the dialog. Cancellation is checked at the same
    call, which means the granularity of stopping is exactly the granularity of
    reporting - per layer, in practice. A layer already being read finishes;
    there is no safe way to interrupt a provider mid-query, and killing a thread
    that is inside one is how a plugin takes QGIS down with it.
    """

    def __init__(self, job: BackgroundJob) -> None:
        self._job = job

    def step(self, percent: int, message: str) -> None:
        """Report position and check for cancellation.

        `percent` of -1 means indeterminate - the bar sweeps rather than fills,
        which is the honest display for a stage whose length is unknown.
        """
        if self._job.is_cancelled:
            raise JobCancelledError
        self._job.progressed.emit(percent, message)

    def check_cancelled(self) -> None:
        if self._job.is_cancelled:
            raise JobCancelledError


class BackgroundJob(QThread):
    """One callable, run on its own thread.

    The work function takes a `Progress` and returns anything; whatever it
    returns arrives on `succeeded`. Exceptions are caught and reported on
    `failed` rather than escaping - an exception let out of `run()` terminates
    the thread through Qt's own handler, which on some platforms takes the
    process with it.
    """

    progressed = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        work: Callable[[Progress], Any],
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._work = work
        self._cancelled = False

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Ask the job to stop at its next progress report.

        Only ever sets a flag. `QThread.terminate()` would stop it sooner and
        leave whatever it was holding - a provider connection, a half-written
        temporary directory - in an undefined state.
        """
        self._cancelled = True

    def run(self) -> None:
        try:
            result = self._work(Progress(self))
        except JobCancelledError:
            self.cancelled.emit()
        # Caught rather than allowed out: an exception escaping run() goes to
        # Qt's own handler, which on some platforms takes the process with it.
        except Exception as exc:
            self.failed.emit(str(exc), traceback.format_exc())
        else:
            if self._cancelled:
                # Cancelled between the last checkpoint and the end. The result
                # is complete and correct, but the user asked for it to stop, so
                # honouring the request beats delivering work they cancelled.
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
