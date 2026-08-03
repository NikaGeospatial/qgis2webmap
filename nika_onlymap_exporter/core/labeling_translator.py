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

from .export_ir import Color, LabelingSpec
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

    report.approximated(
        subject,
        "Label text, font size, colour and halo are translated. QGIS label "
        "placement rules, collision handling and callouts are not - the web "
        "renderer places labels with its own logic.",
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
    )
