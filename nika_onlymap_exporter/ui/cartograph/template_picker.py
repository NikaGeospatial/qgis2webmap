"""Current canvas > choose a layout template: ten designs, each previewed
with the map the canvas shows now.

The picker only chooses -- a template, a page size and an orientation -- and
the controller builds the layout (`core/canvas_cartograph.py`). Each preview
is the real page QGIS would build, drawn by QGIS (`template_preview`), so
what the thumbnail shows is what the layout will be.

**Previews draw one at a time** on a zero-length timer: a heavy project can
take a moment per page, and drawing all ten before the dialog appears would
freeze QGIS for all of them. A changed page size or orientation starts the
round again. The last choice is remembered per user (`QSettings`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable

from qgis.PyQt.QtCore import QMargins, QSettings, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QListView,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_templates import PAGE_SIZE_ORDER, TEMPLATES, get_template
from .. import theme
from ..fitted_label import FittedLabel
from ..i18n import N_, tr

#: Where the last choice is kept, under this plugin's own QSettings group.
SETTINGS_KEY = "qgis2webmap/cartograph/template"

#: How wide each preview is drawn, in pixels; the card shows it scaled to fit.
PREVIEW_WIDTH_PX = 360
ICON_SIZE = QSize(176, 176)
#: Room around each card in the grid, and inside it around the page.
_GRID_GAP = 18
_CARD_PADDING = 10
#: The card behind each page, so white pages stand apart from each other.
_CARD = "#e7e2e8"
_PAGE_EDGE = "#b7aeb9"

_PAGE_LABELS = {
    "Letter": N_("Letter (ANSI A)"),
    "Tabloid": N_("Tabloid (ANSI B)"),
    "Legal": N_("Legal"),
}
_ORIENTATIONS = (
    ("auto", N_("Automatic (fits the map)")),
    ("portrait", N_("Portrait")),
    ("landscape", N_("Landscape")),
)

#: `render(template_id, page_size, orientation, width_px)` -> the preview, or
#: `None` when it cannot be drawn.
PreviewRenderer = Callable[[str, str, str, int], "QImage | None"]


class TemplatePicker(QDialog):
    """The modal template picker. After `exec()` accepts, `template_id`,
    `page_size` and `orientation` hold the choice."""

    def __init__(
        self,
        render: PreviewRenderer,
        *,
        qsettings: QSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Choose a layout template"))
        self.setModal(True)
        self._render = render
        self._qsettings = qsettings if qsettings is not None else QSettings()
        self._queue: list[QListWidgetItem] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)

        saved = self._load_choice()
        layout = QVBoxLayout(self)
        intro = FittedLabel(
            tr(
                "Pick a design for the print layout. Each preview shows the map "
                "the canvas shows now; you can edit the layout in the layout "
                "designer afterwards."
            ),
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.page_combo = QComboBox(self)
        for size in PAGE_SIZE_ORDER:
            self.page_combo.addItem(tr(_PAGE_LABELS.get(size, size)), size)
        self.orientation_combo = QComboBox(self)
        for value, label in _ORIENTATIONS:
            self.orientation_combo.addItem(tr(label), value)
        form.addRow(tr("Page size"), self.page_combo)
        form.addRow(tr("Orientation"), self.orientation_combo)
        layout.addLayout(form)

        self.template_list = QListWidget(self)
        self.template_list.setViewMode(QListView.ViewMode.IconMode)
        self.template_list.setIconSize(ICON_SIZE)
        cell = QSize(ICON_SIZE.width() + _GRID_GAP, ICON_SIZE.height() + 40)
        self.template_list.setGridSize(cell)
        self.template_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.template_list.setMovement(QListView.Movement.Static)
        self.template_list.setWordWrap(True)
        # Five across and two down, with room for a scroll bar that a
        # larger system font may still bring in.
        scrollbar = self.style().pixelMetric(
            self.style().PixelMetric.PM_ScrollBarExtent
        )
        self.template_list.setMinimumSize(
            5 * cell.width() + scrollbar + 12, 2 * cell.height() + 12
        )
        for template in TEMPLATES:
            item = QListWidgetItem(tr(template.name), self.template_list)
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            item.setToolTip(tr(template.description))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            # One size for every cell, whatever the name's length, so the
            # cards line up in even columns.
            item.setSizeHint(cell)
        self.template_list.currentItemChanged.connect(self._on_template)
        self.template_list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.template_list, 1)

        self.description_label = FittedLabel("", self)
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)

        buttons = QDialogButtonBox(self)
        self.create_button = buttons.addButton(
            tr("Create layout"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._select(saved)
        self.page_combo.currentIndexChanged.connect(self._restart_previews)
        self.orientation_combo.currentIndexChanged.connect(self._restart_previews)
        theme.attach_menus(self)
        self.adjustSize()
        self._restart_previews()

    # ---- The choice ----------------------------------------------------------

    @property
    def template_id(self) -> str:
        item = self.template_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else TEMPLATES[0].id

    @property
    def page_size(self) -> str:
        return str(self.page_combo.currentData())

    @property
    def orientation(self) -> str:
        return str(self.orientation_combo.currentData())

    def accept(self) -> None:
        self._timer.stop()
        self._save_choice()
        super().accept()

    def reject(self) -> None:
        self._timer.stop()
        super().reject()

    # ---- Previews ------------------------------------------------------------

    def _restart_previews(self, *_args: object) -> None:
        """Draw every preview again, the selected one first."""
        self._timer.stop()
        placeholder = QIcon()
        items = [
            self.template_list.item(row) for row in range(self.template_list.count())
        ]
        for item in items:
            item.setIcon(placeholder)
        current = self.template_list.currentItem()
        self._queue = ([current] if current else []) + [
            i for i in items if i is not current
        ]
        self._timer.start()

    def _render_next(self) -> None:
        if not self._queue:
            return
        item = self._queue.pop(0)
        template_id = str(item.data(Qt.ItemDataRole.UserRole))
        try:
            image = self._render(
                template_id, self.page_size, self.orientation, PREVIEW_WIDTH_PX
            )
        except Exception:  # a preview must never break the picker
            image = None
        if image is not None and not image.isNull():
            item.setIcon(QIcon(_card(image)))
        else:
            item.setToolTip(
                f"{tr(get_template(template_id).description)}\n"
                + tr("The preview could not be drawn.")
            )
        if self._queue:
            self._timer.start()

    # ---- Helpers -------------------------------------------------------------

    def _on_template(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is None:
            self.description_label.setText("")
            return
        template = get_template(str(current.data(Qt.ItemDataRole.UserRole)))
        self.description_label.setText(tr(template.description))

    def _select(self, saved: tuple[str, str, str]) -> None:
        template_id, page_size, orientation = saved
        for row in range(self.template_list.count()):
            item = self.template_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == template_id:
                self.template_list.setCurrentItem(item)
                break
        else:
            self.template_list.setCurrentRow(0)
        for combo, value in (
            (self.page_combo, page_size),
            (self.orientation_combo, orientation),
        ):
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

    def _load_choice(self) -> tuple[str, str, str]:
        """The last choice, or Classic on A4 fitted to the map. Anything
        stored that is no longer an option falls back to the default."""
        value = self._qsettings.value(SETTINGS_KEY, "", type=str) or ""
        parts = value.split("|")
        if len(parts) != 3:
            return (TEMPLATES[0].id, PAGE_SIZE_ORDER[0], "auto")
        return parts[0], parts[1], parts[2]

    def _save_choice(self) -> None:
        self._qsettings.setValue(
            SETTINGS_KEY, f"{self.template_id}|{self.page_size}|{self.orientation}"
        )


def ask_template(
    render: PreviewRenderer, parent: QWidget | None = None
) -> tuple[str, str, str] | None:
    """Show the picker; `(template_id, page_size, orientation)`, or `None`
    when the user cancels."""
    picker = TemplatePicker(render, parent=parent)
    if picker.exec() != QDialog.DialogCode.Accepted:
        return None
    return picker.template_id, picker.page_size, picker.orientation


def _card(image: QImage) -> QPixmap:
    """`image` (a page) centred on a grey card with a thin edge, so ten white
    pages read as ten pages rather than one white field."""
    card = QPixmap(ICON_SIZE)
    card.fill(QColor(_CARD))
    room = ICON_SIZE.shrunkBy(QMargins(*(4 * [_CARD_PADDING])))
    page = QPixmap.fromImage(image).scaled(
        room,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (ICON_SIZE.width() - page.width()) // 2
    y = (ICON_SIZE.height() - page.height()) // 2
    painter = QPainter(card)
    painter.drawPixmap(x, y, page)
    painter.setPen(QColor(_PAGE_EDGE))
    painter.drawRect(x, y, page.width() - 1, page.height() - 1)
    painter.end()
    return card
