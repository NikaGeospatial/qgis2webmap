"""Long work off the GUI thread, with progress and a way out.

Reading a project means reading every feature of every layer, and writing an
artifact means serialising all of it again. On a half-million-point layer both
take tens of seconds, and run on the GUI thread they freeze the dialog - which a
user cannot tell apart from a crash, and so reports as one.

**The thread boundary.** QGIS's task documentation warns that a background job
must not touch the GUI-owned `QgsProject` or its `QgsMapLayer`s, so the dialog
splits an export at that line instead of hoping the pipeline is thread-safe:

* *GUI thread, before the job starts:* everything that needs a live object -
  `project_reader.prepare_project` translates symbology, labels, popups,
  sources, title, CRS and terrain, and takes a `QgsVectorLayerFeatureSource` (QGIS's
  thread-safe handle on a layer's data) plus value-type copies of the CRS and
  transform context. The result, a `PendingProject`, holds no project or layer.
  The dialog's widgets and settings are read here too, into plain values.
* *Worker thread, the job:* `finish_project` reads and serialises the features
  from those sources - the part that scales with the data - then the writer and
  packaging turn the resulting `ExportProject` (plain dataclasses) into files.
  Rasters are re-opened from their file path by GDAL or a fresh `QgsRasterLayer`
  the worker itself owns, never the project's.

So a work function handed to `BackgroundJob` may use its `Progress`, its
captured snapshots and plain values, and nothing that leads back to a layer,
the project, or a widget. A project that is closed or emptied while the job runs
cannot reach it.

`QThread` is subclassed rather than the `moveToThread` pattern, because there is
exactly one job shape here - run a callable, report progress, hand back a
result - and the object-plus-thread plumbing would be more code saying less.
Signals emitted from `run()` cross to the GUI thread as queued connections, so
every slot on the far side runs where Qt widgets are legal to touch.

**Who owns a running thread.** Not the dialog. A `QThread` that is destroyed
while its thread still runs aborts the process (`qFatal`), and a thread made a
child of a dialog is destroyed with it - which is what closing the window while a
slow layer was being read did. A job has no Qt parent instead; this module keeps
it alive (`_RUNNING`) from `start()` until Qt reports the thread finished, and
only then deletes it. The window that asked for the job can therefore go at any
moment: it calls `detach()`, which drops the job's results on the floor, and
`cancel()`, which asks it to stop at its next checkpoint. Nothing waits for it
beyond a short, bounded courtesy wait.

The results reach the window through the job itself (see `_deliver`), so the
"has the window gone?" check happens on the GUI thread, after the thread has
posted its result and before any slot runs - a result already queued when the
window closed is dropped there rather than reaching a deleted widget.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import time
import traceback
from collections.abc import Callable
from typing import Any

from qgis.PyQt.QtCore import QThread, pyqtSignal, pyqtSlot

from ..core.cancellation import ExportCancelledError

# Every job whose thread has been started and not yet reported finished. The
# strong reference is what keeps a running QThread alive when its window is
# gone; Qt's own `finished` signal is what lets go of it. A module global (rather
# than state on the plugin) so it also outlives a plugin unload: the running
# thread's own frames keep this module's namespace alive until it ends.
_RUNNING: set[BackgroundJob] = set()


def running_jobs() -> int:
    """How many jobs still have a thread that has not finished."""
    return len(_RUNNING)


def retire(job: BackgroundJob, wait_ms: int) -> bool:
    """A window is closing with `job` running: drop its results, ask it to stop,
    and give it up to `wait_ms` to do so cleanly. Returns whether it has ended.

    Safe either way: a job that outlasts the wait stays owned by `_RUNNING` and
    ends on its own, delivering nothing.
    """
    job.detach()
    job.cancel()
    with contextlib.suppress(RuntimeError):
        return job.wait(max(wait_ms, 0))
    return True


def release_all(wait_ms: int) -> None:
    """For plugin unload: stop every job still running, drop their results, and
    give them `wait_ms` in total to end.

    A job that does not end in time is left to finish on its own - it stays
    owned by `_RUNNING`, so it is never destroyed running, and with its results
    detached it calls nothing in the plugin when it does. Bounded: a wedged
    provider cannot hold QGIS's shutdown.
    """
    jobs = list(_RUNNING)
    for job in jobs:
        job.detach()
        job.cancel()
    deadline = time.monotonic() + max(wait_ms, 0) / 1000
    for job in jobs:
        remaining = int((deadline - time.monotonic()) * 1000)
        with contextlib.suppress(RuntimeError):
            job.wait(max(remaining, 0))


class JobCancelledError(ExportCancelledError):
    """Raised inside the worker when the user pressed Cancel.

    Not an error: it unwinds the work and is reported as a cancellation, never
    as a failure the user should report. A subclass of the exception the export
    pipeline raises when `should_cancel` fires, so either ends the job the same
    way.
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
        self.check_cancelled()
        self._job.report_progress(percent, message)

    def check_cancelled(self) -> None:
        if self._job.is_cancelled and not self._job.is_committed:
            raise JobCancelledError

    def commit(self) -> None:
        """Pass the point of no return: from here a Cancel is too late.

        Call it right after the last cancellation check, before the work that
        puts the finished artifact in place. The job then reports success when
        that work completes, whatever the user pressed in the meantime - the file
        is on disk, so "cancelled" would be untrue.
        """
        self._job.mark_committed()

    def should_cancel(self) -> bool:
        """The cancellation check in the shape the export pipeline takes."""
        return self._job.is_cancelled and not self._job.is_committed


