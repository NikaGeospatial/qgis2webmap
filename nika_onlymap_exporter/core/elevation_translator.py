"""QGIS height, in either of the two places QGIS keeps it, to `ElevationSpec`.

QGIS can raise a polygon off the map in two unrelated ways, and a project may
use either:

* **3D view properties** - a `QgsVectorLayer3DRenderer` holding a
  `QgsPolygon3DSymbol` with an extrusion height. This is the real one, used by
  the 3D map view.
* **The 2.5D renderer** - a 2D symbology trick. `Qgs25DRenderer` replaces the
  layer's renderer with one fill symbol whose three layers are a flat base and
  two geometry generators, drawing fake walls and a shifted roof. Its height and
  angle are not properties of the renderer at all: they live in the *project*
  variables `qgis_25d_height` and `qgis_25d_angle`, which the geometry
  generators `eval()`. That is why reading it needs the project and not just the
  layer - and why `renderer_translator` has to take its colours from
  `roofColor()` rather than from its symbol.

Both reduce to the same question - how tall, and driven by what - so both land
in one `ElevationSpec`.

No PyQGIS import, at runtime or otherwise: every read here is a duck-typed
`getattr`. That is deliberate twice over. `qgis._3d` is a separate module that a
QGIS built without 3D support does not ship, so importing it would turn "no
extrusion" into "the plugin fails to load"; and staying pure keeps this module
unit-testable next to `manifest_builder`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from typing import Any

from .export_ir import ElevationSpec, GeometryKind
from .fidelity_report import FidelityReportBuilder

# The project variables the 2.5D renderer's geometry generators evaluate. Named
# by QGIS, not by us - see `Qgs25DRendererWidget`.
HEIGHT_VARIABLE = "qgis_25d_height"

# An OnlyMap accessor references a field as a bare `$name`; the expression
# language has no quoted form. Mirrors `renderer_translator.EXPRESSION_SAFE_FIELD`
# - kept separate rather than imported, because that module pulls in PyQGIS.
EXPRESSION_SAFE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A QGIS expression refers to a field as `"name"`; the 2.5D height box also
# accepts a bare name typed by hand.
_QUOTED_FIELD = re.compile(r'^\s*"([^"]+)"\s*$')


def _unwrap(expression: str) -> tuple[float | None, str | None]:
    """Reduce a QGIS expression to a constant or a single field.

    Returns `(height, field)` with at most one set, and `(None, None)` when the
    expression is something richer - `"floors" * 3`, a `CASE`, a call to
    `overlay_nearest()`. Those are left to the caller to report rather than
    guessed at: OnlyMap's expression language is not QGIS's, and a wrong height
    is worse than an honest omission.
    """
    text = expression.strip()
    if not text:
        return (None, None)

    try:
        return (float(text), None)
    except ValueError:
        pass

    quoted = _QUOTED_FIELD.match(text)
    name = quoted.group(1) if quoted else text
    if EXPRESSION_SAFE_FIELD.match(name):
        return (None, name)
    return (None, None)


def _from_property(
    symbol: Any, enum_name: str
) -> tuple[float | None, str | None, bool]:
    """A data-defined override on a 3D symbol, if one is active.

    Returns `(height, field, was_active)`. The last is what tells the caller
    apart an override it could not translate from no override at all.
    """
    properties = _call(symbol, "dataDefinedProperties")
    key = getattr(symbol, enum_name, None)
    if properties is None or key is None:
        return (None, None, False)

    getter = getattr(properties, "property", None)
    if getter is None:
        return (None, None, False)
    try:
        prop = getter(key)
    except (TypeError, ValueError):
        return (None, None, False)

    if prop is None or not _call(prop, "isActive"):
        return (None, None, False)

    # A field-based property carries the name directly; only an expression-based
    # one has to be parsed.
    field_name = _call(prop, "field")
    if isinstance(field_name, str) and EXPRESSION_SAFE_FIELD.match(field_name.strip()):
        return (None, field_name.strip(), True)

    expression = _call(prop, "expressionString")
    if isinstance(expression, str):
        height, field = _unwrap(expression)
        return (height, field, True)
    return (None, None, True)


def _call(obj: Any, method: str) -> Any:
    """`obj.method()` if it exists, else `None` - the same probe the renderer
    translator uses, for the same reason: these classes share no interface and
    the members differ across QGIS versions."""
    fn = getattr(obj, method, None)
    if fn is None:
        return None
    try:
        return fn()
    except (TypeError, ValueError):
        return None


def _read_3d_symbol(
    symbol: Any,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> ElevationSpec:
    """A `QgsPolygon3DSymbol`, reduced to what deck.gl can draw."""
    height, field, overridden = _from_property(symbol, "PropertyExtrusionHeight")
    if not overridden:
        constant = _call(symbol, "extrusionHeight")
        height = float(constant) if isinstance(constant, (int, float)) else None
    elif height is None and field is None:
        report.unsupported(
            subject,
            "The extrusion height is a QGIS expression, which does not "
            "translate. Set it to a single field or a fixed number to carry it "
            "into the export. The layer exports flat.",
            layer_id,
        )
        return ElevationSpec()

    if not height and field is None:
        return ElevationSpec()

    # deck.gl extrudes from zero. A QGIS base height lifts the whole prism off
    # the ground, and there is nowhere to put that.
    offset = _call(symbol, "offset")
    if isinstance(offset, (int, float)) and offset:
        report.unsupported(
            subject,
            f"The base height of {offset:g} is not carried - the web renderer "
            "extrudes from ground level. The shapes are the right height but "
            "all start at zero.",
            layer_id,
        )

    wireframe = bool(_call(symbol, "edgesEnabled"))
    report.preserved(
        subject,
        "Extrusion height translated from the layer's 3D view properties.",
        layer_id,
    )
    return ElevationSpec(
        extruded=True,
        height=height if field is None else None,
        height_field=field,
        wireframe=wireframe,
        source="3d-renderer",
    )


def _read_25d(
    project: Any,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> ElevationSpec:
    """The 2.5D renderer's height, which lives on the project, not the layer."""
    variables = _call(project, "customVariables")
    raw = (variables or {}).get(HEIGHT_VARIABLE)
    if raw is None:
        return ElevationSpec()

    height, field = _unwrap(str(raw))
    if height is None and field is None:
        report.unsupported(
            subject,
            "The 2.5D height is a QGIS expression, which does not translate. "
            "Set it to a single field or a fixed number to carry it into the "
            "export. The layer exports flat.",
            layer_id,
        )
        return ElevationSpec()
    if not height and field is None:
        return ElevationSpec()

    # The 2.5D renderer's shadows, wall shading and roof colour are a painter's
    # illusion drawn at one fixed angle. A real extrusion lights itself from the
    # camera, so none of that survives, and saying so is better than a user
    # wondering where their shadows went.
    report.approximated(
        subject,
        "The 2.5D renderer becomes a true extrusion, so the height is kept but "
        "its fixed viewing angle, wall shading and shadows are not - the web "
        "map lights the shapes from wherever the camera is.",
        layer_id,
    )
    return ElevationSpec(
        extruded=True,
        height=height if field is None else None,
        height_field=field,
        source="25d-renderer",
    )


