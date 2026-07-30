"""Keeps a view in step with the QGIS layer tree.

The incumbent's dialog reads the layer tree once when it opens and never again.
Its only refresh is `Set All -> Apply`, whose actual job is mutating a bulk
setting - so picking up a reorder costs a settings change, and undoing that costs
a second Apply. Three separate upstream issues (#131, #132, #133) are people
asking for settings that should have been per-layer in the first place.

Three rules, learned from that:

1. **Coalesce.** One drag in the Layers panel fires `willRemoveChildren`,
   `removedChildren`, `addedChildren` *and* `layerOrderChanged`. Rebuilding per
   signal thrashes; a zero-delay single-shot timer collapses them into one
   rebuild per event-loop turn.
2. **Never store settings in widgets.** They live in `DialogState`, keyed by
   layer id, so a rebuild re-binds rather than resets.
3. **Disconnect on close.** A rebuild scheduled against a destroyed dialog is a
   crash, and the user sees QGIS disappear.

There is no refresh button, because there is nothing to refresh.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Callable

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


class LayerTreeWatcher(QObject):
    """Emits `changed` once per event-loop turn when the layer tree moves.

    Signals watched, verified present on QGIS 4.0.3 and 3.22:

    | Signal | Fires on |
    |---|---|
    | `layerOrderChanged` | draw-order change - the main one |
    | `addedChildren` / `removedChildren` | add, remove, drag between groups |
    | `visibilityChanged` | checkbox toggled in the Layers panel |
    | `nameChanged` | layer or group renamed |
    | `customLayerOrderChanged` | a custom draw order was enabled |
    | `layersAdded` / `layersRemoved` | the project's layer set changed |
    """

    changed = pyqtSignal()

    def __init__(self, project: QgsProject | None = None, parent=None) -> None:
        super().__init__(parent)
        self._project = project or QgsProject.instance()
        self._connections: list[tuple[object, str, Callable]] = []

        # Single-shot with a zero interval: fires once, on the next turn of the
        # event loop, however many signals arrived in this one.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.changed.emit)

        self._connect()

    # ---- wiring ---------------------------------------------------------

    def _connect(self) -> None:
        root = self._project.layerTreeRoot()

        tree_signals = (
            "layerOrderChanged",
            "addedChildren",
            "removedChildren",
            "visibilityChanged",
            "nameChanged",
            "customLayerOrderChanged",
        )
        for name in tree_signals:
            self._bind(root, name)

        for name in ("layersAdded", "layersRemoved"):
            self._bind(self._project, name)

    def _bind(self, source: object, signal_name: str) -> None:
        """Connect one signal, skipping any this QGIS version lacks.

        Probing rather than branching on version: the set of signals differs a
        little across the QGIS versions we support, and a missing one should cost
        a little responsiveness, never an exception at dialog-open time.
        """
        signal = getattr(source, signal_name, None)
        if signal is None:
            return
        try:
            signal.connect(self._schedule)
        except (TypeError, AttributeError):  # pragma: no cover - defensive
            return
        self._connections.append((source, signal_name, self._schedule))

    def _schedule(self, *_args) -> None:
        """Mark dirty. The timer collapses a burst into one rebuild."""
        self._timer.start()

    # ---- teardown -------------------------------------------------------

    def disconnect_all(self) -> None:
        """Undo every connection. Must be called when the dialog closes."""
        self._timer.stop()
        for source, signal_name, slot in self._connections:
            signal = getattr(source, signal_name, None)
            if signal is None:
                continue
            # Already gone is fine: a cleared project disposes its tree nodes.
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        self._connections.clear()
