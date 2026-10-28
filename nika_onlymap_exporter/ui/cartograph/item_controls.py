"""The controls for the item selected on the page preview.

The body of the panel's Edit tab: its heading says what is selected (or, with
nothing selected, how to select something), and the controls for it follow.
What it offers depends on the item:

- Every text-carrying item: the size of the lettering, A- / A+
  (`item_text_size`); resizing the box is the handles on the page - the two
  are separate on purpose.
- Text: the words, bold, alignment and colour.
- Map: use the QGIS canvas's extent, rotation, and how it appears in the HTML
  (Interactive or Image, and an Image's resolution). Those two are the Map
  frames list's own choices: this strip sets and shows them through that list,
  so there is one state, not two.
- Legend: its map and columns, and a slot for the legend editor
  (`set_legend_editor_factory`).
- Scale bar and north arrow: the map they are about.

Every edit is one undo step on the layout's own stack, applied once per
gesture: a text edit when it loses focus or Enter is pressed, a rotation when
its box is committed, never on each keystroke or wheel turn.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from qgis.core import (
    QgsLayout,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsReadWriteContext,
    QgsTextFormat,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, QSettings, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPixmap,
    QShowEvent,
    QWheelEvent,
)
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.PyQt.QtXml import QDomDocument

from ...core.locator_context import (
    draw_quietly,
    fit_locator_to_context,
    is_locator,
    locator_view_for,
    omit_locator,
)
from ..i18n import tr
from .callout_controls import CalloutControls
from .flow_layout import flow_row
from .frames_list import LIVE, STATIC, FramesList, _QuietCombo
from .item_text_size import text_size_points
from .layer_membership import drawn_layers, set_layer_included
from .layout_items import (
    delete_items,
    is_detail_map,
    item_edit,
    link_map,
    map_frames,
    map_label,
    one_step,
)

#: What opens a legend editor: called with the tab body as parent, it returns a
#: widget that has `set_legend(QgsLayoutItemLegend | None)`.
LegendEditorFactory = Callable[[QWidget], QWidget]


class _CurrentPageStack(QStackedWidget):
    """A hidden legend editor must not reserve its height for text controls."""

    def sizeHint(self):  # noqa: N802 - Qt override
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


_ALIGNMENTS = (
    (Qt.AlignmentFlag.AlignLeft, "align-left", "Align text left"),
    (Qt.AlignmentFlag.AlignHCenter, "align-center", "Centre text"),
    (Qt.AlignmentFlag.AlignRight, "align-right", "Align text right"),
)


class _QuietSpin(QSpinBox):
    """Never answers the wheel (it scrolls the panel behind it)."""

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


class _QuietDoubleSpin(QDoubleSpinBox):
    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


#: The Edit tab's layout: content inset from the panel's sides, the gap between
#: neighbouring controls, and the clearance between the tab underline and the
#: selected-item header (the same for every kind of item).
PANEL_INSET = 12
PANEL_SPACING = 8
SELECTED_HEADER_CLEARANCE = 16

#: The Edit tab's text box: its default, floor and ceiling, in lines of the
#: panel font, and where the user's own height is remembered (as lines, so a
#: different font or scaling keeps the same number of lines).
TEXT_BOX_LINES_DEFAULT = 9
TEXT_BOX_LINES_MIN = 3
TEXT_BOX_LINES_MAX = 40
TEXT_BOX_LINES_SETTING = "qgis2webmap/cartographTextBoxLines"


def stored_text_box_lines() -> float:
    """The remembered height of the text box, in lines; the default when
    nothing (or nothing usable) is stored."""
    try:
        lines = float(
            QSettings().value(
                TEXT_BOX_LINES_SETTING, TEXT_BOX_LINES_DEFAULT, type=float
            )
        )
    except (TypeError, ValueError):
        return float(TEXT_BOX_LINES_DEFAULT)
    return min(max(lines, TEXT_BOX_LINES_MIN), TEXT_BOX_LINES_MAX)


class _HeightGrip(QFrame):
    """A bar under a box that drags its height, and only its height.

    Emits `dragged(delta)` in pixels while the mouse moves with the button
    down, and `finished()` once at the release - the caller applies the height
    live and remembers it once per gesture. Never takes focus or the wheel
    (the wheel scrolls the panel behind it).
    """

    grabbed = pyqtSignal()
    dragged = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("role", "heightGrip")
        self.setFixedHeight(8)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tr("Drag to change the height of the text box"))
        self.setAccessibleName(tr("Resize the text box"))
        self._press_y: int | None = None

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._press_y = int(event.globalPosition().y())
            self.grabbed.emit()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is not None and self._press_y is not None:
            self.dragged.emit(int(event.globalPosition().y()) - self._press_y)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is not None and self._press_y is not None:
            self._press_y = None
            self.finished.emit()
            event.accept()

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


def _small_button(text: str, tip: str, parent: QWidget) -> QToolButton:
    button = QToolButton(parent)
    button.setText(text)
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    button.setProperty("role", "secondary")
    button.setProperty("hasMenu", False)
    button.setProperty("zoom", True)
    # A click leaves no focus ring behind; the keyboard still reaches it.
    button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    return button


class _ColourButton(QToolButton):
    """A swatch of the text colour; a click opens the colour dialog and the
    colour is reported once, when the dialog is accepted - not on each stop
    of its picker, which would be an undo step apiece."""

    chosen = pyqtSignal(QColor)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._colour = QColor("#000000")
        self.setProperty("role", "secondary")
        self.setProperty("hasMenu", False)
        self.setProperty("zoom", True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(tr("Text colour"))
        self.setAccessibleName(tr("Text colour"))
        self.clicked.connect(self._pick)
        self.set_colour(self._colour)

    def colour(self) -> QColor:
        return QColor(self._colour)

    def set_colour(self, colour: QColor) -> None:
        self._colour = QColor(colour)
        swatch = QPixmap(14, 14)
        swatch.fill(self._colour)
        self.setIcon(QIcon(swatch))

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(self._colour, self, tr("Text colour"))
        if chosen.isValid() and chosen != self._colour:
            self.set_colour(chosen)
            self.chosen.emit(chosen)


def with_bold(text_format: QgsTextFormat, bold: bool) -> QgsTextFormat:
    """`text_format` with its lettering bold or regular.

    The weight lives in the font. `QgsTextFormat.setForcedBold` is never
    called: whatever it is given, `forcedBold()` then reads True for good, so
    "regular" could not be set again and the item stayed bold on reading
    (which is why the B button took two clicks to turn off, and a regular
    text still exported as bold). A format that already carries the flag is
    rebuilt without it.
    """
    font = text_format.font()
    font.setBold(bold)
    text_format.setFont(font)
    if not bold and getattr(text_format, "forcedBold", lambda: False)():
        document = QDomDocument()
        element = text_format.writeXml(document, QgsReadWriteContext())
        style = (
            element
            if element.tagName() == "text-style"
            else element.firstChildElement("text-style")
        )
        style.setAttribute("forcedBold", "0")
        cleared = QgsTextFormat()
        cleared.readXml(element, QgsReadWriteContext())
        text_format = cleared
    return text_format


def is_bold(item: QgsLayoutItemLabel) -> bool:
    text_format = item.textFormat()
    forced = getattr(text_format, "forcedBold", None)
    if forced is not None and forced():
        return True
    weight = text_format.font().weight()
    return int(getattr(weight, "value", weight)) >= int(
        getattr(QFont.Weight.Bold, "value", QFont.Weight.Bold)
    )


class _MapChoice(QWidget):
    """ "Map: [name]" - the map a legend, scale bar or north arrow is about."""

    chosen = pyqtSignal(str)  # the map item's uuid

    def __init__(self, parent: QWidget, labels: list[QLabel] | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.label = QLabel(tr("Map"), self)
        if labels is not None:
            labels.append(self.label)
        self.label.setProperty("role", "hint")
        row.addWidget(self.label)
        self.combo = _QuietCombo(self)
        self.combo.setMinimumContentsLength(8)
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo.setAccessibleName(tr("Map this item is about"))
        self.label.setBuddy(self.combo)
        self.combo.activated.connect(self._on_activated)
        row.addWidget(self.combo, 1)

    def show_maps(self, layout: QgsLayout, linked: QgsLayoutItemMap | None) -> None:
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            for frame in map_frames(layout):
                self.combo.addItem(map_label(frame), frame.uuid())
            index = self.combo.findData(linked.uuid()) if linked is not None else -1
            self.combo.setCurrentIndex(index)
        finally:
            self.combo.blockSignals(False)

    def _on_activated(self, index: int) -> None:
        self.chosen.emit(str(self.combo.itemData(index)))


class _StayOpenMenu(QMenu):
    """A menu of check boxes: ticking one leaves it open for the next, as a
    list of choices does; Escape or a click outside closes it."""

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        action = self.actionAt(event.pos()) if event is not None else None
        if action is not None and action.isCheckable() and action.isEnabled():
            action.trigger()
            return
        super().mouseReleaseEvent(event)


class ContextControls(QFrame):
    """The Edit tab's body: what can be changed on the selected item."""

    #: "Use canvas extent" for a map (its uuid): the controller owns the canvas.
    canvas_extent_requested = pyqtSignal(str)
    #: "Edit view" was switched on or off for the selected map.
    edit_view_toggled = pyqtSignal(bool)
    #: An item was edited here (the page's own refresh follows its undo stack).
    edited = pyqtSignal()
    #: A / A+ was pressed: the selected item's lettering one step (bigger?).
    text_size_step = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "itemControls")
        self._layout: QgsLayout | None = None
        self._item: QgsLayoutItem | None = None
        self._loading = False
        self._frames: FramesList | None = None
        self._legend_factory: LegendEditorFactory | None = None
        self._legend_editor: QWidget | None = None
        self._text_dirty = False
        self._field_labels: list[QLabel] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(
            PANEL_INSET, SELECTED_HEADER_CLEARANCE, PANEL_INSET, 0
        )
        column.setSpacing(PANEL_SPACING)

        head = QHBoxLayout()
        head.setSpacing(PANEL_SPACING)
        # "Selected: Legend", or the hint with nothing selected. The tab's
        # own title never changes, so the tab bar does not jump.
        self.heading = QLabel(self)
        self.heading.setWordWrap(True)
        self.heading.setMinimumWidth(1)
        head.addWidget(self.heading, 1, Qt.AlignmentFlag.AlignVCenter)
        self.delete_button = _small_button(
            tr("Delete"), tr("Delete the selected item (Delete key)"), self
        )
        self.delete_button.setProperty("lineIcon", "trash")
        self.delete_button.clicked.connect(self.delete_selected)
        self.delete_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        head.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.head_row = QWidget(self)
        self.head_row.setLayout(head)
        head.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.head_row)

        # Everything below the heading belongs to a selected item.
        self.body = QWidget(self)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(PANEL_SPACING)
        self._build_text_size_row(body)
        self.pages = _CurrentPageStack(self.body)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.pages.currentChanged.connect(lambda _index: self.pages.updateGeometry())
        body.addWidget(self.pages)
        column.addWidget(self.body)
        # A selected statistic callout's words.
        self.callout_controls = CalloutControls(self)
        self.callout_controls.edited.connect(self.edited)
        column.addWidget(self.callout_controls)
        column.addStretch(1)
        self._build_text_page()
        self._build_map_page()
        self._build_legend_page()
        self._build_linked_page("scale_bar")
        self._build_linked_page("north_arrow")
        self.refresh()

    # ---- pages -----------------------------------------------------------

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self.pages)
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(PANEL_SPACING)
        self.pages.addWidget(page)
        return page, column

    def _build_text_size_row(self, body: QVBoxLayout) -> None:
        """A- / the size / A+: the lettering of a text, a title or a legend."""
        self.text_size_row = QWidget(self.body)
        row = QHBoxLayout(self.text_size_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.text_smaller_button = _small_button(
            "A\N{MINUS SIGN}",
            tr(
                "Smaller lettering in the selected item "
                "(drag its handles to resize the box)"
            ),
            self.text_size_row,
        )
        self.text_bigger_button = _small_button(
            "A+",
            tr(
                "Bigger lettering in the selected item "
                "(drag its handles to resize the box)"
            ),
            self.text_size_row,
        )
        self.text_size_label = QLabel(self.text_size_row)
        self.text_size_label.setProperty("role", "hint")
        self.text_size_label.setMinimumWidth(40)
        self.text_size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.text_smaller_button)
        row.addWidget(self.text_size_label)
        row.addWidget(self.text_bigger_button)
        row.addStretch(1)
        self.text_smaller_button.clicked.connect(
            lambda: self.text_size_step.emit(False)
        )
        self.text_bigger_button.clicked.connect(lambda: self.text_size_step.emit(True))
        body.addWidget(self.text_size_row)

    def _build_text_page(self) -> None:
        self.text_page, column = self._page()
        self.text_edit = QPlainTextEdit(self.text_page)
        self.text_edit.setAccessibleName(tr("Text of the selected item"))
        self.text_edit.setToolTip(
            tr("The words. Press Enter to apply, Shift+Enter for a new line.")
        )
        self.text_edit.setTabChangesFocus(True)
        self.text_edit.textChanged.connect(self._on_text_typed)
        self.text_edit.installEventFilter(self)
        column.addWidget(self.text_edit)
        self.text_grip = _HeightGrip(self.text_page)
        self.text_grip.grabbed.connect(self._on_text_grip_grabbed)
        self.text_grip.dragged.connect(self._on_text_grip_dragged)
        self.text_grip.finished.connect(self._remember_text_lines)
        column.addWidget(self.text_grip)
        self._text_lines = stored_text_box_lines()
        self._drag_start_lines = self._text_lines
        self._apply_text_lines()

        row = QHBoxLayout()
        row.setSpacing(4)
        self.bold_button = _small_button(
            "B", tr("Bold (the lettering; the A- / A+ buttons change its size)"), self
        )
        bold_font = self.bold_button.font()
        bold_font.setBold(True)
        self.bold_button.setFont(bold_font)
        self.bold_button.setCheckable(True)
        self.bold_button.toggled.connect(self._on_bold)
        row.addWidget(self.bold_button)
        self.align_buttons: dict[Qt.AlignmentFlag, QToolButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for flag, icon, tip in _ALIGNMENTS:
            button = _small_button(
                {"align-left": "L", "align-center": "C", "align-right": "R"}[icon],
                tr(tip),
                self,
            )
            button.setProperty("lineIcon", icon)
            button.setCheckable(True)
            group.addButton(button)
            button.clicked.connect(
                lambda _checked=False, flag=flag: self._on_align(flag)
            )
            self.align_buttons[flag] = button
            row.addWidget(button)
        self._align_group = group
        self.colour_button = _ColourButton(self.text_page)
        self.colour_button.chosen.connect(self._on_colour)
        row.addWidget(self.colour_button)
        row.addStretch(1)
        column.addLayout(row)

    def _build_map_page(self) -> None:
        self.map_page, column = self._page()
        self.edit_view_button = QPushButton(tr("Edit view"), self.map_page)
        self.edit_view_button.setProperty("role", "secondary")
        self.edit_view_button.setCheckable(True)
        self.edit_view_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.edit_view_button.setToolTip(
            tr(
                "Drag to pan and use the wheel to zoom what this map shows, "
                "inside its frame. Esc finishes; a click elsewhere on the page "
                "goes back to moving the frame."
            )
        )
        self.edit_view_button.toggled.connect(self._on_edit_view)
        self.canvas_button = QPushButton(tr("Use canvas extent"), self.map_page)
        self.canvas_button.setProperty("role", "secondary")
        self.canvas_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.canvas_button.setToolTip(
            tr("Set this map's extent to what the QGIS map canvas shows")
        )
        self.canvas_button.clicked.connect(self._on_canvas)
        # Two distinct buttons share one row, half the width each.
        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(PANEL_SPACING)
        view_row.addWidget(self.canvas_button, 1)
        view_row.addWidget(self.edit_view_button, 1)
        column.addLayout(view_row)

        # Labels in one column, fields in the other: every field starts and
        # ends at the same edges, however wide the panel.
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(PANEL_SPACING)
        grid.setVerticalSpacing(PANEL_SPACING)
        grid.setColumnStretch(1, 1)
        rotation_label = self._field_label(tr("Rotation"), self.map_page)
        self.rotation_spin = _QuietDoubleSpin(self.map_page)
        self.rotation_spin.setRange(-360.0, 360.0)
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.setSuffix("\N{DEGREE SIGN}")
        self.rotation_spin.setKeyboardTracking(False)
        self.rotation_spin.setAccessibleName(tr("Map rotation"))
        self.rotation_spin.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.rotation_spin.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        rotation_label.setBuddy(self.rotation_spin)
        self.rotation_spin.editingFinished.connect(self._on_rotation)
        grid.addWidget(rotation_label, 0, 0)
        grid.addWidget(self.rotation_spin, 0, 1)
        mode_label = self._field_label(tr("Map type"), self.map_page)
        self.mode_combo = _QuietCombo(self.map_page)
        self._fit_combo(self.mode_combo)
        self.mode_combo.addItem(tr("Interactive"), LIVE)
        self.mode_combo.addItem(tr("Image"), STATIC)
        self.mode_combo.setToolTip(
            tr(
                "Interactive: readers pan and zoom this map in the HTML. "
                "Image: a picture of the map as QGIS draws it."
            )
        )
        self.mode_combo.setAccessibleName(tr("Map type"))
        mode_label.setBuddy(self.mode_combo)
        self.mode_combo.activated.connect(self._on_mode)
        grid.addWidget(mode_label, 1, 0)
        grid.addWidget(self.mode_combo, 1, 1)
        self.dpi_label = self._field_label(tr("Image resolution"), self.map_page)
        self.dpi_combo = _QuietCombo(self.map_page)
        self._fit_combo(self.dpi_combo)
        self.dpi_combo.setToolTip(tr("Image resolution"))
        self.dpi_combo.setAccessibleName(tr("Image resolution"))
        self.dpi_label.setBuddy(self.dpi_combo)
        self.dpi_combo.activated.connect(self._on_dpi)
        grid.addWidget(self.dpi_label, 2, 0)
        grid.addWidget(self.dpi_combo, 2, 1)
        column.addLayout(grid)

        # Only for an inset: what it draws and how much it shows.
        self.context_button = QPushButton(tr("Context layers..."), self.map_page)
        self.context_button.setProperty("role", "secondary")
        self.context_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.context_button.setToolTip(tr("Choose which layers this inset draws"))
        self.context_menu = _StayOpenMenu(self.context_button)
        self.context_menu.aboutToShow.connect(self._fill_context_menu)
        self.context_button.setMenu(self.context_menu)
        self.fit_button = QPushButton(tr("Fit context"), self.map_page)
        self.fit_button.setProperty("role", "secondary")
        self.fit_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.fit_button.clicked.connect(self._on_fit_context)
        self.remove_inset_button = QPushButton(tr("Remove inset"), self.map_page)
        self.remove_inset_button.setProperty("role", "secondary")
        self.remove_inset_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.remove_inset_button.setToolTip(tr("Take this inset off the page"))
        self.remove_inset_button.clicked.connect(self._on_remove_inset)
        self.inset_row = flow_row(
            self.map_page,
            [self.context_button, self.fit_button, self.remove_inset_button],
        )
        column.addWidget(self.inset_row)
        self.context_note = QLabel(self.map_page)
        self.context_note.setProperty("role", "hint")
        self.context_note.setWordWrap(True)
        column.addWidget(self.context_note)
        column.addStretch(1)

    def _field_label(self, text: str, parent: QWidget) -> QLabel:
        """A label of the panel's label column: all share one width."""
        label = QLabel(text, parent)
        label.setProperty("role", "hint")
        self._field_labels.append(label)
        return label

    @staticmethod
    def _fit_combo(combo: QComboBox) -> None:
        """A combo that fills its column and can shrink below its text."""
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(6)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _align_field_labels(self) -> None:
        labels = [label for label in self._field_labels if not sip.isdeleted(label)]
        for label in labels:
            label.setMinimumWidth(0)
        widest = max((label.sizeHint().width() for label in labels), default=0)
        for label in labels:
            label.setMinimumWidth(widest)

    def _build_legend_page(self) -> None:
        self.legend_page, column = self._page()
        self.legend_basics = QWidget(self.legend_page)
        rows = QVBoxLayout(self.legend_basics)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self.legend_map = _MapChoice(self.legend_page, self._field_labels)
        self.legend_map.chosen.connect(self._on_link)
        rows.addWidget(self.legend_map)
        row = QHBoxLayout()
        row.setSpacing(8)
        columns_label = self._field_label(tr("Columns"), self.legend_page)
        row.addWidget(columns_label)
        self.columns_spin = _QuietSpin(self.legend_page)
        self.columns_spin.setRange(1, 6)
        self.columns_spin.setKeyboardTracking(False)
        self.columns_spin.setAccessibleName(tr("Legend columns"))
        columns_label.setBuddy(self.columns_spin)
        self.columns_spin.editingFinished.connect(self._on_columns)
        row.addWidget(self.columns_spin, 1)
        rows.addLayout(row)
        column.addWidget(self.legend_basics)
        # Where the legend editor sits, when the host provides one.
        self.legend_slot = QVBoxLayout()
        self.legend_slot.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self.legend_slot)

    def _build_linked_page(self, name: str) -> None:
        page, column = self._page()
        choice = _MapChoice(page, self._field_labels)
        choice.chosen.connect(self._on_link)
        column.addWidget(choice)
        setattr(self, f"{name}_page", page)
        setattr(self, f"{name}_map", choice)

    # ---- host API --------------------------------------------------------

    def set_legend_editor_factory(self, factory: LegendEditorFactory | None) -> None:
        """Host the legend editor in the legend's controls. `factory(parent)`
        returns a widget with `set_legend(legend | None)`."""
        self._legend_factory = factory
        if self._legend_editor is not None and not sip.isdeleted(self._legend_editor):
            self.legend_slot.removeWidget(self._legend_editor)
            self._legend_editor.deleteLater()
        self._legend_editor = None
        self.refresh()

    def bind_frames(self, frames: FramesList) -> None:
        """Share the Map frames list's mode and resolution choices."""
        self._frames = frames
        frames.mode_changed.connect(lambda *_args: self._show_frame_state())
        frames.dpi_changed.connect(lambda *_args: self._show_frame_state())
        frames.cards_changed.connect(self._show_frame_state)

    def set_layout(self, layout: QgsLayout | None) -> None:
        self._layout = layout
        self._item = None
        self.refresh()

    def selected_item(self) -> QgsLayoutItem | None:
        """The one selected item the tab is showing, if any."""
        return self._item

    def reveal(self, focus: bool = True) -> None:
        """The Edit tab has just been brought up for the item (a double-click
        on it, or placing it): for text, put the cursor in the words."""
        if (
            focus
            and self._item is not None
            and self.pages.currentWidget() is self.text_page
        ):
            self.text_edit.setFocus()
            self.text_edit.selectAll()

    # ---- showing the item ------------------------------------------------

    def refresh(self) -> None:
        """Follow the layout's selection and the item's current values."""
        layout = self._layout
        item = None
        if layout is not None and not sip.isdeleted(layout):
            chosen = layout.selectedLayoutItems()
            if len(chosen) == 1 and self._kind(chosen[0]) is not None:
                item = chosen[0]
        if item is not self._item:
            self._apply_pending_text()
            self._text_dirty = False
        self._item = item
        self.callout_controls.show_for(layout, item)
        if item is None or layout is None:
            self._show_nothing_selected()
            return
        self.head_row.setVisible(True)
        self._loading = True
        try:
            kind = self._kind(item)
            self.heading.setText(
                self._title("detail_map" if is_detail_map(item) else kind)
            )
            self.heading.setProperty("role", "")
            self.heading.style().unpolish(self.heading)
            self.heading.style().polish(self.heading)
            self.delete_button.setVisible(True)
            self.body.setVisible(True)
            self._show_text_size(item)
            if kind == "text":
                self._show_text(item)
            elif kind == "map":
                self._show_map(item)
            elif kind == "legend":
                self._show_legend(layout, item)
            else:
                page, choice = (
                    (self.scale_bar_page, self.scale_bar_map)
                    if kind == "scale_bar"
                    else (self.north_arrow_page, self.north_arrow_map)
                )
                self.pages.setCurrentWidget(page)
                choice.show_maps(layout, item.linkedMap())
            if kind != "legend":
                self._set_legend_editor_item(None)
        finally:
            self._loading = False
        self._sync_button_styles()
        self._keep_buttons_readable()

    def _text_line_metrics(self) -> tuple[int, int]:
        """The height of one line of the box, and of its frame and margins."""
        edit = self.text_edit
        chrome = 2 * edit.frameWidth() + int(2 * edit.document().documentMargin())
        return max(1, edit.fontMetrics().lineSpacing()), chrome

    def _apply_text_lines(self) -> None:
        line, chrome = self._text_line_metrics()
        self.text_edit.setFixedHeight(round(self._text_lines * line) + chrome)

    def _on_text_grip_grabbed(self) -> None:
        self._drag_start_lines = self._text_lines

    def _on_text_grip_dragged(self, delta: int) -> None:
        """The grip moved `delta` px from where the press began."""
        line, _chrome = self._text_line_metrics()
        lines = self._drag_start_lines + delta / line
        self._text_lines = min(max(lines, TEXT_BOX_LINES_MIN), TEXT_BOX_LINES_MAX)
        self._apply_text_lines()

    def _remember_text_lines(self) -> None:
        # A height not remembered is no reason to fail a drag.
        with contextlib.suppress(Exception):
            QSettings().setValue(TEXT_BOX_LINES_SETTING, self._text_lines)

    def _keep_buttons_readable(self) -> None:
        """No button is squeezed below the height its text needs: a short
        window then scrolls the Edit tab instead of cutting the lettering."""
        for button in self.findChildren(QAbstractButton):
            button.setMinimumHeight(button.sizeHint().height())
            # Whole words too: a narrow panel wraps or scrolls, it never cuts.
            if button.text():
                button.setMinimumWidth(button.sizeHint().width())
        self._align_field_labels()

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802
        super().showEvent(event)
        # Hints are only right once the theme has polished the buttons.
        self._apply_text_lines()
        self._keep_buttons_readable()

    def _show_nothing_selected(self) -> None:
        self.heading.setText(tr("Select an item on the page to edit its properties."))
        self.heading.setProperty("role", "hint")
        self.heading.style().unpolish(self.heading)
        self.heading.style().polish(self.heading)
        self.head_row.setVisible(True)
        self.delete_button.setVisible(False)
        self.text_size_row.setVisible(False)
        self.body.setVisible(False)
        self._set_legend_editor_item(None)

    def _show_text_size(self, item: QgsLayoutItem) -> None:
        size = text_size_points(item)
        # The legend editor has its own Text size; two controls for one
        # setting would only disagree in the user's mind.
        if self._legend_factory is not None and self._kind(item) == "legend":
            size = None
        self.text_size_row.setVisible(size is not None)
        if size is not None:
            self.text_size_label.setText(tr("{size} pt").format(size=f"{size:g}"))

    def _sync_button_styles(self) -> None:
        """Pictures on the buttons that have one (the theme paints them), the
        letter otherwise."""
        for button in self.findChildren(QToolButton):
            if not button.property("lineIcon"):
                continue
            if button.icon().isNull():
                style = Qt.ToolButtonStyle.ToolButtonTextOnly
            elif button is self.delete_button:
                style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            else:
                style = Qt.ToolButtonStyle.ToolButtonIconOnly
            button.setToolButtonStyle(style)

    @staticmethod
    def _kind(item: QgsLayoutItem) -> str | None:
        if isinstance(item, QgsLayoutItemLabel):
            return "text"
        if isinstance(item, QgsLayoutItemMap):
            return "map"
        if isinstance(item, QgsLayoutItemLegend):
            return "legend"
        if isinstance(item, QgsLayoutItemScaleBar):
            return "scale_bar"
        if isinstance(item, QgsLayoutItemPicture) and item.linkedMap() is not None:
            return "north_arrow"
        return None

    @staticmethod
    def _title(kind: str | None) -> str:
        names = {
            "text": tr("Text"),
            "map": tr("Map"),
            "detail_map": tr("Detail map"),
            "legend": tr("Legend"),
            "scale_bar": tr("Scale bar"),
            "north_arrow": tr("North arrow"),
        }
        return tr("Selected: {item}").format(item=names.get(kind or "", ""))

    def _show_text(self, item: QgsLayoutItemLabel) -> None:
        self.pages.setCurrentWidget(self.text_page)
        if not (self.text_edit.hasFocus() and self._text_dirty):
            if self.text_edit.toPlainText() != item.text():
                self.text_edit.setPlainText(item.text())
            self._text_dirty = False
        self.bold_button.setChecked(is_bold(item))
        h_align = item.hAlign()
        shown = Qt.AlignmentFlag.AlignLeft
        for flag, _icon, _tip in _ALIGNMENTS:
            if h_align & flag:
                shown = flag
                break
        self._align_group.setExclusive(False)
        for flag, button in self.align_buttons.items():
            button.setChecked(flag == shown)
        self._align_group.setExclusive(True)
        self.colour_button.set_colour(QColor(item.textFormat().color()))

    def _show_map(self, item: QgsLayoutItemMap) -> None:
        self.pages.setCurrentWidget(self.map_page)
        self.rotation_spin.setValue(item.mapRotation())
        self._show_frame_state()
        self._show_inset(item)

    def _show_frame_state(self) -> None:
        item = self._item
        if not isinstance(item, QgsLayoutItemMap) or sip.isdeleted(item):
            return
        frames = self._frames
        card = None
        if frames is not None:
            try:
                card = frames.card(item.uuid())
            except KeyError:
                card = None
        self.mode_combo.setEnabled(card is not None)
        self.canvas_button.setEnabled(
            card is not None and card.canvas_action.isEnabled()
        )
        was_loading = self._loading
        self._loading = True
        try:
            self.dpi_combo.clear()
            if card is None:
                self.dpi_combo.setVisible(False)
                self.dpi_label.setVisible(False)
                return
            for index in range(card.dpi_combo.count()):
                self.dpi_combo.addItem(
                    card.dpi_combo.itemText(index), card.dpi_combo.itemData(index)
                )
            self.dpi_combo.setCurrentIndex(card.dpi_combo.currentIndex())
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(card.mode()))
            self.dpi_combo.setVisible(card.mode() == STATIC)
            self.dpi_label.setVisible(card.mode() == STATIC)
        finally:
            self._loading = was_loading

    def set_editing(self, editing: bool) -> None:
        """Show whether the view inside the selected map is being edited."""
        was_loading = self._loading
        self._loading = True
        try:
            self.edit_view_button.setChecked(editing)
        finally:
            self._loading = was_loading
        self._sync_edit_view_label()

    def _sync_edit_view_label(self) -> None:
        """ "Done editing view" while the map's view is being edited."""
        item = self._item
        inset = (
            isinstance(item, QgsLayoutItemMap)
            and not sip.isdeleted(item)
            and is_locator(item)
        )
        if self.edit_view_button.isChecked():
            text = tr("Done editing view")
        else:
            text = tr("Edit inset view") if inset else tr("Edit view")
        self.edit_view_button.setText(text)
        self.edit_view_button.setMinimumWidth(self.edit_view_button.sizeHint().width())

    def _show_inset(self, item: QgsLayoutItemMap) -> None:
        inset = is_locator(item)
        self._sync_edit_view_label()
        self.inset_row.setVisible(inset)
        self.context_note.setVisible(False)
        if is_detail_map(item) and not inset:
            self.context_note.setText(
                tr(
                    "Choose the area to show in this detail map. Dragging the "
                    "frame on the page moves it; Edit view changes the area "
                    "inside it."
                )
            )
            self.context_note.setVisible(True)
        if not inset:
            return
        view = locator_view_for(item)
        can_fit = view is not None and view.has_context
        self.fit_button.setEnabled(can_fit)
        if view is None:
            note = tr("This inset outlines no map, so there is no view to fit.")
        elif not view.has_context:
            note = tr(
                "No wider context: the layers this inset draws reach no "
                "further than the map it outlines. Add a layer that covers "
                "more ground (Context layers...), or remove the inset."
            )
        else:
            note = ""
        self.fit_button.setToolTip(
            note or tr("Fit the inset to the wider area its layers cover")
        )
        self.context_note.setText(note)
        self.context_note.setVisible(bool(note))

    def _fill_context_menu(self) -> None:
        menu = self.context_menu
        menu.clear()
        item, layout = self._live_item(), self._layout
        if not isinstance(item, QgsLayoutItemMap) or layout is None:
            return
        project = layout.project()
        drawn = {layer.id() for layer in drawn_layers(item)}
        for node in project.layerTreeRoot().findLayers():
            layer = node.layer()
            if layer is None:
                continue
            action = menu.addAction(layer.name())
            action.setCheckable(True)
            action.setChecked(layer.id() in drawn)
            layer_id = layer.id()
            action.toggled.connect(
                lambda checked, lid=layer_id: self._on_context_layer(lid, checked)
            )

    def _on_context_layer(self, layer_id: str, included: bool) -> None:
        item, layout = self._live_item(), self._layout
        if self._loading or not isinstance(item, QgsLayoutItemMap) or layout is None:
            return
        with one_step(layout, tr("Choose inset layers")):
            changed = set_layer_included(
                layout, layout.project(), item, layer_id, included
            )
            if changed:
                # An inset stays quiet: a layer added to it has no labels.
                with item_edit(layout, item, tr("Choose inset layers")):
                    draw_quietly(item)
        if changed:
            self._show_inset(item)
            self.edited.emit()

    def _on_fit_context(self) -> None:
        item = self._live_item()
        if not isinstance(item, QgsLayoutItemMap):
            return
        view = locator_view_for(item)
        if view is None or not view.has_context:
            self._show_inset(item)
            return
        fit_locator_to_context(item)
        self._show_inset(item)
        self.edited.emit()

    def _on_remove_inset(self) -> None:
        item = self._live_item()
        if not isinstance(item, QgsLayoutItemMap):
            return
        omit_locator(item)
        self.edited.emit()
        self.refresh()

    def _on_edit_view(self, on: bool) -> None:
        self._sync_edit_view_label()
        if not self._loading:
            self.edit_view_toggled.emit(on)

    def _show_legend(self, layout: QgsLayout, item: QgsLayoutItemLegend) -> None:
        self.pages.setCurrentWidget(self.legend_page)
        self.legend_map.show_maps(layout, item.linkedMap())
        self.columns_spin.setValue(max(1, item.columnCount()))
        # The hosted editor has its own map and columns: not twice.
        self.legend_basics.setVisible(self._legend_factory is None)
        self._set_legend_editor_item(item)

    def _set_legend_editor_item(self, legend: QgsLayoutItemLegend | None) -> None:
        if legend is not None and self._legend_editor is None and self._legend_factory:
            self._legend_editor = self._legend_factory(self.legend_page)
            # The Edit tab scrolls, so the editor keeps its whole minimum
            # height: squeezed, its rows overlapped.
            self._legend_editor.setMinimumHeight(
                self._legend_editor.minimumSizeHint().height()
            )
            self.legend_slot.addWidget(self._legend_editor)
        editor = self._legend_editor
        if editor is not None and not sip.isdeleted(editor):
            editor.set_legend(legend)  # type: ignore[attr-defined]  # the factory's contract
            editor.setVisible(legend is not None)

    # ---- edits (each one undo step, applied once per gesture) -------------

    def _live_item(self) -> QgsLayoutItem | None:
        item, layout = self._item, self._layout
        if (
            item is None
            or layout is None
            or sip.isdeleted(item)
            or sip.isdeleted(layout)
        ):
            return None
        return item

    def _on_text_typed(self) -> None:
        if not self._loading:
            self._text_dirty = True

    def _apply_pending_text(self) -> None:
        item = self._live_item()
        if (
            self._loading
            or not self._text_dirty
            or not isinstance(item, QgsLayoutItemLabel)
            or self._layout is None
        ):
            return
        self._text_dirty = False
        words = self.text_edit.toPlainText()
        if words == item.text():
            return
        with item_edit(self._layout, item, tr("Edit text")):
            item.setText(words)
        self.edited.emit()

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if watched is self.text_edit and event is not None:
            if event.type() == QEvent.Type.FocusOut:
                self._apply_pending_text()
            elif (
                event.type() == QEvent.Type.KeyPress
                and isinstance(event, QKeyEvent)
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self._apply_pending_text()
                return True
        return False

    def _on_bold(self, bold: bool) -> None:
        item = self._live_item()
        if (
            self._loading
            or not isinstance(item, QgsLayoutItemLabel)
            or not self._layout
        ):
            return
        with item_edit(self._layout, item, tr("Change text weight")):
            item.setTextFormat(with_bold(item.textFormat(), bold))
        self.edited.emit()

    def _on_colour(self, colour: QColor) -> None:
        item = self._live_item()
        if (
            self._loading
            or not isinstance(item, QgsLayoutItemLabel)
            or not self._layout
        ):
            return
        with item_edit(self._layout, item, tr("Change text colour")):
            text_format = item.textFormat()
            text_format.setColor(colour)
            item.setTextFormat(text_format)
        self.edited.emit()

    def _on_align(self, flag: Qt.AlignmentFlag) -> None:
        item = self._live_item()
        if (
            self._loading
            or not isinstance(item, QgsLayoutItemLabel)
            or not self._layout
        ):
            return
        with item_edit(self._layout, item, tr("Change text alignment")):
            item.setHAlign(flag)
        self.edited.emit()

    def _on_rotation(self) -> None:
        item = self._live_item()
        if self._loading or not isinstance(item, QgsLayoutItemMap) or not self._layout:
            return
        value = self.rotation_spin.value()
        if abs(value - item.mapRotation()) < 0.05:
            return
        with item_edit(self._layout, item, tr("Rotate map")):
            item.setMapRotation(value)
        self.edited.emit()

    def _on_columns(self) -> None:
        item = self._live_item()
        if (
            self._loading
            or not isinstance(item, QgsLayoutItemLegend)
            or not self._layout
        ):
            return
        value = self.columns_spin.value()
        if value == item.columnCount():
            return
        with item_edit(self._layout, item, tr("Change legend columns")):
            item.setColumnCount(value)
            item.adjustBoxSize()
            item.refresh()
        self.edited.emit()

    def _on_link(self, uuid: str) -> None:
        item, layout = self._live_item(), self._layout
        if self._loading or item is None or layout is None:
            return
        target = next((m for m in map_frames(layout) if m.uuid() == uuid), None)
        if target is not None and link_map(layout, item, target):
            self.edited.emit()

    def _on_canvas(self) -> None:
        item = self._live_item()
        if isinstance(item, QgsLayoutItemMap):
            self.canvas_extent_requested.emit(item.uuid())

    def _on_mode(self, index: int) -> None:
        item = self._live_item()
        card = self._card(item)
        if self._loading or card is None:
            return
        # The list's own combo: its handlers save the choice and refresh.
        card.mode_combo.setCurrentIndex(
            card.mode_combo.findData(self.mode_combo.itemData(index))
        )

    def _on_dpi(self, index: int) -> None:
        card = self._card(self._live_item())
        if self._loading or card is None:
            return
        card.dpi_combo.setCurrentIndex(index)

    def _card(self, item: QgsLayoutItem | None):  # FrameCard | None
        if not isinstance(item, QgsLayoutItemMap) or self._frames is None:
            return None
        try:
            return self._frames.card(item.uuid())
        except KeyError:
            return None

    def delete_selected(self) -> None:
        """Delete the selected item: one undo step."""
        item, layout = self._live_item(), self._layout
        if item is None or layout is None:
            return
        delete_items(layout, [item])
        self.edited.emit()
        self.refresh()
