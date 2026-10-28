"""Live count: a number on a layout page that counts what a map frame shows.

The governing rule is "count what the map preview currently shows". The user
chooses WHAT to count (a layer, optionally one field's value); WHERE and WHICH
FEATURES come from the map frame the counter follows:

- the ground the frame displays (its final, aspect-adjusted extent, and its
  rotation: a feature in a corner of the enclosing rectangle that the rotated
  frame does not show is not counted);
- whether the frame draws the layer at all (its own layer set, map theme or
  the project's visible layers: `QgsLayoutItemMap.layersToRender`);
- the layer's filter (a provider subset string is applied by the provider);
- the renderer: a feature whose category, rule or class is switched off is not
  drawn, so it is not counted (`QgsFeatureRenderer.willRenderFeature`, in a
  render context made from the frame's own map settings);
- scale-dependent visibility (the layer's and a rule's).

A line or polygon counts when any part of it lies in the displayed ground,
once, however many symbols or rules draw it. It is a count of geographic
features: nothing is measured on pixels, so overlap, labels and symbols hiding
one another are not looked at.

A counter is three ordinary layout items: the number (a label whose text is
computed here, never typed), the caption under it (a label) and an accent rule.
The number carries the configuration in custom properties (stable ids: the map
item's uuid, the layer's id, the field's name); the caption and the rule point
back at it. Nothing about a counter depends on a layout template.

What the number says is always one of: the count (0 included), "—" (not set up,
or what it counts is gone or invalid: the reason is given by `CountOutcome`),
or "n/a" (the count could not be calculated). A number from an earlier state is
never kept once its source has become invalid.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Generator, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsExpressionContextUtils,
    QgsFeatureRenderer,
    QgsFeatureRequest,
    QgsGeometry,
    QgsLayout,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsMapLayerStyle,
    QgsProject,
    QgsReadWriteContext,
    QgsRenderContext,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QMetaType, QSizeF
from qgis.PyQt.QtXml import QDomDocument

from ..ui.i18n import tr

#: On the number label: marks it as a Live count.
MARKER_PROPERTY = "nika/live-count"
#: On a counter's caption and rule: the uuid of the number they belong to.
PART_OF_PROPERTY = "nika/live-count-of"
_MAP = "nika/live-count/map"
_LAYER = "nika/live-count/layer"
_LAYER_NAME = "nika/live-count/layer-name"
_MODE = "nika/live-count/mode"
_FIELD = "nika/live-count/field"
_VALUE = "nika/live-count/value"
_VALUE_IS_NULL = "nika/live-count/value-null"

MODE_ALL = "all"
MODE_VALUE = "value"

#: What the number reads when there is no count to show.
NO_COUNT_TEXT = "\N{EM DASH}"
UNAVAILABLE_TEXT = "n/a"

#: How many features are looked at between two yields of `Prepared.steps`.
BATCH = 250
#: A layer's own scale limit is compared with this margin, as QGIS does.
_SCALE_PRECISION = 0.9999999999


class CountState(Enum):
    OK = "ok"
    #: Not set up yet: the number reads "—" and the inspector says what to do.
    UNCONFIGURED = "unconfigured"
    #: Set up, but what it counts is gone or wrong: "—" and the reason.
    INVALID = "invalid"
    #: The calculation failed: "n/a", never a made-up number.
    UNAVAILABLE = "unavailable"
    #: A calculation is under way (the service shows "…" if it takes long).
    PENDING = "pending"


@dataclass(frozen=True)
class CountOutcome:
    state: CountState
    count: int | None = None
    #: Why, for the inspector, when the state is not OK.
    message: str = ""

    @property
    def text(self) -> str:
        """What the counter's number reads."""
        if self.state is CountState.OK and self.count is not None:
            return str(self.count)
        if self.state is CountState.UNAVAILABLE:
            return UNAVAILABLE_TEXT
        if self.state is CountState.PENDING:
            return "\N{HORIZONTAL ELLIPSIS}"
        return NO_COUNT_TEXT


