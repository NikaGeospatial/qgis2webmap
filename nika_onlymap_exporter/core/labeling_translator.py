"""QGIS labelling to `LabelingSpec`.

Imports PyQGIS; exercised in `tests/qgis/`.

One non-obvious job here: **collecting the character set**. OnlyMap's `TextLayer`
builds a font atlas covering ASCII only; glyphs outside it render blank unless the
page declares `character-set`. Accented names, en dashes and middot separators are
common in real label fields, so the reader walks the label values and records the
distinct characters actually used. Getting this wrong produces labels that are
present, positioned correctly, and invisible.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import string
from typing import TYPE_CHECKING, Any

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsExpressionNodeColumnRef,
    QgsFeatureRequest,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsRuleBasedLabeling,
    QgsUnitTypes,
)

from .export_ir import (
    MM_TO_PIXELS,
    Color,
    LabelingSpec,
    LineLabelRepeat,
)
from .fidelity_report import FidelityReportBuilder
from .label_points import apply_capitalization, to_plane
from .render_units import to_pixels
from .renderer_translator import reference_render_context, reference_scale

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsVectorLayer

    from .cartograph_export import LayerScans

# What the runtime's default atlas already covers.
ASCII_PRINTABLE = frozenset(string.printable)


def _color_from_qcolor(qcolor: Any) -> Color | None:
    if qcolor is None or not getattr(qcolor, "isValid", lambda: False)():
        return None
    return Color(r=qcolor.red(), g=qcolor.green(), b=qcolor.blue(), a=qcolor.alphaF())


def collect_character_set(
    layer: QgsVectorLayer, field_name: str, capitalization: str = "none"
) -> str | None:
    """Every distinct character the label field uses, or `None` if all ASCII.

    `text-character-set` *replaces* the runtime's atlas rather than extending
    it, so this must return the whole set. Returning only the non-ASCII
    additions would evict every ASCII glyph: "Zurich, Geneve" would keep the
    accents and lose the letters, and the labels would render as blanks.

    `None` when everything is ASCII, since the default atlas already covers
    that and an unnecessary attribute is noise.
    """
    if not field_name or field_name not in layer.fields().names():
        return None

    # Every feature, not a prefix: the atlas *replaces* the runtime's, so a
    # glyph first used by feature 5,500 and left out of the scan renders blank
    # on that label alone, which nothing else in the export would reveal. Only
    # the label field is fetched, and each distinct value is walked once.
    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setSubsetOfAttributes([field_name], layer.fields())
    values = {feature[field_name] for feature in layer.getFeatures(request)}
    values.discard(None)

    characters: set[str] = set()
    has_extra = False
    for value in values:
        for char in apply_capitalization(str(value), capitalization):
            characters.add(char)
            if char not in ASCII_PRINTABLE:
                has_extra = True

    if not has_extra:
        return None
    # Sorted so the attribute is byte-stable across runs.
    return "".join(sorted(characters))


def _expression_field(expression: str, layer: QgsVectorLayer) -> str | None:
    """The field an expression is nothing but, `None` for anything else.

    Layers labelled from the expression box often hold only `"name"`, which
    labels exactly as the field does, and is kept as that field. A function, a
    condition or a constant is worked out per feature at export instead, since
    the web map has no way to compute it.
    """
    parsed = QgsExpression(expression)
    if parsed.hasParserError():
        return None
    root = parsed.rootNode()
    if not isinstance(root, QgsExpressionNodeColumnRef):
        return None
    name = str(root.name())
    return name if name in layer.fields().names() else None


#: The property a label worked out from an expression is written under in the
#: exported data, with `_` added while a field of the layer has the name.
EXPRESSION_LABEL_PROPERTY = "label_text"

# The variables QGIS sets from the feature being evaluated, which the context
# a label expression is worked out in has only once it is given the feature.
_FEATURE_VARIABLES = frozenset({"feature", "id", "geometry"})

# Functions whose answer is the moment or chance, not the feature.
_MOMENT_FUNCTIONS = frozenset({"now", "rand", "randf", "uuid"})


def label_expression_context(layer: QgsVectorLayer) -> QgsExpressionContext:
    """The context QGIS works a label expression out in, short of a map:
    the global, project and layer scopes. **GUI thread only.**"""
    return QgsExpressionContext(
        QgsExpressionContextUtils.globalProjectLayerScopes(layer)
    )


def expression_label_property(layer: QgsVectorLayer) -> str:
    """A property name for expression label text that no field of `layer` has.

    The popup lists the layer's fields, so a name none of them has is never
    shown in it; the popup's own renames step around it, as around any field
    the map draws with.
    """
    taken = set(layer.fields().names())
    name = EXPRESSION_LABEL_PROPERTY
    while name in taken:
        name += "_"
    return name


def _report_expression_label(
    parsed: QgsExpression,
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> None:
    """Say that the labels are worked out at export, and what of the
    expression that cannot be."""
    report.approximated(
        subject,
        "Labels come from an expression, which is worked out for each feature "
        "when the map is exported and saved with it. The labels show the data "
        "as it was then, and do not change if it changes afterwards.",
        layer_id,
    )
    context = label_expression_context(layer)
    unset = sorted(
        name
        for name in parsed.referencedVariables()
        if name not in _FEATURE_VARIABLES and not context.hasVariable(name)
    )
    if unset:
        names = ", ".join(f"@{name}" for name in unset)
        report.approximated(
            subject,
            f"The label expression reads {names}, which QGIS sets only while "
            "it draws a map, a layout or an atlas. There is none when the "
            "labels are worked out at export, so the expression sees no value "
            "there and the labels can differ from QGIS's.",
            layer_id,
        )
    moments = sorted(
        name.lower()
        for name in parsed.referencedFunctions()
        if name.lower() in _MOMENT_FUNCTIONS
    )
    if moments:
        names = ", ".join(f"{name}()" for name in moments)
        report.approximated(
            subject,
            f"The label expression uses {names}, so the labels keep the value "
            "it gave at the moment of export.",
            layer_id,
        )


def _single_rule_settings(
    labeling: QgsRuleBasedLabeling,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> Any | None:
    """The settings of a rule-based labelling that is really just one rule.

    A single active rule with no filter and no scale limits labels every feature
    exactly as simple labelling would, so it is translated as that. Anything
    else - several rules, a filter, a scale range - decides per feature or per
    zoom what is labelled and how, which one label spec cannot say, so it is
    reported rather than flattened into labels that are wrong for some features.
    """
    rules = [
        rule
        for rule in labeling.rootRule().descendants()
        if rule.active() and rule.settings() is not None
    ]
    if not rules:
        report.unsupported(
            subject,
            "This layer uses rule-based labelling with no active rule that draws "
            "labels, so no labels appear.",
            layer_id,
        )
        return None
    only = rules[0]
    if (
        len(rules) == 1
        and not only.children()
        and (only.filterExpression() or "").strip().upper() in ("", "TRUE")
        and not only.dependsOnScale()
    ):
        report.preserved(
            subject,
            "Rule-based labelling with one unfiltered rule is translated as "
            "simple labelling.",
            layer_id,
        )
        return only.settings()
    report.unsupported(
        subject,
        f"This layer uses rule-based labelling ({len(rules)} active rules, with "
        "filters or scale ranges). The exporter translates one label style per "
        "layer, so its rules cannot be reproduced and no labels appear. Merge "
        "them into one simple labelling in QGIS to keep the labels.",
        layer_id,
    )
    return None


def translate_labeling(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    scans: LayerScans | None = None,
) -> LabelingSpec:
    """Translate a layer's labelling, or record why it cannot be. `scans`
    (from a cartograph's `LiveFrameCache`) reuses the character set of an
    earlier call while the label values are unchanged."""
    layer_id = layer.id()
    subject = f"Labels on '{layer.name()}'"

    if not layer.labelsEnabled():
        return LabelingSpec(enabled=False)

    labeling = layer.labeling()
    if labeling is None:
        return LabelingSpec(enabled=False)

    if isinstance(labeling, QgsRuleBasedLabeling):
        settings = _single_rule_settings(labeling, report, subject, layer_id)
        if settings is None:
            return LabelingSpec(enabled=False)
    else:
        try:
            settings = labeling.settings()
        except (AttributeError, TypeError):
            report.unsupported(
                subject,
                "This layer uses a labelling mode the exporter cannot translate "
                "yet. Labels will not appear.",
                layer_id,
            )
            return LabelingSpec(enabled=False)

    # "Only obstacle" labelling: the layer keeps its place in QGIS's collision
    # handling but draws no text. The web map has no collision handling to feed,
    # so nothing is drawn, which is what QGIS shows.
    if not settings.drawLabels:
        report.approximated(
            subject,
            "This layer's labelling is set to act only as an obstacle for other "
            "layers' labels and draws no text of its own, so it shows no labels. "
            "The web map has no label collision handling for it to take part in.",
            layer_id,
        )
        return LabelingSpec(enabled=False)

    field_name = settings.fieldName or None
    expression = None
    if settings.isExpression and field_name:
        plain = _expression_field(field_name, layer)
        if plain is not None:
            report.preserved(
                subject,
                f"The label expression is just the field '{plain}', "
                "translated as that field.",
                layer_id,
            )
            field_name = plain
        else:
            # Anything else is worked out per feature as the features are read
            # (`layer_reader.LabelExpression`), into a property of its own.
            parsed = QgsExpression(field_name)
            if parsed.hasParserError():
                report.unsupported(
                    subject,
                    "The label expression cannot be read "
                    f"({parsed.parserErrorString()}), so no labels appear, as "
                    "in QGIS.",
                    layer_id,
                )
                return LabelingSpec(enabled=False)
            _report_expression_label(parsed, layer, report, subject, layer_id)
            expression = field_name
            field_name = expression_label_property(layer)

    if not field_name:
        return LabelingSpec(enabled=False)

    text_format = settings.format()
    font = text_format.font()
    buffer_settings = text_format.buffer()

    halo_width = 0.0
    halo_color = None
    if buffer_settings is not None and buffer_settings.enabled():
        halo_width = to_pixels(buffer_settings.size(), buffer_settings.sizeUnit()) or 0
        halo_color = _color_from_qcolor(buffer_settings.color())

    font_size = to_pixels(text_format.size(), text_format.sizeUnit())
    if font_size is None:
        report.approximated(
            subject,
            "The label size is set in map units, so it grows and shrinks with "
            "the zoom in QGIS. The web map draws labels at a fixed size on "
            "screen, so they use its default size instead.",
            layer_id,
        )

    # **Before the character set, because it changes which glyphs are drawn.**
    # Uppercasing "Zurich" gives "ZURICH" - a codepoint the raw values never
    # contained. `text-character-set` *replaces* the atlas, so collecting from
    # untransformed values would leave every accented capital out of it, and
    # those labels would render blank: present, positioned, invisible.
    capitalization = read_capitalization(text_format)

    # Expression labels have no field to scan: their text exists only once the
    # features are read, and the page collects their characters from it then
    # (`manifest_builder.build_label_element`).
    character_set = (
        None
        if expression is not None
        else collect_character_set(layer, field_name, capitalization)
        if scans is None
        else scans.character_set(
            layer, field_name, capitalization, collect_character_set
        )
    )
    if character_set:
        report.preserved(
            subject,
            "Label text uses characters outside the default font atlas; the "
            f"atlas is rebuilt to cover all {len(character_set)} character(s) "
            "the labels use.",
            layer_id,
        )

    background_color = None
    background_padding = (0.0, 0.0)
    background = text_format.background()
    if background is not None and background.enabled():
        background_color = _color_from_qcolor(background.fillColor())
        size = background.size()
        if size is not None:
            background_padding = (
                float(size.width()) * MM_TO_PIXELS,
                float(size.height()) * MM_TO_PIXELS,
            )

    if font is not None and font.italic():
        report.approximated(
            subject,
            "The label font is italic. The web renderer builds its font atlas "
            "from a family and a weight only, so the labels will be upright.",
            layer_id,
        )

    anchor, baseline = read_quadrant(settings)
    wrap_char = str(getattr(settings, "wrapChar", "") or "")
    auto_wrap = int(getattr(settings, "autoWrapLength", 0) or 0)
    plane = measure_plane()
    repeat = _line_repeat(layer, settings, plane, report, subject, layer_id)

    report.approximated(
        subject,
        "Label text, font, size, colour, background, offset, rotation and the "
        "placement quadrant are translated. A halo becomes a soft box in its "
        "colour behind the text, because the web map's outlined text is hard "
        "to read at label sizes; on a point layer, a boxed label centred on its "
        "point sits just above the marker so the box does not hide it. QGIS "
        "collision handling, callouts and curved placement are not; where "
        "labels would crowd, the layer's labels wait for a closer zoom instead.",
        layer_id,
    )

    return LabelingSpec(
        enabled=True,
        field_name=field_name,
        expression=expression,
        font_family=font.family() if font else None,
        font_size=font_size,
        color=_color_from_qcolor(text_format.color()),
        halo_color=halo_color,
        halo_width=halo_width,
        character_set=character_set,
        bold=bool(font.bold()) if font is not None else False,
        anchor=anchor,
        baseline=baseline,
        offset_x=float(getattr(settings, "xOffset", 0.0) or 0.0) * MM_TO_PIXELS,
        offset_y=float(getattr(settings, "yOffset", 0.0) or 0.0) * MM_TO_PIXELS,
        rotation=float(getattr(settings, "angleOffset", 0.0) or 0.0),
        background_color=background_color,
        background_padding=background_padding,
        capitalization=capitalization,
        wrap_char=wrap_char,
        auto_wrap_length=auto_wrap,
        per_part=bool(getattr(settings, "labelPerPart", False)),
        measure_plane=plane,
        repeat=repeat,
    )


def measure_plane() -> str:
    """The plane QGIS measures lines in for labelling: its map CRS's, which
    for a geographic CRS is plain degrees (`label_points.MEASURE_PLANES`)."""
    crs = QgsProject.instance().crs()
    return "degrees" if crs.isValid() and crs.isGeographic() else "mercator"


# The web map's own repeat for line labels where QGIS sets none: MapLibre's
# default `symbol-spacing`, the spacing web maps repeat line labels at. Fixed at
# the layer's reference scale, where the layer fills the view, so a line that
# crosses it carries a few labels there - none on a line shorter than three
# spacings - and one about every screen two zooms in.
DEFAULT_LINE_REPEAT_PX = 250.0


def _line_repeat(
    layer: QgsVectorLayer,
    settings: Any,
    plane: str,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> LineLabelRepeat | None:
    """How far apart a line layer's labels repeat, in `plane` units, or `None`
    for any other layer, or one with no reference scale to fix it at.

    QGIS's "Repeat" distance is converted by QGIS itself
    (`QgsRenderContext.convertToMapUnits`, in its unit) at the layer's
    reference scale, as the layer's map-unit symbol sizes are. Without its map
    unit scale: QGIS 4.2's labelling draws a repeat in map units with a
    minimum size or a scale limit set exactly as without one (measured by
    rendering both), so applying it would repeat where QGIS does not.
    """
    if layer.geometryType() != Qgis.GeometryType.Line:
        return None
    scale = reference_scale(layer)
    context = reference_render_context(layer)
    per_map_unit = _plane_units_per_map_unit(layer, plane)
    if scale is None or context is None or per_map_unit is None:
        return None
    context.setRendererScale(scale.scale_denominator)
    units_per_pixel = context.mapToPixel().mapUnitsPerPixel() * per_map_unit

    distance = float(getattr(settings, "repeatDistance", 0.0) or 0.0)
    if _data_defined(settings, "RepeatDistance", "RepeatDistanceUnit"):
        report.approximated(
            subject,
            "The labels' repeat distance is set by a data-defined override, "
            "which the web map cannot work out per feature, so the layer's own "
            "repeat setting is used.",
            layer_id,
        )
    if not distance > 0:
        return LineLabelRepeat(
            DEFAULT_LINE_REPEAT_PX * units_per_pixel, units_per_pixel
        )

    unit = settings.repeatDistanceUnit
    map_units = context.convertToMapUnits(distance, unit)
    if not (map_units > 0 and math.isfinite(map_units)):
        return None
    report.approximated(
        subject,
        f"QGIS repeats these labels along each line every {distance:g} "
        f"{QgsUnitTypes.toAbbreviatedString(unit)}, afresh at every scale. The "
        "web map places the repeats once, at that spacing where the layer fills "
        "the view, so they spread apart as you zoom in, and the labels show "
        "from the zoom where the repeats have room.",
        layer_id,
    )
    return LineLabelRepeat(map_units * per_map_unit, units_per_pixel, from_qgis=True)


def _plane_units_per_map_unit(layer: QgsVectorLayer, plane: str) -> float | None:
    """How long one of the layer CRS's map units is in `plane`, measured
    across the middle of the layer's extent."""
    extent = layer.extent()
    if extent.isNull() or extent.isEmpty():
        return None
    centre = extent.center()
    step = max(extent.width(), extent.height()) / 1000.0
    try:
        to_wgs84 = QgsCoordinateTransform(
            layer.crs(),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsProject.instance().transformContext(),
        )
        west = to_wgs84.transform(QgsPointXY(centre.x() - step / 2, centre.y()))
        east = to_wgs84.transform(QgsPointXY(centre.x() + step / 2, centre.y()))
    except QgsCsException:
        return None
    (x0, y0), (x1, y1) = (
        to_plane((west.x(), west.y()), plane),
        to_plane((east.x(), east.y()), plane),
    )
    length = math.hypot(x1 - x0, y1 - y0)
    return length / step if length > 0 and step > 0 else None


def _data_defined(settings: Any, *names: str) -> bool:
    """Whether any of the named label properties is data-defined."""
    try:
        properties = settings.dataDefinedProperties()
        return any(
            properties.isActive(getattr(QgsPalLayerSettings.Property, name))
            for name in names
        )
    except (AttributeError, TypeError):  # pragma: no cover
        return False


# `QgsStringUtils::Capitalization`, by value rather than by name: the enum is
# reachable as `QgsStringUtils.AllUppercase` on PyQt5 and only through the
# scoped form on PyQt6, and the integers are stable across both. `TitleCase`
# (1004) and `UpperCamelCase` (1005) are deliberately far from the rest.
_CAPITALIZATION_BY_VALUE = {
    0: "none",  # MixedCase - leave the data alone
    1: "upper",  # AllUppercase
    2: "lower",  # AllLowercase
    4: "capitalize",  # ForceFirstLetterToCapital
    1004: "title",  # TitleCase
}


def read_capitalization(text_format: Any) -> str:
    """QGIS's text case, as one of the names `apply_capitalization` understands.

    `UpperCamelCase` (1005) has no sensible label rendering - it would run words
    together - so it falls through to "none" and the label keeps its own text
    rather than becoming unreadable.
    """
    try:
        value = int(text_format.capitalization())
    except (AttributeError, TypeError, ValueError):  # pragma: no cover
        return "none"
    return _CAPITALIZATION_BY_VALUE.get(value, "none")


# QGIS names the placement quadrant by where the label sits relative to the
# point; deck.gl wants which edge of the text box is pinned, which is the
# mirror image. A label placed to the LEFT of a point ends at the point, so its
# anchor is "end" - getting this backwards puts every label on the wrong side.
_QUADRANTS: dict[str, tuple[str, str]] = {
    "AboveLeft": ("end", "bottom"),
    "Above": ("middle", "bottom"),
    "AboveRight": ("start", "bottom"),
    "Left": ("end", "center"),
    "Over": ("middle", "center"),
    "Right": ("start", "center"),
    "BelowLeft": ("end", "top"),
    "Below": ("middle", "top"),
    "BelowRight": ("start", "top"),
}


def read_quadrant(settings: object) -> tuple[str, str]:
    """QGIS's placement quadrant as a deck.gl (anchor, baseline) pair.

    Falls back to the neutral centre pair whenever the quadrant cannot be
    identified - an unreadable enum should leave the label on its point, not
    fling it to a corner.
    """
    quadrant = getattr(settings, "quadOffset", None)
    if quadrant is None:
        return ("middle", "center")
    name = str(getattr(quadrant, "name", quadrant))
    # Longest suffix first: "QuadrantBelowLeft" also ends with "Left", and
    # matching that would move a below-left label onto the centre line.
    for key in sorted(_QUADRANTS, key=len, reverse=True):
        if name.endswith(key):
            return _QUADRANTS[key]
    return ("middle", "center")
