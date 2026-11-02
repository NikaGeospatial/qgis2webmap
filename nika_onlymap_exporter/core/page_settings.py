"""Page settings (Title, Attribution, Theme) as edits to the QGIS layout.

They used to be web-page-only fields. Now each lives in the layout, so the
embedded preview, the PDF and the web page (which is read from the layout) all
show it, and Undo takes it back like any other edit:

* **Title.** The layout's title item - a label the plugin built as the title
  (`TEXT_KIND_PROPERTY`) or one a template named "title" - when its text is
  plain, the field edits that text. When the item shows an expression
  (`[% @project_title %]` in every template) the expression is never flattened:
  the field sets the layout's own `@project_title` variable, which the
  expression reads, and blank means the project's title again. Another
  expression is left alone and said so (`title_state(...).dynamic`).
* **Attribution.** The same, for the attribution item. A layout with none has
  no text to edit: the field then belongs to the web page only.
* **Theme.** The page, text, legend, scale bar, north arrow, frames and rules
  take the theme's colours (`core/page_theme.py`). The colours the layout had
  are kept in the layout, so `Default` puts them back; a colour the user
  changed since is left as they changed it. Map symbols and rasters are never
  touched.

Changes go through the layout's undo stack. Pure editing logic, no widgets.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import re
import traceback
from dataclasses import dataclass
from typing import Any

from qgis.core import (
    QgsExpressionContextUtils,
    QgsFillSymbol,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemPolygon,
    QgsLayoutItemPolyline,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLegendStyle,
    QgsSimpleFillSymbolLayer,
)
from qgis.PyQt import sip
from qgis.PyQt.QtGui import QColor

from ..qt_compat import QUndoCommand
from .layout_reader import TEXT_KIND_PROPERTY, _is_north_arrow
from .page_style import parse_color
from .page_theme import PageTheme, readable, theme_colours

TITLE_VARIABLE = "project_title"
#: The theme the layout wears now (a custom property on the layout).
THEME_PROPERTY = "qgis2webmap/page_theme"
#: What the themed items looked like before, as JSON, so `default` restores it.
ORIGIN_PROPERTY = "qgis2webmap/page_theme_origin"

#: Typing in a field is one undo step per burst, as in the layout designer.
_TEXT_BURST_ID = 0x51325701
_VARIABLE_BURST_ID = 0x51325702

_PROJECT_TITLE_EXPRESSION = re.compile(r"\s*\[%\s*@project_title\s*%\]\s*")

_LEGEND_STYLES = (
    QgsLegendStyle.Style.Title,
    QgsLegendStyle.Style.Group,
    QgsLegendStyle.Style.Subgroup,
    QgsLegendStyle.Style.SymbolLabel,
)

Style = dict[str, Any]


# ---------------------------------------------------------------------------
# Title and attribution
# ---------------------------------------------------------------------------


def _text_item(layout: Any, kind: str) -> QgsLayoutItemLabel | None:
    """The label that is positively the `kind` item: marked so by the plugin
    when it built the layout, or named exactly that by a template. Never
    guessed from a word in an imported label's name."""
    found: QgsLayoutItemLabel | None = None
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemLabel):
            continue
        if item.customProperty(TEXT_KIND_PROPERTY) == kind:
            return item
        if found is None and item.id() == kind:
            found = item
    return found


def title_item(layout: Any) -> QgsLayoutItemLabel | None:
    return _text_item(layout, "title")


def attribution_item(layout: Any) -> QgsLayoutItemLabel | None:
    return _text_item(layout, "attribution")


@dataclass(frozen=True)
class FieldState:
    """What a Page settings text field shows and edits.

    `mode` is "item" (the field is that item's text), "variable" (the field
    sets `@project_title`, which the title item reads) or "none" (the layout
    has no such item: the field is for the web page only). `dynamic` is True
    when the item holds an expression this field cannot replace.
    """

    value: str
    mode: str
    dynamic: bool = False


def _is_dynamic(text: str) -> bool:
    return "[%" in text


def title_state(layout: Any) -> FieldState:
    item = title_item(layout)
    override = _title_override(layout)
    if item is None:
        return FieldState(override, "variable")
    text = item.text()
    if not _is_dynamic(text):
        return FieldState(text, "item")
    return FieldState(
        override, "variable", dynamic=_PROJECT_TITLE_EXPRESSION.fullmatch(text) is None
    )