@dataclass(frozen=True)
class CountSpec:
    """What a counter counts, as it is saved with the layout."""

    map_uuid: str = ""
    layer_id: str = ""
    layer_name: str = ""
    mode: str = MODE_ALL
    field: str = ""
    #: The value to match, as typed; `None` until one is chosen.
    value: str | None = None
    #: Match features whose field has no value at all (NULL), which is not the
    #: same as an empty text or the text "NULL".
    value_is_null: bool = False

    @property
    def has_value(self) -> bool:
        return self.value_is_null or self.value is not None


# -- finding a counter's items ------------------------------------------------


def _items(layout: QgsLayout) -> list[QgsLayoutItem]:
    """The layout's items, without the scene's other graphics (the pages)."""
    return [item for item in layout.items() if isinstance(item, QgsLayoutItem)]


def is_count_number(item: object) -> bool:
    return (
        isinstance(item, QgsLayoutItemLabel)
        and not sip.isdeleted(item)
        and bool(item.customProperty(MARKER_PROPERTY))
    )


def is_count_part(item: object) -> bool:
    """A counter's number or caption (the lettering of a Live count)."""
    if not isinstance(item, QgsLayoutItemLabel) or sip.isdeleted(item):
        return False
    return bool(
        item.customProperty(MARKER_PROPERTY) or item.customProperty(PART_OF_PROPERTY)
    )


def live_counts(layout: QgsLayout) -> list[QgsLayoutItemLabel]:
    """The layout's counters (their number labels), in a steady order."""
    found = [item for item in _items(layout) if is_count_number(item)]
    return sorted(found, key=lambda item: (item.id(), item.uuid()))


def owner_of(layout: QgsLayout, item: QgsLayoutItem) -> QgsLayoutItemLabel | None:
    """The number label of the counter `item` belongs to."""
    if is_count_number(item):
        return item  # type: ignore[return-value]
    uuid = item.customProperty(PART_OF_PROPERTY)
    if not uuid:
        return None
    found = layout.itemByUuid(str(uuid))
    return found if is_count_number(found) else None  # type: ignore[return-value]


def parts_of(layout: QgsLayout, number: QgsLayoutItem) -> list[QgsLayoutItem]:
    """The caption and rule that belong to the counter `number`."""
    uuid = number.uuid()
    return [
        item
        for item in _items(layout)
        if item is not number and item.customProperty(PART_OF_PROPERTY) == uuid
    ]


def caption_of(layout: QgsLayout, number: QgsLayoutItem) -> QgsLayoutItemLabel | None:
    for item in parts_of(layout, number):
        if isinstance(item, QgsLayoutItemLabel):
            return item
    return None


def with_count_parts(
    layout: QgsLayout, items: Iterable[QgsLayoutItem]
) -> list[QgsLayoutItem]:
    """`items` plus, for each counter's number among them, its caption and
    rule: taking a counter off the page takes all of it."""
    wanted = list(items)
    seen = {item.uuid() for item in wanted}
    for item in list(wanted):
        if is_count_number(item):
            for part in parts_of(layout, item):
                if part.uuid() not in seen:
                    seen.add(part.uuid())
                    wanted.append(part)
    return wanted


# -- the saved configuration --------------------------------------------------


def read_spec(item: QgsLayoutItem) -> CountSpec:
    value = item.customProperty(_VALUE)
    return CountSpec(
        map_uuid=str(item.customProperty(_MAP, "") or ""),
        layer_id=str(item.customProperty(_LAYER, "") or ""),
        layer_name=str(item.customProperty(_LAYER_NAME, "") or ""),
        mode=MODE_VALUE if item.customProperty(_MODE) == MODE_VALUE else MODE_ALL,
        field=str(item.customProperty(_FIELD, "") or ""),
        value=None if value is None else str(value),
        value_is_null=bool(item.customProperty(_VALUE_IS_NULL)),
    )


