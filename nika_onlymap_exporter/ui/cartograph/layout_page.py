"""The Cartograph tab's three parts: the page, the layout list, the file line.

The tab used to be three stacked boxes - Source, Cartograph file, Page - with
no picture of the page anywhere. Every product we compared (ArcGIS Pro's
layout view, Felt's print mode, Canva) leads with the page itself, so the tab
is now split: `LayoutPreview` on the left is QGIS's own layout canvas, live -
it is the same scene the layout designer draws, so a change there, a restyled
layer or a new title shows at once - and the few settings sit beside it.

- `LayoutPicker` is the layout list, with "From canvas..." and "Import..." at
  its end, where ArcGIS Pro keeps "New layout": both make a layout, which is
  what the list chooses between. They were a radio button that acted like a
  button and a button beside the list.
- `SaveLocation` says where the file saves, in one line. The path was a text
  box asked for before anything else; the file saves itself, so where it goes
  is a detail to change, not a question to answer first.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.gui import QgsLayoutView, QgsLayoutViewToolPan
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QPalette, QResizeEvent, QShowEvent, QStandardItemModel
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

# What a row of the layout list is, in its `Qt.UserRole` data.
LAYOUT, FROM_CANVAS, IMPORT = "layout", "canvas", "import"
_KIND = Qt.ItemDataRole.UserRole


class LayoutPicker(QComboBox):
    """The project's print layouts, then the two ways to make another.

    `currentText()` is always a layout's name or "": picking one of the two
    actions at the end puts the list back where it was and emits the action's
    signal instead, so the rest of the tab never sees them as a selection.
    """

    from_canvas_requested = pyqtSignal()
    import_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(tr("No print layouts yet"))
        self._last_layout_index = -1
        self._canvas_reason = ""
        self.activated.connect(self._on_activated)
        self.currentIndexChanged.connect(self._remember)

    def set_layouts(self, names: list[str]) -> None:
        """Replace the list. Signals are the caller's to block."""
        self.clear()
        for name in names:
            self.addItem(name, LAYOUT)
        if names:
            self.insertSeparator(self.count())
        self.addItem(tr("From canvas..."), FROM_CANVAS)
        self.addItem(tr("Import cartograph..."), IMPORT)
        self.setItemData(
            self.count() - 1,
            tr("Turn a cartograph HTML file back into a QGIS print layout"),
            Qt.ItemDataRole.ToolTipRole,
        )
        self._apply_canvas_state()
        if not names:
            self.setCurrentIndex(-1)

    def select_layout(self, name: str) -> None:
        """Show `name` without emitting anything - for the caller's own
        refreshes, which must still be what an action returns the list to."""
        self.blockSignals(True)
        self.setCurrentIndex(self.findText(name) if name else -1)
        self.blockSignals(False)
        self._remember(self.currentIndex())

    def current_layout(self) -> str:
        """The layout shown, or "" while an action row is passing through."""
        return self.currentText() if self.currentData() == LAYOUT else ""

    def set_canvas_available(self, available: bool, reason: str = "") -> None:
        """Grey out "From canvas..." when there is no canvas, saying why."""
        self._canvas_reason = "" if available else reason
        self._apply_canvas_state()

    def can_build_from_canvas(self) -> bool:
        index = self.findData(FROM_CANVAS)
        model = self.model()
        if index < 0 or not isinstance(model, QStandardItemModel):
            return False
        item = model.item(index)
        return item is not None and item.isEnabled()

    def canvas_tooltip(self) -> str:
        index = self.findData(FROM_CANVAS)
        return str(self.itemData(index, Qt.ItemDataRole.ToolTipRole) or "")

    def _apply_canvas_state(self) -> None:
        index = self.findData(FROM_CANVAS)
        model = self.model()
        if index < 0 or not isinstance(model, QStandardItemModel):
            return
        item = model.item(index)
        if item is None:
            return
        item.setEnabled(not self._canvas_reason)
        item.setToolTip(
            self._canvas_reason
            or tr(
                "Builds a print layout from what the map canvas shows - title, "
                "map, legend, scale bar and north arrow - and uses it here. "
                "Edit it in the layout designer any time."
            )
        )

    def _remember(self, index: int) -> None:
        if index >= 0 and self.itemData(index) == LAYOUT:
            self._last_layout_index = index

    def _on_activated(self, index: int) -> None:
        kind = self.itemData(index)
        if kind == LAYOUT:
            return
        # Back to the layout that was showing before the action was picked.
        self.blockSignals(True)
        self.setCurrentIndex(
            self._last_layout_index
            if self._last_layout_index < self.count()
            and self.itemData(self._last_layout_index) == LAYOUT
            else -1
        )
        self.blockSignals(False)
        if kind == FROM_CANVAS:
            self.from_canvas_requested.emit()
        elif kind == IMPORT:
            self.import_requested.emit()


