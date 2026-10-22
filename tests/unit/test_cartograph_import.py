"""`core/cartograph_import.py`: a cartograph HTML file back into a spec.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from nika_onlymap_exporter.core.cartograph_import import (
    CartographImportError,
    parse_cartograph_html,
    read_cartograph_file,
)
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
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)

_RUNTIME = CartographRuntimeBundle(
    cartograph_js=b"// carto",
    cartograph_css="/* carto */",
    core_js=b"// core",
    core_css="/* core */",
)

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))

_LIVE_MAP_MARKUP = (
    '<om-map center="[0,0]" zoom="3"><om-layer id="l1" type="geojson">'
    '<script type="application/json">{"type": "FeatureCollection", '
    '"features": [{"a": "<b>"}]}</script></om-layer></om-map>'
)


def _full_spec() -> CartographSpec:
    """Every item kind and every optional attribute the writer emits, with
    numbers the writer's 4-decimal formatting reproduces exactly."""
    return CartographSpec(
        cartograph_id="survey-utm",
        title="Survey & co <UTM>",
        attribution="© Survey Dept",
        theme="sepia",
        width=297.0,
        height=210.0,
        orientation="landscape",
        bg="#fffef8",
        color="#222222",
        font="Inter",
        notes="hand-edited",
        bleed=3.0,
        crop_marks=True,
        safe_zone=5.0,
        dpi=300,
        flatten=True,
        color_profile="FOGRA39",
        cvd="deuteranopia",
        items=(
            FrameSpec(
                id="main",
                box=Box(10.0, 24.0, 180.0, 128.5),
                mode="static",
                z=1,
                crs="EPSG:32648",
                crs_def="+proj=utm +zone=48 +datum=WGS84 +units=m +no_defs",
                corners=(
                    (103.7812345, 1.3312345),
                    (103.8012345, 1.3312345),
                    (103.8012345, 1.3112345),
                    (103.7812345, 1.3112345),
                ),
                image_bytes=_PNG,
                dpi=200.0,
                frame_stroke="#000000",
                frame_bg="#ffffff",
                attribution="© Basemap",
            ),
            FrameSpec(
                id="inset",
                box=Box(195.0, 24.0, 40.0, 40.0),
                rotation=12.5,
                z=2,
                locked=True,
                center=(103.8, 1.35),
                zoom=9.25,
                bearing=15.0,
                pitch=10.0,
                overview_of="main",
                overview_stroke="#ff0000",
                atlas_fit="feature",
                atlas_margin=0.25,
            ),
            GraticuleSpec(
                id="grid",
                box=Box(10.0, 24.0, 180.0, 128.5),
                for_="main",
                crs="EPSG:32648",
                interval_x=500.0,
                interval_y=250.0,
                kind="cross",
                labels="inside",
                format="dms",
                color="#333333",
                z=3,
            ),
            LegendSpec(
                id="legend",
                box=Box(195.0, 70.0, 40.0, 30.0),
                for_="inset",
                title="Legend",
                columns=2,
                bg="#ffffff",
                color="#000000",
                stroke="#999999",
                font_size=8.0,
                padding=2.0,
                rows=(
                    LegendRowSpec(layer="parcels", match="A", label="Lot A", order=1),
                ),
                z=4,
            ),
            LegendSpec(
                id="static-legend",
                box=Box(195.0, 101.0, 40.0, 10.0),
                for_="main",
                derived=False,
                rows=(
                    LegendRowSpec(
                        layer="parcels",
                        label="parcels",
                        color="#eba941",
                        shape="square",
                    ),
                    LegendRowSpec(label="hidden row", hide=True),
                ),
                hidden=True,
            ),
            ScalebarSpec(
                id="bar",
                box=Box(10.0, 168.0, 70.0, 9.0),
                for_="main",
                units="imperial",
                segments=4,
                segment_length=250.0,
                kind="double",
                color="#111111",
            ),
            NorthSpec(
                id="north",
                box=Box(195.0, 115.0, 15.0, 15.0),
                for_="main",
                kind="rose",
                color="#222222",
                rotation=-3.0,
            ),
            TextSpec(
                id="title",
                box=Box(10.0, 5.0, 180.0, 12.0),
                body="{{title}} — 1:{{frame.inset.scale}} <&> {{date}}",
                kind="title",
                font_size=18.0,
                weight="bold",
                align="center",
                font="Inter",
                color="#000000",
                bg="#eeeeee",
            ),
            ImageSpec(
                id="logo",
                box=Box(250.0, 5.0, 30.0, 12.0),
                src="data:image/svg+xml;base64,PHN2Zy8+",
                fit="cover",
            ),
            ShapeSpec(
                id="box",
                box=Box(160.0, 5.0, 20.0, 10.0),
                kind="ellipse",
                fill="#ff0000",
                fill_opacity=0.5,
                stroke="#000000",
                stroke_width=0.3,
                radius=1.5,
            ),
        ),
        atlas=AtlasSpec(
            for_="inset",
            layer="parcels",
            id="pages",
            filter="$area > 500",
            sort="-lot",
            filename="lot-{{atlas.lot}}",
            page_name="{{atlas.name}}",
            pager="none",
        ),
    )