def write_spec(item: QgsLayoutItem, spec: CountSpec) -> None:
    """Save `spec` on the number label (the caller makes it one undo step)."""
    item.setCustomProperty(MARKER_PROPERTY, True)
    item.setCustomProperty(_MAP, spec.map_uuid)
    item.setCustomProperty(_LAYER, spec.layer_id)
    item.setCustomProperty(_LAYER_NAME, spec.layer_name)
    item.setCustomProperty(_MODE, spec.mode)
    item.setCustomProperty(_FIELD, spec.field)
    if spec.value is None:
        item.removeCustomProperty(_VALUE)
    else:
        item.setCustomProperty(_VALUE, spec.value)
    item.setCustomProperty(_VALUE_IS_NULL, spec.value_is_null)


# -- the layers a counter can count -------------------------------------------


def is_countable(layer: object) -> bool:
    """A valid vector layer with geometry: the only thing a counter counts."""
    return (
        isinstance(layer, QgsVectorLayer)
        and layer.isValid()
        and layer.geometryType() != QgsWkbTypes.GeometryType.NullGeometry
    )


def countable_layers(project: QgsProject) -> list[QgsVectorLayer]:
    """The project's point, line and polygon layers, in the Layers panel's
    order."""
    ordered = [
        layer for layer in project.layerTreeRoot().layerOrder() if is_countable(layer)
    ]
    ids = {layer.id() for layer in ordered}
    extra = [
        layer
        for layer in project.mapLayers().values()
        if is_countable(layer) and layer.id() not in ids
    ]
    return ordered + sorted(extra, key=lambda layer: layer.name().lower())


def map_draws(map_item: QgsLayoutItemMap, layer_id: str) -> bool:
    """Whether the frame draws the layer: in its layer set, its theme or the
    project's visible layers, whichever decides what it shows."""
    return any(layer.id() == layer_id for layer in map_item.layersToRender())


# -- one calculation ----------------------------------------------------------


def counted(count: int) -> CountOutcome:
    return CountOutcome(CountState.OK, count)


def _no(state: CountState, message: str) -> CountOutcome:
    return CountOutcome(state, None, message)


def _is_null(value: object) -> bool:
    if value is None:
        return True
    is_null = getattr(value, "isNull", None)
    return bool(is_null()) if callable(is_null) else False


def field_kind(layer: QgsVectorLayer, name: str) -> str | None:
    """ "number", "bool", "date" or "text" for the field, `None` if missing."""
    index = layer.fields().lookupField(name)
    if index < 0:
        return None
    field = layer.fields().at(index)
    type_name = field.typeName().lower()
    if type_name.startswith("bool") or field.type() == QMetaType.Type.Bool:
        return "bool"
    if field.isNumeric():
        return "number"
    if field.type() in (QMetaType.Type.QDate, QMetaType.Type.QDateTime):
        return "date"
    return "text"


def parse_number(text: str) -> float | None:
    cleaned = text.strip()
    try:
        return float(cleaned)
    except ValueError:
        pass
    try:  # the user's own decimal separator
        from qgis.PyQt.QtCore import QLocale

        number, ok = QLocale().toDouble(cleaned)
        return number if ok else None
    except Exception:
        return None


def _parse_bool(text: str) -> bool | None:
    word = text.strip().lower()
    if word in ("true", "yes", "1", "t", "y"):
        return True
    if word in ("false", "no", "0", "f", "n"):
        return False
    return None