def attribution_state(layout: Any) -> FieldState:
    item = attribution_item(layout)
    if item is None:
        return FieldState("", "none")
    text = item.text()
    if _is_dynamic(text):
        return FieldState("", "item", dynamic=True)
    return FieldState(text, "item")


def attribution_in_layout(layout: Any) -> bool:
    """Whether the page attribution is drawn by a plain text item of the
    layout (so every output carries it)."""
    state = attribution_state(layout)
    return state.mode == "item" and not state.dynamic


def _title_override(layout: Any) -> str:
    scope = QgsExpressionContextUtils.layoutScope(layout)
    if not scope.hasVariable(TITLE_VARIABLE):
        return ""
    return str(scope.variable(TITLE_VARIABLE) or "")


def _guarded(function: Any, *args: Any) -> None:
    """Run an undo or redo body; an exception inside a Qt virtual would abort
    QGIS, so it is reported instead."""
    try:
        function(*args)
    except Exception:
        traceback.print_exc()


class _VariableCommand(QUndoCommand):
    """A change of the layout's `@project_title` as one undo step; typing
    merges into one."""

    def __init__(self, layout: Any, before: str, after: str, text: str) -> None:
        super().__init__(text)
        self._layout = layout
        self._before = before
        self._after = after

    def id(self) -> int:
        return _VARIABLE_BURST_ID

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802 - Qt override
        if not isinstance(other, _VariableCommand) or other._layout is not self._layout:
            return False
        self._after = other._after
        return True

    def redo(self) -> None:
        _guarded(_set_title_override, self._layout, self._after)

    def undo(self) -> None:
        _guarded(_set_title_override, self._layout, self._before)


def _set_title_override(layout: Any, text: str) -> None:
    if sip.isdeleted(layout):
        return
    if text:
        QgsExpressionContextUtils.setLayoutVariable(layout, TITLE_VARIABLE, text)
    else:
        names = list(layout.customProperty("variableNames") or [])
        values = list(layout.customProperty("variableValues") or [])
        kept = {
            name: value
            for name, value in zip(names, values, strict=False)
            if name != TITLE_VARIABLE
        }
        QgsExpressionContextUtils.setLayoutVariables(layout, kept)
    for item in layout.items():
        if isinstance(item, QgsLayoutItem) and not isinstance(item, QgsLayoutItemPage):
            if isinstance(item, QgsLayoutItemLabel):
                item.refresh()
            item.update()


def _edit_label(layout: Any, item: QgsLayoutItemLabel, text: str, label: str) -> bool:
    if item.text() == text:
        return False
    layout.undoStack().beginCommand(item, label, _TEXT_BURST_ID)
    try:
        item.setText(text)
    finally:
        layout.undoStack().endCommand()
    item.refresh()
    item.update()
    return True


def set_title(layout: Any, text: str, label: str) -> bool:
    """Make the page title `text` ("" for the project's own). True if anything
    changed. A plain title item takes the text; otherwise `@project_title`."""
    state = title_state(layout)
    if state.mode == "item":
        item = title_item(layout)
        return item is not None and _edit_label(layout, item, text, label)
    if state.value == text:
        return False
    layout.undoStack().stack().push(_VariableCommand(layout, state.value, text, label))
    return True


def set_title_quietly(layout: Any, text: str) -> None:
    """The same as `set_title` without an undo step: for a layout saved before
    the title lived in it."""
    if title_state(layout).mode == "item":
        item = title_item(layout)
        if item is not None:
            item.setText(text)
        return
    _set_title_override(layout, text)


def set_attribution(layout: Any, text: str, label: str) -> bool:
    """Make the page attribution `text`. False (and nothing changed) when the
    layout has no plain attribution item to carry it."""
    state = attribution_state(layout)
    item = attribution_item(layout)
    if item is None or state.dynamic:
        return False
    return _edit_label(layout, item, text, label)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


def _argb(color: QColor) -> str:
    return str(color.name(QColor.NameFormat.HexArgb))


