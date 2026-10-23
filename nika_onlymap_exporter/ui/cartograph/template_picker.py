"""Current canvas > choose a layout template: every design, each previewed
with the map the canvas shows now.

The picker only chooses -- a template, a page size and an orientation -- and
the controller builds the layout (`core/canvas_cartograph.py`). Each preview
is the real page QGIS would build, drawn by QGIS (`template_preview`), so
what the thumbnail shows is what the layout will be. The one thing it also
writes is a design's key figures, typed under the list and saved to the
project's variables as it accepts, where the layout then reads them. The
other is the Title, Legend and Notes size steps (`TextSizes`), kept the same
way.

**Previews draw one at a time** on a zero-length timer: a heavy project can
take a moment per page, and drawing them all before the dialog appears would
freeze QGIS for all of them. Each turn of the event loop draws the most
useful missing page -- the selected design, then the ones in view, then the
ones a short scroll away -- and nothing else: a design filtered out, or far
down the list, is drawn only once it comes near the view. Scrolling, filtering
or changing the page settings simply changes what is drawn next, a page for
settings no longer shown is never drawn, and nothing is drawn while the list
is still scrolling. Drawn pages are kept (`_PageCache`), so going back to a
page size is instant; given a `canvas_key`, they are kept for the next
opening too, until the canvas or project changes. The last choice is
remembered per user (`QSettings`).

**What a card says it includes** is checked against the page this project
gets (`core/template_cues.py`): "Includes" lists what the page shows, "Can
also show" what the design would show with more content -- notes, key
figures, a source line -- with a tooltip saying where it comes from.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from typing import Protocol

from qgis.core import QgsProject
from qgis.PyQt.QtCore import (
    QEvent,
    QObject,
    QRect,
    QRegularExpression,
    QSettings,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPixmap,
    QRegularExpressionValidator,
    QResizeEvent,
    QShowEvent,
)
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QStackedLayout,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import layout_templates
from ...core.canvas_cartograph import (
    FIGURE_VARIABLES,
    PREVIEW_LEGEND_LEFT_OUT_KEY,
    PendingPreview,
    project_figure_rows,
    project_text_sizes,
    set_project_figures,
    set_project_text_sizes,
)
from ...core.layout_templates import (
    CHARACTERFUL,
    ESSENTIAL,
    PAGE_SIZE_ORDER,
    TEXT_SIZE_STEPS,
    LayoutTemplate,
    TemplatePlan,
    TextSizes,
)
from ...core.template_cues import CardCues, declared_cues, plan_cues
from .. import theme
from ..i18n import N_, plural, tr

#: Where the last choice is kept, under this plugin's own QSettings group.
SETTINGS_KEY = "qgis2webmap/cartograph/template"

#: The design a first-time user starts on (Daylight): the safest choice.
DEFAULT_TEMPLATE = "classic"

#: The widest a page preview is shown, in logical pixels. Every page is drawn
#: at this width (times the screen's pixel ratio) and scaled down to its box,
#: so resizing the dialog never draws a page again.
RENDER_WIDTH = 360
#: The narrowest preview box; below it a page is too small to judge.
_MIN_BOX_WIDTH = 180
#: A landscape A-series page: the shape a preview box is cut to.
_PAGE_ASPECT = 1.414
#: Room inside each card, around the preview and the text.
_ROW_PADDING = 12
#: Between cards, and round the edge of the list.
_LIST_SPACING = 4
#: A card is at least this wide before the list puts two side by side.
_CARD_MIN_WIDTH = 440
_MAX_COLUMNS = 3
_PAGE_EDGE = "#b7aeb9"

#: The opening size when the screen has room, and the most of the screen's
#: work area (taskbars excluded) the picker opens across.
_DEFAULT_SIZE = QSize(860, 720)
_MINIMUM_SIZE = QSize(640, 480)
_SCREEN_SHARE_W = 0.9
_SCREEN_SHARE_H = 0.88
#: On a large screen the picker grows to this share of it, past the default.
_LARGE_SHARE_W = 0.7
_LARGE_SHARE_H = 0.8

#: Pages kept between openings of the picker, when the caller names the
#: canvas state they were drawn from: at most this many, and this many
#: bytes. A page at 2x is up to 3 MB, so the bytes bound holds every design
#: on a couple of page settings.
_SHARED_CACHE_LIMIT = 48
_SHARED_CACHE_BYTES = 64 * 1024 * 1024
#: Cards this close to the view, as a share of its height, count as in
#: view: drawn before they are scrolled to, not just after.
_NEAR_VIEW = 0.5
#: How long the list must rest after a scroll before the next page is
#: drawn: a page takes the event loop for its whole drawing, so none
#: starts while the user is still scrolling.
_SCROLL_SETTLE_MS = 150

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
#: The filter above the list: every design, or one category.
FILTER_ALL = "all"
_FILTERS = (
    (FILTER_ALL, N_("All")),
    (ESSENTIAL, N_("Essentials")),
    (CHARACTERFUL, N_("Characterful")),
)

#: Key figures as the picker's rows hold them: one (value, label) pair per
#: `FIGURE_VARIABLES` slot, blank ones included.
Figures = tuple[tuple[str, str], ...]
_NO_FIGURES: Figures = (("", ""),) * len(FIGURE_VARIABLES)
#: How long typing in a figure pauses before the page is drawn again.
_FIGURES_DELAY_MS = 350
#: Long enough for any real figure; longer would no longer fit its block.
_FIGURE_VALUE_MAX = 24
_FIGURE_LABEL_MAX = 80
#: How long a size slider rests before the pages are drawn again.
_SIZES_DELAY_MS = 250
#: The Standard step's position on a size slider.
STANDARD_INDEX = 2
#: The widest a value's field grows, in logical pixels.
_FIGURE_VALUE_WIDTH = 160


class PreviewRenderer(Protocol):
    """`render(template_id, page_size, orientation, width_px, figures=...,
    sizes=...)`
    -> the preview, or `None` when it cannot be drawn; or a pending preview
    (`canvas_cartograph.PendingPreview`: a `finished` signal carrying the
    image or `None`, and `cancel()`), drawn in the background while the
    picker stays usable. `figures` are the key figure rows as typed for a
    design that takes them, `()` for the rest; `sizes` the title, legend and
    notes steps chosen."""

    def __call__(
        self,
        template_id: str,
        page_size: str,
        orientation: str,
        width_px: int,
        *,
        figures: Figures,
        sizes: TextSizes,
    ) -> QImage | PendingPreview | None: ...


class PreviewPlanner(Protocol):
    """`plan(template_id, page_size, orientation, figures=..., sizes=...)` -> the page
    `template_id` makes of this project, as the planner lays it out (what
    the preview draws), for the card's cues. Cheap next to drawing it."""

    def __call__(
        self,
        template_id: str,
        page_size: str,
        orientation: str,
        *,
        figures: Figures,
        sizes: TextSizes,
    ) -> TemplatePlan: ...