def _matcher(layer: QgsVectorLayer, spec: CountSpec):  # type: ignore[no-untyped-def]
    """A function `(feature) -> bool` for the field and value, or a
    `CountOutcome` saying why there is none."""
    if not spec.field:
        return _no(CountState.UNCONFIGURED, tr("Choose the field to match."))
    kind = field_kind(layer, spec.field)
    if kind is None:
        return _no(
            CountState.INVALID,
            tr("The layer “{layer}” has no field “{field}” any more.").format(
                layer=layer.name(), field=spec.field
            ),
        )
    field = spec.field
    if spec.value_is_null:
        return lambda feature: _is_null(feature[field])
    if spec.value is None:
        return _no(CountState.UNCONFIGURED, tr("Choose the value to match."))
    text = spec.value
    if kind == "number":
        number = parse_number(text)
        if number is None:
            return _no(
                CountState.INVALID,
                tr(
                    "“{value}” is not a number, and “{field}” is a numeric field."
                ).format(value=text, field=field),
            )
        return lambda feature: (
            not _is_null(feature[field]) and _same_number(feature[field], number)
        )
    if kind == "bool":
        wanted = _parse_bool(text)
        if wanted is None:
            return _no(
                CountState.INVALID,
                tr(
                    "“{value}” is not Yes or No, and “{field}” is a yes/no field."
                ).format(value=text, field=field),
            )
        return lambda feature: (
            not _is_null(feature[field]) and bool(feature[field]) is wanted
        )
    if kind == "date":
        return lambda feature: (
            not _is_null(feature[field]) and _date_text(feature[field]) == text.strip()
        )
    return lambda feature: not _is_null(feature[field]) and str(feature[field]) == text


def _same_number(attribute: object, number: float) -> bool:
    try:
        return float(attribute) == number  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _date_text(value: object) -> str:
    to_string = getattr(value, "toString", None)
    if callable(to_string):
        from qgis.PyQt.QtCore import Qt

        return str(to_string(Qt.DateFormat.ISODate))
    return str(value)


def _style_parts(
    xml: str,
) -> tuple[QgsFeatureRenderer | None, bool, float, float]:
    """The renderer and scale limits a saved layer style (a map theme's or a
    frame's override) draws the layer with; the renderer is `None` when the
    style does not carry one."""
    document = QDomDocument()
    if not document.setContent(xml):
        return None, False, 0.0, 0.0
    root = document.documentElement()
    scaled = root.attribute("hasScaleBasedVisibilityFlag", "0") == "1"
    minimum = float(root.attribute("minScale", "0") or 0)
    maximum = float(root.attribute("maxScale", "0") or 0)
    element = root.firstChildElement("renderer-v2")
    renderer = None
    if not element.isNull():
        renderer = QgsFeatureRenderer.load(element, QgsReadWriteContext())
    return renderer, scaled, minimum, maximum


def _in_scale(scaled: bool, minimum: float, maximum: float, scale: float) -> bool:
    if not scaled:
        return True
    return (minimum == 0 or minimum * _SCALE_PRECISION > scale) and (
        maximum == 0 or scale >= maximum
    )


def style_overrides(map_item: QgsLayoutItemMap) -> dict[str, str]:
    """The layer styles the frame draws with, by layer id: its map theme's
    or its own kept styles; empty when it draws layers as they are."""
    layout = map_item.layout()
    project = layout.project() if layout is not None else None
    if map_item.followVisibilityPreset() and project is not None:
        name = map_item.followVisibilityPresetName()
        return dict(project.mapThemeCollection().mapThemeStyleOverrides(name))
    if map_item.keepLayerStyles():
        return dict(map_item.layerStyleOverrides())
    return {}


class Prepared:
    """A calculation that can run in slices: what is known up front (the
    layer, the displayed ground, the renderer) and `steps`, the feature loop."""

    def __init__(
        self,
        layer: QgsVectorLayer,
        request: QgsFeatureRequest,
        area: QgsGeometry,
        renderer: QgsFeatureRenderer,
        context: QgsRenderContext,
        matches,  # type: ignore[no-untyped-def]
    ) -> None:
        self.layer = layer
        self.request = request
        self.area = area
        self.renderer = renderer
        self.context = context
        self.matches = matches

    def steps(self) -> Generator[None, None, int]:
        """Count the qualifying features, yielding every `BATCH` features so
        a caller can keep the interface responsive. Returns the count."""
        total = 0
        engine = QgsGeometry.createGeometryEngine(self.area.constGet())
        engine.prepareGeometry()
        fields = self.layer.fields()
        self.renderer.startRender(self.context, fields)
        iterator = self.layer.getFeatures(self.request)
        try:
            seen = 0
            for feature in iterator:
                seen += 1
                if seen % BATCH == 0:
                    yield
                geometry = feature.geometry()
                if geometry is None or geometry.isNull():
                    continue
                if not engine.intersects(geometry.constGet()):
                    continue
                self.context.expressionContext().setFeature(feature)
                if not self.renderer.willRenderFeature(feature, self.context):
                    continue
                if self.matches is not None and not self.matches(feature):
                    continue
                total += 1
        finally:
            iterator.close()
            self.renderer.stopRender(self.context)
        return total