def translate_elevation(
    layer: Any,
    report: FidelityReportBuilder,
    geometry_kind: GeometryKind,
    project: Any = None,
) -> ElevationSpec:
    """How tall this layer's features stand, from whichever place QGIS kept it.

    `project` is needed only for the 2.5D renderer; pass `None` and that path is
    skipped rather than guessed at.
    """
    layer_id = _call(layer, "id") or ""
    name = _call(layer, "name") or ""
    subject = f"Height of '{name}'"

    renderer_3d = _call(layer, "renderer3D")
    symbol = _call(renderer_3d, "symbol") if renderer_3d is not None else None

    if symbol is not None:
        # deck.gl extrudes polygons only: the `GeoJsonLayer` sublayer that draws
        # lines is a `PathLayer`, which has no elevation at all, and 3D point
        # symbols are meshes - spheres, cylinders, imported models - with no
        # equivalent in a `GeoJsonLayer` at all.
        if geometry_kind is not GeometryKind.POLYGON:
            shape = type(symbol).__name__
            report.unsupported(
                subject,
                f"The layer's 3D view properties use {shape}, which the web "
                "renderer cannot draw - only polygons can be raised. The layer "
                "exports flat, with its 2D symbology.",
                layer_id,
            )
            return ElevationSpec()
        return _read_3d_symbol(symbol, report, subject, layer_id)

    renderer = _call(layer, "renderer")
    if renderer is not None and _call(renderer, "type") == "25dRenderer":
        return _read_25d(project, report, subject, layer_id)

    return ElevationSpec()
