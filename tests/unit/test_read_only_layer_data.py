"""A built layer's GeoJSON cannot change under the text kept for it.

`ExportLayer.geojson_text` is serialised once and kept, and a cartograph's
live-frame cache hands the same layer to later builds, so the data must never
change in place. These change it every way Python allows - through the layer,
through the snapshot that hands the same data out, and through the dict the
layer was built from - and check that each is refused or cannot reach the
layer, that the text still says what the data says, and that reading the data
is unchanged.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import copy
import dataclasses
import gc
import json
import pickle
from collections.abc import Callable
from typing import Any

import pytest

from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    ExportProject,
    GeometryKind,
    SourceKind,
)
from nika_onlymap_exporter.core.read_only_json import ReadOnlyDict, ReadOnlyList


def _geojson() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": 1,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]],
                },
                "properties": {"name": "Field", "tags": ["a", "b"], "meta": {"k": 1}},
            },
            {
                "type": "Feature",
                "id": 2,
                "geometry": {"type": "Point", "coordinates": [2.5, 3.5]},
                "properties": {"name": "Well", "tags": [], "meta": None},
            },
        ],
    }


def _layer(geojson: dict[str, Any] | None) -> ExportLayer:
    return ExportLayer(
        layer_id="fields",
        name="Fields",
        geometry_kind=GeometryKind.POLYGON,
        source_kind=SourceKind.FILE,
        feature_count=2,
        geojson=geojson,
    )


def _text(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


Change = Callable[[dict[str, Any]], object]

#: Every in-place change of a dict or a list, at every depth of the data.
CHANGES: dict[str, Change] = {
    "set a key": lambda g: g.__setitem__("type", "Feature"),
    "delete a key": lambda g: g.__delitem__("type"),
    "merge in": lambda g: g.__ior__({"bbox": [0, 0, 1, 1]}),
    "update": lambda g: g.update(bbox=[0, 0, 1, 1]),
    "setdefault": lambda g: g.setdefault("bbox", []),
    "pop a key": lambda g: g.pop("type"),
    "popitem": lambda g: g.popitem(),
    "clear": lambda g: g.clear(),
    "append a feature": lambda g: g["features"].append({}),
    "extend": lambda g: g["features"].extend([{}]),
    "insert": lambda g: g["features"].insert(0, {}),
    "pop a feature": lambda g: g["features"].pop(),
    "remove": lambda g: g["features"].remove(g["features"][0]),
    "clear features": lambda g: g["features"].clear(),
    "sort": lambda g: g["features"].sort(key=lambda f: -f["id"]),
    "reverse": lambda g: g["features"].reverse(),
    "replace a feature": lambda g: g["features"].__setitem__(0, {}),
    "replace a slice": lambda g: g["features"].__setitem__(slice(0, 1), []),
    "delete a feature": lambda g: g["features"].__delitem__(0),
    "add to the list": lambda g: g["features"].__iadd__([{}]),
    "multiply the list": lambda g: g["features"].__imul__(2),
    "change a property": lambda g: g["features"][0]["properties"].__setitem__(
        "name", "Changed"
    ),
    "drop a property": lambda g: g["features"][0]["properties"].pop("name"),
    "change a list property": lambda g: g["features"][0]["properties"]["tags"].append(
        "c"
    ),
    "change a nested property": lambda g: g["features"][0]["properties"][
        "meta"
    ].__setitem__("k", 2),
    "change an id": lambda g: g["features"][1].__setitem__("id", 9),
    "change a geometry": lambda g: g["features"][1]["geometry"].__setitem__(
        "type", "MultiPoint"
    ),
    "move a vertex": lambda g: g["features"][0]["geometry"]["coordinates"][0][
        1
    ].__setitem__(0, 5.0),
    "drop a ring": lambda g: g["features"][0]["geometry"]["coordinates"].pop(),
    "move a point": lambda g: g["features"][1]["geometry"]["coordinates"].__setitem__(
        1, 9.0
    ),
}


@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_no_change_through_the_layer_is_allowed(change: Change) -> None:
    layer = _layer(_geojson())
    text = layer.geojson_text
    with pytest.raises(TypeError, match="read-only"):
        change(layer.geojson)  # type: ignore[arg-type]
    assert layer.geojson == _geojson()
    assert _text(layer.geojson) == text == layer.geojson_text


@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_no_change_through_the_snapshot_is_allowed(change: Change) -> None:
    """The snapshot hands out the layer's own data, uncopied."""
    layer = _layer(_geojson())
    project = ExportProject(title="Fields", layers=(layer,))
    text = layer.geojson_text
    for handed_out in (
        layer.snapshot(include_geometry=True)["geojson"],
        project.snapshot(include_geometry=True)["layers"][0]["geojson"],
    ):
        assert handed_out is layer.geojson
        with pytest.raises(TypeError, match="read-only"):
            change(handed_out)
    assert _text(layer.geojson) == text == layer.geojson_text