def prepare(layout: QgsLayout, number: QgsLayoutItemLabel) -> CountOutcome | Prepared:
    """Read what the counter counts and what its map shows now. Either the
    answer straight away (nothing to scan: not set up, invalid, or the layer
    is not drawn so nothing is shown), or a `Prepared` to run. GUI thread."""
    spec = read_spec(number)
    if not spec.layer_id:
        return _no(CountState.UNCONFIGURED, tr("Choose the layer to count."))
    project = layout.project()
    layer = project.mapLayer(spec.layer_id) if project is not None else None
    if layer is None:
        name = spec.layer_name or tr("the chosen layer")
        return _no(
            CountState.INVALID,
            tr(
                "The layer “{layer}” is no longer in the project. Choose another."
            ).format(layer=name),
        )
    if not is_countable(layer):
        return _no(
            CountState.INVALID,
            tr(
                "“{layer}” has nothing to count: choose a point, line or polygon layer."
            ).format(layer=layer.name()),
        )
    map_item = layout.itemByUuid(spec.map_uuid) if spec.map_uuid else None
    if not isinstance(map_item, QgsLayoutItemMap):
        return _no(
            CountState.INVALID,
            tr("The map this counter followed is no longer on the page. Choose a map."),
        )
    matches = None
    if spec.mode == MODE_VALUE:
        built = _matcher(layer, spec)
        if isinstance(built, CountOutcome):
            return built
        matches = built

    overrides = style_overrides(map_item)
    if not map_draws(map_item, layer.id()):
        return counted(0)
    renderer: QgsFeatureRenderer | None = None
    scaled, minimum, maximum = (
        layer.hasScaleBasedVisibility(),
        layer.minimumScale(),
        layer.maximumScale(),
    )
    if layer.id() in overrides:
        styled, s_scaled, s_min, s_max = _style_parts(
            QgsMapLayerStyle(overrides[layer.id()]).xmlData()
        )
        if styled is not None:
            renderer = styled
        # The scale limits are part of a style only when it carries them.
        document = QDomDocument()
        if document.setContent(
            overrides[layer.id()]
        ) and document.documentElement().hasAttribute("hasScaleBasedVisibilityFlag"):
            scaled, minimum, maximum = s_scaled, s_min, s_max
    if renderer is None:
        source = layer.renderer()
        renderer = source.clone() if source is not None else None
    if renderer is None or layer.opacity() <= 0:
        return counted(0)
    scale = map_item.scale()
    if not _in_scale(scaled, minimum, maximum, scale):
        return counted(0)

    try:
        area = _displayed_area(map_item, layer)
        extent = map_item.extent()
        settings = map_item.mapSettings(
            extent, QSizeF(800.0, 800.0 * extent.height() / extent.width()), 96.0, True
        )
        context = QgsRenderContext.fromMapSettings(settings)
        expression = map_item.createExpressionContext()
        expression.appendScopes(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer)
        )
        context.setExpressionContext(expression)
        context.setRendererScale(scale)
        request = QgsFeatureRequest().setFilterRect(area.boundingBox())
    except Exception as exc:  # a CRS that cannot be carried, a bad extent
        return _no(
            CountState.UNAVAILABLE,
            tr("The count could not be calculated: {reason}").format(reason=exc),
        )
    return Prepared(layer, request, area, renderer, context, matches)