def _css(argb: str) -> str:
    """`#aarrggbb` as the `#rrggbb` the contrast helpers read."""
    return "#" + argb[3:] if len(argb) == 9 else argb


def _with_alpha(rgb: str, like: str) -> str:
    """`rgb` (`#rrggbb`) at the alpha of `like` (`#aarrggbb`)."""
    return "#" + (like[1:3] if len(like) == 9 else "ff") + rgb[1:]


def _symbol_color(symbol: Any) -> str:
    return _argb(symbol.color())


def _first_fill_layer(symbol: Any) -> Any:
    layer = symbol.symbolLayer(0) if symbol.symbolLayerCount() else None
    return layer if isinstance(layer, QgsSimpleFillSymbolLayer) else None


def _themable(item: Any) -> bool:
    return isinstance(item, QgsLayoutItem) and not isinstance(item, QgsLayoutItemPage)


def _is_north(item: Any) -> bool:
    return isinstance(item, QgsLayoutItemPicture) and _is_north_arrow(item)


def _item_key(item: Any) -> str:
    return str(item.uuid())


def capture_styles(layout: Any) -> dict[str, Style]:
    """The colours the theme may change, for every page and furniture item."""
    styles: dict[str, Style] = {}
    for index, page in enumerate(layout.pageCollection().pages()):
        styles[f"page:{index}"] = {"fill": _symbol_color(page.pageStyleSymbol())}
    for item in layout.items():
        if not _themable(item):
            continue
        style = _capture_item(item)
        if style is not None:
            styles[_item_key(item)] = style
    return styles


def _capture_item(item: Any) -> Style | None:
    common: Style = {
        "bg": _argb(item.backgroundColor()),
        "bg_on": bool(item.hasBackground()),
        "frame": _argb(item.frameStrokeColor()),
        "frame_on": bool(item.frameEnabled()),
    }
    if isinstance(item, QgsLayoutItemLabel):
        common["kind"] = "label"
        common["text"] = _argb(item.textFormat().color())
    elif isinstance(item, QgsLayoutItemLegend):
        common["kind"] = "legend"
        common["styles"] = {
            str(int(style)): _argb(item.style(style).textFormat().color())
            for style in _LEGEND_STYLES
        }
    elif isinstance(item, QgsLayoutItemScaleBar):
        common["kind"] = "scalebar"
        common["text"] = _argb(item.textFormat().color())
        common["fill"] = _symbol_color(item.fillSymbol())
        common["fill2"] = _symbol_color(item.alternateFillSymbol())
        common["line"] = _symbol_color(item.lineSymbol())
    elif _is_north(item):
        common["kind"] = "north"
        common["fill"] = _argb(item.svgFillColor())
        common["stroke"] = _argb(item.svgStrokeColor())
    elif isinstance(item, QgsLayoutItemShape | QgsLayoutItemPolygon):
        symbol = item.symbol()
        common["kind"] = "shape"
        common["fill"] = _symbol_color(symbol)
        layer = _first_fill_layer(symbol)
        common["stroke"] = _argb(layer.strokeColor()) if layer is not None else ""
    elif isinstance(item, QgsLayoutItemPolyline):
        common["kind"] = "line"
        common["fill"] = _symbol_color(item.symbol())
    elif isinstance(item, QgsLayoutItemMap):
        common["kind"] = "map"
    else:
        return None
    return common


def _restore_item(item: Any, style: Style) -> None:
    item.setBackgroundColor(QColor(style["bg"]))
    item.setFrameStrokeColor(QColor(style["frame"]))
    kind = style.get("kind")
    if kind == "label":
        text_format = item.textFormat()
        text_format.setColor(QColor(style["text"]))
        item.setTextFormat(text_format)
    elif kind == "legend":
        for number, color in style["styles"].items():
            legend_style = QgsLegendStyle(item.style(QgsLegendStyle.Style(int(number))))
            text_format = legend_style.textFormat()
            text_format.setColor(QColor(color))
            legend_style.setTextFormat(text_format)
            item.setStyle(QgsLegendStyle.Style(int(number)), legend_style)
    elif kind == "scalebar":
        text_format = item.textFormat()
        text_format.setColor(QColor(style["text"]))
        item.setTextFormat(text_format)
        for getter, setter, key in (
            (item.fillSymbol, item.setFillSymbol, "fill"),
            (item.alternateFillSymbol, item.setAlternateFillSymbol, "fill2"),
            (item.lineSymbol, item.setLineSymbol, "line"),
        ):
            symbol = getter().clone()
            symbol.setColor(QColor(style[key]))
            setter(symbol)
    elif kind == "north":
        item.setSvgFillColor(QColor(style["fill"]))
        item.setSvgStrokeColor(QColor(style["stroke"]))
    elif kind == "shape":
        symbol = item.symbol().clone()
        symbol.setColor(QColor(style["fill"]))
        layer = _first_fill_layer(symbol)
        if layer is not None and style.get("stroke"):
            layer.setStrokeColor(QColor(style["stroke"]))
        item.setSymbol(symbol)
    elif kind == "line":
        symbol = item.symbol().clone()
        symbol.setColor(QColor(style["fill"]))
        item.setSymbol(symbol)


