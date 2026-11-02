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

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from .export_ir import PopupFieldMode, PopupFieldSpec, PopupSpec
from .fidelity_report import FidelityReportBuilder

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

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
    except AttributeError:  # a layer with no attribute table, such as a raster
        return frozenset()

    for column in config.columns():
        if getattr(column, "hidden", False) and getattr(column, "name", None):
            hidden.add(column.name)
    return frozenset(hidden)


def popup_field_names(layer: QgsVectorLayer) -> tuple[str, ...]:
    """Fields a popup can show, in layer order.

    Shared with the dialog so the per-field list it offers is exactly the list
    `translate_popup` builds rows from. A field offered in one and dropped by
    the other is a control whose effect the user can never see.
    """
    try:
        fields = layer.fields()
    except AttributeError:  # a raster, or any layer without an attribute table
        return ()

    return tuple(
        field.name()
        for field in fields
        if (field.typeName() or "").lower() not in _UNPRINTABLE_TYPE_NAMES
    )


def apply_field_modes(
    fields: tuple[PopupFieldSpec, ...],
    overrides: Mapping[str, str] | None,
) -> tuple[PopupFieldSpec, ...]:
    """Overlay the dialog's per-field choices on translated fields.

    Applied *after* the layer's own configuration is read, so QGIS's hidden
    columns remain the default and an explicit choice in the dialog wins over
    them. A field with no entry, or an entry this build cannot parse, keeps the
    derived mode - persisted settings are never trusted to be well-formed.
    """
    if not overrides:
        return fields

    applied: list[PopupFieldSpec] = []
    for spec in fields:
        raw = overrides.get(spec.name)
        if raw is None:
            applied.append(spec)
            continue
        try:
            mode = PopupFieldMode(raw)
        except ValueError:
            applied.append(spec)
            continue
        applied.append(replace(spec, mode=mode))
    return tuple(applied)


def translate_popup(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    enabled: bool = True,
    field_modes: Mapping[str, str] | None = None,
    on_hover: bool = False,
) -> PopupSpec:
    """Build a popup spec from the layer's fields, aliases and hidden columns.

    `field_modes` carries the dialog's per-field overrides. They are applied
    before the all-fields-hidden check below so the warning describes what the
    user will actually see, not what QGIS alone implied.
    """
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

    chosen = apply_field_modes(tuple(fields), field_modes)

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

    visible = sum(1 for f in chosen if f.mode is not PopupFieldMode.HIDDEN)
    if enabled and visible == 0:
        report.unsupported(
            subject,
            "Popups are enabled but every field is hidden, so clicking a feature "
            "would show an empty popup. Un-hide a field, or turn popups off for "
            "this layer.",
            layer_id,
        )

    return PopupSpec(enabled=enabled, fields=chosen, on_hover=on_hover)


def rename_untemplatable_fields(
    collection: dict,
    popup: PopupSpec,
    protected: set[str],
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> tuple[dict, PopupSpec]:
    """Rename popup fields the web template cannot reference.

    The runtime's popup interpolator substitutes `{{name}}` only for `\\w+`
    names, so a field called "Last Known Eruption" leaves a literal
    `{{Last Known Eruption}}` in every popup. The field is renamed in the
    exported data and the popup references the new name; the label the reader
    sees keeps the original spelling. `protected` names - anything the map
    draws with - are never renamed, because accessors reference them by their
    real name: their value is copied under the safe name instead, and only the
    popup reads the copy.
    """
    # The runtime's interpolator is JavaScript, whose `\w` is ASCII-only -
    # Python's is Unicode-aware, so without re.ASCII a field named 'Höhe'
    # would be judged templatable here and still leak `{{Höhe}}` in the map.
    # `existing` includes `protected` because drawing-only fields survive in
    # the feature properties without appearing among the popup fields; a
    # candidate colliding with one would silently clobber it.
    existing = {f.name for f in popup.fields} | protected
    renames: dict[str, str] = {}
    copies: dict[str, str] = {}
    for field in popup.fields:
        if re.fullmatch(r"\w+", field.name, flags=re.ASCII):
            continue
        candidate = (
            re.sub(r"\W+", "_", field.name, flags=re.ASCII).strip("_") or "field"
        )
        while (
            candidate in existing
            or candidate in renames.values()
            or candidate in copies.values()
        ):
            candidate += "_"
        if field.name in protected:
            copies[field.name] = candidate
        else:
            renames[field.name] = candidate

    if not renames and not copies:
        return collection, popup

    safe_names = {**renames, **copies}
    fields = tuple(
        replace(f, name=safe_names[f.name], alias=f.alias or f.name)
        if f.name in safe_names
        else f
        for f in popup.fields
    )
    features = []
    for feature in collection.get("features") or ():
        properties = feature.get("properties") or {}
        renamed = {renames.get(name, name): value for name, value in properties.items()}
        renamed.update(
            {
                copy: properties[name]
                for name, copy in copies.items()
                if name in properties
            }
        )
        features.append({**feature, "properties": renamed})
    count = len(safe_names)
    first = next(iter(safe_names))
    report.approximated(
        subject,
        f"{count} field name{'s' if count != 1 else ''} contain characters the "
        "web popup template cannot reference (only letters, digits and "
        "underscores work). The attribute is renamed in the exported data - "
        f"for example '{first}' becomes '{safe_names[first]}' - and popups keep "
        "the original spelling as the label."
        + (
            f" {len(copies)} of them also draw the map, so that value is "
            "carried a second time under the new name."
            if copies
            else ""
        ),
        layer_id,
    )
    return {**collection, "features": features}, replace(popup, fields=fields)