def _displayed_area(map_item: QgsLayoutItemMap, layer: QgsVectorLayer) -> QgsGeometry:
    """The ground the frame shows, as a polygon in the layer's CRS: its final
    extent turned by its rotation, not the rectangle enclosing it."""
    shown = QgsGeometry.fromQPolygonF(map_item.visibleExtentPolygon())
    if shown.isNull() or shown.isEmpty():
        raise ValueError(tr("the map shows no area"))
    layout = map_item.layout()
    project = layout.project() if layout is not None else None
    if map_item.crs() != layer.crs():
        context = (
            project.transformContext()
            if project is not None
            else QgsCoordinateTransformContext()
        )
        transform = QgsCoordinateTransform(map_item.crs(), layer.crs(), context)
        # Edges bend when carried to another CRS: follow them in small steps.
        shown = shown.densifyByCount(24)
        if shown.transform(transform) != Qgis.GeometryOperationResult.Success:
            raise ValueError(tr("the map's area cannot be carried to the layer's CRS"))
    return shown


def run(prepared: Prepared) -> CountOutcome:
    """Run a calculation to its end (the service runs it in slices instead)."""
    try:
        steps = prepared.steps()
        while True:
            try:
                next(steps)
            except StopIteration as done:
                return counted(int(done.value))
    except Exception as exc:
        return failure(exc)


def failure(exc: BaseException) -> CountOutcome:
    return _no(
        CountState.UNAVAILABLE,
        tr("The count could not be calculated: {reason}").format(reason=exc),
    )


def evaluate(layout: QgsLayout, number: QgsLayoutItemLabel) -> CountOutcome:
    """The counter's outcome now, calculated to the end."""
    try:
        prepared = prepare(layout, number)
    except Exception as exc:
        return failure(exc)
    return prepared if isinstance(prepared, CountOutcome) else run(prepared)


def apply_outcome(number: QgsLayoutItemLabel, outcome: CountOutcome) -> bool:
    """Show `outcome` as the number's text. A computed value, not an edit: it
    is not an undo step. Returns whether the text changed."""
    if sip.isdeleted(number) or number.text() == outcome.text:
        return False
    number.setText(outcome.text)
    number.refresh()
    return True


def refresh_live_counts(layout: QgsLayout) -> dict[str, CountOutcome]:
    """Calculate every counter on `layout` to the end and show the results.
    Called before a layout is saved, printed or exported so that the numbers
    are those of the view that leaves. GUI thread. Returns the outcomes by
    the number item's uuid."""
    outcomes: dict[str, CountOutcome] = {}
    for number in live_counts(layout):
        outcome = evaluate(layout, number)
        apply_outcome(number, outcome)
        outcomes[number.uuid()] = outcome
    return outcomes


def source_summary(layout: QgsLayout, number: QgsLayoutItemLabel) -> str:
    """What the counter counts, in a line: "Monitoring sites · Status: Active".
    (Which map it follows is the line above it in the Edit tab.)"""
    spec = read_spec(number)
    project = layout.project()
    layer = project.mapLayer(spec.layer_id) if project and spec.layer_id else None
    parts = []
    name = layer.name() if layer is not None else spec.layer_name
    if name:
        parts.append(name)
    if spec.mode == MODE_VALUE and spec.field:
        shown = (
            tr("(no value)")
            if spec.value_is_null
            else (spec.value if spec.value is not None else "?")
        )
        parts.append(f"{spec.field}: {shown}")
    return " \N{MIDDLE DOT} ".join(parts)


def unique_values(layer: QgsVectorLayer, field: str, limit: int) -> Sequence[str]:
    """Up to `limit` values the field holds, as text, for a list of
    suggestions (a value can always be typed instead)."""
    index = layer.fields().lookupField(field)
    if index < 0:
        return ()
    found = []
    for value in layer.uniqueValues(index, limit):
        if _is_null(value):
            continue
        found.append(
            _date_text(value) if field_kind(layer, field) == "date" else str(value)
        )
    return sorted(set(found), key=str.lower)
