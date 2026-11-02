"""Keeping a layout's Live counts in step with the maps they follow.

`core/live_count.py` decides what a counter counts. This is when: whenever
what a map frame shows could have changed, and without ever blocking the
interface or showing an old number as the current one.

What is listened to, instead of polled:

- the layout's undo stack (adding or deleting a counter, a change to its
  setup, undo and redo, a map's extent edited on the page);
- each map frame (`extentChanged`, `mapRotationChanged`, `crsChanged`,
  `themeChanged`, `layerStyleOverridesChanged`, `sizePositionChanged`, and
  `previewRefreshed`, the frame's picture being redrawn);
- the layer tree and the map themes (a layer shown, hidden, added, removed);
- the layers themselves, through the same watchers the rest of the dialog
  uses (`LayerContentWatcher`: a restyle, a renderer or category switched, a
  filter, a data edit).

A burst of signals starts one calculation after a short rest. A calculation
that is not trivial runs in slices on the GUI thread (QGIS layers are not
for other threads), a few milliseconds at a time, so the interface stays
alive; a newer request cancels an older one and a result only counts if it is
from the newest request. A count taking longer than `PENDING_MS` shows "…"
rather than the previous number.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Generator
from dataclasses import dataclass

from qgis.core import QgsLayout, QgsLayoutItemLabel, QgsLayoutItemMap
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

from ...core import live_count as lc
from ..layer_watcher import LayerContentWatcher, LayerTreeWatcher

#: How long signals must rest before a calculation starts.
DEBOUNCE_MS = 100
#: How long one slice of a calculation may run.
SLICE_SECONDS = 0.012
#: How long a calculation may take before the number reads "…".
PENDING_MS = 400

_MAP_SIGNALS = (
    "extentChanged",
    "mapRotationChanged",
    "crsChanged",
    "themeChanged",
    "layerStyleOverridesChanged",
    "sizePositionChanged",
    "previewRefreshed",
)


@dataclass
class _Job:
    uuid: str
    request: int
    steps: Generator[None, None, int]


class LiveCounts(QObject):
    """The Live counts of the layout on show."""

    #: A counter's number changed: the layout's content (and its file) did.
    content_changed = pyqtSignal()
    #: A counter's outcome (number or explanation) is new; its item's uuid.
    outcome_changed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._layout: QgsLayout | None = None
        self._project = None
        self._maps: dict[str, QgsLayoutItemMap] = {}
        self._outcomes: dict[str, lc.CountOutcome] = {}
        self._jobs: dict[str, _Job] = {}
        self._request = 0
        self._tree: LayerTreeWatcher | None = None
        self._content: LayerContentWatcher | None = None

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._start)
        self._slice = QTimer(self)
        self._slice.setSingleShot(True)
        self._slice.setInterval(0)
        self._slice.timeout.connect(self._run_slice)
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(PENDING_MS)
        self._pending.timeout.connect(self._show_pending)

    # ---- which layout ----------------------------------------------------

    def set_layout(self, layout: QgsLayout | None) -> None:
        if layout is self._layout:
            return
        self._detach()
        if layout is None or sip.isdeleted(layout):
            return
        self._layout = layout
        project = layout.project()
        self._project = project
        layout.undoStack().stack().indexChanged.connect(self._on_stack_changed)
        self._watch_maps()
        if project is not None:
            self._tree = LayerTreeWatcher(project, self)
            self._tree.changed.connect(self.mark_dirty)
            self._content = LayerContentWatcher(project, self)
            self._content.changed.connect(self.mark_dirty)
            themes = project.mapThemeCollection()
            for name in ("mapThemeChanged", "mapThemesChanged"):
                signal = getattr(themes, name, None)
                if signal is not None:
                    signal.connect(self.mark_dirty)
        self.mark_dirty()

    def _detach(self) -> None:
        self._debounce.stop()
        self._cancel_jobs()
        layout, self._layout = self._layout, None
        if layout is not None and not sip.isdeleted(layout):
            with contextlib.suppress(TypeError, RuntimeError):
                layout.undoStack().stack().indexChanged.disconnect(
                    self._on_stack_changed
                )
        project, self._project = self._project, None
        if project is not None:
            themes = project.mapThemeCollection()
            for name in ("mapThemeChanged", "mapThemesChanged"):
                signal = getattr(themes, name, None)
                if signal is not None:
                    with contextlib.suppress(TypeError, RuntimeError):
                        signal.disconnect(self.mark_dirty)
        for watcher in (self._tree, self._content):
            if watcher is not None:
                watcher.disconnect_all()
                watcher.deleteLater()
        self._tree = self._content = None
        for item in self._maps.values():
            self._unwatch(item)
        self._maps.clear()
        self._outcomes.clear()

    def close(self) -> None:
        """Stop listening (the dialog is closing)."""
        self._detach()

    def _watch_maps(self) -> None:
        layout = self._layout
        if layout is None or sip.isdeleted(layout):
            return
        current = {
            item.uuid(): item
            for item in layout.items()
            if isinstance(item, QgsLayoutItemMap)
        }
        for uuid in list(self._maps):
            if uuid not in current:
                self._unwatch(self._maps.pop(uuid))
        for uuid, item in current.items():
            if uuid not in self._maps:
                self._maps[uuid] = item
                for name in _MAP_SIGNALS:
                    signal = getattr(item, name, None)
                    if signal is not None:
                        signal.connect(self.mark_dirty)

    def _unwatch(self, item: QgsLayoutItemMap) -> None:
        if sip.isdeleted(item):
            return
        for name in _MAP_SIGNALS:
            signal = getattr(item, name, None)
            if signal is not None:
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect(self.mark_dirty)

    def _on_stack_changed(self, *_args: object) -> None:
        self._watch_maps()
        self.mark_dirty()

    # ---- asking for a calculation ----------------------------------------

    def mark_dirty(self, *_args: object) -> None:
        """What a counter shows may have changed: calculate again once the
        signals have rested. Anything under way is stale from now on."""
        if self._layout is None or sip.isdeleted(self._layout):
            return
        if self._jobs:
            self._cancel_jobs()
        self._debounce.start()

    def recount_now(self) -> None:
        """Calculate to the end, here and now: before an export or a save, so
        the number is that of the view that leaves."""
        layout = self._layout
        if layout is None or sip.isdeleted(layout):
            return
        self._debounce.stop()
        self._cancel_jobs()
        self._request += 1
        for uuid, outcome in lc.refresh_live_counts(layout).items():
            self._store(uuid, outcome)

    def settle(self) -> None:
        """Make the numbers final if a calculation is waiting or running; do
        nothing when they already are (a large layer is not scanned again
        for every save)."""
        if self.busy():
            self.recount_now()

    def outcome(self, number: QgsLayoutItemLabel) -> lc.CountOutcome | None:
        """The latest outcome of a counter, if one has been calculated."""
        return self._outcomes.get(number.uuid())

    def running(self) -> bool:
        """Whether a calculation is under way (not merely waiting to start)."""
        return bool(self._jobs)

    def busy(self) -> bool:
        return bool(self._jobs) or self._debounce.isActive()

    # ---- the calculation ---------------------------------------------------

    def _cancel_jobs(self) -> None:
        self._pending.stop()
        self._slice.stop()
        for job in self._jobs.values():
            with contextlib.suppress(Exception):
                job.steps.close()
        self._jobs.clear()

    def _start(self) -> None:
        layout = self._layout
        if layout is None or sip.isdeleted(layout):
            return
        self._cancel_jobs()
        self._request += 1
        request = self._request
        live = {number.uuid() for number in lc.live_counts(layout)}
        for uuid in list(self._outcomes):
            if uuid not in live:
                del self._outcomes[uuid]
        for number in lc.live_counts(layout):
            try:
                prepared = lc.prepare(layout, number)
            except Exception as exc:
                prepared = lc.failure(exc)
            if isinstance(prepared, lc.CountOutcome):
                self._finish(number.uuid(), prepared)
            else:
                self._jobs[number.uuid()] = _Job(
                    number.uuid(), request, prepared.steps()
                )
        if self._jobs:
            self._pending.start()
            self._slice.start()

    def _run_slice(self) -> None:
        deadline = time.monotonic() + SLICE_SECONDS
        for uuid, job in list(self._jobs.items()):
            if self._jobs.get(uuid) is not job:
                continue
            while True:
                try:
                    next(job.steps)
                except StopIteration as done:
                    self._jobs.pop(uuid, None)
                    self._finish(uuid, lc.counted(int(done.value)), job.request)
                    break
                except Exception as exc:  # a layer deleted under the loop, ...
                    self._jobs.pop(uuid, None)
                    self._finish(uuid, lc.failure(exc), job.request)
                    break
                if time.monotonic() >= deadline:
                    break
            if time.monotonic() >= deadline:
                break
        if self._jobs:
            self._slice.start()
        else:
            self._pending.stop()

    def _show_pending(self) -> None:
        """A count is taking its time: the old number must not stand for it."""
        for uuid in list(self._jobs):
            self._store(uuid, lc.CountOutcome(lc.CountState.PENDING))

    def _finish(
        self, uuid: str, outcome: lc.CountOutcome, request: int | None = None
    ) -> None:
        if request is not None and request != self._request:
            return  # a newer calculation was asked for: this one is stale
        self._store(uuid, outcome)

    def _store(self, uuid: str, outcome: lc.CountOutcome) -> None:
        layout = self._layout
        if layout is None or sip.isdeleted(layout):
            return
        number = layout.itemByUuid(uuid)
        if not lc.is_count_number(number):
            self._outcomes.pop(uuid, None)
            return
        self._outcomes[uuid] = outcome
        changed = lc.apply_outcome(number, outcome)
        self.outcome_changed.emit(uuid)
        if changed and outcome.state is not lc.CountState.PENDING:
            self.content_changed.emit()
