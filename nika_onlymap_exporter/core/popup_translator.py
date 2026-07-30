"""Field configuration to `PopupSpec`.

Imports PyQGIS; exercised in `tests/qgis/`.

**The default is labelled, deliberately.** qgis2web defaults every popup field to
"no label", so enabling popups produces a list of bare values - `42 / 22 / 1,569 /
NORTHWAY / Civilian/Public` - with nothing saying what any of them mean. Making
popups usable there needs a second, undiscoverable bulk operation. Turning popups
on here produces something readable in one step; hiding labels is the opt-in.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .export_ir import PopupFieldMode, PopupFieldSpec, PopupSpec
from .fidelity_report import FidelityReportBuilder

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsVectorLayer

# Field types QGIS can hold that carry no useful popup text.
_UNPRINTABLE_TYPE_NAMES = frozenset({"binary", "geometry"})


def hidden_field_names(layer: QgsVectorLayer) -> frozenset[str]:
    """Fields the user hid in the QGIS attribute table.

    Treated as intent: a field hidden in QGIS should not surface in a popup that
    the author's audience can read.
    """
    hidden: set[str] = set()
    try:
        config = layer.attributeTableConfig()
    except AttributeError:  # pragma: no cover - very old QGIS
        return frozenset()

    for column in config.columns():
        if getattr(column, "hidden", False) and getattr(column, "name", None):
            hidden.add(column.name)
    return frozenset(hidden)


def translate_popup(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    enabled: bool = True,
) -> PopupSpec:
    """Build a popup spec from the layer's fields, aliases and hidden columns."""
    layer_id = layer.id()
    subject = f"Popup fields of '{layer.name()}'"
    hidden = hidden_field_names(layer)

    fields: list[PopupFieldSpec] = []
    skipped: list[str] = []

    for field in layer.fields():
        name = field.name()
        type_name = (field.typeName() or "").lower()

        if type_name in _UNPRINTABLE_TYPE_NAMES:
            skipped.append(name)
            continue

        alias = field.alias() or None
        mode = (
            PopupFieldMode.HIDDEN if name in hidden else PopupFieldMode.INLINE_WITH_DATA
        )
        fields.append(PopupFieldSpec(name=name, alias=alias, mode=mode))

    if skipped:
        report.unsupported(
            subject,
            "These fields hold binary or geometry values and cannot be shown in "
            f"a popup: {', '.join(skipped)}.",
            layer_id,
        )

    if hidden:
        report.preserved(
            subject,
            f"{len(hidden)} field(s) hidden in the QGIS attribute table are also "
            "hidden in popups.",
            layer_id,
        )

    visible = sum(1 for f in fields if f.mode is not PopupFieldMode.HIDDEN)
    if enabled and visible == 0:
        report.unsupported(
            subject,
            "Popups are enabled but every field is hidden, so clicking a feature "
            "would show an empty popup. Un-hide a field, or turn popups off for "
            "this layer.",
            layer_id,
        )

    return PopupSpec(enabled=enabled, fields=tuple(fields))
