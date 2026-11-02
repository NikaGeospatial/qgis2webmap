"""Saved settings load entry by entry: one bad value never costs the others.

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


def _write(project, key: str, value: object) -> None:
    project.writeEntry(SCOPE, key, json.dumps(value))


class TestLayerEntries:
    def test_a_malformed_entry_in_the_middle_keeps_the_valid_ones(
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
        assert layers["last"].include is False
        assert layers["last"].popup is False
        assert "broken" not in layers or layers["broken"].include is True

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
    def test_false_strings_read_as_false(self, project, text: str) -> None:
        _write(project, "layers", {"a": {"include": text, "label": text}})
        layer = load_state(project).layers["a"]
        assert layer.include is False
        assert layer.label is False

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

    def test_a_hover_override_of_false_text_is_false_not_inherited(
        self, project
    ) -> None:
        _write(project, "layers", {"a": {"popupOnHover": "false"}})
        assert load_state(project).layers["a"].popup_on_hover is False

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
    def test_false_strings_read_as_false(self, project) -> None:
        _write(
            project,
            "widgets",
            {"legend": "false", "scaleBar": "0", "showTitle": "no"},
        )
        state = load_state(project)
        assert state.show_legend is False
        assert state.show_scale_bar is False
        assert state.show_title is False

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