class BackgroundJob(QThread):
    """One callable, run on its own thread.

    The work function takes a `Progress` and returns anything; whatever it
    returns arrives on `succeeded`. Exceptions are caught and reported on
    `failed` rather than escaping - an exception let out of `run()` terminates
    the thread through Qt's own handler, which on some platforms takes the
    process with it.

    Parentless on purpose, and owned by `_RUNNING` from `start()` until it has
    finished; see the module docstring. The signals below are emitted on the
    GUI thread, by `_deliver_*`, and not at all once the job is `detach`ed.
    """

    progressed = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    cancelled = pyqtSignal()
    # The work passed its point of no return; Cancel can no longer stop it.
    committing = pyqtSignal()

    # The worker thread posts to these; they land on this object, which lives on
    # the GUI thread, so the matching `_deliver_*` slot runs there.
    _progress_posted = pyqtSignal(int, str)
    _succeeded_posted = pyqtSignal(object)
    _failed_posted = pyqtSignal(str, str)
    _cancelled_posted = pyqtSignal()
    _committing_posted = pyqtSignal()

    def __init__(self, work: Callable[[Progress], Any]) -> None:
        super().__init__()
        self._work: Callable[[Progress], Any] | None = work
        self._cancelled = False
        self._committed = False
        self._detached = False
        self._progress_posted.connect(self._deliver_progress)
        self._succeeded_posted.connect(self._deliver_succeeded)
        self._failed_posted.connect(self._deliver_failed)
        self._cancelled_posted.connect(self._deliver_cancelled)
        self._committing_posted.connect(self._deliver_committing)
        self.finished.connect(self._on_finished)

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def is_committed(self) -> bool:
        return self._committed

    @property
    def is_detached(self) -> bool:
        return self._detached

    def mark_committed(self) -> None:
        self._committed = True
        self._committing_posted.emit()

    def report_progress(self, percent: int, message: str) -> None:
        self._progress_posted.emit(percent, message)

    def cancel(self) -> None:
        """Ask the job to stop at its next progress report.

        Only ever sets a flag. `QThread.terminate()` would stop it sooner and
        leave whatever it was holding - a provider connection, a half-written
        temporary directory - in an undefined state.
        """
        self._cancelled = True

    def detach(self) -> None:
        """Stop delivering results: the window that wanted them has gone.

        Called on the GUI thread. A result the thread already posted is dropped
        by the `_deliver_*` slot, which checks this flag where the slots run.
        The thread itself is not touched - it ends on its own, still owned by
        `_RUNNING`.
        """
        self._detached = True

    def start(self, *args: Any) -> None:
        # Owned before it runs, so there is no moment a running thread has no
        # owner.
        _RUNNING.add(self)
        super().start(*args)

    @pyqtSlot()
    def _on_finished(self) -> None:
        """Qt reports the thread finished: now, and only now, let the object go."""
        _RUNNING.discard(self)
        self._work = None
        self.deleteLater()

    @pyqtSlot(int, str)
    def _deliver_progress(self, percent: int, message: str) -> None:
        if not self._detached:
            self.progressed.emit(percent, message)

    @pyqtSlot(object)
    def _deliver_succeeded(self, result: object) -> None:
        if not self._detached:
            self.succeeded.emit(result)

    @pyqtSlot(str, str)
    def _deliver_failed(self, message: str, details: str) -> None:
        if not self._detached:
            self.failed.emit(message, details)

    @pyqtSlot()
    def _deliver_cancelled(self) -> None:
        if not self._detached:
            self.cancelled.emit()

    @pyqtSlot()
    def _deliver_committing(self) -> None:
        if not self._detached:
            self.committing.emit()

    def run(self) -> None:
        work = self._work
        if work is None:
            return
        try:
            result = work(Progress(self))
        except ExportCancelledError:
            self._cancelled_posted.emit()
        # Caught rather than allowed out: an exception escaping run() goes to
        # Qt's own handler, which on some platforms takes the process with it.
        except Exception as exc:
            self._failed_posted.emit(str(exc), traceback.format_exc())
        else:
            if self._cancelled and not self._committed:
                # Cancelled between the last checkpoint and the end of work that
                # has nothing to commit (a preview, a read). The result is
                # complete, but the user asked for it to stop, so honouring the
                # request beats delivering work they cancelled. A job that has
                # committed is different: its file is already in place, so it
                # reports that, not a cancellation.
                self._cancelled_posted.emit()
            else:
                self._succeeded_posted.emit(result)