def _write(spec: CartographSpec, live: dict[str, str] | None = None) -> str:
    return write_cartograph_html(spec, _RUNTIME, live)


def _subjects(result) -> list[str]:
    return [issue.subject for issue in result.issues]


class TestWriterRoundTrip:
    def test_every_item_and_attribute_comes_back_equal(self) -> None:
        spec = _full_spec()
        result = parse_cartograph_html(_write(spec))
        assert result.spec == spec
        assert result.issues == ()

    def test_static_frame_image_is_decoded_to_bytes(self) -> None:
        result = parse_cartograph_html(_write(_full_spec()))
        main = next(i for i in result.spec.items if i.id == "main")
        assert isinstance(main, FrameSpec)
        assert main.image_bytes == _PNG
        assert main.image_path is None

    def test_embedded_live_map_is_skipped_with_one_note(self) -> None:
        spec = _full_spec()
        result = parse_cartograph_html(_write(spec, {"inset": _LIVE_MAP_MARKUP}))
        assert result.spec == spec
        assert len(result.issues) == 1
        assert "inset" in result.issues[0].subject
        assert "layers" in result.issues[0].detail

    def test_size_preset_round_trips(self) -> None:
        spec = CartographSpec(
            cartograph_id="a", size="A3", orientation="portrait", title="t"
        )
        assert parse_cartograph_html(_write(spec)).spec == spec


_HAND_WRITTEN = """<!doctype html>
<html><head><meta charset="utf-8"></head>
<body>
<om-cartograph cartograph-id="cg-lot-12" format="cartograph/2" size="A4" theme="minimal"
          title="Lot 12 &mdash; parcel survey" attribution="&copy; Survey Dept 2026">
  <om-text id="title" kind="title" x="10" y="8" w="150" h="12">
     {{title}}
  </om-text>
  <!-- a comment -->
  <om-frame id="main" mode="static" x="10" y="24" w="190" h="190"
            crs="EPSG:3857"
            corners="[[103.80,1.32],[103.82,1.32],[103.82,1.30],[103.80,1.30]]"
            style="left:10mm;top:24mm;width:190mm;height:190mm">
    <img src="lot-12.png" alt="">
  </om-frame>
  <om-scalebar id="bar" for="main" x="10" y="222" w="70" h="9"
               kind="double"></om-scalebar>
  <om-north id="north" for="main" x="186" y="220" w="14" h="18" kind="rose"
            locked hidden></om-north>
</om-cartograph>
</body></html>
"""


class TestHandWritten:
    def test_bare_cartograph_in_body(self) -> None:
        result = parse_cartograph_html(_HAND_WRITTEN)
        spec = result.spec
        assert spec.cartograph_id == "cg-lot-12"
        assert spec.size == "A4" and spec.theme == "minimal"
        assert spec.title == "Lot 12 — parcel survey"
        assert spec.attribution == "© Survey Dept 2026"
        assert [type(i).__name__ for i in spec.items] == [
            "TextSpec",
            "FrameSpec",
            "ScalebarSpec",
            "NorthSpec",
        ]
        assert result.issues == ()

    def test_text_body_whitespace_is_trimmed(self) -> None:
        spec = parse_cartograph_html(_HAND_WRITTEN).spec
        assert spec.items[0].body == "{{title}}"  # type: ignore[union-attr]

    def test_relative_image_becomes_image_path(self) -> None:
        frame = parse_cartograph_html(_HAND_WRITTEN).spec.items[1]
        assert isinstance(frame, FrameSpec)
        assert frame.image_path == "lot-12.png" and frame.image_bytes is None
        assert frame.corners == (
            (103.80, 1.32),
            (103.82, 1.32),
            (103.82, 1.30),
            (103.80, 1.30),
        )

    def test_valueless_booleans(self) -> None:
        north = parse_cartograph_html(_HAND_WRITTEN).spec.items[3]
        assert isinstance(north, NorthSpec)
        assert north.locked and north.hidden


def _page(body: str, root_attrs: str = 'cartograph-id="c" size="A4"') -> str:
    return f"<body><om-cartograph {root_attrs}>{body}</om-cartograph></body>"


_FRAME = (
    '<om-frame id="f" x="0" y="0" w="10" h="10" center="[1,2]" zoom="3"></om-frame>'
)