#: (template, page size, orientation, width, canvas, figures drawn, text
#: sizes).
_PageKey = tuple[str, str, str, int, str, Figures, TextSizes]
#: Where in a `_PageKey` the canvas state is.
_CANVAS = 4


class _PageCache:
    """Drawn pages by `(template, page size, orientation, width, canvas)`,
    the least recently used dropped once there are more than `limit` or
    they take more than `max_bytes`."""

    def __init__(self, limit: int | None = None, max_bytes: int | None = None) -> None:
        self.limit = limit
        self.max_bytes = max_bytes
        self._pages: OrderedDict[_PageKey, QImage] = OrderedDict()
        self._bytes = 0

    def get(self, key: _PageKey) -> QImage | None:
        page = self._pages.get(key)
        if page is not None:
            self._pages.move_to_end(key)
        return page

    def put(self, key: _PageKey, page: QImage) -> None:
        self._drop(key)
        self._pages[key] = page
        self._bytes += page.sizeInBytes()
        while len(self._pages) > 1 and (
            (self.limit is not None and len(self._pages) > self.limit)
            or (self.max_bytes is not None and self._bytes > self.max_bytes)
        ):
            self._drop(next(iter(self._pages)))

    def __len__(self) -> int:
        return len(self._pages)

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def clear(self) -> None:
        self._pages.clear()
        self._bytes = 0

    def keep_only(self, canvas_key: str) -> None:
        """Forget every page drawn from another canvas state: the canvas or
        project has changed since, so they can never be shown again."""
        for key in [k for k in self._pages if k[_CANVAS] != canvas_key]:
            self._drop(key)

    def _drop(self, key: _PageKey) -> None:
        page = self._pages.pop(key, None)
        if page is not None:
            self._bytes -= page.sizeInBytes()

    def __contains__(self, key: object) -> bool:
        return key in self._pages

    def drop_variants(self, key: _PageKey) -> None:
        """Forget the pages that differ from `key` only in their figures: a
        design's page is kept for the figures typed now, not for every one
        typed on the way there."""
        for other in [k for k in self._pages if k[:5] == key[:5] and k != key]:
            self._drop(other)


#: Shared by every picker opened with the same `canvas_key`.
_SHARED_PAGES = _PageCache(_SHARED_CACHE_LIMIT, _SHARED_CACHE_BYTES)


def dialog_sizes(available: QSize) -> tuple[QSize, QSize]:
    """The picker's opening and minimum size on a screen whose work area is
    `available`: the default, more on a large screen, never past
    90% x 88% of the work area."""
    cap_w = int(available.width() * _SCREEN_SHARE_W)
    cap_h = int(available.height() * _SCREEN_SHARE_H)
    width = min(
        cap_w, max(_DEFAULT_SIZE.width(), int(available.width() * _LARGE_SHARE_W))
    )
    height = min(
        cap_h, max(_DEFAULT_SIZE.height(), int(available.height() * _LARGE_SHARE_H))
    )
    minimum = QSize(
        min(_MINIMUM_SIZE.width(), cap_w), min(_MINIMUM_SIZE.height(), cap_h)
    )
    return QSize(max(width, minimum.width()), max(height, minimum.height())), minimum


