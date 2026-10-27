"""Statistic callouts on a layout: read, add, change and remove them after the
page is made.

A callout is the Signal design's key figure: a value shown large over a short
line saying what it counts. Each lives on the layout it is drawn on -- three
ordinary layout items (`rule-figure-N`, `figure-N`, `figure-N-label`) -- so
changing one cannot change any other layout, and it goes with the layout when
the cartograph is saved, exported or imported. The numbers are the user's
own; nothing here computes or supplies one.

Adding or removing a callout changes how much room the page has, so the page
is planned again with the new callouts and every item still where the design
first put it moves to its new place (`_follow_new_plan`). An item the user has
moved or resized by hand stays where they put it. Changing only the words of
a callout keeps the page as it is, so the lettering the user chose survives.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsLayout,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QPointF, QRectF
from qgis.PyQt.QtXml import QDomDocument

from ...core.canvas_cartograph import (
    FIGURE_VARIABLES,
    TemplateRecord,
    _build_once,
    _fit_legend,
    _project_dirty_state_kept,
    project_figure_rows,
    template_record,
)
from ...core.layout_templates import get_template
from ..i18n import tr
from .layout_items import item_edit, one_step

#: The most callouts a page carries: the design lays out three at most.
CALLOUT_LIMIT = 3
#: How long a callout's value and its line may be: longer would not fit its
#: block.
VALUE_MAX_LENGTH = 24
LABEL_MAX_LENGTH = 80

_ITEM_ID = re.compile(r"^(?:rule-)?figure-(\d+)(?:-label)?$")
#: Positions and sizes closer than this, in millimetres, are the same place.
_SAME_PLACE_MM = 0.05

Figure = tuple[str, str]


@dataclass(frozen=True)
class Callout:
    """One callout as the page shows it."""

    index: int
    value: str
    label: str


def _layout_items(layout: QgsLayout) -> list[QgsLayoutItem]:
    """The layout's items, without the scene's other graphics (the pages)."""
    return [item for item in layout.items() if isinstance(item, QgsLayoutItem)]


def callout_index(item: QgsLayoutItem) -> int | None:
    """Which callout `item` belongs to, from its id; `None` for any other
    item."""
    match = _ITEM_ID.match(item.id())
    return int(match.group(1)) if match else None


def layout_callouts(layout: QgsLayout) -> tuple[Callout, ...]:
    """The callouts drawn on `layout`, in order: a value with nothing in it
    is not a callout."""
    found: dict[int, list[str]] = {}
    for item in _layout_items(layout):
        index = callout_index(item)
        if index is None or not isinstance(item, QgsLayoutItemLabel):
            continue
        slot = found.setdefault(index, ["", ""])
        slot[1 if item.id().endswith("-label") else 0] = item.text().strip()
    return tuple(
        Callout(index, value, label)
        for index, (value, label) in sorted(found.items())
        if value
    )


def supports_callouts(layout: QgsLayout) -> bool:
    """Whether `layout` was made from a design that has callouts, or already
    carries some: where the Edit tab offers them."""
    if layout_callouts(layout):
        return True
    return _design_takes_callouts(layout)


def _design_takes_callouts(layout: QgsLayout) -> bool:
    record = template_record(layout)
    if record is None:
        return False
    try:
        return get_template(record.template_id).takes_figures
    except ValueError:
        return False


def can_add_callout(layout: QgsLayout) -> bool:
    """Whether another callout can be added: the page is the design's own
    (so it can be planned again) and has room for one more."""
    return (
        len(layout_callouts(layout)) < CALLOUT_LIMIT
        and _design_takes_callouts(layout)
        and _replannable(layout)
    )


def add_callout(layout: QgsLayout, value: str, label: str = "") -> bool:
    """Add a callout after the others, as one undo step. Returns whether one
    was added: not for a blank value, a fourth callout, one the page already
    has, or a page that cannot be planned again."""
    value, label = value.strip(), label.strip()
    current = layout_callouts(layout)
    if (
        not value
        or (value, label) in [(c.value, c.label) for c in current]
        or not can_add_callout(layout)
    ):
        return False
    figures = [(c.value, c.label) for c in current] + [(value, label)]
    return _reflow(layout, figures, undo_text=tr("Add callout"), keep=None)


def change_callout(layout: QgsLayout, index: int, value: str, label: str) -> bool:
    """Change callout `index`'s words, as one undo step. The lettering the
    user gave it stays; a line added or cleared makes the page room for it
    again. Returns whether anything changed."""
    value, label = value.strip(), label.strip()
    current = layout_callouts(layout)
    target = next((c for c in current if c.index == index), None)
    if target is None or not value or (value, label) == (target.value, target.label):
        return False
    if any((c.value, c.label) == (value, label) for c in current if c.index != index):
        return False
    if bool(label) == bool(target.label):
        return _set_words(layout, index, value, label)
    figures = [
        (value, label) if c.index == index else (c.value, c.label) for c in current
    ]
    if not _replannable(layout):
        return _set_words(layout, index, value, label)
    return _reflow(layout, figures, undo_text=tr("Change callout"), keep=None)


def remove_callout(layout: QgsLayout, index: int) -> bool:
    """Take callout `index` off the page and close up the others, as one undo
    step. Returns whether one was removed."""
    current = layout_callouts(layout)
    if not any(c.index == index for c in current):
        return False
    figures = [(c.value, c.label) for c in current if c.index != index]
    if not _replannable(layout):
        return _remove_items(layout, index, renumber=True)
    return _reflow(layout, figures, undo_text=tr("Remove callout"), keep=index)


# -- the words ----------------------------------------------------------------


def _set_words(layout: QgsLayout, index: int, value: str, label: str) -> bool:
    changed = False
    with one_step(layout, tr("Change callout")):
        for suffix, text in (("", value), ("-label", label)):
            item = layout.itemById(f"figure-{index}{suffix}")
            if not isinstance(item, QgsLayoutItemLabel) or item.text() == text:
                continue
            with item_edit(layout, item, tr("Change callout")):
                item.setText(text)
            item.update()
            changed = True
    layout.refresh()
    return changed


# -- planning the page again --------------------------------------------------


def _replannable(layout: QgsLayout) -> bool:
    """Whether the layout is still the page its design planned, in the sheet
    it was planned for: otherwise no planned place means anything."""
    record = template_record(layout)
    if record is None or layout.project() is None:
        return False
    if layout.units() != QgsUnitTypes.LayoutMillimeters:
        return False
    page = layout.pageCollection().page(0)
    if page is None:
        return False
    portrait = page.pageSize().width() <= page.pageSize().height()
    return portrait == (record.orientation == "portrait")


def _plan_page(layout: QgsLayout, record: TemplateRecord, figures: Sequence[Figure]):
    """The page `record`'s design makes with `figures`, built outside the
    project and kept only for its items."""
    project = layout.project()
    layers = [
        layer
        for layer_id in record.layer_ids
        if (layer := project.mapLayer(layer_id)) is not None
    ]
    crs = QgsCoordinateReferenceSystem.fromWkt(record.crs_wkt)
    extent = QgsRectangle(*record.extent)
    with _project_dirty_state_kept(project):
        return _build_once(
            extent,
            crs,
            layers,
            project,
            rotation=record.rotation,
            page_size=record.page_size,
            orientation=record.orientation,
            template_id=record.template_id,
            placeholders=record.placeholders,
            add_to_project=False,
            figures=figures,
            text_sizes=None,
        )


def _reflow(
    layout: QgsLayout,
    figures: Sequence[Figure],
    *,
    undo_text: str,
    keep: int | None,
) -> bool:
    """Show `figures` as the layout's callouts and move what they push, as
    one undo step. `keep` is a callout that is being removed."""
    record = template_record(layout)
    if record is None:
        return False
    old = layout_callouts(layout)
    before = _plan_page(layout, record, [(c.value, c.label) for c in old])
    after = _plan_page(layout, record, list(figures))
    with one_step(layout, undo_text):
        if keep is not None:
            _remove_items(layout, keep, renumber=True)
        _renumber(layout)
        _follow_new_plan(layout, before[2].items_by_id, after[2].items_by_id)
        _fit_legends(layout, after[0].legend_min_pt)
    layout.refresh()
    return True


def _renumber(layout: QgsLayout) -> None:
    """Callout ids run 0, 1, 2 ... in order, whatever has been removed."""
    indexes = sorted(
        {i for item in _layout_items(layout) if (i := callout_index(item)) is not None}
    )
    for new, old in enumerate(indexes):
        if new == old:
            continue
        for item in list(_layout_items(layout)):
            if callout_index(item) == old:
                _rename(layout, item, item.id().replace(f"-{old}", f"-{new}", 1))


def _remove_items(layout: QgsLayout, index: int, *, renumber: bool) -> bool:
    gone = [item for item in _layout_items(layout) if callout_index(item) == index]
    if not gone:
        return False
    with one_step(layout, tr("Remove callout")):
        for item in gone:
            layout.removeLayoutItem(item)
        if renumber:
            _renumber(layout)
    layout.refresh()
    return True


def _rename(layout: QgsLayout, item: QgsLayoutItem, new_id: str) -> None:
    layout.undoStack().beginCommand(item, tr("Change callout"))
    item.setId(new_id)
    layout.undoStack().endCommand()


def _rect(item: QgsLayoutItem) -> QRectF:
    return QRectF(item.pos(), item.rect().size())


def _same_place(a: QRectF, b: QRectF, *, size: bool) -> bool:
    close = (
        abs(a.x() - b.x()) <= _SAME_PLACE_MM and abs(a.y() - b.y()) <= _SAME_PLACE_MM
    )
    if not size:
        return close
    return (
        close
        and abs(a.width() - b.width()) <= _SAME_PLACE_MM
        and abs(a.height() - b.height()) <= _SAME_PLACE_MM
    )


def _sized_by_content(item: QgsLayoutItem) -> bool:
    """A scale bar draws itself at the size it needs; only its corner is
    the design's to place."""
    return isinstance(item, QgsLayoutItemScaleBar)


