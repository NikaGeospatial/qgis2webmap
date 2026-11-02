"""Current canvas > choose a layout template: immediate layout guides,
replaced card by card with the current map in each design.

The picker only chooses -- a template, a page size and an orientation -- and
the controller builds the layout (`core/canvas_cartograph.py`). Each preview
is the real page QGIS would build, drawn by QGIS (`template_preview`), so
what the thumbnail shows is what the layout will be. What goes on the page --
the text and its sizes -- is edited on the
layout afterwards, in the dialog's Edit tab, not here.

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

The window is made at once: the cards are planned (their cues and layout
guides) a few at a time on the event loop after it shows -- the chosen one,
then those in view -- and a card not planned yet says "Waiting to draw".
Each card then starts with an inexpensive diagram of its planned layout, labelled
"Waiting to draw", then "Drawing your map..." while its turn comes, then the
real page. There is no budget for the whole opening: a slow map still fills
every card, one at a time, and a card that fails or takes too long (one
timeout, `_CARD_RENDER_TIMEOUT_MS`) shows "Preview unavailable" with a Retry
while the cards after it carry on. A page is drawn only as wide as its card
shows it (`_render_pixels`). Preview opens the real map at a useful size,
showing the card's page at once while the sharper one is drawn. The final
grid card is a request-form link, never a design that can be selected or
created.

Closing the picker, or a setting making the page in flight obsolete, drops
its result at once; QGIS winds the page down in the background and the next
one waits (`canvas_cartograph.render_slot_busy`).

**What a card says it includes** is checked against the page this project
gets (`core/template_cues.py`): "Includes" lists what the page shows, "Can
also show" what the design would show with more content -- notes, a source
line -- with a tooltip saying where it comes from.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import time
from collections import OrderedDict
from collections.abc import Iterable
from typing import Protocol

from qgis.core import QgsProject
from qgis.PyQt.QtCore import (
    QEvent,
    QObject,
    QRect,
    QRectF,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
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
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedLayout,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import layout_templates, template_registry
from ...core.canvas_cartograph import (
    PREVIEW_LEGEND_LEFT_OUT_KEY,
    PREVIEW_LOCATOR_NO_CONTEXT_KEY,
    PendingPreview,
    render_slot_busy,
)
from ...core.layout_templates import (
    CHARACTERFUL,
    ESSENTIAL,
    PAGE_SIZE_ORDER,
    LayoutTemplate,
    TemplateInput,
    TemplatePlan,
)
from ...core.template_cues import CardCues, declared_cues, plan_cues
from ...core.template_framing import (
    FRAMING_VARIABLE,
    preview_framing,
    project_framing,
    set_project_framing,
)
from .. import theme
from ..i18n import N_, plural, tr
from ..links import TEMPLATE_REQUEST_URL

#: Where the last choice is kept, under this plugin's own QSettings group.
SETTINGS_KEY = "qgis2webmap/cartograph/template"

#: The design a first-time user starts on (Daylight): the safest choice.
DEFAULT_TEMPLATE = "classic"
_DARK_TEMPLATES = frozenset(("dark", "night-desk"))

#: The widest a page preview is shown, in logical pixels. A page is drawn at
#: the width its card shows it, up to this, times the screen's pixel ratio.
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
#: The line height of the text the minimum size was laid out for.
_TEXT_HEIGHT = 22
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
_SHARED_PLAN_LIMIT = 96
#: Cards this close to the view, as a share of its height, count as in
#: view: drawn before they are scrolled to, not just after.
_NEAR_VIEW = 0.5
#: How long the list must rest after a scroll before the next page is
#: drawn: a page takes the event loop for its whole drawing, so none
#: starts while the user is still scrolling.
_SCROLL_SETTLE_MS = 150
#: One card that has not drawn in this long is given up on -- that card
#: only: it shows "Preview unavailable" with a Retry, and the cards after it
#: carry on. There is no budget for the opening as a whole: a slow map still
#: fills every card, one at a time.
_CARD_RENDER_TIMEOUT_MS = 45_000
#: Pages are drawn at a multiple of this many pixels wide.
_WIDTH_STEP = 40
#: How soon to look again while a cancelled page still holds the render slot.
_SLOT_RECHECK_MS = 100
#: How long one turn of planning cards may run before the window gets the
#: event loop back (a single card's plan is not split, so a turn can run a
#: little past it).
_PLAN_TURN_S = 0.05

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


class PreviewRenderer(Protocol):
    """`render(template_id, page_size, orientation, width_px)`
    -> the preview, or `None` when it cannot be drawn; or a pending preview
    (`canvas_cartograph.PendingPreview`: a `finished` signal carrying the
    image or `None`, and `cancel()`), drawn in the background while the
    picker stays usable."""

    def __call__(
        self,
        template_id: str,
        page_size: str,
        orientation: str,
        width_px: int,
    ) -> QImage | PendingPreview | None: ...


class PreviewPlanner(Protocol):
    """`plan(template_id, page_size, orientation)` -> the page
    `template_id` makes of this project, as the planner lays it out (what
    the preview draws), for the card's cues. Cheap next to drawing it."""

    def __call__(
        self,
        template_id: str,
        page_size: str,
        orientation: str,
    ) -> TemplatePlan: ...