def _restore_page(page: Any, style: Style) -> None:
    symbol = page.pageStyleSymbol().clone()
    if not isinstance(symbol, QgsFillSymbol):
        return
    symbol.setColor(QColor(style["fill"]))
    page.setPageStyleSymbol(symbol)


def restore_styles(layout: Any, styles: dict[str, Style]) -> None:
    """Put `styles` back (items or pages that are gone are skipped) and have
    the preview repaint at once."""
    pages = layout.pageCollection().pages()
    for index, page in enumerate(pages):
        style = styles.get(f"page:{index}")
        if style is not None:
            _restore_page(page, style)
            page.update()
    for item in layout.items():
        if not _themable(item):
            continue
        style = styles.get(_item_key(item))
        if style is None:
            continue
        _restore_item(item, style)
        item.invalidateCache()
        item.update()


def has_theme_record(layout: Any) -> bool:
    """Whether the layout has ever been given a theme (even `default`)."""
    return layout.customProperty(THEME_PROPERTY, None) is not None


def adopt_theme(layout: Any, theme_id: str) -> None:
    """Record `theme_id` as the theme a layout already looks like - an
    imported cartograph whose items were drawn in it - without restyling:
    `default` then leaves it as it is."""
    layout.setCustomProperty(THEME_PROPERTY, theme_id)
    if theme_id != "default":
        _set_origin(layout, capture_styles(layout))


def current_theme(layout: Any) -> str:
    return str(layout.customProperty(THEME_PROPERTY, "default") or "default")


def _origin(layout: Any) -> dict[str, Style]:
    raw = layout.customProperty(ORIGIN_PROPERTY, "")
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _set_origin(layout: Any, origin: dict[str, Style]) -> None:
    if origin:
        layout.setCustomProperty(ORIGIN_PROPERTY, json.dumps(origin, sort_keys=True))
    else:
        layout.removeCustomProperty(ORIGIN_PROPERTY)


@dataclass(frozen=True)
class _Box:
    left: float
    top: float
    right: float
    bottom: float
    z: float


def _boxes(layout: Any) -> dict[str, _Box]:
    boxes: dict[str, _Box] = {}
    for item in layout.items():
        if _themable(item):
            rect = item.sceneBoundingRect()
            boxes[_item_key(item)] = _Box(
                rect.left(), rect.top(), rect.right(), rect.bottom(), item.zValue()
            )
    return boxes