def _follow_new_plan(
    layout: QgsLayout,
    before: dict[str, QgsLayoutItem],
    after: dict[str, QgsLayoutItem],
) -> None:
    """Make the layout's items match `after`, the page planned with the new
    callouts. An item goes to its new place only if it is still where
    `before`, the page planned with the old ones, put it: what the user has
    moved is left alone. The callouts' own items always follow the plan, and
    are added or dropped as it has them."""
    for item_id, wanted in after.items():
        mine = layout.itemById(item_id)
        is_callout = callout_index(wanted) is not None
        if mine is None:
            if is_callout:
                _copy_item(layout, wanted)
            continue
        sized = not _sized_by_content(mine)
        if is_callout:
            _set_text(layout, mine, wanted)
        else:
            planned_then = before.get(item_id)
            if planned_then is None or not _same_place(
                _rect(mine), _rect(planned_then), size=sized
            ):
                continue
        if not _same_place(_rect(mine), _rect(wanted), size=sized):
            _place(layout, mine, _rect(wanted), sized)
    for item in list(_layout_items(layout)):
        if callout_index(item) is not None and item.id() not in after:
            layout.removeLayoutItem(item)


def _set_text(layout: QgsLayout, mine: QgsLayoutItem, wanted: QgsLayoutItem) -> None:
    if (
        isinstance(mine, QgsLayoutItemLabel)
        and isinstance(wanted, QgsLayoutItemLabel)
        and mine.text() != wanted.text()
    ):
        with item_edit(layout, mine, tr("Change callout")):
            mine.setText(wanted.text())


