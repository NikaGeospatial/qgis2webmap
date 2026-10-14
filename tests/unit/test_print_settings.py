"""Print settings (issue #5): size and bleed arithmetic, JSON, apply_to_spec.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import re

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    PAGE_SIZE_NAMES,
    Box,
    CartographSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.print_settings import (
    CARTOGRAPH_COLOR_PROFILE,
    DEFAULT_SAFE_ZONE_MM,
    PAGE_CUSTOM,
    PAGE_FROM_LAYOUT,
    PAGE_PRESETS_MM,
    PROFILE_CMYK,
    PROFILE_ICC,
    PROFILE_RGB,
    PrintSettings,
    apply_to_spec,
    crop_mark_segments,
    parse_version,
    preset_size_mm,
    presets_from_json,
    presets_to_json,
    qt_supports_colour_management,
)
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)

A4_LANDSCAPE = (297.0, 210.0)


def _spec() -> CartographSpec:
    return CartographSpec(
        cartograph_id="cg-test",
        width=297.0,
        height=210.0,
        orientation="landscape",
        items=(TextSpec(id="t", box=Box(10, 10, 50, 10), body="Hi"),),
    )


class TestSizes:
    def test_presets_cover_exactly_the_runtimes_sizes(self) -> None:
        assert set(PAGE_PRESETS_MM) == set(PAGE_SIZE_NAMES)

    def test_a3_with_3mm_bleed_prints_303_by_426(self) -> None:
        settings = PrintSettings(page="A3", orientation="portrait", bleed="3mm")
        assert settings.sheet_size_mm(A4_LANDSCAPE) == (303.0, 426.0)

    def test_landscape_swaps_the_preset(self) -> None:
        assert preset_size_mm("A3", "landscape") == (420.0, 297.0)
        settings = PrintSettings(page="A3", orientation="landscape", bleed="5mm")
        assert settings.sheet_size_mm(A4_LANDSCAPE) == (430.0, 307.0)

    def test_eighth_inch_bleed_is_3_175_mm(self) -> None:
        settings = PrintSettings(page="Letter", bleed="eighth_inch")
        assert settings.bleed_mm == pytest.approx(3.175)
        width, height = settings.sheet_size_mm(A4_LANDSCAPE)
        # US Letter + 1/8 in each side = 8.75 x 11.25 in.
        assert width == pytest.approx(8.75 * 25.4)
        assert height == pytest.approx(11.25 * 25.4)

    def test_from_layout_uses_the_layout_page(self) -> None:
        settings = PrintSettings(page=PAGE_FROM_LAYOUT, bleed="none")
        assert settings.trim_size_mm(A4_LANDSCAPE) == A4_LANDSCAPE
        assert settings.sheet_size_mm(A4_LANDSCAPE) == A4_LANDSCAPE

    def test_custom_size(self) -> None:
        settings = PrintSettings(
            page=PAGE_CUSTOM, custom_width_mm=500, custom_height_mm=700, bleed="3mm"
        )
        assert settings.sheet_size_mm(A4_LANDSCAPE) == (506.0, 706.0)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"page": "B5"},
            {"orientation": "sideways"},
            {"dpi": 72},
            {"bleed": "2mm"},
            {"profile": "lab"},
            {"profile": PROFILE_ICC, "icc_path": ""},
            {"custom_width_mm": 0.0},
            {"custom_height_mm": float("nan")},
            {"custom_width_mm": 1e9},
        ],
    )
    def test_rejects_impossible_values(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            PrintSettings(**kwargs)  # type: ignore[arg-type]


class TestCropMarks:
    def test_crop_marks_need_a_bleed(self) -> None:
        assert PrintSettings(bleed="3mm", crop_marks=True).crop_marks_effective
        assert not PrintSettings(bleed="none", crop_marks=True).crop_marks_effective
        assert not PrintSettings(bleed="3mm", crop_marks=False).crop_marks_effective

    def test_segments_mirror_the_runtime(self) -> None:
        segments = crop_mark_segments(100.0, 50.0, 3.0)
        assert len(segments) == 8
        # Top-left corner: gap min(3*0.35, 1.5) = 1.05, then the stroke runs
        # to the sheet edge (the runtime's SVG clips its overshoot there).
        assert segments[0] == pytest.approx((-1.05, 0.0, -3.0, 0.0))
        assert segments[1] == pytest.approx((0.0, -1.05, 0.0, -3.0))
        # Every stroke stays outside the trim and inside the bleed.
        for x1, y1, x2, y2 in segments:
            for x, y in ((x1, y1), (x2, y2)):
                assert -3.0 - 1e-9 <= x <= 103.0 + 1e-9
                assert -3.0 - 1e-9 <= y <= 53.0 + 1e-9
                assert not (0 < x < 100 and 0 < y < 50)

    def test_long_marks_are_capped(self) -> None:
        x1, _, x2, _ = crop_mark_segments(100.0, 50.0, 10.0)[0]
        assert x1 == pytest.approx(-1.5)
        assert x2 == pytest.approx(-5.5)

    def test_no_bleed_no_marks(self) -> None:
        assert crop_mark_segments(100.0, 50.0, 0.0) == ()


class TestJson:
    def test_round_trip(self) -> None:
        settings = PrintSettings(
            page=PAGE_CUSTOM,
            custom_width_mm=333.5,
            custom_height_mm=444.0,
            dpi=600,
            bleed="eighth_inch",
            crop_marks=False,
            safe_zone=False,
            profile=PROFILE_ICC,
            icc_path="/tmp/fogra39.icc",
            flatten=True,
            embed_fonts=False,
        )
        assert PrintSettings.from_json(settings.to_json()) == settings

    def test_not_an_object_is_rejected(self) -> None:
        for text in ("", "not json", "[1, 2]", "42", "null"):
            with pytest.raises(ValueError):
                PrintSettings.from_json(text)

    def test_bad_fields_are_repaired_not_fatal(self) -> None:
        text = json.dumps(
            {
                "dpi": "300",  # string, not a number
                "bleed": "9mm",  # unknown choice
                "crop_marks": 1,  # int, not a bool
                "page": "A2",  # good
                "custom_width_mm": -5,  # out of range
                "future_field": True,  # ignored
            }
        )
        settings, repaired = PrintSettings.from_dict(json.loads(text))
        assert settings.page == "A2"
        assert settings.dpi == 300 and settings.bleed == "3mm"
        assert settings.crop_marks is True
        assert set(repaired) == {"dpi", "bleed", "crop_marks", "custom_width_mm"}
        assert PrintSettings.from_json(text) == settings

    def test_integral_float_dpi_is_accepted(self) -> None:
        assert PrintSettings.from_json('{"dpi": 600.0}').dpi == 600

    def test_icc_without_a_path_falls_back_to_rgb(self) -> None:
        settings, repaired = PrintSettings.from_dict({"profile": "icc", "icc_path": 7})
        assert settings.profile == PROFILE_RGB
        assert "profile" in repaired and "icc_path" in repaired


class TestPresets:
    def test_round_trip(self) -> None:
        presets = {
            "Print shop A3": PrintSettings(page="A3", bleed="3mm", dpi=300),
            "US poster": PrintSettings(
                page="Letter", bleed="eighth_inch", profile=PROFILE_CMYK
            ),
        }
        assert presets_from_json(presets_to_json(presets)) == presets

    @pytest.mark.parametrize(
        "text",
        [None, 5, "", "{", "[]", '{"presets": []}', '{"presets": "x"}'],
    )
    def test_corrupt_store_reads_as_empty(self, text: object) -> None:
        assert presets_from_json(text) == {}

    def test_corrupt_entries_are_dropped_or_repaired(self) -> None:
        text = json.dumps(
            {
                "presets": {
                    "good": {"page": "A1"},
                    "": {"page": "A2"},
                    "   ": {"page": "A2"},
                    "not an object": [1],
                    "bad dpi": {"dpi": 1},
                }
            }
        )
        presets = presets_from_json(text)
        assert set(presets) == {"good", "bad dpi"}
        assert presets["good"].page == "A1"
        assert presets["bad dpi"].dpi == 300


class TestApplyToSpec:
    def test_print_attributes(self) -> None:
        spec = apply_to_spec(
            _spec(),
            PrintSettings(
                bleed="3mm", crop_marks=True, safe_zone=True, dpi=600, flatten=True
            ),
        )
        assert spec.bleed == 3.0
        assert spec.crop_marks is True
        assert spec.safe_zone == DEFAULT_SAFE_ZONE_MM
        assert spec.dpi == 600
        assert spec.flatten is True
        assert spec.color_profile == CARTOGRAPH_COLOR_PROFILE
        # From layout: the page box is untouched.
        assert (spec.width, spec.height, spec.size) == (297.0, 210.0, None)
        assert spec.items == _spec().items

    def test_crop_marks_are_dropped_without_a_bleed(self) -> None:
        # CartographSpec itself rejects crop marks without a bleed, so this
        # would raise if apply_to_spec passed the preference straight through.
        spec = apply_to_spec(_spec(), PrintSettings(bleed="none", crop_marks=True))
        assert spec.bleed is None
        assert spec.crop_marks is False

    def test_safe_zone_off(self) -> None:
        assert apply_to_spec(_spec(), PrintSettings(safe_zone=False)).safe_zone is None

    def test_preset_page_replaces_dimensions(self) -> None:
        spec = apply_to_spec(_spec(), PrintSettings(page="A3", orientation="portrait"))
        assert (spec.size, spec.width, spec.height) == ("A3", None, None)
        assert spec.orientation == "portrait"

    def test_custom_page(self) -> None:
        spec = apply_to_spec(
            _spec(),
            PrintSettings(page=PAGE_CUSTOM, custom_width_mm=400, custom_height_mm=300),
        )
        assert (spec.size, spec.width, spec.height) == (None, 400.0, 300.0)
        assert spec.orientation == "landscape"

    def test_written_root_carries_the_attributes(self) -> None:
        spec = apply_to_spec(
            _spec(), PrintSettings(page="A3", bleed="eighth_inch", dpi=300)
        )
        html = write_cartograph_html(
            spec, CartographRuntimeBundle(cartograph_js=b"x", cartograph_css="")
        )
        match = re.search(r"<om-cartograph[^>]*>", html)
        assert match is not None
        root = match.group(0)
        assert 'size="A3"' in root
        assert 'bleed="3.175"' in root
        assert "crop-marks" in root
        assert 'dpi="300"' in root
        assert 'color-profile="sRGB"' in root


class TestQtVersion:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("6.8.0", (6, 8, 0)),
            ("5.15.13", (5, 15, 13)),
            ("6.11.2-beta", (6, 11, 2)),
            ("", ()),
        ],
    )
    def test_parse(self, text: str, expected: tuple[int, ...]) -> None:
        assert parse_version(text) == expected

    @pytest.mark.parametrize(
        ("text", "supported"),
        [
            ("6.8.0", True),
            ("6.11.2", True),
            ("6.7.3", False),
            ("5.15.13", False),
            ("", False),
        ],
    )
    def test_colour_management_needs_qt_6_8(self, text: str, supported: bool) -> None:
        assert qt_supports_colour_management(text) is supported
