"""The places Qt 5 and Qt 6, or QGIS 3.44 and QGIS 4, still differ for us.

One switch each, so a caller reads the same on both and dropping a version is
an edit here. Only binding and API shapes live here: what a value means (font
weights, dash patterns, render units) stays in its own module.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import Any

from qgis.core import Qgis, QgsLayoutItemLegend
from qgis.PyQt.QtCore import QPoint, QPointF

try:  # QtGui on Qt 6 (QGIS 4), QtWidgets on Qt 5 (QGIS 3).
    from qgis.PyQt.QtGui import QUndoCommand
except ImportError:  # pragma: no cover - QGIS 3
    from qgis.PyQt.QtWidgets import QUndoCommand  # type: ignore[attr-defined,no-redef]

__all__ = [
    "QUndoCommand",
    "detached_started",
    "enum_value",
    "event_global_point",
    "event_point",
    "legend_is_manual",
    "set_legend_manual",
]


def enum_value(value: Any) -> int:
    """An enum member's number: a sip enum (Qt 5) is an int already, a Python
    enum (Qt 6) carries it as `.value`."""
    return int(getattr(value, "value", value))


def event_point(event: Any) -> QPoint:
    """A mouse or wheel event's position in its widget.

    Qt 6 has `position()`; Qt 5 (QGIS 3) only `pos()`.
    """
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


def event_global_point(event: Any) -> QPointF:
    """A mouse event's position on the screen.

    Qt 6 has `globalPosition()`; Qt 5 (QGIS 3) only `globalPos()`.
    """
    if hasattr(event, "globalPosition"):
        return QPointF(event.globalPosition())
    return QPointF(event.globalPos())


def detached_started(result: Any) -> bool:
    """Whether `QProcess.startDetached` started the program.

    PyQt5 returns a bool; PyQt6 returns (bool, pid).
    """
    return bool(result[0] if isinstance(result, tuple) else result)


def legend_is_manual(legend: QgsLayoutItemLegend) -> bool:
    """Whether the user edits this legend's content by hand.

    QGIS 4 replaced `autoUpdateModel()` with `syncMode()` (deprecated, still
    present); QGIS 3.44 only has the former.
    """
    sync_mode = getattr(legend, "syncMode", None)
    if sync_mode is not None:  # QGIS >= 4.0
        return bool(sync_mode() == Qgis.LegendSyncMode.Manual)
    return not legend.autoUpdateModel()


def set_legend_manual(legend: QgsLayoutItemLegend) -> None:
    """Make a legend's content the user's to edit, not the project's."""
    if hasattr(legend, "setSyncMode"):  # QGIS >= 4.0
        legend.setSyncMode(Qgis.LegendSyncMode.Manual)
    else:  # QGIS 3: the same switch, deprecated in 4.0
        legend.setAutoUpdateModel(False)