def _place(layout: QgsLayout, item: QgsLayoutItem, rect: QRectF, sized: bool) -> None:
    layout.undoStack().beginCommand(item, tr("Change callout"))
    try:
        if isinstance(item, QgsLayoutItemMap):
            _place_map(item, rect)
        elif sized:
            item.attemptSetSceneRect(rect)
        else:
            item.attemptMove(_layout_point(item, rect.topLeft()))
    finally:
        layout.undoStack().endCommand()
    item.update()


def _layout_point(item: QgsLayoutItem, point: QPointF):
    return QgsLayoutPoint(point.x(), point.y(), item.positionWithUnits().units())


def _place_map(item: QgsLayoutItemMap, rect: QRectF) -> None:
    """Move and resize a map frame, showing the same scale and the same
    centre: more or less of the ground, never a different one."""
    before = item.extent()
    old = item.rect()
    item.attemptSetSceneRect(rect)
    if old.width() <= 0 or old.height() <= 0:
        return
    half_w = before.width() * rect.width() / old.width() / 2
    half_h = before.height() * rect.height() / old.height() / 2
    centre = before.center()
    item.zoomToExtent(
        QgsRectangle(
            centre.x() - half_w,
            centre.y() - half_h,
            centre.x() + half_w,
            centre.y() + half_h,
        )
    )


def _copy_item(layout: QgsLayout, source: QgsLayoutItem) -> None:
    """A callout's label or rule from the planned page, added to `layout`
    as it is there."""
    document = QDomDocument()
    holder = document.createElement("copy")
    document.appendChild(holder)
    context = QgsReadWriteContext()
    if not source.writeXml(holder, document, context):
        return
    element = holder.firstChildElement()
    made: QgsLayoutItem
    if isinstance(source, QgsLayoutItemLabel):
        made = QgsLayoutItemLabel(layout)
    elif isinstance(source, QgsLayoutItemShape):
        made = QgsLayoutItemShape(layout)
    else:
        return
    if not made.readXml(element, document, context):
        return
    layout.addLayoutItem(made)
    made.setId(source.id())
    made.update()


def _fit_legends(layout: QgsLayout, minimum_pt: float) -> None:
    for item in _layout_items(layout):
        if isinstance(item, QgsLayoutItemLegend):
            _fit_legend(item, minimum_pt)


# -- the project's older, shared figures --------------------------------------


def migrate_project_figures(project: QgsProject) -> None:
    """Older versions kept the key figures in the project's variables
    (`nika_figure_1` ...), shared by every layout. A layout made from them
    already drew them as its own items, so what it shows stays as it is; the
    variables that one of the project's layouts shows are dropped here, so
    that they cannot reach any other layout. Any not shown anywhere stay for
    the next Signal page to take (`canvas_cartograph.cartograph_from_view`)."""
    legacy = project_figure_rows(project)
    if not any(value for value, _label in legacy):
        return
    shown = {
        (callout.value, callout.label)
        for layout in project.layoutManager().layouts()
        for callout in layout_callouts(layout)
    }
    variables = dict(project.customVariables())
    changed = False
    for name, (value, label) in zip(FIGURE_VARIABLES, legacy, strict=True):
        if value and (value, label) in shown and name in variables:
            del variables[name]
            changed = True
    if changed:
        project.setCustomVariables(variables)