def themed_styles(
    layout: Any, origin: dict[str, Style], theme: PageTheme
) -> dict[str, Style]:
    """What every item in `origin` looks like in `theme`.

    Pass one decides the surfaces (page, panels, plates); pass two the text
    and line art on them, each pulled to 4.5:1 of the surface that is really
    under it.
    """
    boxes = _boxes(layout)
    page_origin = [
        parse_color(_css(s["fill"])) for k, s in origin.items() if k.startswith("page:")
    ]
    old_page = page_origin[0] if page_origin and page_origin[0] else None
    result: dict[str, Style] = {}
    surfaces: dict[str, str] = {}
    for key, style in origin.items():
        if key.startswith("page:"):
            result[key] = {"fill": _with_alpha(theme.page, style["fill"])}
            continue
        new = dict(style)
        kind = style.get("kind")
        if style.get("bg_on"):
            new["bg"] = _with_alpha(theme.panel, style["bg"])
        if style.get("frame_on"):
            new["frame"] = _with_alpha(theme.rule, style["frame"])
        if kind == "shape":
            fill = parse_color(_css(style["fill"]))
            on_page = (
                fill is not None
                and old_page is not None
                and all(abs(a - b) < 0.02 for a, b in zip(fill[:3], old_page[:3]))
            )
            solid = fill is not None and fill[3] > 0.01
            if solid:
                paint = theme.page if on_page else theme.plate_fill
                new["fill"] = _with_alpha(paint, style["fill"])
                surfaces[key] = paint
            if style.get("stroke"):
                new["stroke"] = _with_alpha(theme.rule, style["stroke"])
        elif kind == "line":
            new["fill"] = _with_alpha(theme.rule, style["fill"])
        elif kind == "map" and style.get("bg_on"):
            surfaces[key] = theme.panel
        if style.get("bg_on") and kind in ("label", "legend", "scalebar"):
            surfaces[key] = theme.panel
        result[key] = new

    def surface_under(key: str) -> str:
        box = boxes.get(key)
        if box is None:
            return theme.page
        cx, cy = (box.left + box.right) / 2, (box.top + box.bottom) / 2
        best: tuple[float, str] | None = None
        for other, paint in surfaces.items():
            under = boxes.get(other)
            if other == key or under is None or under.z >= box.z:
                continue
            inside = under.left <= cx <= under.right and under.top <= cy <= under.bottom
            if inside and (best is None or under.z > best[0]):
                best = (under.z, paint)
        return best[1] if best else theme.page

    for key, style in origin.items():
        kind = style.get("kind")
        new = result[key]
        if kind in ("label", "legend", "scalebar", "north"):
            own = surfaces.get(key)
            surface = own if own else surface_under(key)
            on_panel = own is not None
            if kind == "label":
                role = _text_role(style, key, layout)
                base = theme.panel_ink if on_panel else getattr(theme, role)
                new["text"] = _with_alpha(readable(base, surface), style["text"])
            elif kind == "legend":
                ink = readable(theme.panel_ink if on_panel else theme.ink, surface)
                new["styles"] = {
                    n: _with_alpha(ink, c) for n, c in style["styles"].items()
                }
            elif kind == "scalebar":
                art = readable(theme.line_art, surface, 3.0)
                ink = readable(theme.panel_ink if on_panel else theme.ink, surface)
                new["text"] = _with_alpha(ink, style["text"])
                new["fill"] = _with_alpha(art, style["fill"])
                new["fill2"] = _with_alpha(surface, style["fill2"])
                new["line"] = _with_alpha(art, style["line"])
            else:
                art = readable(theme.line_art, surface, 3.0)
                new["fill"] = _with_alpha(art, style["fill"])
                new["stroke"] = _with_alpha(art, style["stroke"])
    return result


def _text_role(style: Style, key: str, layout: Any) -> str:
    """Which theme ink a label uses: the title's, the attribution's, or body."""
    for item in layout.items():
        if _themable(item) and _item_key(item) == key:
            kind = item.customProperty(TEXT_KIND_PROPERTY)
            if kind == "title" or item.id() == "title":
                return "title"
            if kind == "attribution" or item.id() == "attribution":
                return "muted"
    return "ink"


def surface_of(layout: Any, item: Any) -> str:
    """The colour (`#rrggbb`) an item's text sits on right now: its own
    background, else the topmost opaque panel or plate under it, else the page."""
    if item.hasBackground() and item.backgroundColor().alpha() > 250:
        return _css(_argb(item.backgroundColor()))
    rect = item.sceneBoundingRect()
    cx, cy = rect.center().x(), rect.center().y()
    best: tuple[float, str] | None = None
    for other in layout.items():
        if other is item or not _themable(other) or other.zValue() >= item.zValue():
            continue
        paint = None
        if isinstance(other, QgsLayoutItemShape):
            color = other.symbol().color()
            paint = color if color.alpha() > 250 else None
        elif other.hasBackground() and other.backgroundColor().alpha() > 250:
            paint = other.backgroundColor()
        if paint is None or not other.sceneBoundingRect().contains(cx, cy):
            continue
        if best is None or other.zValue() > best[0]:
            best = (other.zValue(), _css(_argb(paint)))
    if best:
        return best[1]
    page = layout.pageCollection().pages()
    if page:
        return _css(_symbol_color(page[0].pageStyleSymbol()))
    return "#ffffff"


