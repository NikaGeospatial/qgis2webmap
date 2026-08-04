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

import string
from typing import TYPE_CHECKING

from .export_ir import MM_TO_PIXELS, Color, LabelingSpec
from .fidelity_report import FidelityReportBuilder

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsVectorLayer

# What the runtime's default atlas already covers.
ASCII_PRINTABLE = frozenset(string.printable)

# Walking every feature to collect glyphs is wasteful on large layers, and the
# tail of a long dataset rarely introduces a character the head did not.
CHARACTER_SCAN_LIMIT = 5000


def _color_from_qcolor(qcolor: object) -> Color | None:
    if qcolor is None or not getattr(qcolor, "isValid", lambda: False)():
        return None
    return Color(r=qcolor.red(), g=qcolor.green(), b=qcolor.blue(), a=qcolor.alphaF())


def collect_character_set(layer: QgsVectorLayer, field_name: str) -> str | None:
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

    characters: set[str] = set()
    has_extra = False
    for index, feature in enumerate(layer.getFeatures()):
        if index >= CHARACTER_SCAN_LIMIT:
            break
        value = feature[field_name]
        if value is None:
            continue
        for char in str(value):
            characters.add(char)
            if char not in ASCII_PRINTABLE:
                has_extra = True

    if not has_extra:
        return None
    # Sorted so the attribute is byte-stable across runs.
    return "".join(sorted(characters))


def translate_labeling(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
) -> LabelingSpec:
    """Translate a layer's labelling, or record why it cannot be."""
    layer_id = layer.id()
    subject = f"Labels on '{layer.name()}'"

    if not layer.labelsEnabled():
        return LabelingSpec(enabled=False)

    labeling = layer.labeling()
    if labeling is None:
        return LabelingSpec(enabled=False)

    try:
        settings = labeling.settings()
    except (AttributeError, TypeError):
        report.unsupported(
            subject,
            "This layer uses a labelling mode that 0.1.0 cannot translate "
            "(rule-based labelling). Labels will not appear.",
            layer_id,
        )
        return LabelingSpec(enabled=False)

    if settings.isExpression:
        report.unsupported(
            subject,
            "Labels come from an expression rather than a single field. 0.1.0 "
            "translates field-based labels only, so labels will not appear.",
            layer_id,
        )
        return LabelingSpec(enabled=False)

    field_name = settings.fieldName or None
    if not field_name:
        return LabelingSpec(enabled=False)

    text_format = settings.format()
    font = text_format.font()
    buffer_settings = text_format.buffer()

    halo_width = 0.0
    halo_color = None
    if buffer_settings is not None and buffer_settings.enabled():
        halo_width = float(buffer_settings.size())
        halo_color = _color_from_qcolor(buffer_settings.color())

    character_set = collect_character_set(layer, field_name)
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

    report.approximated(
        subject,
        "Label text, font, colour, halo, background, offset, rotation and the "
        "placement quadrant are translated. QGIS collision handling, callouts "
        "and curved placement are not - the web renderer resolves overlaps "
        "with its own logic.",
        layer_id,
    )

    return LabelingSpec(
        enabled=True,
        field_name=field_name,
        font_family=font.family() if font else None,
        font_size=float(text_format.size()),
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
    )


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
