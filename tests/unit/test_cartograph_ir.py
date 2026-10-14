"""`core/cartograph_ir.py`: the frozen dataclasses, no qgis/Qt import.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    AtlasSpec,
    Box,
    CartographSpec,
    FrameSpec,
    GraticuleSpec,
    ImageSpec,
    LegendRowSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)


def test_module_imports_no_qgis_or_qt() -> None:
    import nika_onlymap_exporter.core.cartograph_ir as mod

    with open(mod.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "import qgis" not in source
    assert "from qgis" not in source
    assert "PyQt" not in source


# --------------------------------------------------------------------------
# Box
# --------------------------------------------------------------------------


class TestBox:
    def test_valid(self) -> None:
        box = Box(x=10, y=24, w=150, h=150)
        assert box.w == 150

    @pytest.mark.parametrize("w,h", [(0, 10), (-5, 10), (10, 0), (10, -5)])
    def test_rejects_non_positive_size(self, w: float, h: float) -> None:
        with pytest.raises(ValueError):
            Box(x=0, y=0, w=w, h=h)

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError):
            Box(x=float("nan"), y=0, w=10, h=10)

    def test_rejects_infinite(self) -> None:
        with pytest.raises(ValueError):
            Box(x=0, y=0, w=float("inf"), h=10)


# --------------------------------------------------------------------------
# FrameSpec
# --------------------------------------------------------------------------


def _live_frame(id_: str = "main") -> FrameSpec:
    return FrameSpec(
        id=id_,
        box=Box(10, 24, 150, 150),
        mode="live",
        center=(103.81, 1.31),
        zoom=14,
    )


def _static_frame(id_: str = "main") -> FrameSpec:
    return FrameSpec(
        id=id_,
        box=Box(10, 24, 150, 150),
        mode="static",
        crs="EPSG:32648",
        crs_def="+proj=utm +zone=48",
        corners=((103.8, 1.32), (103.82, 1.32), (103.82, 1.30), (103.8, 1.30)),
        image_bytes=b"fake-png",
    )


class TestFrameSpec:
    def test_live_frame(self) -> None:
        frame = _live_frame()
        assert frame.mode == "live"

    def test_static_frame(self) -> None:
        frame = _static_frame()
        assert frame.crs == "EPSG:32648"

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(id="main", box=Box(0, 0, 10, 10), mode="sideways")

    def test_live_frame_rejects_static_fields(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(
                id="main",
                box=Box(0, 0, 10, 10),
                mode="live",
                crs="EPSG:3857",
            )

    def test_static_frame_rejects_live_fields(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(
                id="main",
                box=Box(0, 0, 10, 10),
                mode="static",
                center=(0, 0),
                crs="EPSG:3857",
                corners=((0, 1), (1, 1), (1, 0), (0, 0)),
                image_bytes=b"x",
            )

    def test_static_frame_needs_image(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(
                id="main",
                box=Box(0, 0, 10, 10),
                mode="static",
                crs="EPSG:3857",
                corners=((0, 1), (1, 1), (1, 0), (0, 0)),
            )

    def test_static_frame_needs_exactly_four_corners(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(
                id="main",
                box=Box(0, 0, 10, 10),
                mode="static",
                crs="EPSG:3857",
                corners=((0, 1), (1, 1), (1, 0)),  # type: ignore[arg-type]
                image_bytes=b"x",
            )

    def test_static_frame_rejects_non_finite_corner(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(
                id="main",
                box=Box(0, 0, 10, 10),
                mode="static",
                crs="EPSG:3857",
                corners=(
                    (float("nan"), 1),
                    (1, 1),
                    (1, 0),
                    (0, 0),
                ),
                image_bytes=b"x",
            )

    def test_image_path_is_accepted_without_bytes(self) -> None:
        frame = FrameSpec(
            id="main",
            box=Box(0, 0, 10, 10),
            mode="static",
            crs="EPSG:3857",
            corners=((0, 1), (1, 1), (1, 0), (0, 0)),
            image_path="main.frames/main.png",
        )
        assert frame.image_path is not None

    def test_overview_fill_does_not_exist(self) -> None:
        # The runtime has no overview-fill attribute -- only overview-stroke.
        # This is a documentation test: it would fail to construct if a
        # future edit resurrected `overview_fill` on FrameSpec.
        assert not hasattr(_live_frame(), "overview_fill")

    def test_unknown_atlas_fit_rejected(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(
                id="main",
                box=Box(0, 0, 10, 10),
                mode="live",
                atlas_fit="zoomed",
            )

    def test_negative_atlas_margin_rejected(self) -> None:
        with pytest.raises(ValueError):
            FrameSpec(id="main", box=Box(0, 0, 10, 10), mode="live", atlas_margin=-0.1)


# --------------------------------------------------------------------------
# GraticuleSpec / ScalebarSpec / NorthSpec / LegendSpec
# --------------------------------------------------------------------------


class TestGraticuleSpec:
    def test_valid(self) -> None:
        spec = GraticuleSpec(
            id="grid", box=Box(0, 0, 10, 10), for_="main", interval=500
        )
        assert spec.kind == "solid"

    def test_requires_for(self) -> None:
        with pytest.raises(ValueError):
            GraticuleSpec(id="grid", box=Box(0, 0, 10, 10))

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            GraticuleSpec(id="grid", box=Box(0, 0, 10, 10), for_="main", kind="zebra")

    def test_unknown_format_rejected(self) -> None:
        with pytest.raises(ValueError):
            GraticuleSpec(
                id="grid", box=Box(0, 0, 10, 10), for_="main", format="decimal"
            )

    def test_non_positive_interval_rejected(self) -> None:
        with pytest.raises(ValueError):
            GraticuleSpec(id="grid", box=Box(0, 0, 10, 10), for_="main", interval=0)


class TestLegendSpec:
    def test_derived_default(self) -> None:
        legend = LegendSpec(id="lg", box=Box(0, 0, 10, 10), for_="main")
        assert legend.derived is True

    def test_non_derived_needs_rows(self) -> None:
        with pytest.raises(ValueError):
            LegendSpec(id="lg", box=Box(0, 0, 10, 10), for_="main", derived=False)

    def test_non_derived_with_rows(self) -> None:
        legend = LegendSpec(
            id="lg",
            box=Box(0, 0, 10, 10),
            for_="main",
            derived=False,
            rows=(LegendRowSpec(label="Parcels", color="#eba941"),),
        )
        assert legend.rows[0].label == "Parcels"

    def test_requires_for(self) -> None:
        with pytest.raises(ValueError):
            LegendSpec(id="lg", box=Box(0, 0, 10, 10))

    def test_non_positive_columns_rejected(self) -> None:
        with pytest.raises(ValueError):
            LegendSpec(id="lg", box=Box(0, 0, 10, 10), for_="main", columns=0)


class TestScalebarSpec:
    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScalebarSpec(id="bar", box=Box(0, 0, 10, 10), for_="main", kind="fancy")

    def test_unknown_units_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScalebarSpec(id="bar", box=Box(0, 0, 10, 10), for_="main", units="furlongs")


class TestNorthSpec:
    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            NorthSpec(id="north", box=Box(0, 0, 10, 10), for_="main", kind="compass")


# --------------------------------------------------------------------------
# TextSpec / ImageSpec / ShapeSpec
# --------------------------------------------------------------------------


class TestTextSpec:
    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            TextSpec(id="t", box=Box(0, 0, 10, 10), kind="heading")

    def test_tokens_pass_through_as_plain_body(self) -> None:
        text = TextSpec(id="t", box=Box(0, 0, 10, 10), body="{{title}}")
        assert text.body == "{{title}}"


class TestImageSpec:
    def test_requires_src(self) -> None:
        with pytest.raises(ValueError):
            ImageSpec(id="logo", box=Box(0, 0, 10, 10), src="")

    def test_unknown_fit_rejected(self) -> None:
        with pytest.raises(ValueError):
            ImageSpec(id="logo", box=Box(0, 0, 10, 10), src="logo.png", fit="stretch")


class TestShapeSpec:
    def test_there_is_no_path_kind(self) -> None:
        # Issue #1 §2 proposed om-shape kind="path"; the runtime schema has
        # no such thing -- only rect/ellipse/line/arrow.
        with pytest.raises(ValueError):
            ShapeSpec(id="s", box=Box(0, 0, 10, 10), kind="path")

    def test_fill_opacity_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            ShapeSpec(id="s", box=Box(0, 0, 10, 10), fill_opacity=1.5)

    def test_negative_stroke_width_rejected(self) -> None:
        with pytest.raises(ValueError):
            ShapeSpec(id="s", box=Box(0, 0, 10, 10), stroke_width=-1)


# --------------------------------------------------------------------------
# AtlasSpec
# --------------------------------------------------------------------------


class TestAtlasSpec:
    def test_requires_for_and_layer(self) -> None:
        with pytest.raises(ValueError):
            AtlasSpec(for_="main")
        with pytest.raises(ValueError):
            AtlasSpec(layer="parcels")

    def test_unknown_pager_rejected(self) -> None:
        with pytest.raises(ValueError):
            AtlasSpec(for_="main", layer="parcels", pager="sidebar")


# --------------------------------------------------------------------------
# CartographSpec
# --------------------------------------------------------------------------


class TestCartographSpec:
    def test_minimal_named_size(self) -> None:
        spec = CartographSpec(cartograph_id="cg-1", size="A4")
        assert spec.size == "A4"

    def test_minimal_explicit_dimensions(self) -> None:
        spec = CartographSpec(cartograph_id="cg-1", width=210, height=297)
        assert spec.width == 210

    def test_requires_cartograph_id(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="", size="A4")

    def test_rejects_both_size_and_dimensions(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", size="A4", width=210, height=297)

    def test_rejects_neither_size_nor_dimensions(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1")

    def test_rejects_width_without_height(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", width=210)

    def test_unknown_size_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", size="A4.5")

    def test_unknown_theme_rejected(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", size="A4", theme="neon")

    def test_unknown_orientation_rejected(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", size="A4", orientation="sideways")

    def test_duplicate_item_id_rejected(self) -> None:
        frame = _live_frame("main")
        text = TextSpec(id="main", box=Box(0, 0, 10, 10), body="dup id")
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", size="A4", items=(frame, text))

    def test_dangling_for_reference_rejected(self) -> None:
        bar = ScalebarSpec(id="bar", box=Box(0, 0, 10, 10), for_="ghost")
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", size="A4", items=(bar,))

    def test_dangling_overview_of_rejected(self) -> None:
        main = _live_frame("main")
        locator = FrameSpec(
            id="locator",
            box=Box(150, 0, 30, 20),
            mode="live",
            center=(103.81, 1.31),
            zoom=9,
            overview_of="ghost",
        )
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg-1", size="A4", items=(main, locator))

    def test_valid_overview_reference(self) -> None:
        main = _live_frame("main")
        locator = FrameSpec(
            id="locator",
            box=Box(150, 0, 30, 20),
            mode="live",
            center=(103.81, 1.31),
            zoom=9,
            overview_of="main",
        )
        spec = CartographSpec(cartograph_id="cg-1", size="A4", items=(main, locator))
        assert len(spec.items) == 2

    def test_dangling_atlas_reference_rejected(self) -> None:
        with pytest.raises(ValueError):
            CartographSpec(
                cartograph_id="cg-1",
                size="A4",
                atlas=AtlasSpec(for_="ghost", layer="parcels"),
            )

    def test_valid_atlas_reference(self) -> None:
        main = _static_frame("main")
        spec = CartographSpec(
            cartograph_id="cg-1",
            size="A4",
            items=(main,),
            atlas=AtlasSpec(for_="main", layer="parcels"),
        )
        assert spec.atlas is not None

    def test_full_spec_round_trips(self) -> None:
        main = _static_frame("main")
        grid = GraticuleSpec(
            id="grid", box=Box(10, 24, 150, 150), for_="main", interval=500
        )
        legend = LegendSpec(
            id="lg",
            box=Box(165, 24, 35, 60),
            for_="main",
            derived=False,
            rows=(LegendRowSpec(label="Parcels", color="#eba941"),),
        )
        bar = ScalebarSpec(
            id="bar", box=Box(10, 180, 70, 9), for_="main", kind="double"
        )
        north = NorthSpec(id="north", box=Box(186, 178, 14, 18), for_="main")
        title = TextSpec(
            id="title", box=Box(10, 8, 150, 12), kind="title", body="{{title}}"
        )
        shape = ShapeSpec(
            id="frame-border", box=Box(9, 23, 192, 192), fill=None, stroke="#000"
        )
        image = ImageSpec(
            id="logo", box=Box(10, 2, 20, 5), src="data:image/png;base64,AA=="
        )
        spec = CartographSpec(
            cartograph_id="cg-survey",
            title="Lot 12 survey",
            attribution="(c) Survey Dept",
            theme="minimal",
            size="A4",
            orientation="landscape",
            items=(main, grid, legend, bar, north, title, shape, image),
        )
        assert len(spec.items) == 8