@dataclass(frozen=True)
class _Snapshot:
    theme: str
    origin: str
    styles: dict[str, Style]


def _snapshot(layout: Any) -> _Snapshot:
    return _Snapshot(
        current_theme(layout),
        str(layout.customProperty(ORIGIN_PROPERTY, "") or ""),
        capture_styles(layout),
    )


def _restore(layout: Any, snap: _Snapshot) -> None:
    if sip.isdeleted(layout):
        return
    layout.setCustomProperty(THEME_PROPERTY, snap.theme)
    if snap.origin:
        layout.setCustomProperty(ORIGIN_PROPERTY, snap.origin)
    else:
        layout.removeCustomProperty(ORIGIN_PROPERTY)
    restore_styles(layout, snap.styles)


class _ThemeCommand(QUndoCommand):
    def __init__(
        self, layout: Any, before: _Snapshot, after: _Snapshot, text: str
    ) -> None:
        super().__init__(text)
        self._layout = layout
        self._before = before
        self._after = after
        self._applied = True  # the change is made before the command is pushed

    def redo(self) -> None:
        if self._applied:
            self._applied = False
            return
        _guarded(_restore, self._layout, self._after)

    def undo(self) -> None:
        _guarded(_restore, self._layout, self._before)


def apply_theme(layout: Any, theme_id: str, label: str) -> bool:
    """Dress the layout in `theme_id` as one undo step. True if anything
    changed."""
    before = _snapshot(layout)
    _apply(layout, theme_id)
    after = _snapshot(layout)
    if before == after:
        return False
    layout.undoStack().stack().push(_ThemeCommand(layout, before, after, label))
    return True


def apply_theme_quietly(layout: Any, theme_id: str) -> None:
    """The same without an undo step: bringing a layout saved before themes
    lived in the layout up to its recorded theme."""
    _apply(layout, theme_id)


def _apply(layout: Any, theme_id: str) -> None:
    theme = theme_colours(theme_id)
    now = current_theme(layout)
    current_styles = capture_styles(layout)
    origin = _origin(layout)
    if now == "default" or not origin:
        origin = current_styles
        now = "default"
    else:
        # Items added since keep the look they have as their own original.
        for key, style in current_styles.items():
            origin.setdefault(key, style)
    # Undo the theme being worn, except where the user changed a colour since.
    worn = theme_colours(now)
    if worn is not None:
        applied = themed_styles(layout, origin, worn)
        back: dict[str, Style] = {}
        for key, original in origin.items():
            present = current_styles.get(key)
            if present is None:
                continue
            back[key] = _merge_back(present, applied.get(key, {}), original)
        restore_styles(layout, back)
    if theme is None:
        layout.setCustomProperty(THEME_PROPERTY, "default")
        _set_origin(layout, {})
        return
    layout.setCustomProperty(THEME_PROPERTY, theme_id)
    _set_origin(layout, origin)
    restore_styles(layout, themed_styles(layout, origin, theme))


def _merge_back(present: Style, applied: Style, original: Style) -> Style:
    """`original`, except for each colour the user changed from `applied`."""
    merged: Style = {}
    for name, value in original.items():
        if name in ("kind", "bg_on", "frame_on"):
            merged[name] = value
        elif isinstance(value, dict):
            merged[name] = {
                sub: (
                    original_value
                    if present.get(name, {}).get(sub) == applied.get(name, {}).get(sub)
                    else present[name][sub]
                )
                for sub, original_value in value.items()
            }
        else:
            merged[name] = (
                value if present.get(name) == applied.get(name) else present[name]
            )
    return merged


__all__ = [
    "FieldState",
    "adopt_theme",
    "apply_theme",
    "apply_theme_quietly",
    "attribution_in_layout",
    "attribution_item",
    "attribution_state",
    "current_theme",
    "has_theme_record",
    "set_attribution",
    "set_title",
    "set_title_quietly",
    "surface_of",
    "title_item",
    "title_state",
]
