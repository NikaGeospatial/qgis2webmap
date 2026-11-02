"""A layer's GeoJSON is serialised once, and every reader still gets what it got.

The size measure, the compression choice and the page all used to serialise
the layer for themselves. They now share `ExportLayer.geojson_text`; these
check that the numbers and the page are unchanged by that, and that the
serialising really happens once.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from nika_onlymap_exporter.core import export_ir
from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    ExportProject,
    GeometryKind,
    SourceKind,
)
from nika_onlymap_exporter.core.manifest_builder import (
    build_layer_element,
    json_for_script,
)
from nika_onlymap_exporter.packaging.dependency_scanner import (
    ascii_json_bytes,
    data_bytes_by_layer,
)


def _old_size(value: object) -> int:
    """What `data_bytes_by_layer` measured a layer's GeoJSON as before."""
    return len(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _text(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def test_every_code_point_measures_as_its_ascii_escape() -> None:
    """One character at a time through the Basic Multilingual Plane, lone
    surrogates included; the planes beyond it, all written alike as a pair of
    escapes, a block at a time. As a string value and as a key."""
    for code in range(0x10000):
        character = chr(code)
        assert ascii_json_bytes(_text(character)) == _old_size(character), hex(code)
    for start in range(0x10000, 0x110000, 0x1000):
        block = "".join(map(chr, range(start, start + 0x1000)))
        assert ascii_json_bytes(_text(block)) == _old_size(block), hex(start)
    assert ascii_json_bytes(_text({"\U0001f30b": 1})) == _old_size({"\U0001f30b": 1})


PROPERTIES: dict[str, Any] = {
    "plain": "Mount Fuji",
    "accents": "\u00dcn\u00efc\u00f8d\u00e9 \u2013 \u201cquoted\u201d",
    "separators": "line \u2028 paragraph \u2029",
    "emoji": "\U0001f30b\U0001f5fb",
    "delete": "a\x7fb",
    "controls": "tab\tnew\nline\x00\x1f",
    "markup": '</script><b>&"\\',
    "number": 3776.24,
    "nothing": None,
    "flag": True,
    "日本語": "富士山",
}


def _layer(geojson: dict[str, Any] | None) -> ExportLayer:
    return ExportLayer(
        layer_id="peaks",
        name="Peaks",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=2,
        geojson=geojson,
    )


GEOJSON: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": number,
            "geometry": {"type": "Point", "coordinates": [138.7274 + number, 35.3606]},
            "properties": PROPERTIES,
        }
        for number in range(2)
    ],
}


@pytest.mark.parametrize(
    "geojson",
    [
        GEOJSON,
        {"type": "FeatureCollection", "features": []},
        {"type": "FeatureCollection", "features": [{"properties": {"a": "plain"}}]},
        # A lone surrogate cannot be written into a page, but it can be measured.
        {"type": "FeatureCollection", "features": [{"properties": {"a": "\ud83d"}}]},
    ],
)
def test_the_size_measure_is_unchanged(geojson: dict[str, Any]) -> None:
    layer = _layer(geojson)
    project = ExportProject(title="Peaks", layers=(layer,))
    assert data_bytes_by_layer(project) == {"peaks": _old_size(geojson)}


def test_the_page_carries_the_same_data() -> None:
    """Uncompressed, so the block is the text itself."""
    layer = _layer(GEOJSON)
    element = build_layer_element(layer)
    assert f"\n{json_for_script(GEOJSON)}\n" in element
    # Escaped, so an attribute cannot close the block early.
    assert element.count("</script>") == 1
    assert "\\u003c/script\\u003e\\u003cb\\u003e\\u0026" in element


def test_one_layer_is_serialised_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    real_dumps = json.dumps

    def counting_dumps(value: object, *args: Any, **kwargs: Any) -> str:
        if value is GEOJSON:
            calls.append(value)
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(export_ir.json, "dumps", counting_dumps)
    layer = _layer(GEOJSON)
    project = ExportProject(title="Peaks", layers=(layer,))
    data_bytes_by_layer(project)
    data_bytes_by_layer(project)
    build_layer_element(layer)
    build_layer_element(layer, compress_data=True)
    assert len(calls) == 1


def test_a_changed_layer_is_serialised_afresh() -> None:
    layer = _layer(GEOJSON)
    assert json.loads(layer.geojson_text) == GEOJSON
    smaller = {"type": "FeatureCollection", "features": GEOJSON["features"][:1]}
    changed = dataclasses.replace(layer, geojson=smaller)
    assert json.loads(changed.geojson_text) == smaller
    assert layer.geojson_text != changed.geojson_text
    # The cached text is not a field: equality and the snapshot ignore it.
    assert layer == _layer(GEOJSON)
    assert "geojson_text" not in layer.snapshot(include_geometry=True)
