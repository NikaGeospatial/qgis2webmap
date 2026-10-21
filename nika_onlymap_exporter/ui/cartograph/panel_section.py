"""One collapsible section of the Cartograph panel: `▸ Map frames  2 live`.

The Cartograph output was seven tabs, most of which hid the page. Every tool
people already know for this - ArcGIS Pro's layout view, QGIS's own layout
designer, Felt, Datawrapper - keeps the page in view and the settings in one
panel beside it, in sections that open and close. Closed, each section still
says how it stands in a few words, so the panel reads at a glance and
nothing has to be opened to check it.

Which sections are open is remembered per user (`QSettings`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QSettings, Qt, pyqtSignal
from qgis.PyQt.QtGui import QResizeEvent
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

#: Where each section's open state is kept, under `<prefix>/<key>`.
SETTINGS_PREFIX = "qgis2webmap/cartograph/sections"


class PanelSection(QFrame):
    """A header that opens and closes the body under it.

    `body` is the section's own widget; `body_layout` is where its contents
    go. `toggled` fires with the new state when the user opens or closes it.
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        key: str,
        title: str,
        parent: QWidget | None = None,
        *,
        open_by_default: bool = False,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.setProperty("role", "panelSection")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.header = QToolButton(self)
        self.header.setProperty("role", "sectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # Right to left, Qt draws the arrow over a header stretched across
        # the row, so there it keeps its own width and starts the row; the
        # summary goes to the far end after a stretch.
        rtl = self.isRightToLeft()
        self.header.setSizePolicy(
            QSizePolicy.Policy.Preferred if rtl else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.header.toggled.connect(self._on_header_toggled)
        header.addWidget(self.header, 0 if rtl else 1)
        if rtl:
            header.addStretch(1)
        # Its own label rather than part of the button's text, so it can be
        # quieter and sit at the right; clicks pass through to the header.
        self._summary_text = ""
        self.summary = QLabel("", self)
        self.summary.setProperty("role", "sectionSummary")
        self.summary.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(self.summary)
        column.addLayout(header)

        self.body = QWidget(self)
        self.body.setProperty("role", "sectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4, 4, 0, 10)
        self.body_layout.setSpacing(6)
        column.addWidget(self.body)

        stored = QSettings().value(
            f"{SETTINGS_PREFIX}/{key}", open_by_default, type=bool
        )
        self.header.setChecked(bool(stored))
        self._show(bool(stored))

    def set_summary(self, text: str) -> None:
        self._summary_text = text
        self.summary.setToolTip(text)
        self._elide_summary()

    def summary_text(self) -> str:
        return self._summary_text

    def _elide_summary(self) -> None:
        # Shortened to the room the title leaves, so a long summary never
        # widens the panel.
        title = self.header.fontMetrics().horizontalAdvance(self.header.text())
        room = max(40, self.width() - title - 60)
        metrics = self.summary.fontMetrics()
        self.summary.setText(
            metrics.elidedText(self._summary_text, Qt.TextElideMode.ElideRight, room)
        )

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide_summary()

    def is_open(self) -> bool:
        return self.header.isChecked()

    def set_open(self, open_: bool) -> None:
        """Open or close it, as a click on the header would."""
        self.header.setChecked(open_)

    def _on_header_toggled(self, open_: bool) -> None:
        self._show(open_)
        QSettings().setValue(f"{SETTINGS_PREFIX}/{self.key}", open_)
        self.toggled.emit(open_)

    def _show(self, open_: bool) -> None:
        self.body.setVisible(open_)
        closed = (
            Qt.ArrowType.LeftArrow if self.isRightToLeft() else Qt.ArrowType.RightArrow
        )
        self.header.setArrowType(Qt.ArrowType.DownArrow if open_ else closed)
