"""A closed dropdown never answers the mouse wheel; the panel behind it scrolls.

Qt delivers a wheel turn to the widget under the pointer. For a plain
`QComboBox` (and `QgsMapLayerComboBox`, `QgsFieldComboBox`) that is the combo
itself. For an editable combo, which is what `QgsFieldExpressionWidget` is made
of, it is the combo's line edit, and the line edit ignores the event so Qt then
hands it to the combo, which changes its value. An application-level filter sees
the event at the first receiver, so one filter covers every dropdown without
touching the widgets that create them. It re-sends the turn to the combo's
parent, which is exactly what Qt would have done had the combo ignored it, so
the enclosing scroll area scrolls.

An open popup is a separate widget (its list view is not the combo or its line
edit), so it is never touched and scrolls normally. Only widgets inside one of
this plugin's own windows are affected; QGIS's own dialogs keep their behaviour.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import traceback

from qgis.PyQt.QtCore import QEvent, QObject, QPoint, QPointF
from qgis.PyQt.QtGui import QWheelEvent
from qgis.PyQt.QtWidgets import QApplication, QComboBox, QLineEdit, QWidget

_PLUGIN_PACKAGE = __name__.split(".ui.", 1)[0]


def _in_plugin_window(widget: QWidget) -> bool:
    """True when `widget` sits inside a window class defined by this plugin."""
    node: QWidget | None = widget
    while node is not None:
        if type(node).__module__.startswith(_PLUGIN_PACKAGE):
            return True
        node = node.parentWidget()
    return False


def _owning_combo(receiver: QObject) -> QComboBox | None:
    """The combo whose value a wheel turn on `receiver` would change, if any."""
    if isinstance(receiver, QComboBox):
        return receiver
    if isinstance(receiver, QLineEdit):
        parent = receiver.parentWidget()
        if isinstance(parent, QComboBox) and parent.lineEdit() is receiver:
            return parent
    return None


class WheelGuard(QObject):
    """Application event filter: wheel turns over closed combos go to the parent."""

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        # An exception escaping a Qt callback aborts QGIS: never let one out.
        try:
            return self._redirect(watched, event)
        except Exception:
            traceback.print_exc()
            return False

    def _redirect(self, watched: QObject | None, event: QEvent | None) -> bool:
        if (
            watched is None
            or not isinstance(event, QWheelEvent)
            or not isinstance(watched, QWidget)
        ):
            return False
        combo = _owning_combo(watched)
        if combo is None or not _in_plugin_window(combo):
            return False
        # Qt only walks a wheel turn up the parents for events that came from
        # the window system, so the climb is done here, as Qt would do it.
        node = combo.parentWidget()
        while node is not None:
            offset = watched.mapTo(node, QPoint(0, 0))
            forwarded = QWheelEvent(
                event.position() + QPointF(offset),
                event.globalPosition(),
                event.pixelDelta(),
                event.angleDelta(),
                event.buttons(),
                event.modifiers(),
                event.phase(),
                event.inverted(),
            )
            forwarded.ignore()
            QApplication.sendEvent(node, forwarded)
            if forwarded.isAccepted():
                event.accept()
                return True
            if node.isWindow():
                break
            node = node.parentWidget()
        event.ignore()
        return True


_guard: WheelGuard | None = None


def install_wheel_guard() -> None:
    """Start guarding; safe to call more than once."""
    global _guard
    app = QApplication.instance()
    if app is None or _guard is not None:
        return
    _guard = WheelGuard(app)
    app.installEventFilter(_guard)


def remove_wheel_guard() -> None:
    """Stop guarding (plugin unload)."""
    global _guard
    app = QApplication.instance()
    if app is not None and _guard is not None:
        app.removeEventFilter(_guard)
        _guard.deleteLater()
    _guard = None