def _preview_box(card_width: int, list_height: int, tall: bool = False) -> QSize:
    """The box a card's page is fitted into, the shape of the pages: about
    half the card for a wide page, a third for a tall one, and short enough
    that more than one card shows in a short list."""
    cap = max(round(_MIN_BOX_WIDTH / _PAGE_ASPECT), round(list_height * 0.6))
    if tall:
        width = max(_MIN_BOX_WIDTH // 2, round(card_width * 0.34))
        height = min(round(width * _PAGE_ASPECT), cap)
        return QSize(round(height / _PAGE_ASPECT), height)
    width = max(_MIN_BOX_WIDTH, min(RENDER_WIDTH, round(card_width * 0.46)))
    height = round(width / _PAGE_ASPECT)
    if height > cap:
        height = cap
        width = round(height * _PAGE_ASPECT)
    return QSize(width, height)


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def legend_left_out(image: QImage) -> int:
    """How many legend entries the drawn page left out because they did not
    fit at a readable size (`canvas_cartograph.template_preview`)."""
    try:
        return max(0, int(image.text(PREVIEW_LEGEND_LEFT_OUT_KEY) or 0))
    except ValueError:
        return 0


class _TemplateRow(QFrame):
    """One card of the list: the page, large, then the name, the description,
    what this page includes and what else the design can show.

    A click selects the card's design in the list under it, which owns the
    selection and the keyboard; a double-click accepts it. The card is not
    transparent to the mouse only so that a failed page's Retry can be
    pressed.
    """

    def __init__(
        self,
        template: LayoutTemplate,
        picker: TemplatePicker,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.template_id = template.id
        self._picker = picker
        self.setProperty("role", "templateRow")
        self.setProperty("chosen", False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        row = QHBoxLayout(self)
        row.setContentsMargins(*(4 * [_ROW_PADDING]))
        row.setSpacing(16)

        # The page, or the reason there is none: one box, two faces.
        self.preview = QFrame(self)
        self.preview.setProperty("role", "templatePreview")
        faces = QStackedLayout(self.preview)
        faces.setContentsMargins(0, 0, 0, 0)
        self.page = QLabel(tr("Drawing the preview\u2026"), self.preview)
        self.page.setProperty("role", "templatePage")
        self.page.setProperty("drawn", False)
        self.page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page.setWordWrap(True)
        faces.addWidget(self.page)
        self.failure = QFrame(self.preview)
        self.failure.setProperty("role", "templateFailure")
        failure = QVBoxLayout(self.failure)
        failure.setContentsMargins(8, 8, 8, 8)
        failure.addStretch(1)
        message = QLabel(tr("Preview could not be drawn"), self.failure)
        message.setProperty("role", "templateFailureText")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        failure.addWidget(message)
        self.retry = QPushButton(tr("Retry"), self.failure)
        self.retry.setAutoDefault(False)
        self.retry.setAccessibleName(
            tr("Retry the preview of {name}").format(name=tr(template.name))
        )
        self.retry.clicked.connect(lambda: picker.retry(self.template_id))
        failure.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignHCenter)
        failure.addStretch(1)
        faces.addWidget(self.failure)
        self._faces = faces
        row.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)

        self.text = QWidget(self)
        text = QVBoxLayout(self.text)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        name_row = QHBoxLayout()
        self.name = QLabel(tr(template.name), self.text)
        self.name.setProperty("role", "templateName")
        self.name.setWordWrap(True)
        name_row.addWidget(self.name, 1)
        self.tick = QLabel("", self.text)
        self.tick.setProperty("role", "templateTick")
        self.tick.setVisible(False)
        name_row.addWidget(self.tick, 0, Qt.AlignmentFlag.AlignTop)
        text.addLayout(name_row)
        self.description = QLabel(tr(template.description), self.text)
        self.description.setProperty("role", "templateDescription")
        self.description.setWordWrap(True)
        text.addWidget(self.description)
        self.includes = QLabel("", self.text)
        self.includes.setProperty("role", "templateIncludes")
        self.includes.setWordWrap(True)
        text.addWidget(self.includes)
        self.supports = QLabel("", self.text)
        self.supports.setProperty("role", "templateSupports")
        self.supports.setWordWrap(True)
        text.addWidget(self.supports)
        # Shown by `show_page` when this page's legend could not hold every
        # entry at a readable size.
        self.crowded = QLabel("", self.text)
        self.crowded.setProperty("role", "templateCrowded")
        self.crowded.setWordWrap(True)
        text.addWidget(self.crowded)
        # Shown by `set_cues` once there is something to list.
        self.includes.setVisible(False)
        self.supports.setVisible(False)
        self.crowded.setVisible(False)
        text.addStretch(1)
        self.cues = CardCues()
        self.set_cues(declared_cues(template))
        row.addWidget(self.text, 1)

        self.drawn = False
        self.failed = False
        self._image: QImage | None = None
        self._box = QSize(_MIN_BOX_WIDTH, round(_MIN_BOX_WIDTH / _PAGE_ASPECT))

    # ---- What the text says --------------------------------------------------

    def set_cues(self, cues: CardCues) -> bool:
        """Say what this page includes and what else the design can show;
        whether the text changed (and the card may need another height)."""
        if cues == self.cues:
            return False
        self.cues = cues
        includes, supports = cue_lines(cues)
        self.includes.setText(includes)
        self.includes.setVisible(bool(includes))
        self.supports.setText(supports)
        self.supports.setVisible(bool(supports))
        hints = cue_hints(cues)
        self.supports.setToolTip(hints)
        self.supports.setAccessibleDescription(hints)
        return True

    # ---- What the box shows --------------------------------------------------

    def set_box(self, box: QSize) -> None:
        """Fit the page into `box`."""
        self._box = QSize(box)
        self._refit()

    def show_page(self, image: QImage | None) -> bool:
        """`image` drawn, or `None`: waiting for it. Whether the card's text
        changed (and the card may need another height)."""
        left_out = legend_left_out(image) if image is not None else 0
        crowded = (
            plural(
                left_out,
                "Legend: {n} entry does not fit at a readable size. "
                "Try a larger page or shorter layer names.",
                "Legend: {n} entries do not fit at a readable size. "
                "Try a larger page or shorter layer names.",
            )
            if left_out
            else ""
        )
        changed = crowded != self.crowded.text()
        self.crowded.setText(crowded)
        self.crowded.setVisible(bool(crowded))
        self._image = image
        self.drawn = image is not None
        self.failed = False
        self._faces.setCurrentWidget(self.page)
        if image is None:
            self.page.setPixmap(QPixmap())
            self.page.setText(tr("Drawing the preview\u2026"))
        self.page.setProperty("drawn", self.drawn)
        _repolish(self.page)
        self._refit()
        return changed

    def show_failure(self) -> None:
        self._image = None
        self.drawn = False
        self.failed = True
        self._faces.setCurrentWidget(self.failure)
        self._refit()

    def _refit(self) -> None:
        box = self._box
        if self._image is not None:
            ratio = max(1.0, self.devicePixelRatioF())
            pixmap = _page(self._image, box, ratio)
            self.page.setPixmap(pixmap)
            width = max(1, round(pixmap.width() / ratio))
        else:
            width = box.width()
        self.preview.setFixedSize(width, box.height())

    def text_height(self, width: int) -> int:
        """How tall the text column is at `width`, wrapped."""
        layout = self.text.layout()
        if layout is not None and layout.hasHeightForWidth():
            return int(layout.heightForWidth(max(1, width)))
        return int(self.text.sizeHint().height())

    def set_chosen(self, chosen: bool) -> None:
        self.tick.setText("\u2713" if chosen else "")
        # Hidden, not just blank: an empty tick still took a gap beside the
        # name, which showed as an indent right to left.
        self.tick.setVisible(chosen)
        if self.property("chosen") != chosen:
            self.setProperty("chosen", chosen)
            _repolish(self)

    # ---- The mouse -----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        self._picker.choose(self.template_id)
        if event is not None:
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        self._picker.choose(self.template_id)
        self._picker.accept()
        if event is not None:
            event.accept()


def cue_lines(cues: CardCues) -> tuple[str, str]:
    """The card's "Includes: ..." and "Can also show: ..." lines, translated;
    each empty when it has nothing to list."""
    includes = (
        tr("Includes: {list}").format(list=", ".join(tr(c) for c in cues.includes))
        if cues.includes
        else ""
    )
    supports = (
        tr("Can also show: {list}").format(list=", ".join(tr(c) for c in cues.can_show))
        if cues.can_show
        else ""
    )
    return includes, supports


def cue_hints(cues: CardCues) -> str:
    """Where each cue the page lacks would come from, a line each."""
    return "\n".join(
        tr("{cue}: {hint}").format(cue=tr(cue), hint=tr(hint))
        for cue, hint in zip(cues.can_show, cues.hints)
        if hint
    )


def card_tooltip(template: LayoutTemplate) -> str:
    """A card's tooltip: its description, then the jobs it suits. The card
    has no spare height for a "Best for" line of its own."""
    return "\n".join(
        tr(line) for line in (template.description, template.best_for) if line
    )


def cue_description(cues: CardCues) -> str:
    """Both lines and the hints, for a screen reader."""
    return "\n".join(part for part in (*cue_lines(cues), cue_hints(cues)) if part)


class _KeyFigures(QFrame):
    """The "Key figures" fields under the list, for a design that draws key
    figures: three figures side by side, each a value and what it counts,
    and a line saying they are optional and saved with the project.

    `edited` fires on every keystroke; the picker waits for a pause before
    it draws the page again."""

    edited = pyqtSignal()

    def __init__(self, rows: Figures, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("role", "keyFigures")
        self.setAccessibleName(tr("Key figures"))
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(4)
        heading = QLabel(tr("Key figures"), self)
        heading.setProperty("role", "panelHeading")
        heading.setWordWrap(True)
        box.addWidget(heading)
        fields = QHBoxLayout()
        fields.setSpacing(6)
        # A bar would split the value when it is read back ("value | label").
        no_bar = QRegularExpression("[^|]*")
        self.values: list[QLineEdit] = []
        self.labels: list[QLineEdit] = []
        for index in range(len(FIGURE_VARIABLES)):
            if index:
                fields.addSpacing(10)
            value = QLineEdit(self)
            value.setPlaceholderText(tr("Value"))
            value.setToolTip(tr("The figure itself, such as 214"))
            value.setAccessibleName(tr("Key figure {n}: value").format(n=index + 1))
            value.setValidator(QRegularExpressionValidator(no_bar, value))
            value.setMaxLength(_FIGURE_VALUE_MAX)
            label = QLineEdit(self)
            label.setPlaceholderText(tr("Label"))
            label.setToolTip(tr("What it counts, such as parcels surveyed"))
            label.setAccessibleName(tr("Key figure {n}: label").format(n=index + 1))
            label.setMaxLength(_FIGURE_LABEL_MAX)
            for edit in (value, label):
                # Narrow enough for the smallest dialog; the stretch shares
                # the rest, a label getting more room than its value.
                edit.setMinimumWidth(48)
                edit.textEdited.connect(self.edited)
            # A value is short; past this its field only adds blank room.
            value.setMaximumWidth(_FIGURE_VALUE_WIDTH)
            fields.addWidget(value, 1)
            fields.addWidget(label, 2)
            self.values.append(value)
            self.labels.append(label)
        box.addLayout(fields)
        self.note = QLabel(
            tr(
                "Optional. A label without a value is left out. Figures are "
                "saved with the project."
            ),
            self,
        )
        self.note.setProperty("role", "hint")
        self.note.setWordWrap(True)
        box.addWidget(self.note)
        self.set_rows(rows)

    def rows(self) -> Figures:
        """The figures as typed, (value, label) per slot, trimmed."""
        return tuple(
            (value.text().strip(), label.text().strip())
            for value, label in zip(self.values, self.labels)
        )

    def set_rows(self, rows: Sequence[tuple[str, str]]) -> None:
        for index, (value, label) in enumerate(zip(self.values, self.labels)):
            text = rows[index] if index < len(rows) else ("", "")
            value.setText(text[0])
            label.setText(text[1])
            # Show where the text starts, not where the cursor was left.
            value.setCursorPosition(0)
            label.setCursorPosition(0)


class TextSizeControls(QFrame):
    """The Title, Legend and Notes size steps (`TEXT_SIZE_STEPS`), three
    labelled sliders with five snapping positions in a row under the list:
    the design's text made smaller gives the map room, larger takes it. They
    apply to every design. Each label names the current step.

    `changed` fires once a pause follows the last move (or the handle is
    released), not on every value a drag passes; `sizes()` is the steps as
    last committed, `live_sizes()` as the handles stand."""

    changed = pyqtSignal()

    def __init__(
        self, sizes: TextSizes, parent: QWidget, *, stacked: bool = False
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "keyFigures")
        self.setAccessibleName(tr("Text sizes"))
        # Closed, only the header shows, in the picker's foot (`toggle`):
        # the dialog's smallest size has no room to spare for more rows.
        self.toggle = QToolButton(parent)
        self.toggle.setProperty("role", "sectionHeader")
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.toggled.connect(self._on_toggled)
        # Stacked: one slider to a row, for a column as narrow as the
        # Layout tab's; the picker's foot has the width for one row.
        row = QVBoxLayout(self) if stacked else QHBoxLayout(self)
        row.setContentsMargins(12, 2, 12, 2)
        row.setSpacing(6)
        self.sliders: dict[str, QSlider] = {}
        self._captions: dict[str, QLabel] = {}
        self._names = {
            "title": tr("Title"),
            "legend": tr("Legend"),
            "notes": tr("Notes"),
        }
        tips = {
            "title": tr("Size of the title"),
            "legend": tr("Size of the legend"),
            "notes": tr("Size of the notes and key figures"),
        }
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_SIZES_DELAY_MS)
        self._timer.timeout.connect(self._commit_and_emit)
        for index, element in enumerate(self._names):
            if index and not stacked:
                row.addSpacing(10)
            column = QHBoxLayout()
            column.setSpacing(6)
            caption = QLabel(self)
            caption.setProperty("role", "hint")
            slider = QSlider(Qt.Orientation.Horizontal, self)
            slider.setRange(0, len(TEXT_SIZE_STEPS) - 1)
            slider.setPageStep(1)
            slider.setFixedHeight(24)
            slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            slider.setAccessibleName(self._names[element])
            slider.setProperty("tip", tips[element])
            slider.setValue(self._index(getattr(sizes, element)))
            slider.valueChanged.connect(lambda _v, e=element: self._on_value(e))
            slider.sliderReleased.connect(self._timer.start)
            column.addWidget(caption)
            column.addWidget(slider)
            row.addLayout(column, 1)
            self.sliders[element] = slider
            self._captions[element] = caption
            self._describe(element)
        self._committed = self.live_sizes()
        self.toggle.setChecked(self._committed != TextSizes())
        self._on_toggled(self.toggle.isChecked())

    def _on_toggled(self, opened: bool) -> None:
        self.setVisible(opened)
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow
        )
        self._label_toggle()

    def _label_toggle(self) -> None:
        changed = self.live_sizes() != TextSizes()
        self.toggle.setText(tr("Text sizes (changed)") if changed else tr("Text sizes"))

    @staticmethod
    def _index(step_id: str) -> int:
        for index, (known, _name, _factor) in enumerate(TEXT_SIZE_STEPS):
            if known == step_id:
                return index
        return STANDARD_INDEX

    def _describe(self, element: str) -> None:
        slider = self.sliders[element]
        step = tr(TEXT_SIZE_STEPS[slider.value()][1])
        label = f"{self._names[element]}: {step}"
        self._captions[element].setText(label)
        self._label_toggle()
        slider.setToolTip(
            f"{slider.property('tip')}: {step}. "
            + tr("Smaller text gives the map more room.")
        )
        slider.setAccessibleDescription(step)

    def _on_value(self, element: str) -> None:
        self._describe(element)
        if not self.sliders[element].isSliderDown():
            # A key press or wheel step: wait out a run of them.
            self._timer.start()

    def live_sizes(self) -> TextSizes:
        return TextSizes(
            **{
                element: TEXT_SIZE_STEPS[slider.value()][0]
                for element, slider in self.sliders.items()
            }
        )

    def sizes(self) -> TextSizes:
        return self._committed

    def set_sizes(self, sizes: TextSizes) -> None:
        """Show `sizes` as the committed steps, without a `changed`."""
        self._timer.stop()
        for element, slider in self.sliders.items():
            slider.blockSignals(True)
            slider.setValue(self._index(getattr(sizes, element)))
            slider.blockSignals(False)
            self._describe(element)
        self._committed = self.live_sizes()

    def commit(self) -> bool:
        """Take the handles as they stand; whether that changed anything."""
        self._timer.stop()
        live = self.live_sizes()
        changed = live != self._committed
        self._committed = live
        return changed

    def stop(self) -> None:
        self._timer.stop()

    def _commit_and_emit(self) -> None:
        if self.commit():
            self.changed.emit()


