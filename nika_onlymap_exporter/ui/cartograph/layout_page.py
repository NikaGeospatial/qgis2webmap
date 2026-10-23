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

from pathlib import Path
from types import SimpleNamespace

from qgis.gui import QgsLayoutView, QgsLayoutViewToolPan, QgsLayoutViewToolSelect
from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import (
    QKeyEvent,
    QKeySequence,
    QPalette,
    QResizeEvent,
    QShowEvent,
    QStandardItemModel,
)
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import plural, tr

#: The layout list's narrowest, beside its Add... button in the panel.
PICKER_MIN_WIDTH = 120

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
        # A long layout name shortens rather than widening the panel past
        # the dialog's edge; the full name is in the open list. An explicit
        # minimum, because a styled combo box otherwise reports its longest
        # item as the least it can take.
        self.setMinimumWidth(PICKER_MIN_WIDTH)
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

    Two tools, as in the designer: Select (the default), QGIS's own select
    tool, which moves and resizes items as undo commands on the layout's
    stack; and Pan, where a drag moves the view and nothing on the page. The
    wheel zooms in both; Space or the middle button pans while Select is on,
    which QgsLayoutView does itself.
    """

    from_canvas_requested = pyqtSignal()
    import_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = QgsLayoutView(self)
        self.view.setProperty("role", "layoutPreview")
        self._pan = QgsLayoutViewToolPan(self.view)
        # The select tool takes the view's mouse handles, which exist only
        # once the view has a layout: it is made in `show_layout`, and
        # made before that it crashes on the first wheel turn.
        self._select: QgsLayoutViewToolSelect | None = None
        self._select_mode = True
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
        # A zoom is the user's when it comes from their wheel or the zoom
        # buttons: the view also reports a zoom change when it is merely
        # resized, which once stopped the page fitting at all.
        self._user_zoomed = False
        self.view.viewport().installEventFilter(self)
        # Ctrl+Z / Ctrl+Shift+Z in the page undo and redo its edits, on the
        # layout's own stack as in the designer. A key filter rather than a
        # QShortcut: that one waits for the window to be the active one.
        self.view.installEventFilter(self)
        # Not the first Tab stop: opening the dialog put the focus ring round
        # the page. A click still focuses it; the zoom bar is the keyboard's.
        self.view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
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
            self._select = QgsLayoutViewToolSelect(self.view)
            self._select.setLayout(layout)
            self.set_select_mode(self._select_mode)
            self._user_zoomed = False
        self.setCurrentWidget(self.view)
        self._fit_timer.start()

    def showing_page(self) -> bool:
        return self.currentWidget() is self.view

    def fit_page(self) -> None:
        """Fit page: the whole page again, and fitted from now on, as if
        the user had never zoomed."""
        self._user_zoomed = False
        self._fit()
        # Again once the zoomed page's scroll bars have gone: the first fit
        # was measured with them still taking room.
        self._fit_timer.start()

    def set_select_mode(self, select: bool) -> None:
        """Select/Move (True) or Pan (False)."""
        self._select_mode = select
        self.view.setTool(self._select if select and self._select else self._pan)

    def select_mode(self) -> bool:
        return self._select is not None and self.view.tool() is self._select

    def _step_undo(self, redo: bool) -> None:
        layout = self.view.currentLayout()
        if layout is None:
            return
        stack = layout.undoStack().stack()
        stack.redo() if redo else stack.undo()

    def zoom_in(self) -> None:
        self._user_zoomed = True
        self.view.zoomIn()

    def zoom_out(self) -> None:
        self._user_zoomed = True
        self.view.zoomOut()

    def _fit(self) -> None:
        if not (self.isVisible() and self.showing_page()) or self._user_zoomed:
            return
        self.view.zoomFull()

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if (
            event is not None
            and watched is self.view
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            if event.matches(QKeySequence.StandardKey.Undo):
                self._step_undo(redo=False)
                return True
            if event.matches(QKeySequence.StandardKey.Redo):
                self._step_undo(redo=True)
                return True
        if event is not None and (
            event.type() == QEvent.Type.Wheel
            # Having selected or moved something, the page stays as the user
            # has it: an edit's refresh must not fit the page under their hand.
            or (event.type() == QEvent.Type.MouseButtonPress and self.select_mode())
        ):
            self._user_zoomed = True
        return False

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


class PagePane(QWidget):
    """The preview with what it is said above it and how to move it below.

    Above: "QGIS layout · A4 landscape". The page is QGIS's drawing of the
    layout, and the cartograph can differ from it - a north arrow the
    runtime draws its own way, the Page settings applied on export - so it
    is named for what it is; Preview shows the HTML itself. Below: Fit page,
    zoom out, the zoom level and zoom in, which the wheel and a drag also do
    but nothing on screen said so.
    """

    shown = pyqtSignal()
    changes_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self._change_count = 0
        self.caption = QLabel(self)
        self.caption.setProperty("role", "pageCaption")
        self.caption.setToolTip(
            tr(
                "QGIS's drawing of the print layout. The cartograph can differ "
                "in places - see Checks - and Preview shows it in the browser."
            )
        )
        caption_row = QHBoxLayout()
        caption_row.setSpacing(8)
        caption_row.addWidget(self.caption)
        caption_row.addStretch(1)
        # Beside the caption, so the page and how the HTML differs from it
        # are read together: the differences themselves are in Checks.
        self.changes_link = QPushButton(self)
        self.changes_link.setProperty("role", "link")
        self.changes_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.changes_link.setToolTip(tr("See them in Checks"))
        self.changes_link.clicked.connect(self.changes_requested)
        self.changes_link.setVisible(False)
        caption_row.addWidget(self.changes_link)
        column.addLayout(caption_row)
        self.preview = LayoutPreview(self)
        column.addWidget(self.preview, 1)

        self.zoom_bar = QWidget(self)
        bar = QHBoxLayout(self.zoom_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        self.select_button = self._bar_button(
            tr("Select"), tr("Select, move and resize items (hold Space to pan)")
        )
        self.pan_button = self._bar_button(tr("Pan"), tr("Pan the page"))
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        for button in (self.select_button, self.pan_button):
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            modes.addButton(button)
            bar.addWidget(button)
        self.select_button.setChecked(True)
        self._modes = modes
        self.select_button.toggled.connect(self.preview.set_select_mode)
        self.fit_button = self._bar_button(tr("Fit page"), tr("Fit the whole page"))
        self.fit_button.clicked.connect(self.preview.fit_page)
        bar.addWidget(self.fit_button)
        bar.addStretch(1)
        self.zoom_out_button = self._bar_button("\N{MINUS SIGN}", tr("Zoom out"))
        self.zoom_out_button.clicked.connect(self.preview.zoom_out)
        bar.addWidget(self.zoom_out_button)
        self.zoom_label = QLabel(self.zoom_bar)
        self.zoom_label.setProperty("role", "hint")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.zoom_label)
        self.zoom_in_button = self._bar_button("+", tr("Zoom in"))
        self.zoom_in_button.clicked.connect(self.preview.zoom_in)
        bar.addWidget(self.zoom_in_button)
        column.addWidget(self.zoom_bar)

        self.preview.view.zoomLevelChanged.connect(self._show_zoom)
        self.preview.currentChanged.connect(lambda _index: self._show_page_parts())
        self._show_page_parts()

    def set_change_count(self, count: int) -> None:
        """How many things the HTML changes from this page; none hides it."""
        self._change_count = count
        self.changes_link.setText(
            plural(count, "{n} change in the HTML", "{n} changes in the HTML")
        )
        self._show_page_parts()

    def _bar_button(self, text: str, name: str) -> QToolButton:
        button = QToolButton(self.zoom_bar)
        button.setText(text)
        button.setToolTip(name)
        button.setAccessibleName(name)
        button.setProperty("role", "secondary")
        button.setProperty("hasMenu", False)
        button.setProperty("zoom", True)
        return button

    def show_layout(self, layout) -> None:  # QgsPrintLayout | None
        self.preview.show_layout(layout)
        self.caption.setText(
            tr("QGIS layout · {page}").format(page=_page_name(layout))
            if layout is not None
            else ""
        )
        self._show_page_parts()

    def _show_page_parts(self) -> None:
        showing = self.preview.showing_page()
        self.caption.setVisible(showing)
        self.changes_link.setVisible(showing and self._change_count > 0)
        self.zoom_bar.setVisible(showing)
        self._show_zoom()

    def _show_zoom(self, *_args: object) -> None:
        # 100% is the page at its real size on this screen, as QGIS's own
        # layout designer counts it.
        view = self.preview.view
        per_mm = view.physicalDpiX() / 25.4
        level = view.transform().m11() / per_mm if per_mm else 0.0
        self.zoom_label.setText(f"{round(level * 100)}%")

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.shown.emit()


def _page_name(layout) -> str:  # QgsPrintLayout
    """ "A4 landscape", or "216 x 279 mm portrait", for the first page."""
    from qgis.core import QgsLayoutMeasurementConverter, QgsUnitTypes

    from .status import page_label

    pages = layout.pageCollection()
    if pages.pageCount() == 0:
        return ""
    size = QgsLayoutMeasurementConverter().convert(
        pages.page(0).pageSize(), QgsUnitTypes.LayoutMillimeters
    )
    width, height = size.width(), size.height()
    orientation = "landscape" if width >= height else "portrait"
    spec = SimpleNamespace(width=round(width), height=round(height), size=None)
    spec.orientation = tr(orientation.capitalize()).lower()
    return page_label(spec) or ""


class SaveLocation(QWidget):
    """Where the cartograph saves: its file name, then the folder under it.

        parcels-Survey.html                     Change...
        ~/Documents/QGIS2WebMap/Cartographs     Show in folder

    The name first and whole, because it is what someone sharing the file
    looks for; the folder below, shortened in the middle if it must be, with
    the full path as its tooltip. It was one line - "Saves to" and a path
    cut in the middle - which hid the one part people check.

    Holds the path the way the text box it replaced did - `text()` and
    `setText()` - and emits `textEdited` only when the user picks a file, so
    the controller's rules for a typed path apply unchanged.
    """

    textEdited = pyqtSignal(str)  # noqa: N815 - mirrors QLineEdit's
    change_requested = pyqtSignal()
    reveal_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(0)
        self._path = ""
        self._placeholder = ""
        self.label = QLabel(self)
        self.label.setProperty("role", "saveName")
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setMinimumWidth(1)
        grid.addWidget(self.label, 0, 0)
        self.folder_label = QLabel(self)
        self.folder_label.setProperty("role", "saveLocation")
        self.folder_label.setMinimumWidth(1)
        grid.addWidget(self.folder_label, 1, 0)
        self.change_button = QPushButton(tr("Change..."), self)
        self.change_button.setProperty("role", "link")
        self.change_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_button.clicked.connect(self.change_requested)
        grid.addWidget(self.change_button, 0, 1, Qt.AlignmentFlag.AlignRight)
        self.reveal_button = QPushButton(tr("Show in folder"), self)
        self.reveal_button.setProperty("role", "link")
        self.reveal_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_button.clicked.connect(self.reveal_requested)
        grid.addWidget(self.reveal_button, 1, 1, Qt.AlignmentFlag.AlignRight)
        grid.setColumnStretch(0, 1)

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
        self.folder_label.setToolTip(str(Path(self._path).parent) if self._path else "")
        has_path = bool(self._path)
        self.folder_label.setVisible(has_path)
        self.reveal_button.setVisible(has_path)
        self._elide()

    def _elide(self) -> None:
        if not self._path:
            self.label.setText(self._placeholder)
            return
        path = Path(self._path)
        name_metrics = self.label.fontMetrics()
        self.label.setText(
            name_metrics.elidedText(
                path.name, Qt.TextElideMode.ElideMiddle, max(40, self.label.width())
            )
        )
        folder = _home_relative(path.parent)
        metrics = self.folder_label.fontMetrics()
        self.folder_label.setText(
            metrics.elidedText(
                folder, Qt.TextElideMode.ElideMiddle, max(40, self.folder_label.width())
            )
        )

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()


def _home_relative(folder: Path) -> str:
    """`~/Documents/...` rather than `/home/name/Documents/...`: shorter,
    and the part of the path the user recognises."""
    try:
        return str(Path("~") / folder.relative_to(Path.home()))
    except ValueError:
        return str(folder)