class TestTolerance:
    def test_no_cartograph_is_an_error(self) -> None:
        with pytest.raises(CartographImportError):
            parse_cartograph_html("<html><body><p>hello</p></body></html>")

    def test_unknown_attribute_is_reported(self) -> None:
        result = parse_cartograph_html(
            _page(
                '<om-frame id="f" x="0" y="0" w="10" h="10" data-nika="1"></om-frame>'
            )
        )
        assert len(result.spec.items) == 1
        assert any("data-nika" in issue.detail for issue in result.issues)

    def test_the_plugins_own_attributes_are_not_reported(self) -> None:
        # Derived data the export writes again on every save.
        result = parse_cartograph_html(
            _page(
                '<om-frame id="f" x="0" y="0" w="10" h="10" atlas-fit="fixed" '
                "data-qgis2webmap-atlas-cameras='{\"7\":[103.8,1.3,15]}'>"
                "</om-frame>"
            )
        )
        assert not any("qgis2webmap" in issue.detail for issue in result.issues)

    def test_a_live_map_drawn_in_a_plane_keeps_its_crs(self) -> None:
        result = parse_cartograph_html(
            _page(
                _FRAME.replace(
                    "></om-frame>",
                    '><om-map crs="EPSG:32648" '
                    'crs-def="+proj=utm +zone=48 +datum=WGS84 +units=m +no_defs">'
                    "</om-map></om-frame>",
                )
            )
        )
        frame = result.spec.items[0]
        assert isinstance(frame, FrameSpec)
        assert frame.plane_crs == "EPSG:32648"
        assert (
            frame.plane_crs_def == "+proj=utm +zone=48 +datum=WGS84 +units=m +no_defs"
        )

    def test_a_live_map_in_web_mercator_has_no_plane(self) -> None:
        result = parse_cartograph_html(
            _page(_FRAME.replace("></om-frame>", "><om-map></om-map></om-frame>"))
        )
        frame = result.spec.items[0]
        assert isinstance(frame, FrameSpec)
        assert frame.plane_crs is None and frame.plane_crs_def is None

    def test_unknown_root_attribute_is_reported(self) -> None:
        result = parse_cartograph_html(
            _page("", 'cartograph-id="c" size="A4" keep-badge sparkle="yes"')
        )
        details = " ".join(issue.detail for issue in result.issues)
        assert "keep-badge" in details and "sparkle" in details

    def test_unknown_element_is_reported(self) -> None:
        result = parse_cartograph_html(_page(_FRAME + '<om-chart id="c"></om-chart>'))
        assert len(result.spec.items) == 1
        assert any("om-chart" in issue.detail for issue in result.issues)

    def test_om_source_is_reported(self) -> None:
        result = parse_cartograph_html(
            _page(
                '<om-frame id="f" x="0" y="0" w="10" h="10">'
                '<om-source src="m.html"></om-source></om-frame>'
            )
        )
        assert any("om-source" in issue.detail for issue in result.issues)

    def test_bad_number_skips_the_item(self) -> None:
        result = parse_cartograph_html(
            _page(_FRAME + '<om-text id="t" x="a" y="0" w="1" h="1">x</om-text>')
        )
        assert [i.id for i in result.spec.items] == ["f"]
        assert any("t" in issue.subject for issue in result.issues)

    def test_missing_box_skips_the_item(self) -> None:
        result = parse_cartograph_html(_page('<om-text id="t" x="0">x</om-text>'))
        assert result.spec.items == ()
        assert result.issues

    def test_dangling_for_drops_the_item(self) -> None:
        result = parse_cartograph_html(
            _page(
                _FRAME
                + '<om-scalebar id="s" for="nope" x="0" y="0" w="1" h="1">'
                + "</om-scalebar>"
            )
        )
        assert [i.id for i in result.spec.items] == ["f"]
        assert any("nope" in issue.detail for issue in result.issues)

    def test_dropped_frame_takes_its_furniture_with_it(self) -> None:
        result = parse_cartograph_html(
            _page(
                '<om-frame id="f" mode="static" x="0" y="0" w="1" h="1"></om-frame>'
                '<om-north id="n" for="f" x="0" y="0" w="1" h="1"></om-north>'
            )
        )
        assert result.spec.items == ()
        assert len(result.issues) == 2

    def test_dangling_overview_is_cleared(self) -> None:
        result = parse_cartograph_html(
            _page(
                '<om-frame id="f" x="0" y="0" w="10" h="10" overview-of="x"></om-frame>'
            )
        )
        frame = result.spec.items[0]
        assert isinstance(frame, FrameSpec) and frame.overview_of is None
        assert result.issues

    def test_duplicate_id_is_renamed(self) -> None:
        result = parse_cartograph_html(
            _page(
                '<om-text id="t" x="0" y="0" w="1" h="1">a</om-text>'
                '<om-text id="t" x="0" y="0" w="1" h="1">b</om-text>'
            )
        )
        assert [i.id for i in result.spec.items] == ["t", "t-2"]
        assert result.issues

    def test_missing_id_is_generated(self) -> None:
        result = parse_cartograph_html(
            _page('<om-text x="0" y="0" w="1" h="1">a</om-text>')
        )
        assert result.spec.items[0].id == "text-1"
        assert result.issues

    def test_missing_cartograph_id_and_size(self) -> None:
        result = parse_cartograph_html("<om-cartograph title='Lot 7'></om-cartograph>")
        assert result.spec.cartograph_id == "Lot-7"
        assert result.spec.size == "A4"
        assert len(result.issues) == 2

    def test_size_and_dimensions_keeps_the_size(self) -> None:
        result = parse_cartograph_html(
            '<om-cartograph cartograph-id="c" size="A3" width="100" height="100">'
            "</om-cartograph>"
        )
        assert result.spec.size == "A3" and result.spec.width is None
        assert result.issues

    def test_invalid_enum_skips_with_reason(self) -> None:
        result = parse_cartograph_html(
            _page(
                _FRAME + '<om-north id="n" for="f" x="0" y="0" w="1" h="1" kind="star">'
                "</om-north>"
            )
        )
        assert [i.id for i in result.spec.items] == ["f"]
        assert any("star" in issue.detail for issue in result.issues)

    def test_markup_inside_text_keeps_its_words(self) -> None:
        result = parse_cartograph_html(
            _page('<om-text id="t" x="0" y="0" w="1" h="1">a <b>bold</b> c</om-text>')
        )
        assert result.spec.items[0].body == "a bold c"  # type: ignore[union-attr]
        assert any("<b>" in issue.detail for issue in result.issues)

    def test_atlas_on_unknown_frame_is_dropped(self) -> None:
        result = parse_cartograph_html(
            _page(_FRAME + '<om-atlas for="x" layer="parcels"></om-atlas>')
        )
        assert result.spec.atlas is None
        assert result.issues

    _SOURCE: ClassVar[str] = (
        '{"filter":{"qgis":"\\"score\\" <> 1","written":"d.id == 3"},'
        '"sort":{"qgis":"\\"rank\\"","ascending":false,"written":"qgis2webmap_atlas_order"}}'
    )

    def _atlas(self, filter_text: str, sort_text: str):
        source = self._SOURCE
        return parse_cartograph_html(
            _page(
                _FRAME + f'<om-atlas for="f" layer="parcels" filter="{filter_text}" '
                f"sort=\"{sort_text}\" data-qgis2webmap-atlas-source='{source}'>"
                "</om-atlas>"
            )
        )

    def test_qgis_s_own_filter_and_sort_come_back_while_unedited(self) -> None:
        atlas = self._atlas("d.id == 3", "qgis2webmap_atlas_order").spec.atlas
        assert atlas is not None
        assert atlas.filter_source == '"score" <> 1'
        assert atlas.sort_source == '"rank"'
        assert atlas.sort_ascending is False

    def test_an_edited_filter_or_sort_is_not_overruled_by_the_old_source(self) -> None:
        atlas = self._atlas("d.id == 4", "lot").spec.atlas
        assert atlas is not None
        assert atlas.filter == "d.id == 4" and atlas.filter_source is None
        assert atlas.sort == "lot" and atlas.sort_source is None
        assert atlas.sort_ascending is True

    def test_unreadable_source_is_ignored(self) -> None:
        result = parse_cartograph_html(
            _page(
                _FRAME + '<om-atlas for="f" layer="p" filter="d.id == 3" '
                'data-qgis2webmap-atlas-source="{nope"></om-atlas>'
            )
        )
        assert result.spec.atlas is not None
        assert result.spec.atlas.filter_source is None
        assert not any("data-qgis2webmap" in issue.detail for issue in result.issues)

    def test_second_cartograph_is_reported(self) -> None:
        result = parse_cartograph_html(_page("") + _page(""))
        assert any("second" in issue.detail.lower() for issue in result.issues)

    def test_unclosed_elements_still_parse(self) -> None:
        result = parse_cartograph_html(
            '<om-cartograph cartograph-id="c" size="A4">'
            '<om-text id="t" x="0" y="0" w="1" h="1">hello'
        )
        assert result.spec.items[0].body == "hello"  # type: ignore[union-attr]


class TestReadFile:
    def test_reads_a_file(self, tmp_path: Path) -> None:
        path = tmp_path / "c.html"
        path.write_text(_write(_full_spec()), encoding="utf-8")
        assert read_cartograph_file(path).spec == _full_spec()

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(CartographImportError):
            read_cartograph_file(tmp_path / "nope.html")

    def test_not_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "c.html"
        path.write_bytes(b"\xff\xfe\x00<om-cartograph>")
        with pytest.raises(CartographImportError):
            read_cartograph_file(path)