class _ViewportWatcher(QObject):
    """Tells the picker when the list's visible area changes size."""

    def __init__(self, picker: TemplatePicker) -> None:
        super().__init__(picker)
        self._picker = picker

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._picker._relayout()
        return False


class TemplatePicker(QDialog):
    """The modal template picker. After `exec()` accepts, `template_id`,
    `page_size` and `orientation` hold the choice.

    `canvas_key` names the canvas state the previews show (extent, layers,
    styles: `canvas_cartograph.preview_state_key`). Given, drawn pages are
    shared with the next picker opened with the same key, and pages drawn
    from any other state are dropped; empty, they are kept for this picker
    only.

    `planner` lays out a design's page without drawing it, so each card can
    say what this project's page includes (`core/template_cues.py`); without
    it a card lists what every page of the design has, and offers the rest.

    `project` is where the key figures are kept (`FIGURE_VARIABLES`): the
    "Key figures" fields, shown while a design that takes them is chosen,
    start from it, and accepting writes them back. Its page previews the
    figures as they are typed, before anything is saved."""

    def __init__(
        self,
        render: PreviewRenderer,
        *,
        qsettings: QSettings | None = None,
        canvas_key: str = "",
        project: QgsProject | None = None,
        planner: PreviewPlanner | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Choose a layout template"))
        self.setModal(True)
        if parent is not None:
            # A window takes QGIS's direction, not its parent's: follow the
            # dialog, which is right to left in the plugin's RTL languages.
            self.setLayoutDirection(parent.layoutDirection())
        self._render = render
        self._qsettings = qsettings if qsettings is not None else QSettings()
        self._canvas_key = canvas_key
        if canvas_key:
            self._pages = _SHARED_PAGES
            self._pages.keep_only(canvas_key)
        else:
            self._pages = _PageCache()
        self._failed: set[_PageKey] = set()
        self._planner = planner
        #: Each card's cues by page key, so a page setting seen before is
        #: not planned again.
        self._cues: dict[_PageKey, CardCues] = {}
        # Read once: the designs as the registry has them now.
        # Essentials first, so "All" lists the safe choices before the rest.
        self._templates: dict[str, LayoutTemplate] = {
            t.id: t
            for t in sorted(
                layout_templates.TEMPLATES, key=lambda t: t.category != ESSENTIAL
            )
        }
        #: The designs still to draw for the current settings, most useful
        #: first. Recomputed whenever what is useful changes.
        self._queue: list[str] = []
        #: The design being drawn in the background, its page key and the
        #: pending preview; one at a time, so the order above holds.
        self._inflight: tuple[str, _PageKey, PendingPreview] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)
        #: Running while the list scrolls: no page is drawn until it stops.
        self._scroll_rest = QTimer(self)
        self._scroll_rest.setSingleShot(True)
        self._scroll_rest.setInterval(_SCROLL_SETTLE_MS)
        self._scroll_rest.timeout.connect(self._reprioritise)
        self._laid_out: tuple[int, int, int, bool] | None = None
        #: Whether "Automatic" turned out tall pages for this map.
        self._auto_tall = False
        self._project = project
        #: The figures as the project had them, to write back only a change.
        self._saved_figures: Figures = (
            project_figure_rows(project) if project is not None else _NO_FIGURES
        )
        #: The size steps as the project had them, to write back only a change.
        self._saved_sizes = (
            project_text_sizes(project) if project is not None else TextSizes()
        )
        self._figures_timer = QTimer(self)
        self._figures_timer.setSingleShot(True)
        self._figures_timer.setInterval(_FIGURES_DELAY_MS)
        self._figures_timer.timeout.connect(self._on_figures)
        #: Set when the key figures appear or go: the list changes height,
        #: and the next layout keeps the chosen card in view (`_relayout`).
        self._keep_in_view = False

        saved = self._load_choice()
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(tr("Each preview shows the map the canvas shows now."), self)
        intro.setProperty("role", "hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # The filter, then the two page settings: side by side when the
        # dialog is wide enough for both, the page settings on a line of
        # their own when it is not (`_place_controls`).
        self.filter_group = QFrame(self)
        self.filter_group.setProperty("role", "outputGroup")
        self.filter_group.setAccessibleName(tr("Show designs"))
        filters = QHBoxLayout(self.filter_group)
        filters.setContentsMargins(1, 1, 1, 1)
        filters.setSpacing(0)
        self.filter_buttons: dict[str, QToolButton] = {}
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for index, (key, label) in enumerate(_FILTERS):
            button = QToolButton(self.filter_group)
            button.setText(tr(label))
            button.setCheckable(True)
            button.setProperty("role", "outputPart")
            if index == 0:
                button.setProperty("position", "left")
            elif index == len(_FILTERS) - 1:
                button.setProperty("position", "right")
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._filter_group.addButton(button, index)
            filters.addWidget(button)
            self.filter_buttons[key] = button
        self.filter_buttons[FILTER_ALL].setChecked(True)
        self._filter_group.buttonToggled.connect(self._on_filter)

        # A compact labelled row for the two page settings: the labels are
        # persistent (not just a tooltip) and may wrap in a long translation.
        self.page_settings = QWidget(self)
        # Never what stops the dialog narrowing: past the point where the
        # page settings fit beside the filter, they move under it.
        self.page_settings.setMinimumWidth(1)
        bar = QHBoxLayout(self.page_settings)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        page_label = QLabel(tr("Page size"), self.page_settings)
        page_label.setProperty("role", "hint")
        page_label.setWordWrap(True)
        bar.addWidget(page_label)
        self.page_combo = QComboBox(self.page_settings)
        self.page_combo.setAccessibleName(tr("Page size"))
        self.page_combo.setToolTip(tr("Page size"))
        for size in PAGE_SIZE_ORDER:
            self.page_combo.addItem(tr(_PAGE_LABELS.get(size, size)), size)
        page_label.setBuddy(self.page_combo)
        bar.addWidget(self.page_combo)
        bar.addSpacing(8)
        orientation_label = QLabel(tr("Orientation"), self.page_settings)
        orientation_label.setProperty("role", "hint")
        orientation_label.setWordWrap(True)
        bar.addWidget(orientation_label)
        self.orientation_combo = QComboBox(self.page_settings)
        self.orientation_combo.setAccessibleName(tr("Orientation"))
        self.orientation_combo.setToolTip(tr("Orientation"))
        for value, label in _ORIENTATIONS:
            self.orientation_combo.addItem(tr(label), value)
        orientation_label.setBuddy(self.orientation_combo)
        bar.addWidget(self.orientation_combo)

        self._controls = QGridLayout()
        self._controls.setHorizontalSpacing(16)
        self._controls.setVerticalSpacing(8)
        self._controls.setColumnStretch(2, 1)
        self._controls_wrapped: bool | None = None
        layout.addLayout(self._controls)
        self._place_controls(wrapped=False)

        # The designs as cards: one column on a narrow dialog, two or three
        # side by side on a wide one (`_relayout`). Only the list scrolls.
        self.template_list = QListWidget(self)
        self.template_list.setProperty("role", "templateList")
        self.template_list.setAccessibleName(tr("Layout templates"))
        self.template_list.setViewMode(QListView.ViewMode.ListMode)
        self.template_list.setFlow(QListView.Flow.LeftToRight)
        self.template_list.setWrapping(True)
        self.template_list.setMovement(QListView.Movement.Static)
        self.template_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.template_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.template_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.template_list.verticalScrollBar().setSingleStep(24)
        self.template_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.template_list.setSpacing(_LIST_SPACING)
        self.template_list.setMinimumHeight(140)
        self.rows: dict[str, _TemplateRow] = {}
        self._items: dict[str, QListWidgetItem] = {}
        for template in self._templates.values():
            item = QListWidgetItem(self.template_list)
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            spoken = tr("{name}: {description}").format(
                name=tr(template.name), description=tr(template.description)
            )
            item.setData(Qt.ItemDataRole.AccessibleTextRole, spoken)
            item.setToolTip(card_tooltip(template))
            row = _TemplateRow(template, self, self.template_list.viewport())
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole, cue_description(row.cues)
            )
            self.template_list.setItemWidget(item, row)
            self.rows[template.id] = row
            self._items[template.id] = item
        # Under the list, for the design that is chosen: made here so that
        # the keyboard reaches it after the list and before the buttons.
        self.figures_group = _KeyFigures(self._saved_figures, self)
        self.figures_group.setVisible(False)
        self.figures_group.edited.connect(self._figures_timer.start)
        self.size_controls = TextSizeControls(self._saved_sizes, self)
        self.size_controls.changed.connect(self._restart_previews)
        self.template_list.currentItemChanged.connect(self._on_template)
        self.template_list.itemDoubleClicked.connect(lambda _item: self.accept())
        bar = self.template_list.verticalScrollBar()
        bar.valueChanged.connect(self._reprioritise)
        # The user's own scrolling only -- wheel, drag, arrows -- not the
        # list's when it lays itself out or shows the chosen card.
        bar.actionTriggered.connect(self._on_user_scroll)
        self._viewport_watcher = _ViewportWatcher(self)
        self.template_list.viewport().installEventFilter(self._viewport_watcher)
        layout.addWidget(self.template_list, 1)
        layout.addWidget(self.figures_group)
        layout.addWidget(self.size_controls)

        # The choice in words beside the buttons, for anyone not looking at
        # the ringed card; a long translated name wraps rather than clips.
        foot = QHBoxLayout()
        foot.setSpacing(12)
        self.selected_label = QLabel("", self)
        self.selected_label.setProperty("role", "hint")
        self.selected_label.setWordWrap(True)
        foot.addWidget(self.size_controls.toggle)
        foot.addWidget(self.selected_label, 1)
        buttons = QDialogButtonBox(self)
        self.create_button = buttons.addButton(
            tr("Create layout"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        foot.addWidget(buttons)
        layout.addLayout(foot)

        self._select(saved)
        self.page_combo.currentIndexChanged.connect(self._restart_previews)
        self.orientation_combo.currentIndexChanged.connect(self._restart_previews)
        theme.attach_menus(self)
        self._fit_to_screen(parent)
        self._restart_previews()

    # ---- The choice ----------------------------------------------------------

    @property
    def template_id(self) -> str:
        item = self.template_list.currentItem()
        if item is not None:
            return str(item.data(Qt.ItemDataRole.UserRole))
        return self._default_template()

    @property
    def page_size(self) -> str:
        return str(self.page_combo.currentData())

    @property
    def orientation(self) -> str:
        return str(self.orientation_combo.currentData())

    @property
    def figures(self) -> Figures:
        """The key figures as typed: (value, label) per slot, blank ones
        included."""
        return self.figures_group.rows()

    @property
    def text_sizes(self) -> TextSizes:
        """The title, legend and notes steps as chosen."""
        return self.size_controls.sizes()

    @property
    def category_filter(self) -> str:
        for key, button in self.filter_buttons.items():
            if button.isChecked():
                return key
        return FILTER_ALL

    def choose(self, template_id: str) -> None:
        """Select `template_id`'s card and give the list the keyboard."""
        item = self._items.get(template_id)
        if item is None:
            return
        self.template_list.setCurrentItem(item)
        self.template_list.setFocus(Qt.FocusReason.MouseFocusReason)

    def set_filter(self, key: str) -> None:
        button = self.filter_buttons.get(key)
        if button is not None:
            button.setChecked(True)

    def accept(self) -> None:
        self._timer.stop()
        self._cancel_inflight()
        self._figures_timer.stop()
        self._save_choice()
        self._save_figures()
        self.size_controls.commit()
        self._save_sizes()
        super().accept()

    def reject(self) -> None:
        self._timer.stop()
        self._cancel_inflight()
        self._figures_timer.stop()
        self.size_controls.stop()
        super().reject()

    # ---- Previews ------------------------------------------------------------

    def _key(self, template_id: str) -> _PageKey:
        ratio = max(1.0, self.devicePixelRatioF())
        return (
            template_id,
            self.page_size,
            self.orientation,
            round(RENDER_WIDTH * ratio),
            self._canvas_key,
            self.figures if self._templates[template_id].takes_figures else (),
            self.text_sizes,
        )

    def _restart_previews(self, *_args: object) -> None:
        """Show what is already drawn for the current page settings, and draw
        the rest, the most useful first. Never draws in this call: a page
        setting changed over and over costs nothing but the last one."""
        self._update_cues(self.rows)
        self._relayout()
        for template_id in self.rows:
            self._show_state(template_id)
        self._reprioritise()

    def _show_state(self, template_id: str) -> None:
        row = self.rows[template_id]
        key = self._key(template_id)
        page = self._pages.get(key)
        if page is not None:
            if row.show_page(page):
                # The card's text changed: its height may have to.
                self._laid_out = None
                self._relayout()
        elif key in self._failed:
            row.show_failure()
            self._mark_failed_item(template_id)
        else:
            row.show_page(None)
            self._items[template_id].setToolTip(
                card_tooltip(self._templates[template_id])
            )

    def _update_cues(self, template_ids: Iterable[str]) -> None:
        """Check the cards' cues against the pages this project gets with the
        current page settings (and typed figures): planned, not drawn."""
        if self._planner is None:
            return
        for template_id in template_ids:
            key = self._key(template_id)
            cues = self._cues.get(key)
            if cues is None:
                template = self._templates[template_id]
                try:
                    plan = self._planner(
                        template_id, key[1], key[2], figures=key[5], sizes=key[6]
                    )
                except Exception:  # a page the planner cannot lay out
                    cues = declared_cues(template)
                else:
                    cues = plan_cues(template, plan)
                self._cues[key] = cues
            if self.rows[template_id].set_cues(cues):
                self._items[template_id].setData(
                    Qt.ItemDataRole.AccessibleDescriptionRole, cue_description(cues)
                )
                # The card's text changed: its height may have to.
                self._laid_out = None

    def _reprioritise(self, *_args: object) -> None:
        """Order what is left to draw: the selected design, then the cards in
        view, then the ones a short scroll away. A design filtered out or
        further down the list waits until it comes near the view."""
        current = self.template_list.currentItem()
        chosen = (
            str(current.data(Qt.ItemDataRole.UserRole))
            if current is not None and not current.isHidden()
            else None
        )
        order = ([chosen] if chosen else []) + self.visible_ids() + self.near_ids()
        if self._inflight is not None:
            drawing, drawing_key, _pending = self._inflight
            if drawing not in order or self._key(drawing) != drawing_key:
                # Not wanted any more: a newer request has taken its place.
                self._cancel_inflight()
        queue: list[str] = []
        for template_id in order:
            if template_id in queue:
                continue
            if self._inflight is not None and template_id == self._inflight[0]:
                continue
            key = self._key(template_id)
            if key in self._pages or key in self._failed:
                continue
            queue.append(template_id)
        self._queue = queue
        if queue and not self._timer.isActive() and not self._scroll_rest.isActive():
            self._timer.start()

    def visible_ids(self) -> list[str]:
        """The designs whose cards are at least partly in view, in order."""
        return self._ids_within(0.0)

    def near_ids(self) -> list[str]:
        """The designs whose cards are in view or a short scroll from it."""
        return self._ids_within(_NEAR_VIEW)

    def _ids_within(self, share: float) -> list[str]:
        viewport = self.template_list.viewport().rect()
        margin = round(viewport.height() * share)
        area = viewport.adjusted(0, -margin, 0, margin)
        return [
            template_id
            for template_id, item in self._items.items()
            if not item.isHidden()
            and self.template_list.visualItemRect(item).intersects(area)
        ]

    def _on_user_scroll(self, *_args: object) -> None:
        """The user is scrolling: draw nothing until the list rests, then
        what is in view there."""
        self._timer.stop()
        self._scroll_rest.start()

    def _render_next(self) -> None:
        if self._scroll_rest.isActive() or self._inflight is not None:
            return
        self._reprioritise()
        if not self._queue:
            return
        template_id = self._queue.pop(0)
        key = self._key(template_id)
        try:
            result = self._render(
                template_id, key[1], key[2], key[3], figures=key[5], sizes=key[6]
            )
        except Exception:  # a preview must never break the picker
            result = None
        if result is not None and not isinstance(result, QImage):
            # Drawn in the background: the next one waits for this one.
            self._inflight = (template_id, key, result)
            result.finished.connect(
                lambda image, pending=result: self._on_pending_done(pending, image)
            )
            self._show_state(template_id)
            return
        self._store_page(template_id, key, result)
        if self._queue:
            self._timer.start()

    def _store_page(
        self, template_id: str, key: _PageKey, image: QImage | None
    ) -> None:
        """Keep a drawn page for its card, or mark the card failed."""
        if image is not None and not image.isNull():
            self._pages.put(key, image)
            if self._templates[template_id].takes_figures:
                self._pages.drop_variants(key)
            if self.orientation == "auto":
                # Fitted to the map: the pages show which way it went.
                self._auto_tall = image.height() > image.width()
                self._relayout()
        else:
            self._failed.add(key)
        self._show_state(template_id)

    def _on_pending_done(self, pending: PendingPreview, image: QImage | None) -> None:
        if self._inflight is None or self._inflight[2] is not pending:
            return  # cancelled while it finished
        template_id, key, _pending = self._inflight
        self._inflight = None
        self._store_page(template_id, key, image)
        self._reprioritise()

    def _cancel_inflight(self) -> None:
        """Stop the page being drawn in the background, if any."""
        if self._inflight is None:
            return
        _template_id, _key, pending = self._inflight
        self._inflight = None
        pending.cancel()

    def retry(self, template_id: str) -> None:
        """Draw `template_id`'s page again, next."""
        self._failed.discard(self._key(template_id))
        self._show_state(template_id)
        self._reprioritise()
        if self._queue and self._queue[0] != template_id:
            self._queue.remove(template_id)
            self._queue.insert(0, template_id)

    def _mark_failed_item(self, template_id: str) -> None:
        self._items[template_id].setToolTip(
            f"{card_tooltip(self._templates[template_id])}\n"
            + tr("Preview could not be drawn")
        )

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._relayout()
        if self.template_list.currentItem() is None:
            # Nothing chosen (and nothing remembered): arrows need a start.
            for item in self._items.values():
                if not item.isHidden():
                    self.template_list.setCurrentItem(item)
                    break
        # The keyboard starts on the designs: arrows choose, Enter creates.
        self.template_list.setFocus(Qt.FocusReason.OtherFocusReason)
        # The remembered design may be far down the list.
        current = self.template_list.currentItem()
        if current is not None:
            self.template_list.scrollToItem(
                current, QAbstractItemView.ScrollHint.PositionAtCenter
            )
        self._reprioritise()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_controls(wrapped=self._controls_need_wrap())

    # ---- Layout --------------------------------------------------------------

    def _fit_to_screen(self, parent: QWidget | None) -> None:
        available = None
        with contextlib.suppress(Exception):
            screen = (parent.screen() if parent is not None else None) or (
                QGuiApplication.primaryScreen()
            )
            if screen is not None:
                available = screen.availableGeometry()
        if available is None or available.isEmpty():
            available = QRect(0, 0, 1280, 800)
        size, minimum = dialog_sizes(available.size())
        self.setMinimumSize(minimum)
        self.resize(size)

    def _controls_need_wrap(self) -> bool:
        margins = self.layout().contentsMargins() if self.layout() else None
        room = self.width() - (margins.left() + margins.right() if margins else 0)
        need = (
            self.filter_group.sizeHint().width()
            + self._controls.horizontalSpacing()
            + self.page_settings.sizeHint().width()
        )
        return need > room

    def _place_controls(self, *, wrapped: bool) -> None:
        if wrapped == self._controls_wrapped:
            return
        self._controls_wrapped = wrapped
        self._controls.removeWidget(self.filter_group)
        self._controls.removeWidget(self.page_settings)
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self._controls.addWidget(self.filter_group, 0, 0, left)
        if wrapped:
            self._controls.addWidget(self.page_settings, 1, 0, 1, 3, left)
        else:
            self._controls.addWidget(self.page_settings, 0, 1, left)

    def _relayout(self) -> None:
        """Size the cards to the list: as many columns as fit, every card the
        same height, tall enough for its wrapped text."""
        view = self.template_list
        width = view.viewport().width()
        scrollbar = view.verticalScrollBar()
        if scrollbar is not None and not scrollbar.isVisible():
            # Keep room for the scroll bar either way, so it appearing never
            # wraps a card onto the next line.
            width -= view.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        height = view.viewport().height()
        columns = max(
            1,
            min(
                _MAX_COLUMNS,
                (width - _LIST_SPACING) // (_CARD_MIN_WIDTH + 2 * _LIST_SPACING),
            ),
        )
        # The list puts its spacing on both sides of every card, and wraps a
        # card that would end within one spacing of the edge; two spare
        # pixels for rounding.
        card_w = max(1, (width - _LIST_SPACING * (2 * columns + 1)) // columns - 2)
        tall = self.orientation == "portrait" or (
            self.orientation == "auto" and self._auto_tall
        )
        state = (card_w, height, columns, tall)
        if state == self._laid_out:
            return
        self._laid_out = state
        # The chosen card, in view before the cards change size, stays in
        # view after: a short list must not leave it scrolled off.
        current = view.currentItem()
        anchored = (
            current is not None
            and not current.isHidden()
            and (
                self._keep_in_view
                or view.visualItemRect(current).intersects(view.viewport().rect())
            )
        )
        self._keep_in_view = False
        box = _preview_box(card_w, height, tall)
        text_w = card_w - box.width() - 2 * _ROW_PADDING - 16
        card_h = box.height() + 2 * _ROW_PADDING
        for row in self.rows.values():
            row.set_box(box)
            card_h = max(card_h, row.text_height(text_w) + 2 * _ROW_PADDING)
        hint = QSize(card_w, card_h)
        for item in self._items.values():
            item.setSizeHint(hint)
        view.doItemsLayout()
        if anchored and current is not None:
            view.scrollToItem(current, QAbstractItemView.ScrollHint.EnsureVisible)
        self._reprioritise()

    # ---- Helpers -------------------------------------------------------------

    def _on_template(self, current: QListWidgetItem | None, _previous: object) -> None:
        chosen = current.data(Qt.ItemDataRole.UserRole) if current else None
        for template_id, row in self.rows.items():
            row.set_chosen(template_id == chosen)
        template = self._templates.get(str(chosen)) if chosen else None
        name = tr(template.name) if template else ""
        self.selected_label.setText(tr("Selected: {name}").format(name=name))
        takes_figures = template is not None and template.takes_figures
        if self.figures_group.isVisibleTo(self) != takes_figures:
            self.figures_group.setVisible(takes_figures)
            self._keep_in_view = True
        self._reprioritise()

    def _on_figures(self) -> None:
        """Typing paused: draw the pages that show the figures again, and
        say whether they now include them. Until a page is drawn, the one
        before stays rather than a blank box."""
        self._update_cues(
            [t for t, template in self._templates.items() if template.takes_figures]
        )
        self._relayout()
        for template_id, template in self._templates.items():
            if not template.takes_figures:
                continue
            key = self._key(template_id)
            if (
                key in self._pages
                or key in self._failed
                or self.rows[template_id].failed
            ):
                self._show_state(template_id)
        self._reprioritise()

    def _save_figures(self) -> None:
        """Write the figures to the project, when they changed: a cleared
        row removes its variable (`set_project_figures`)."""
        if self._project is None or self.figures == self._saved_figures:
            return
        set_project_figures(self._project, self.figures)
        self._saved_figures = self.figures

    def _save_sizes(self) -> None:
        """Write the size steps to the project, when they changed
        (`set_project_text_sizes`)."""
        if self._project is None or self.text_sizes == self._saved_sizes:
            return
        set_project_text_sizes(self._project, self.text_sizes)
        self._saved_sizes = self.text_sizes

    def _on_filter(self, _button: object, checked: bool) -> None:
        if not checked:
            return
        key = self.category_filter
        for template_id, item in self._items.items():
            category = self._templates[template_id].category
            item.setHidden(key != FILTER_ALL and category != key)
        current = self.template_list.currentItem()
        if current is None or current.isHidden():
            for item in self._items.values():
                if not item.isHidden():
                    self.template_list.setCurrentItem(item)
                    break
        current = self.template_list.currentItem()
        if current is not None and not current.isHidden():
            self.template_list.scrollToItem(
                current, QAbstractItemView.ScrollHint.EnsureVisible
            )
        self._reprioritise()

    def _default_template(self) -> str:
        if DEFAULT_TEMPLATE in self._templates:
            return DEFAULT_TEMPLATE
        return next(iter(self._templates))

    def _select(self, saved: tuple[str, str, str]) -> None:
        template_id, page_size, orientation = saved
        item = self._items.get(template_id) or self._items.get(self._default_template())
        if item is not None:
            self.template_list.setCurrentItem(item)
        for combo, value in (
            (self.page_combo, page_size),
            (self.orientation_combo, orientation),
        ):
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

    def _load_choice(self) -> tuple[str, str, str]:
        """The last choice, or Daylight on A4 fitted to the map. Anything
        stored that is no longer an option falls back to the default."""
        value = self._qsettings.value(SETTINGS_KEY, "", type=str) or ""
        parts = value.split("|")
        default = (self._default_template(), PAGE_SIZE_ORDER[0], "auto")
        if len(parts) != 3:
            return default
        template_id = parts[0] if parts[0] in self._templates else default[0]
        return template_id, parts[1], parts[2]

    def _save_choice(self) -> None:
        self._qsettings.setValue(
            SETTINGS_KEY, f"{self.template_id}|{self.page_size}|{self.orientation}"
        )


def ask_template(
    render: PreviewRenderer,
    parent: QWidget | None = None,
    canvas_key: str = "",
    project: QgsProject | None = None,
    planner: PreviewPlanner | None = None,
) -> tuple[str, str, str] | None:
    """Show the picker; `(template_id, page_size, orientation)`, or `None`
    when the user cancels. Accepted, the key figures typed in it and
    the text size steps chosen are saved to `project`."""
    picker = TemplatePicker(
        render,
        canvas_key=canvas_key,
        project=project,
        planner=planner,
        parent=parent,
    )
    try:
        if picker.exec() != QDialog.DialogCode.Accepted:
            return None
        return picker.template_id, picker.page_size, picker.orientation
    finally:
        # A child of `parent` lives as long as it does: every opening would
        # keep its cards and pages until the plugin's dialog closed.
        picker.deleteLater()


def _page(image: QImage, box: QSize, ratio: float) -> QPixmap:
    """`image` (a page) fitted into `box`, with a thin edge so a white page
    stands apart from a light card."""
    room = QSize(round(box.width() * ratio), round(box.height() * ratio))
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