class LayoutPreview(QStackedWidget):
    """The layout's page, as the layout designer draws it - or, with no
    layout, the two ways to make one.

    Read-only: the pan tool is the only tool, so a drag moves the view and
    the wheel zooms, but nothing on the page can be moved from here.
    """

    from_canvas_requested = pyqtSignal()
    import_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = QgsLayoutView(self)
        self.view.setProperty("role", "layoutPreview")
        self._pan = QgsLayoutViewToolPan(self.view)
        self.view.setTool(self._pan)
        self.addWidget(self.view)

        empty = QFrame(self)
        empty.setProperty("role", "layoutEmpty")
        column = QVBoxLayout(empty)
        column.addStretch(1)
        heading = QLabel(tr("No print layout yet"), empty)
        heading.setProperty("role", "layoutEmptyHeading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(heading)
        note = QLabel(
            tr("Build one from what the map canvas shows, or open a cartograph."),
            empty,
        )
        note.setProperty("role", "layoutEmptyNote")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        column.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.canvas_button = QToolButton(empty)
        self.canvas_button.setText(tr("From canvas..."))
        self.canvas_button.setProperty("role", "primary")
        self.canvas_button.setProperty("hasMenu", False)
        self.canvas_button.clicked.connect(self.from_canvas_requested)
        buttons.addWidget(self.canvas_button)
        self.import_button = QToolButton(empty)
        self.import_button.setText(tr("Import cartograph..."))
        self.import_button.setProperty("role", "secondary")
        self.import_button.setProperty("hasMenu", False)
        self.import_button.clicked.connect(self.import_requested)
        buttons.addWidget(self.import_button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        column.addStretch(1)
        self.addWidget(empty)
        self._empty = empty

        # The page fills the space - on first show, on a resize, on another
        # layout - until the user zooms; their zoom then stays, until another
        # layout is chosen.
        self._user_zoomed = False
        self._fitting = False
        self.view.zoomLevelChanged.connect(self._on_zoom_changed)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit)

    def show_layout(self, layout) -> None:  # QgsPrintLayout | None
        if layout is None:
            self.setCurrentWidget(self._empty)
            return
        if self.view.currentLayout() is not layout:
            self.view.setCurrentLayout(layout)
            self._user_zoomed = False
        self.setCurrentWidget(self.view)
        self._fit_timer.start()

    def showing_page(self) -> bool:
        return self.currentWidget() is self.view

    def _fit(self) -> None:
        if not (self.isVisible() and self.showing_page()) or self._user_zoomed:
            return
        self._fitting = True
        try:
            self.view.zoomFull()
        finally:
            self._fitting = False

    def _on_zoom_changed(self) -> None:
        if not self._fitting:
            self._user_zoomed = True

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        # Around the page, the dialog's own window colour rather than the
        # designer's grey; the theme sets it on the palette. The window's
        # palette, which the theme sets, not this widget's inherited copy.
        self.view.setBackgroundBrush(
            self.window().palette().color(QPalette.ColorRole.Window)
        )
        self._fit_timer.start()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_timer.start()


class SaveLocation(QWidget):
    """`Saves to ~/Documents/parcels.html  Change...`, in one line.

    Holds the path the way the text box it replaces did - `text()` and
    `setText()` - and emits `textEdited` only when the user picks a file, so
    the controller's rules for a typed path apply unchanged.
    """

    textEdited = pyqtSignal(str)  # noqa: N815 - mirrors QLineEdit's
    change_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._path = ""
        self._placeholder = ""
        self.label = QLabel(self)
        self.label.setProperty("role", "saveLocation")
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setMinimumWidth(1)
        row.addWidget(self.label, 1)
        self.change_button = QPushButton(tr("Change..."), self)
        self.change_button.setProperty("role", "link")
        self.change_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_button.clicked.connect(self.change_requested)
        row.addWidget(self.change_button)

    def text(self) -> str:
        return self._path

    def setText(self, path: str) -> None:  # noqa: N802 - mirrors QLineEdit
        self._path = path
        self._show()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - mirrors QLineEdit
        self._placeholder = text
        self._show()

    def placeholderText(self) -> str:  # noqa: N802 - mirrors QLineEdit
        return self._placeholder

    def choose(self, path: str) -> None:
        """A path the user picked: shown, and reported as their edit."""
        self.setText(path)
        self.textEdited.emit(path)

    def _show(self) -> None:
        self.label.setToolTip(self._path)
        self._elide()

    def _elide(self) -> None:
        if not self._path:
            self.label.setText(self._placeholder)
            return
        prefix = tr("Saves to {path}")
        metrics = self.label.fontMetrics()
        room = max(40, self.label.width() - metrics.horizontalAdvance(prefix))
        shown = metrics.elidedText(self._path, Qt.TextElideMode.ElideMiddle, room)
        self.label.setText(prefix.format(path=shown))

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()