@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_changing_the_data_a_layer_was_built_from_leaves_the_layer(
    change: Change,
) -> None:
    source = _geojson()
    layer = _layer(source)
    text = layer.geojson_text
    change(source)
    assert source != _geojson()
    assert layer.geojson == _geojson()
    assert _text(layer.geojson) == text == layer.geojson_text


def test_reading_the_data_is_unchanged() -> None:
    plain = _geojson()
    data = _layer(_geojson()).geojson
    assert data is not None
    assert data == plain and plain == data
    assert _text(data) == _text(plain)
    assert json.dumps(data, indent=2, sort_keys=True) == json.dumps(
        plain, indent=2, sort_keys=True
    )
    assert repr(data) == repr(plain)
    assert isinstance(data, dict) and isinstance(data["features"], list)
    assert data["features"][0]["geometry"]["coordinates"][0][2] == [1.0, 1.0]
    assert data["features"][::-1] == plain["features"][::-1]
    assert [feature["id"] for feature in data["features"]] == [1, 2]
    assert data.get("bbox") is None and "features" in data
    assert len(data["features"][0]["properties"]) == 3


def test_copies_are_plain_and_free_to_change() -> None:
    data = _layer(_geojson()).geojson
    assert data is not None
    copies: list[Any] = [
        dict(data),
        {**data},
        data.copy(),
        data | {},
        copy.copy(data),
        copy.deepcopy(data),
        pickle.loads(pickle.dumps(data)),
    ]
    for made in copies:
        assert type(made) is dict and made == data
        made["type"] = "changed"
    deep = copy.deepcopy(data)
    assert type(deep["features"]) is list
    assert type(deep["features"][0]["geometry"]["coordinates"][0][0]) is list
    deep["features"][0]["geometry"]["coordinates"][0][0][0] = 7.0
    unpickled = pickle.loads(pickle.dumps(data))
    assert type(unpickled["features"][0]["properties"]) is dict
    unpickled["features"][0]["properties"]["name"] = "changed"
    for made_list in (
        list(data["features"]),
        data["features"][:],
        data["features"] + [],
        copy.copy(data["features"]),
    ):
        assert type(made_list) is list
        made_list.append({})
    assert data == _geojson()


def test_a_layer_built_from_read_only_data_shares_it() -> None:
    """`dataclasses.replace` copies nothing, and a changed layer starts with
    data of its own."""
    layer = _layer(_geojson())
    assert dataclasses.replace(layer, name="Renamed").geojson is layer.geojson
    assert layer.geojson is not None
    features = layer.geojson["features"]
    changed = dataclasses.replace(
        layer, geojson={**layer.geojson, "features": features[:1]}
    )
    assert type(changed.geojson) is ReadOnlyDict
    assert type(changed.geojson["features"]) is ReadOnlyList
    assert changed.geojson["features"][0] is features[0]
    assert _text(changed.geojson) == changed.geojson_text != layer.geojson_text


def test_no_data_and_tuples() -> None:
    assert _layer(None).geojson is None
    data = _layer({"type": "FeatureCollection", "features": ({"id": 1},)}).geojson
    assert data is not None
    with pytest.raises(TypeError, match="read-only"):
        data["features"][0]["id"] = 2


@pytest.mark.parametrize("enabled", [True, False])
def test_the_cycle_collector_is_left_as_it_was(enabled: bool) -> None:
    was = gc.isenabled()
    try:
        (gc.enable if enabled else gc.disable)()
        _layer(_geojson())
        assert gc.isenabled() is enabled
    finally:
        (gc.enable if was else gc.disable)()
