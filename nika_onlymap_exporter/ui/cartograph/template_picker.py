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

from qgis.PyQt.QtCore import QSettings, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPixmap, QShowEvent
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_templates import PAGE_SIZE_ORDER, TEMPLATES, get_template
from .. import theme
from ..i18n import N_, tr

#: Where the last choice is kept, under this plugin's own QSettings group.
SETTINGS_KEY = "qgis2webmap/cartograph/template"

#: The box each page preview is fitted into, in each row of the list. The
#: page is drawn at this width times the screen's pixel ratio, so it is sharp.
PREVIEW_BOX = QSize(300, 212)
#: Room inside each row, around the preview and the text.
_ROW_PADDING = 12
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


class _TemplateRow(QFrame):
    """One row of the list: the page, large, then the name and description.

    Transparent to the mouse, so a click or a double-click reaches the list
    under it, which owns the selection and the keyboard.
    """

    def __init__(self, name: str, description: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("role", "templateRow")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(*(4 * [_ROW_PADDING]))
        row.setSpacing(18)

        self.preview = QLabel(self)
        self.preview.setProperty("role", "templatePreview")
        self.preview.setFixedSize(PREVIEW_BOX)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.preview)

        text = QVBoxLayout()
        text.setSpacing(4)
        name_row = QHBoxLayout()
        self.name = QLabel(name, self)
        self.name.setProperty("role", "templateName")
        name_row.addWidget(self.name, 1)
        self.tick = QLabel("", self)
        self.tick.setProperty("role", "templateTick")
        name_row.addWidget(self.tick)
        text.addLayout(name_row)
        self.description = QLabel(description, self)
        self.description.setProperty("role", "templateDescription")
        self.description.setWordWrap(True)
        self.description.setAlignment(Qt.AlignmentFlag.AlignTop)
        text.addWidget(self.description)
        text.addStretch(1)
        row.addLayout(text, 1)
        self.drawn = False

    def show_page(self, page: QPixmap | None) -> None:
        self.drawn = page is not None
        self.preview.setPixmap(page if page is not None else QPixmap())

    def set_chosen(self, chosen: bool) -> None:
        self.tick.setText("\u2713" if chosen else "")


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
        layout.setSpacing(10)

        # One slim bar: what the previews show, then the two page settings.
        bar = QHBoxLayout()
        bar.setSpacing(8)
        intro = QLabel(tr("Each preview shows the map the canvas shows now."), self)
        intro.setProperty("role", "hint")
        bar.addWidget(intro, 1)
        self.page_combo = QComboBox(self)
        self.page_combo.setAccessibleName(tr("Page size"))
        self.page_combo.setToolTip(tr("Page size"))
        for size in PAGE_SIZE_ORDER:
            self.page_combo.addItem(tr(_PAGE_LABELS.get(size, size)), size)
        self.orientation_combo = QComboBox(self)
        self.orientation_combo.setAccessibleName(tr("Orientation"))
        self.orientation_combo.setToolTip(tr("Orientation"))
        for value, label in _ORIENTATIONS:
            self.orientation_combo.addItem(tr(label), value)
        bar.addWidget(self.page_combo)
        bar.addWidget(self.orientation_combo)
        layout.addLayout(bar)

        # A list, one design per row across the whole width, so each page is
        # large enough to judge before choosing it; the list scrolls.
        self.template_list = QListWidget(self)
        self.template_list.setProperty("role", "templateList")
        self.template_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.template_list.verticalScrollBar().setSingleStep(24)
        self.template_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.template_list.setSpacing(3)
        self.rows: dict[str, _TemplateRow] = {}
        row_height = PREVIEW_BOX.height() + 2 * _ROW_PADDING
        for template in TEMPLATES:
            item = QListWidgetItem(self.template_list)
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, tr(template.name))
            item.setToolTip(tr(template.description))
            item.setSizeHint(QSize(0, row_height))
            row = _TemplateRow(tr(template.name), tr(template.description), self)
            self.template_list.setItemWidget(item, row)
            self.rows[template.id] = row
        self.template_list.currentItemChanged.connect(self._on_template)
        self.template_list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.template_list, 1)

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
        self.setMinimumSize(560, 420)
        self.resize(780, 660)
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
        for row in self.rows.values():
            row.show_page(None)
        items = [
            self.template_list.item(row) for row in range(self.template_list.count())
        ]
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
        ratio = max(1.0, self.devicePixelRatioF())
        try:
            image = self._render(
                template_id,
                self.page_size,
                self.orientation,
                round(PREVIEW_BOX.width() * ratio),
            )
        except Exception:  # a preview must never break the picker
            image = None
        if image is not None and not image.isNull():
            self.rows[template_id].show_page(_page(image, ratio))
        else:
            item.setToolTip(
                f"{tr(get_template(template_id).description)}\n"
                + tr("The preview could not be drawn.")
            )
        if self._queue:
            self._timer.start()

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        # The remembered design may be far down the list.
        current = self.template_list.currentItem()
        if current is not None:
            self.template_list.scrollToItem(
                current, QAbstractItemView.ScrollHint.PositionAtCenter
            )

    # ---- Helpers -------------------------------------------------------------

    def _on_template(self, current: QListWidgetItem | None, _previous: object) -> None:
        chosen = current.data(Qt.ItemDataRole.UserRole) if current else None
        for template_id, row in self.rows.items():
            row.set_chosen(template_id == chosen)

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


def _page(image: QImage, ratio: float) -> QPixmap:
    """`image` (a page) fitted into the preview box, with a thin edge so a
    white page stands apart from a light row."""
    room = QSize(
        round(PREVIEW_BOX.width() * ratio), round(PREVIEW_BOX.height() * ratio)
    )
    page = QPixmap.fromImage(image).scaled(
        room,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(page)
    painter.setPen(QColor(_PAGE_EDGE))
    painter.drawRect(0, 0, page.width() - 1, page.height() - 1)
    painter.end()
    page.setDevicePixelRatio(ratio)
    return page