#: (template, page size, orientation, width, canvas, map framing).
_PageKey = tuple[str, str, str, int, str, str]
#: A `_PageKey` without the width: what a plan or a card's cues depend on.
_PlanKey = tuple[str, str, str, str, str]


def _plan_key(key: _PageKey) -> _PlanKey:
    return (*key[:3], *key[4:])


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


#: Shared by every picker opened with the same `canvas_key`.
_SHARED_PAGES = _PageCache(_SHARED_CACHE_LIMIT, _SHARED_CACHE_BYTES)
#: Plans (the layout each design makes of the canvas, for the cards' cues and
#: guides) kept between openings under the same `canvas_key`: planning all
#: the designs is the time a picker takes to open, and a plan is a function of
#: the canvas state and the page settings alone.
_SHARED_PLANS: OrderedDict[tuple[str, _PlanKey], TemplatePlan] = OrderedDict()


def dialog_sizes(available: QSize, text_scale: float = 1.0) -> tuple[QSize, QSize]:
    """The picker's opening and minimum size on a screen whose work area is
    `available`: the default, more on a large screen, never past
    90% x 88% of the work area. The minimum grows with `text_scale`, the
    text height against the one it was laid out for (`_TEXT_HEIGHT`)."""
    cap_w = int(available.width() * _SCREEN_SHARE_W)
    cap_h = int(available.height() * _SCREEN_SHARE_H)
    width = min(
        cap_w, max(_DEFAULT_SIZE.width(), int(available.width() * _LARGE_SHARE_W))
    )
    height = min(
        cap_h, max(_DEFAULT_SIZE.height(), int(available.height() * _LARGE_SHARE_H))
    )
    # Larger text needs more room for the same controls and cards.
    minimum = QSize(
        min(round(_MINIMUM_SIZE.width() * text_scale), cap_w),
        min(round(_MINIMUM_SIZE.height() * text_scale), cap_h),
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
    width = max(_MIN_BOX_WIDTH, min(RENDER_WIDTH, round(card_width * 0.54)))
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


def locator_without_context(image: QImage) -> bool:
    """Whether the drawn page has a locator inset with no wider context to
    show (`canvas_cartograph.locator_lacks_context`)."""
    return image.text(PREVIEW_LOCATOR_NO_CONTEXT_KEY) == "1"


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
        self.page = QLabel("", self.preview)
        self.page.setProperty("role", "templatePage")
        self.page.setProperty("drawn", False)
        self.page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page.setWordWrap(True)
        # What the page is doing while it is not drawn yet, over the layout
        # guide: "Waiting to draw" or "Drawing your map...". Hidden once the
        # real page shows. A label of its own, so the box never changes size.
        over = QVBoxLayout(self.page)
        over.setContentsMargins(4, 4, 4, 4)
        over.addStretch(1)
        self.status = QLabel("", self.page)
        self.status.setProperty("role", "templateStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        over.addWidget(self.status, 0, Qt.AlignmentFlag.AlignHCenter)
        over.addStretch(1)
        faces.addWidget(self.page)
        self.failure = QFrame(self.preview)
        self.failure.setProperty("role", "templateFailure")
        failure = QVBoxLayout(self.failure)
        failure.setContentsMargins(8, 8, 8, 8)
        failure.addStretch(1)
        message = QLabel(tr("Preview unavailable"), self.failure)
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
        self.preview_button = QPushButton(tr("Preview"), self.text)
        self.preview_button.setProperty("role", "secondary")
        self.preview_button.setAutoDefault(False)
        # Beside the name, so the card is no taller for it. The list's
        # Space key opens the selected preview. Ten extra Tab stops,
        # including offscreen cards, would make the picker harder.
        self.preview_button.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.preview_button.setToolTip(f"{tr('Preview')} (Space)")
        self.preview_button.setAccessibleDescription(tr(template.name))
        self.preview_button.clicked.connect(
            lambda: picker.open_large_preview(self.template_id)
        )
        name_row.addWidget(self.preview_button, 0, Qt.AlignmentFlag.AlignTop)
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
        #: Whether the page is taller than wide, as last seen: kept while
        #: the page is replaced by a failure, so the box does not change shape.
        self.tall = False
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
        if image is not None and locator_without_context(image):
            crowded = " ".join(
                part
                for part in (
                    crowded,
                    tr(
                        "Locator: the data reaches no further than this map, so "
                        "it shows only a margin round it. Delete it on the "
                        "page, or add a layer that covers the wider area."
                    ),
                )
                if part
            )
        changed = crowded != self.crowded.text()
        self.crowded.setText(crowded)
        self.crowded.setVisible(bool(crowded))
        self._image = image
        if image is not None:
            self.tall = image.height() > image.width()
        self.drawn = image is not None
        self.failed = False
        self._faces.setCurrentWidget(self.page)
        if image is None:
            self.page.setPixmap(QPixmap())
        self.status.setVisible(False)
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

    def show_guide(self, image: QImage, drawing: bool = False) -> None:
        """The layout guide, with the page's state over it: waiting its turn
        or being drawn now."""
        self.show_page(image)
        self.drawn = False
        self.status.setText(
            tr("Drawing your map\u2026") if drawing else tr("Waiting to draw")
        )
        self.status.setVisible(True)
        self.page.setToolTip(
            tr("Layout guide. Your current map replaces it as it is drawn.")
        )

    def show_waiting(self) -> None:
        """An empty page, "Waiting to draw": the card is not planned yet, so
        there is no layout guide to show over it."""
        self.show_page(None)
        self.status.setText(tr("Waiting to draw"))
        self.status.setVisible(True)
        self.page.setToolTip(
            tr("Layout guide. Your current map replaces it as it is drawn.")
        )

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


class _ViewportWatcher(QObject):
    """Tells the picker when the list's visible area changes size."""

    def __init__(self, picker: TemplatePicker) -> None:
        super().__init__(picker)
        self._picker = picker

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._picker._relayout()
        return False


class _TemplateList(QListWidget):
    preview_requested = pyqtSignal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.preview_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _RequestTemplateCard(QPushButton):
    """An action in the card grid, separate from the template selection."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr("Request a template"))
        self.setAccessibleDescription(tr("Opens the template suggestion form."))
        self.setToolTip(tr("Opens the template suggestion form."))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        column = QVBoxLayout(self)
        column.setContentsMargins(16, 12, 16, 12)
        column.setSpacing(4)
        column.addStretch()
        for text, size in (
            ("+", 28),
            (tr("Request a template"), 0),
            (tr("Suggest a design you\u2019d like to use."), 0),
        ):
            label = QLabel(text, self)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            if size:
                font = label.font()
                font.setPointSize(size)
                label.setFont(font)
            column.addWidget(label)
        column.addStretch()
        self.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(TEMPLATE_REQUEST_URL))
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        active = self.hasFocus() or self.underMouse()
        colour = palette.color(
            QPalette.ColorRole.Highlight if active else QPalette.ColorRole.Mid
        )
        painter.setPen(QPen(colour, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(palette.brush(QPalette.ColorRole.Base))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


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

    `project` is where the map framing is kept; accepting writes it back."""

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
            for stale in [k for k in _SHARED_PLANS if k[0] != canvas_key]:
                del _SHARED_PLANS[stale]
        else:
            self._pages = _PageCache()
        self._failed: set[_PageKey] = set()
        self._guides = _PageCache(limit=24, max_bytes=16 * 1024 * 1024)
        #: Set once the picker is answered: nothing more is drawn or shown.
        self._closed = False
        #: How wide, in logical pixels, a wide page is shown on its card now;
        #: pages are drawn for this, not more (`_render_pixels`).
        self._thumb_width = RENDER_WIDTH
        self._preview_deadline = QTimer(self)
        self._preview_deadline.setSingleShot(True)
        self._preview_deadline.setInterval(_CARD_RENDER_TIMEOUT_MS)
        self._preview_deadline.timeout.connect(self._card_timed_out)
        self._planner = planner
        self._plans: OrderedDict[_PlanKey, TemplatePlan] = OrderedDict()
        #: Each card's cues by page key, so a page setting seen before is
        #: not planned again.
        self._cues: dict[_PlanKey, CardCues] = {}
        # Read once: the designs as the registry has them now.
        # Essentials first, followed by characterful designs, then the two
        # dark sheets together at the end.
        self._templates: dict[str, LayoutTemplate] = {
            t.id: t
            for t in sorted(
                template_registry.starter_templates(),
                key=lambda t: (t.id in _DARK_TEMPLATES, t.category != ESSENTIAL),
            )
        }
        #: The designs still to draw for the current settings, most useful
        #: first. Recomputed whenever what is useful changes.
        self._queue: list[str] = []
        #: The designs whose cues and layout guide are still to be planned,
        #: most useful first; planned in short turns after the window shows
        #: (`_plan_turn`), never all in the constructor.
        self._plan_queue: list[str] = []
        #: False until the constructor is done: choosing a card plans it at
        #: once only from then on.
        self._ready = False
        #: The design being drawn in the background, its page key and the
        #: pending preview; one at a time, so the order above holds.
        self._inflight: tuple[str, _PageKey, PendingPreview] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)
        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(0)
        self._plan_timer.timeout.connect(self._plan_turn)
        #: Running while the list scrolls: no page is drawn until it stops.
        self._scroll_rest = QTimer(self)
        self._scroll_rest.setSingleShot(True)
        self._scroll_rest.setInterval(_SCROLL_SETTLE_MS)
        self._scroll_rest.timeout.connect(self._reprioritise)
        self._laid_out: tuple[int, int, int, tuple[bool, ...]] | None = None
        self._project = project
        saved = self._load_choice()
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            tr("Choose a design. Preview shows it with your current map."), self
        )
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
        self._controls_arrangement_now: str | None = None
        self._base_minimum_height = _MINIMUM_SIZE.height()
        self._screen_cap_height = 1 << 20
        layout.addLayout(self._controls)

        # Beside the design filter when they fit together, so the choice
        # costs the card list no height (`_place_controls`).
        self.framing_combo = QComboBox(self)
        self.framing_combo.setAccessibleName(tr("Map framing"))
        self.framing_combo.addItem(tr("Framing: fill the frame"), "fill")
        self.framing_combo.addItem(tr("Framing: whole view"), "whole")
        self.framing_combo.setToolTip(
            tr(
                "Fill crops the view, not the data. "
                "Whole view keeps the entire canvas extent."
            )
        )
        if project is not None:
            from qgis.core import QgsExpressionContextUtils

            if QgsExpressionContextUtils.projectScope(project).hasVariable(
                FRAMING_VARIABLE
            ):
                self.framing_combo.setCurrentIndex(
                    self.framing_combo.findData(project_framing(project))
                )
        self._place_controls("row")

        # The designs as cards: one column on a narrow dialog, two or three
        # side by side on a wide one (`_relayout`). Only the list scrolls.
        self.template_list = _TemplateList(self)
        self.template_list.preview_requested.connect(
            lambda: self.open_large_preview(self.template_id)
        )
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
        self.request_item = QListWidgetItem(self.template_list)
        self.request_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.request_item.setData(
            Qt.ItemDataRole.AccessibleTextRole, tr("Request a template")
        )
        self.request_button = _RequestTemplateCard(self.template_list.viewport())
        self.template_list.setItemWidget(self.request_item, self.request_button)
        self.template_list.currentItemChanged.connect(self._on_template)
        self.template_list.itemDoubleClicked.connect(
            lambda item: self.accept() if item is not self.request_item else None
        )
        bar = self.template_list.verticalScrollBar()
        bar.valueChanged.connect(self._reprioritise)
        # The user's own scrolling only -- wheel, drag, arrows -- not the
        # list's when it lays itself out or shows the chosen card.
        bar.actionTriggered.connect(self._on_user_scroll)
        self._viewport_watcher = _ViewportWatcher(self)
        self.template_list.viewport().installEventFilter(self._viewport_watcher)
        layout.addWidget(self.template_list, 1)

        # The choice in words beside the buttons, for anyone not looking at
        # the ringed card; a long translated name wraps rather than clips.
        foot = QHBoxLayout()
        foot.setSpacing(12)
        self.selected_label = QLabel("", self)
        self.selected_label.setProperty("role", "hint")
        self.selected_label.setWordWrap(True)
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
        self.framing_combo.currentIndexChanged.connect(self._restart_previews)
        theme.attach_menus(self)
        self._fit_to_screen(parent)
        self._restart_previews()
        self._ready = True

    # ---- The choice ----------------------------------------------------------

    @property
    def template_id(self) -> str:
        item = self.template_list.currentItem()
        if item is not None and item is not self.request_item:
            return str(item.data(Qt.ItemDataRole.UserRole))
        return self._default_template()

    @property
    def page_size(self) -> str:
        return str(self.page_combo.currentData())

    @property
    def orientation(self) -> str:
        return str(self.orientation_combo.currentData())

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

    def open_large_preview(self, template_id: str) -> None:
        """Inspect a page; no layout is created and no setting is saved."""
        from .template_preview_dialog import TemplatePreviewDialog

        self.choose(template_id)
        key = self._key(template_id)
        self._timer.stop()
        self._scroll_rest.stop()
        self._cancel_inflight()

        def render(width: int):
            large_key = (*key[:3], width, *key[4:])
            cached = self._pages.get(large_key)
            if cached is not None:
                return cached
            with preview_framing(key[5]):
                return self._render(template_id, key[1], key[2], width)

        template = self._templates[template_id]
        preview = TemplatePreviewDialog(
            tr(template.name),
            tr(template.description),
            f"{self.page_combo.currentText()} · {self.orientation_combo.currentText()}",
            render,
            self,
            self._pages.get(key),
        )
        try:
            preview.show()
            preview.start()
            preview.exec()
        finally:
            image = preview.page.image
            if image is not None and not image.isNull():
                self._pages.put((*key[:3], image.width(), *key[4:]), image)
                thumbnail = image.scaledToWidth(
                    key[3], Qt.TransformationMode.SmoothTransformation
                )
                for name in image.textKeys():
                    thumbnail.setText(name, image.text(name))
                self._store_page(template_id, key, thumbnail)
            preview.deleteLater()
            self._reprioritise()

    def accept(self) -> None:
        self._stop_drawing()
        self._save_choice()
        if self._project is not None:
            set_project_framing(self._project, self.framing_combo.currentData())
        super().accept()

    def reject(self) -> None:
        self._stop_drawing()
        super().reject()

    # ---- Previews ------------------------------------------------------------

    def _render_pixels(self) -> int:
        """The width a page is drawn at: what its card can show (the box's
        width times the screen's pixel ratio), rounded up to a step so a
        window resized a little keeps the pages it has."""
        ratio = max(1.0, self.devicePixelRatioF())
        return -(-round(self._thumb_width * ratio) // _WIDTH_STEP) * _WIDTH_STEP

    def _key(self, template_id: str) -> _PageKey:
        return (
            template_id,
            self.page_size,
            self.orientation,
            self._render_pixels(),
            self._canvas_key,
            str(self.framing_combo.currentData()),
        )

    def _restart_previews(self, *_args: object) -> None:
        """Show what is already drawn for the current page settings, and draw
        the rest, the most useful first. Never draws in this call: a page
        setting changed over and over costs nothing but the last one."""
        self._relayout()
        for template_id in self.rows:
            self._show_state(template_id)
        self._relayout()
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
            drawing = (
                self._inflight is not None
                and self._inflight[0] == template_id
                and self._inflight[1] == key
            )
            if not self._is_planned(template_id):
                if not drawing:
                    row.show_waiting()
                    self._items[template_id].setToolTip(
                        card_tooltip(self._templates[template_id])
                    )
                    return
                self._update_cues([template_id])
            row.show_guide(self._guide(template_id, key), drawing)
            self._items[template_id].setToolTip(
                card_tooltip(self._templates[template_id])
            )

    def _guide(self, template_id: str, key: _PageKey) -> QImage:
        from .template_guide import layout_guide

        image = self._guides.get(key)
        if image is not None:
            return image
        plan = None
        with contextlib.suppress(Exception):
            plan = self._plan(template_id, key)
        if plan is None:
            plan = layout_templates.plan_template(
                template_id,
                TemplateInput(
                    1000,
                    700,
                    False,
                    page_size=key[1],
                    orientation=key[2],
                    title=tr("Title"),
                    layer_names=("A", "B", "C"),
                    credits=("Source",),
                    notes=tr("Notes"),
                    fill_frame=key[5] == "fill",
                ),
            )
        image = layout_guide(plan, key[3])
        self._guides.put(key, image)
        return image

    def _plan(self, template_id: str, key: _PageKey) -> TemplatePlan | None:
        """Share the cheap plan between card cues and its layout guide."""
        # A plan does not depend on the pixels a page is drawn with.
        plan_key = _plan_key(key)
        plan = self._plans.get(plan_key)
        if plan is not None:
            self._plans.move_to_end(plan_key)
            return plan
        if self._planner is None:
            return None
        shared_key = (self._canvas_key, plan_key)
        plan = _SHARED_PLANS.get(shared_key) if self._canvas_key else None
        if plan is None:
            with preview_framing(key[5]):
                plan = self._planner(template_id, key[1], key[2])
            if self._canvas_key:
                _SHARED_PLANS[shared_key] = plan
                while len(_SHARED_PLANS) > _SHARED_PLAN_LIMIT:
                    _SHARED_PLANS.popitem(last=False)
        self._plans[plan_key] = plan
        while len(self._plans) > _SHARED_CACHE_LIMIT:
            self._plans.popitem(last=False)
        return plan

    def _is_planned(self, template_id: str) -> bool:
        """Whether the card's cues are known for the current page settings.
        Without a planner there is nothing to plan."""
        return self._planner is None or _plan_key(self._key(template_id)) in self._cues

    def _plan_order(self) -> list[str]:
        """The unplanned designs, most useful first: the chosen one, the
        cards in view, the ones a short scroll away, then the rest of the
        list."""
        current = self.template_list.currentItem()
        chosen = (
            str(current.data(Qt.ItemDataRole.UserRole))
            if current is not None and not current.isHidden()
            else None
        )
        shown = [t for t, item in self._items.items() if not item.isHidden()]
        order = ([chosen] if chosen else []) + self.visible_ids() + self.near_ids()
        order += shown
        seen: set[str] = set()
        wanted: list[str] = []
        for t in order:
            if t in self._templates and t not in seen:
                seen.add(t)
                if not self._is_planned(t):
                    wanted.append(t)
        return wanted

    def _plan_turn(self) -> None:
        """Plan cards for a short while, then let the window breathe: each
        card's cues and layout guide appear as it is planned."""
        if self._closed:
            return
        deadline = time.monotonic() + _PLAN_TURN_S
        planned = False
        while self._plan_queue:
            template_id = self._plan_queue.pop(0)
            if self._is_planned(template_id):
                continue
            self._update_cues([template_id])
            self._show_state(template_id)
            planned = True
            if time.monotonic() >= deadline:
                break
        if planned:
            self._relayout()
        if self._plan_queue:
            self._plan_timer.start()

    def _stop_drawing(self) -> None:
        """The picker is answered: obsolete callbacks are dropped at once
        (a late page finds no page in flight) and the page being drawn is
        handed back to QGIS to cancel."""
        self._closed = True
        self._timer.stop()
        self._plan_timer.stop()
        self._scroll_rest.stop()
        self._queue.clear()
        self._plan_queue.clear()
        self._cancel_inflight()

    def _update_cues(self, template_ids: Iterable[str]) -> None:
        """Check the cards' cues against the pages this project gets with the
        current page settings: planned, not drawn."""
        if self._planner is None:
            return
        for template_id in template_ids:
            key = self._key(template_id)
            cues = self._cues.get(_plan_key(key))
            if cues is None:
                template = self._templates[template_id]
                try:
                    plan = self._plan(template_id, key)
                except Exception:  # a page the planner cannot lay out
                    cues = declared_cues(template)
                else:
                    cues = (
                        plan_cues(template, plan)
                        if plan is not None
                        else declared_cues(template)
                    )
                self._cues[_plan_key(key)] = cues
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
        if self._closed:
            self._queue.clear()
            self._plan_queue.clear()
            return
        self._plan_queue = self._plan_order()
        current = self.template_list.currentItem()
        chosen = (
            str(current.data(Qt.ItemDataRole.UserRole))
            if current is not None and not current.isHidden()
            else None
        )
        if chosen not in self._templates:
            chosen = None
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
        # After the page's turn is queued: the first real page comes before
        # the other cards' guides.
        if self._plan_queue and not self._plan_timer.isActive():
            self._plan_timer.start()

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
        if self._closed or self._scroll_rest.isActive() or self._inflight is not None:
            return
        if render_slot_busy():
            # A cancelled page is still being wound down by QGIS: wait for
            # it instead of drawing two pages at once.
            self._timer.start(_SLOT_RECHECK_MS)
            return
        self._reprioritise()
        if not self._queue:
            return
        template_id = self._queue.pop(0)
        key = self._key(template_id)
        try:
            with preview_framing(key[5]):
                result = self._render(template_id, key[1], key[2], key[3])
        except Exception:  # a preview must never break the picker
            result = None
        if result is not None and not isinstance(result, QImage):
            # Drawn in the background: the next one waits for this one.
            self._inflight = (template_id, key, result)
            # A bound method of this dialog, not a closure: the render can
            # finish after the picker is gone, and Qt drops the connection then.
            result.finished.connect(self._on_pending_done)
            self._preview_deadline.start()
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
        else:
            self._failed.add(key)
        self._show_state(template_id)
        self._relayout()

    def _on_pending_done(self, image: QImage | None) -> None:
        if self._closed or self._inflight is None:
            return  # cancelled while it finished
        emitter = self.sender()
        if emitter is not None and emitter is not self._inflight[2]:
            return  # a page that was cancelled for this one
        template_id, key, _pending = self._inflight
        self._inflight = None
        self._preview_deadline.stop()
        self._store_page(template_id, key, image)
        self._reprioritise()

    def _card_timed_out(self) -> None:
        """The page being drawn took too long: that card fails, with a
        Retry, and the queue goes on."""
        if self._inflight is None:
            return
        template_id, key, _pending = self._inflight
        self._cancel_inflight()
        self._store_page(template_id, key, None)
        self._reprioritise()

    def _cancel_inflight(self) -> None:
        """Stop the page being drawn in the background, if any. QGIS winds
        it down without blocking this event; until it has, `render_slot_busy`
        holds the next page back."""
        self._preview_deadline.stop()
        if self._inflight is None:
            return
        _template_id, _key, pending = self._inflight
        self._inflight = None
        pending.cancel()

    def retry(self, template_id: str) -> None:
        """Draw `template_id`'s page again, next."""
        if self._closed:
            return
        self._failed.discard(self._key(template_id))
        self._show_state(template_id)
        self._reprioritise()
        if template_id in self._queue and self._queue[0] != template_id:
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
        self._place_controls(self._controls_arrangement())

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
        text_scale = max(1.0, self.fontMetrics().height() / _TEXT_HEIGHT)
        size, minimum = dialog_sizes(available.size(), text_scale)
        self._base_minimum_height = minimum.height()
        self._screen_cap_height = int(available.height() * _SCREEN_SHARE_H)
        self.setMinimumSize(minimum)
        self.resize(size)
        self._controls_arrangement_now = None
        self._place_controls(self._controls_arrangement())

    def _controls_arrangement(self) -> str:
        """`row` when the filter, the page settings and the framing fit on
        one line; `pair` when only the filter and the framing do (the page
        settings under them); else `stack`, one line each."""
        margins = self.layout().contentsMargins() if self.layout() else None
        room = self.width() - (margins.left() + margins.right() if margins else 0)
        gap = self._controls.horizontalSpacing()
        filter_w = self.filter_group.sizeHint().width()
        framing_w = self.framing_combo.sizeHint().width()
        if (
            filter_w + self.page_settings.sizeHint().width() + framing_w + 2 * gap
            <= room
        ):
            return "row"
        return "pair" if filter_w + framing_w + gap <= room else "stack"

    def _place_controls(self, arrangement: str) -> None:
        if arrangement == self._controls_arrangement_now:
            return
        self._controls_arrangement_now = arrangement
        for widget in (self.filter_group, self.page_settings, self.framing_combo):
            self._controls.removeWidget(widget)
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self._controls.addWidget(self.filter_group, 0, 0, left)
        # A third line is height the cards need: the dialog may not shrink
        # past it, as far as the screen allows.
        line = self.framing_combo.sizeHint().height() + self._controls.verticalSpacing()
        extra = line * (arrangement == "stack")
        self.setMinimumHeight(
            min(self._base_minimum_height + extra, self._screen_cap_height)
        )
        if arrangement == "row":
            self._controls.addWidget(self.page_settings, 0, 1, left)
            self._controls.addWidget(self.framing_combo, 0, 2, left)
        elif arrangement == "pair":
            self._controls.addWidget(self.framing_combo, 0, 1, 1, 2, left)
            self._controls.addWidget(self.page_settings, 1, 0, 1, 3, left)
        else:
            self._controls.addWidget(self.page_settings, 1, 0, 1, 3, left)
            self._controls.addWidget(self.framing_combo, 2, 0, 1, 3, left)

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
        drawn_at = self._render_pixels()
        self._thumb_width = _preview_box(card_w, height, False).width()
        if self._render_pixels() != drawn_at:
            # The pages to show are other ones (a different width): what is
            # drawn for this width, or its guide, takes the cards.
            for template_id in self.rows:
                self._show_state(template_id)
        turns = tuple(
            self.orientation == "portrait" or (self.orientation == "auto" and row.tall)
            for row in self.rows.values()
        )
        state = (card_w, height, columns, turns)
        if state == self._laid_out:
            return
        self._laid_out = state
        # The chosen card, in view before the cards change size, stays in
        # view after: a short list must not leave it scrolled off.
        current = view.currentItem()
        anchored = (
            current is not None
            and not current.isHidden()
            and view.visualItemRect(current).intersects(view.viewport().rect())
        )
        card_h = 1
        for row, tall in zip(self.rows.values(), turns):
            box = _preview_box(card_w, height, tall)
            text_w = card_w - box.width() - 2 * _ROW_PADDING - 16
            row.set_box(box)
            card_h = max(
                card_h,
                box.height() + 2 * _ROW_PADDING,
                row.text_height(text_w) + 2 * _ROW_PADDING,
            )
        hint = QSize(card_w, card_h)
        for item in self._items.values():
            item.setSizeHint(hint)
        self.request_item.setSizeHint(hint)
        view.doItemsLayout()
        if anchored and current is not None:
            view.scrollToItem(current, QAbstractItemView.ScrollHint.EnsureVisible)
        self._reprioritise()

    # ---- Helpers -------------------------------------------------------------

    def _on_template(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is self.request_item:
            # Qt can make a non-selectable item current through keyboard
            # navigation. The request action never replaces the map choice.
            previous = (
                _previous
                if isinstance(_previous, QListWidgetItem)
                and _previous is not self.request_item
                else self._items[self._default_template()]
            )
            self.template_list.setCurrentItem(previous)
            return
        chosen = current.data(Qt.ItemDataRole.UserRole) if current else None
        if self._inflight is not None and self._inflight[0] != chosen:
            self._cancel_inflight()
        for template_id, row in self.rows.items():
            row.set_chosen(template_id == chosen)
        template = self._templates.get(str(chosen)) if chosen else None
        name = tr(template.name) if template else ""
        self.selected_label.setText(tr("Selected: {name}").format(name=name))
        if self._ready and chosen and not self._is_planned(str(chosen)):
            # Chosen before its turn: plan it now, as if it always had been.
            self._update_cues([str(chosen)])
            self._show_state(str(chosen))
            self._relayout()
        self._reprioritise()

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
    when the user cancels. Accepted, the map framing is saved to
    `project`."""
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
