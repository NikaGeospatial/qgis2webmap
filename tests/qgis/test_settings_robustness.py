"""Saved settings read exactly as 0.3.0 read them, and none stops the dialog opening.

0.3.1 changes nothing a user sees, saved settings included, so every value
here is pinned to what 0.3.0 made of it -- `bool("false")` is True, as it
was. Where 0.3.0 raised instead (an infinite or huge number, JSON nested too
deep), the value now reads as one that cannot be understood.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("qgis.core")

from nika_onlymap_exporter.core.settings import (
    DialogState,
    LayerSettings,
    load_state,
    save_state,
)

SCOPE = "qgis2webmap"

HUGE = 10**400
NAN = float("nan")
INF = float("inf")


def _write(project, key: str, value: object) -> None:
    project.writeEntry(SCOPE, key, json.dumps(value))


class TestLayerEntries:
    def test_loading_stops_at_the_first_malformed_entry_as_in_030(
        self, project
    ) -> None:
        _write(
            project,
            "layers",
            {
                "first": {"include": False},
                "broken": "not a dict",
                "also-broken": ["x"],
                "last": {"include": False, "popup": False},
            },
        )
        layers = load_state(project).layers
        assert layers["first"].include is False
        assert list(layers) == ["first"]

    def test_a_layer_with_garbage_fields_keeps_its_good_fields(self, project) -> None:
        _write(
            project,
            "layers",
            {
                "a": {
                    "include": False,
                    "popup": "maybe",
                    "quantizePrecision": "lots",
                    "fields": 7,
                },
                "b": {"include": False},
            },
        )
        layers = load_state(project).layers
        assert layers["a"].include is False
        assert layers["a"].popup is True
        assert layers["a"].quantize_precision is None
        assert layers["b"].include is False

    @pytest.mark.parametrize("text", ["false", "False", "0", "no", "NO", "off"])
    def test_false_strings_read_as_true_as_in_030(self, project, text: str) -> None:
        _write(project, "layers", {"a": {"include": text, "label": text}})
        layer = load_state(project).layers["a"]
        assert layer.include is True
        assert layer.label is True

    @pytest.mark.parametrize("text", ["true", "TRUE", "1", "yes", "Yes", "on"])
    def test_true_strings_read_as_true(self, project, text: str) -> None:
        _write(project, "layers", {"a": {"include": text, "cluster": text}})
        layer = load_state(project).layers["a"]
        assert layer.include is True
        assert layer.cluster is True

    def test_numbers_read_as_booleans(self, project) -> None:
        _write(project, "layers", {"a": {"include": 0, "popup": 1}})
        layer = load_state(project).layers["a"]
        assert layer.include is False
        assert layer.popup is True

    def test_a_hover_override_of_false_text_is_true_as_in_030(self, project) -> None:
        _write(project, "layers", {"a": {"popupOnHover": "false"}})
        assert load_state(project).layers["a"].popup_on_hover is True

    def test_a_top_level_list_is_ignored(self, project) -> None:
        _write(project, "layers", [1, 2])
        assert load_state(project).layers == {}

    def test_round_trip_keeps_every_layer(self, project) -> None:
        state = DialogState()
        state.layers["x"] = LayerSettings(include=False)
        state.layers["y"] = LayerSettings(popup=False, popup_on_hover=True)
        save_state(project, state)
        restored = load_state(project).layers
        assert restored["x"].include is False
        assert restored["y"].popup is False
        assert restored["y"].popup_on_hover is True


class TestMapWideWidgets:
    def test_false_strings_read_as_true_as_in_030(self, project) -> None:
        _write(
            project,
            "widgets",
            {"legend": "false", "scaleBar": "0", "showTitle": "no"},
        )
        state = load_state(project)
        assert state.show_legend is True
        assert state.show_scale_bar is True
        assert state.show_title is True

    def test_one_garbage_field_keeps_the_others(self, project) -> None:
        _write(
            project,
            "widgets",
            {
                "legend": False,
                "chromeScale": "huge",
                "titleCorner": "nowhere",
                "extentSource": 12,
                "quantizePrecision": "x",
                "scaleBar": False,
                "clipToExtent": True,
                "basemap": "osm",
            },
        )
        state = load_state(project)
        default = DialogState()
        assert state.show_legend is False
        assert state.show_scale_bar is False
        assert state.clip_to_extent is True
        assert state.basemap == "osm"
        assert state.chrome_scale == default.chrome_scale
        assert state.title_corner == default.title_corner
        assert state.extent_source == default.extent_source
        assert state.quantize_precision is None

    def test_a_widgets_entry_that_is_not_an_object_is_ignored(self, project) -> None:
        _write(project, "widgets", ["legend"])
        assert load_state(project).show_legend is DialogState().show_legend

    def test_a_project_saved_before_the_title_existed_gets_the_new_default(
        self, project
    ) -> None:
        _write(project, "widgets", {"legend": False})
        state = load_state(project)
        assert state.show_title is True
        assert state.show_legend is False


class TestCartographTables:
    def test_a_bad_frame_entry_does_not_drop_the_good_ones(self, project) -> None:
        _write(
            project,
            "cartographFrames",
            {
                "Layout A": {
                    "i1": {"mode": "static", "dpi": 300},
                    "i2": "junk",
                    "i3": {"mode": "static", "dpi": "high"},
                    "i4": {"mode": "static", "dpi": 150},
                },
                "Layout B": "junk",
                "Layout C": {"c1": {"mode": "static", "dpi": 96}},
            },
        )
        frames = load_state(project).cartograph_frames
        assert frames["Layout A"]["i1"].dpi == 300
        assert frames["Layout A"]["i3"].mode == "static"
        assert frames["Layout A"]["i4"].dpi == 150
        assert frames["Layout C"]["c1"].dpi == 96

    def test_a_bad_page_entry_does_not_drop_the_good_ones(self, project) -> None:
        _write(
            project,
            "cartographPage",
            {
                "A": {"title": "One", "theme": "no-such-theme"},
                "B": 5,
                "C": {"title": "Three"},
            },
        )
        pages = load_state(project).cartograph_page
        assert pages["A"].title == "One"
        assert pages["A"].theme == "default"
        assert pages["C"].title == "Three"

    def test_a_non_object_table_is_ignored(self, project) -> None:
        for key in ("cartographFiles", "cartographFrames", "cartographPage"):
            _write(project, key, [1])
        state = load_state(project)
        assert state.cartograph_files == {}
        assert state.cartograph_frames == {}
        assert state.cartograph_page == {}


# Each saved value, and what 0.3.0 read it as: a layer's include/popup/label/
# cluster and every map-wide on/off widget; a layer's hover override; the
# map-wide and per-layer coordinate precision; the chrome scale; a static
# frame's DPI; and which of the layers {"a": ok, "b": value, "c": excluded}
# were loaded.
AS_030 = [
    (True, True, True, None, None, 1.0, 1, "a"),
    (False, False, False, None, None, 1.0, 0, "a"),
    (0, False, False, None, 0, 1.0, 0, "a"),
    (1, True, True, 1, 1, 1.0, 1, "a"),
    (2, True, True, 2, 2, 2.0, 2, "a"),
    (-1, True, True, None, None, 1.0, -1, "a"),
    (3, True, True, 3, 3, 1.0, 3, "a"),
    (6, True, True, 6, 6, 1.0, 6, "a"),
    (15, True, True, 15, 15, 1.0, 15, "a"),
    (16, True, True, None, None, 1.0, 16, "a"),
    (96, True, True, None, None, 1.0, 96, "a"),
    (200, True, True, None, None, 1.0, 200, "a"),
    (300, True, True, None, None, 1.0, 300, "a"),
    (0.0, False, False, None, 0, 1.0, 0, "a"),
    (1.0, True, True, 1, 1, 1.0, 1, "a"),
    (-0.0, False, False, None, 0, 1.0, 0, "a"),
    (0.5, True, True, None, 0, 1.0, 0, "a"),
    (0.75, True, True, None, 0, 0.75, 0, "a"),
    (1.25, True, True, 1, 1, 1.25, 1, "a"),
    (2.0, True, True, 2, 2, 2.0, 2, "a"),
    (2.5, True, True, 2, 2, 1.0, 2, "a"),
    (6.0, True, True, 6, 6, 1.0, 6, "a"),
    (6.9, True, True, 6, 6, 1.0, 6, "a"),
    (15.0, True, True, 15, 15, 1.0, 15, "a"),
    (15.5, True, True, 15, 15, 1.0, 15, "a"),
    (100.0, True, True, None, None, 1.0, 100, "a"),
    (1e-320, True, True, None, 0, 1.0, 0, "a"),
    (10**20, True, True, None, None, 1.0, 10**20, "a"),
    (NAN, True, True, None, None, 1.0, 200, "a"),
    ("", False, False, None, None, 1.0, 200, "a"),
    (" ", True, True, None, None, 1.0, 200, "a"),
    ("true", True, True, None, None, 1.0, 200, "a"),
    ("True", True, True, None, None, 1.0, 200, "a"),
    ("TRUE", True, True, None, None, 1.0, 200, "a"),
    ("false", True, True, None, None, 1.0, 200, "a"),
    ("False", True, True, None, None, 1.0, 200, "a"),
    ("FALSE", True, True, None, None, 1.0, 200, "a"),
    ("0", True, True, None, 0, 1.0, 0, "a"),
    ("1", True, True, 1, 1, 1.0, 1, "a"),
    ("2", True, True, 2, 2, 2.0, 2, "a"),
    ("yes", True, True, None, None, 1.0, 200, "a"),
    ("no", True, True, None, None, 1.0, 200, "a"),
    ("on", True, True, None, None, 1.0, 200, "a"),
    ("off", True, True, None, None, 1.0, 200, "a"),
    ("junk", True, True, None, None, 1.0, 200, "a"),
    ("6", True, True, 6, 6, 1.0, 6, "a"),
    ("6.0", True, True, None, None, 1.0, 200, "a"),
    ("6.9", True, True, None, None, 1.0, 200, "a"),
    ("1e2", True, True, None, None, 1.0, 200, "a"),
    (" 6 ", True, True, 6, 6, 1.0, 6, "a"),
    ("6_0", True, True, None, None, 1.0, 60, "a"),
    ("\u0666", True, True, 6, 6, 1.0, 6, "a"),
    ("inf", True, True, None, None, 1.0, 200, "a"),
    ("nan", True, True, None, None, 1.0, 200, "a"),
    ("-inf", True, True, None, None, 1.0, 200, "a"),
    ("Infinity", True, True, None, None, 1.0, 200, "a"),
    ("0x10", True, True, None, None, 1.0, 200, "a"),
    ("+7", True, True, 7, 7, 1.0, 7, "a"),
    ("-0", True, True, None, 0, 1.0, 0, "a"),
    ("live", True, True, None, None, 1.0, 200, "a"),
    ("static", True, True, None, None, 1.0, 200, "a"),
    ("default", True, True, None, None, 1.0, 200, "a"),
    ("dark", True, True, None, None, 1.0, 200, "a"),
    ("none", True, True, None, None, 1.0, 200, "a"),
    ("osm", True, True, None, None, 1.0, 200, "a"),
    ("#ff0000", True, True, None, None, 1.0, 200, "a"),
    ("#ff000080", True, True, None, None, 1.0, 200, "a"),
    ("top-left", True, True, None, None, 1.0, 200, "a"),
    ("bottom-right", True, True, None, None, 1.0, 200, "a"),
    ("project", True, True, None, None, 1.0, 200, "a"),
    ("layers", True, True, None, None, 1.0, 200, "a"),
    ("label", True, True, None, None, 1.0, 200, "a"),
    ("hidden", True, True, None, None, 1.0, 200, "a"),
    ("header", True, True, None, None, 1.0, 200, "a"),
    ("\xe9t\xe9", True, True, None, None, 1.0, 200, "a"),
    ("\x00", True, True, None, None, 1.0, 200, "a"),
    ("\U0001f642", True, True, None, None, 1.0, 200, "a"),
    (
        "\u0627\u0644\u0639\u0631\u0628\u064a\u0629",
        True,
        True,
        None,
        None,
        1.0,
        200,
        "a",
    ),
    (None, False, None, None, None, 1.0, 200, "a"),
    ([], False, False, None, None, 1.0, 200, "a"),
    ([1], True, True, None, None, 1.0, 200, "a"),
    (["x"], True, True, None, None, 1.0, 200, "a"),
    ({}, False, False, None, None, 1.0, 200, "abc"),
    ({"a": 1}, True, True, None, None, 1.0, 200, "abc"),
    ({"include": False}, True, True, None, None, 1.0, 200, "abc"),
]

# The same columns for the values 0.3.0 raised on, crashing the dialog: each
# now reads as a value that cannot be understood.
RAISED_IN_030 = [
    (HUGE, True, True, None, None, 1.0, HUGE, "a"),
    (-HUGE, True, True, None, None, 1.0, -HUGE, "a"),
    (INF, True, True, None, None, 1.0, 200, "a"),
    (-INF, True, True, None, None, 1.0, 200, "a"),
]

MAP_FLAGS = (
    "legend",
    "layerSwitcher",
    "zoomControls",
    "scaleBar",
    "popupOnHover",
    "showTitle",
    "showAbstract",
    "clipToExtent",
)
MAP_FLAG_FIELDS = (
    "show_legend",
    "show_layer_switcher",
    "show_zoom_controls",
    "show_scale_bar",
    "popup_on_hover",
    "show_title",
    "show_abstract",
    "clip_to_extent",
)


def _same(got: object, expected: object) -> bool:
    return type(got) is type(expected) and got == expected


def _map_wide_mismatches(project, rows) -> list[object]:
    wrong = []
    for raw, flag, _, precision, _, scale, _, _ in rows:
        widgets = dict.fromkeys(MAP_FLAGS, raw)
        widgets.update(chromeScale=raw, quantizePrecision=raw)
        _write(project, "widgets", widgets)
        state = load_state(project)
        got = [getattr(state, name) for name in MAP_FLAG_FIELDS]
        got += [state.quantize_precision, state.chrome_scale]
        expected = [flag] * len(MAP_FLAG_FIELDS) + [precision, scale]
        if not all(map(_same, got, expected)):
            wrong.append((raw, got, expected))
    return wrong


def _layer_mismatches(project, rows) -> list[object]:
    wrong = []
    for raw, flag, hover, _, precision, _, _, _ in rows:
        entry = dict.fromkeys(("include", "popup", "label", "cluster"), raw)
        entry.update(popupOnHover=raw, quantizePrecision=raw)
        _write(project, "layers", {"a": entry})
        layer = load_state(project).layers["a"]
        got = [layer.include, layer.popup, layer.label, layer.cluster]
        got += [layer.popup_on_hover, layer.quantize_precision]
        expected = [flag] * 4 + [hover, precision]
        if not all(map(_same, got, expected)):
            wrong.append((raw, got, expected))
    return wrong


def _dpi_mismatches(project, rows) -> list[object]:
    wrong = []
    for raw, *_, dpi, _ in rows:
        _write(project, "cartographFrames", {"L": {"i": {"dpi": raw}}})
        got = load_state(project).cartograph_frames["L"]["i"].dpi
        if not _same(got, dpi):
            wrong.append((raw, got, dpi))
    return wrong


class TestEveryValueAsIn030:
    """The whole corpus, one load per value; every mismatch is listed."""

    def test_map_wide_values(self, project) -> None:
        assert _map_wide_mismatches(project, AS_030) == []

    def test_layer_values(self, project) -> None:
        assert _layer_mismatches(project, AS_030) == []

    def test_frame_dpi(self, project) -> None:
        assert _dpi_mismatches(project, AS_030) == []

    def test_which_layers_load_around_a_value(self, project) -> None:
        wrong = []
        for raw, *_, loaded in AS_030 + RAISED_IN_030:
            _write(
                project,
                "layers",
                {"a": {"include": True}, "b": raw, "c": {"include": False}},
            )
            got = "".join(load_state(project).layers)
            if got != loaded:
                wrong.append((raw, got, loaded))
        assert wrong == []

    def test_missing_values_take_030s_defaults(self, project) -> None:
        _write(project, "widgets", {})
        _write(project, "layers", {"a": {}})
        _write(project, "cartographFrames", {"L": {"i": {}}})
        state = load_state(project)
        got = [getattr(state, name) for name in MAP_FLAG_FIELDS]
        assert got == [True, True, True, True, False, True, False, False]
        assert (state.chrome_scale, state.quantize_precision) == (1.0, None)
        layer = state.layers["a"]
        assert (layer.include, layer.popup, layer.label, layer.cluster) == (
            True,
            True,
            True,
            True,
        )
        assert (layer.popup_on_hover, layer.quantize_precision) == (None, None)
        assert state.cartograph_frames["L"]["i"].dpi == 200


class TestWhat030CouldNotRead:
    """Inputs 0.3.0 raised on: each now reads as a value not understood."""

    def test_huge_and_infinite_numbers(self, project) -> None:
        assert _map_wide_mismatches(project, RAISED_IN_030) == []
        assert _layer_mismatches(project, RAISED_IN_030) == []
        assert _dpi_mismatches(project, RAISED_IN_030) == []

    @pytest.mark.parametrize(
        "key",
        [
            "widgets",
            "layers",
            "cartographFiles",
            "cartographFrames",
            "cartographPage",
            "cartographPrint",
        ],
    )
    def test_json_nested_too_deep_is_ignored(self, project, key: str) -> None:
        project.writeEntry(SCOPE, key, "[" * 200000 + "]" * 200000)
        state = load_state(project)
        default = DialogState()
        assert state.layers == {}
        assert state.show_legend is default.show_legend
        assert state.cartograph_files == {}
        assert state.cartograph_frames == {}
        assert state.cartograph_page == {}
        assert state.cartograph_print == {}

    def test_a_huge_or_infinite_number_keeps_the_rest(self, project) -> None:
        _write(
            project,
            "widgets",
            {"chromeScale": HUGE, "quantizePrecision": INF, "basemap": "osm"},
        )
        _write(
            project,
            "layers",
            {"a": {"quantizePrecision": -INF}, "b": {"include": False}},
        )
        _write(project, "cartographFrames", {"L": {"i": {"dpi": INF}}})
        state = load_state(project)
        assert (state.chrome_scale, state.quantize_precision) == (1.0, None)
        assert state.basemap == "osm"
        assert state.layers["a"].quantize_precision is None
        assert state.layers["b"].include is False
        assert state.cartograph_frames["L"]["i"].dpi == 200
