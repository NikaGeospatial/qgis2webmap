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

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ..core.cancellation import ExportCancelledError


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
        self._job.progressed.emit(percent, message)

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
    """

    progressed = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    cancelled = pyqtSignal()
    # The work passed its point of no return; Cancel can no longer stop it.
    committing = pyqtSignal()

    def __init__(
        self,
        work: Callable[[Progress], Any],
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._work = work
        self._cancelled = False
        self._committed = False

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def is_committed(self) -> bool:
        return self._committed

    def mark_committed(self) -> None:
        self._committed = True
        self.committing.emit()

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
        except ExportCancelledError:
            self.cancelled.emit()
        # Caught rather than allowed out: an exception escaping run() goes to
        # Qt's own handler, which on some platforms takes the process with it.
        except Exception as exc:
            self.failed.emit(str(exc), traceback.format_exc())
        else:
            if self._cancelled and not self._committed:
                # Cancelled between the last checkpoint and the end of work that
                # has nothing to commit (a preview, a read). The result is
                # complete, but the user asked for it to stop, so honouring the
                # request beats delivering work they cancelled. A job that has
                # committed is different: its file is already in place, so it
                # reports that, not a cancellation.
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
