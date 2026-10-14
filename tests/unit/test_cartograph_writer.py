"""`writers/cartograph_writer.py`: CartographSpec -> standalone HTML.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import gzip
import re
from xml.etree import ElementTree

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    AtlasSpec,
    Box,
    CartographSpec,
    FrameSpec,
    GraticuleSpec,
    LegendRowSpec,
    LegendSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from nika_onlymap_exporter.packaging.asset_embedder import GZIP_SCRIPT_TYPE
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)

CARTO_JS = b"/* fake cartograph.standalone.js */ export const x = 1;\n" * 10
CARTO_CSS = "om-cartograph { display: block; }"
CORE_JS = b"/* fake onlymap.standalone.js, much bigger in reality */ " * 50
CORE_CSS = "om-map { display: block; }"


def _runtime(with_core: bool = False) -> CartographRuntimeBundle:
    if with_core:
        return CartographRuntimeBundle(
            cartograph_js=CARTO_JS,
            cartograph_css=CARTO_CSS,
            core_js=CORE_JS,
            core_css=CORE_CSS,
        )
    return CartographRuntimeBundle(cartograph_js=CARTO_JS, cartograph_css=CARTO_CSS)


def _static_spec(
    title: str = "UTM spike", cartograph_id: str = "cg-static"
) -> CartographSpec:
    frame = FrameSpec(
        id="main",
        box=Box(10, 24, 150, 150),
        mode="static",
        crs="EPSG:32648",
        crs_def="+proj=utm +zone=48 +datum=WGS84",
        corners=((103.8, 1.32), (103.82, 1.32), (103.82, 1.30), (103.8, 1.30)),
        image_bytes=b"\x89PNG-fake-bytes",
    )
    grid = GraticuleSpec(
        id="grid", box=Box(10, 24, 150, 150), for_="main", interval=500
    )
    bar = ScalebarSpec(id="bar", box=Box(10, 180, 70, 9), for_="main", kind="double")
    credit = TextSpec(
        id="credit",
        box=Box(10, 195, 190, 6),
        kind="attribution",
        body="1:{{frame.main.scale}} · {{frame.main.crs}} · OnlyMap",
    )
    return CartographSpec(
        cartograph_id=cartograph_id,
        title=title,
        size="A4",
        orientation="landscape",
        items=(frame, grid, bar, credit),
    )


def _live_spec() -> CartographSpec:
    main = FrameSpec(
        id="main",
        box=Box(10, 24, 150, 150),
        mode="live",
        center=(103.81, 1.31),
        zoom=14,
    )
    title = TextSpec(
        id="title", box=Box(10, 8, 150, 12), kind="title", body="{{title}}"
    )
    return CartographSpec(
        cartograph_id="cg-live",
        title="Spike",
        size="A4",
        items=(main, title),
    )


def _extract_template_inner(html_doc: str) -> str:
    match = re.search(r'<template id="sheet">(.*)</template>', html_doc, re.DOTALL)
    assert match is not None, "no <template id=sheet> found"
    return match.group(1)


def _parse_sheet(html_doc: str) -> ElementTree.Element:
    inner = _extract_template_inner(html_doc)
    # ElementTree needs a single root and no raw '&' -- the writer only ever
    # emits well-formed entities, so wrapping is all that's needed.
    return ElementTree.fromstring(f"<root>{inner}</root>")


class TestDocumentShape:
    def test_has_doctype_and_title(self) -> None:
        doc = write_cartograph_html(_static_spec(title="Lot 12"), _runtime())
        assert doc.startswith("<!doctype html>")
        assert "<title>Lot 12</title>" in doc

    def test_falls_back_to_cartograph_id_for_title(self) -> None:
        doc = write_cartograph_html(
            _static_spec(title="", cartograph_id="cg-x"), _runtime()
        )
        assert "<title>cg-x</title>" in doc

    def test_sheet_is_inside_a_template(self) -> None:
        doc = write_cartograph_html(_static_spec(), _runtime())
        assert '<template id="sheet">' in doc
        template_start = doc.index('<template id="sheet">')
        body_start = doc.index("<body")
        assert body_start < template_start

    def test_root_element_is_om_cartograph(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime()))
        cartograph = root.find("om-cartograph")
        assert cartograph is not None
        assert cartograph.get("format") == "cartograph/2"
        assert cartograph.get("cartograph-id") == "cg-static"

    def test_allow_url_actions_always_present(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime()))
        cartograph = root.find("om-cartograph")
        assert cartograph is not None
        assert cartograph.get("allow-url-actions") == ""


class TestRuntimeEmbedding:
    def test_static_only_omits_the_core(self) -> None:
        doc = write_cartograph_html(_static_spec(), _runtime(with_core=False))
        core_gz = base64.b64encode(gzip.compress(CORE_JS, 9, mtime=0)).decode()
        assert core_gz not in doc
        assert "setCoreLoader" not in doc
        assert CORE_CSS not in doc

    def test_static_only_still_embeds_the_cartograph_runtime(self) -> None:
        doc = write_cartograph_html(_static_spec(), _runtime(with_core=False))
        carto_gz = base64.b64encode(gzip.compress(CARTO_JS, 9, mtime=0)).decode()
        assert carto_gz in doc
        assert CARTO_CSS in doc

    def test_live_frame_embeds_the_core(self) -> None:
        doc = write_cartograph_html(_live_spec(), _runtime(with_core=True))
        core_gz = base64.b64encode(gzip.compress(CORE_JS, 9, mtime=0)).decode()
        assert core_gz in doc
        assert "setCoreLoader" in doc
        assert CORE_CSS in doc

    def test_live_frame_without_core_bundle_raises(self) -> None:
        with pytest.raises(ValueError):
            write_cartograph_html(_live_spec(), _runtime(with_core=False))

    def test_template_appended_after_core_loader_is_set(self) -> None:
        # The ordering non-negotiable: setCoreLoader must run before the
        # template content ever touches the document.
        doc = write_cartograph_html(_live_spec(), _runtime(with_core=True))
        script_match = re.search(r'<script type="module">(.*)</script>', doc, re.DOTALL)
        assert script_match is not None
        script = script_match.group(1)
        set_loader_pos = script.index("setCoreLoader")
        append_pos = script.index("appendChild")
        assert set_loader_pos < append_pos

    def test_gzip_data_blocks_inflate_inside_the_fragment_first(self) -> None:
        # A live frame's embedded <om-map> markup may carry gzipped layer
        # data (packaging.asset_embedder.GZIP_SCRIPT_TYPE), exactly like a
        # standalone map. querySelectorAll on `document` cannot see inside a
        # <template>, so the bootstrap must clone the fragment, inflate the
        # data blocks against *that* fragment, and only then set the core
        # loader and append -- in that order.
        doc = write_cartograph_html(_live_spec(), _runtime(with_core=True))
        script_match = re.search(r'<script type="module">(.*)</script>', doc, re.DOTALL)
        assert script_match is not None
        script = script_match.group(1)

        assert GZIP_SCRIPT_TYPE in script
        clone_pos = script.index("cloneNode")
        gzip_selector_pos = script.index(GZIP_SCRIPT_TYPE)
        set_loader_pos = script.index("setCoreLoader")
        append_pos = script.index("appendChild")
        assert clone_pos < gzip_selector_pos < set_loader_pos < append_pos

    def test_static_only_bootstrap_still_inflates_data_blocks(self) -> None:
        # Harmless no-op for a page with no such blocks, but the same single
        # code path runs regardless of has_live -- there is no separate
        # static-only branch to drift out of sync.
        doc = write_cartograph_html(_static_spec(), _runtime())
        script_match = re.search(r'<script type="module">(.*)</script>', doc, re.DOTALL)
        assert script_match is not None
        assert GZIP_SCRIPT_TYPE in script_match.group(1)


class TestPlacementAgreement:
    def test_every_placed_element_has_matching_attrs_and_style(self) -> None:
        doc = write_cartograph_html(_static_spec(), _runtime())
        root = _parse_sheet(doc)
        placed_tags = {
            "om-frame",
            "om-graticule",
            "om-legend",
            "om-scalebar",
            "om-north",
            "om-text",
            "om-image",
            "om-shape",
        }
        checked = 0
        for element in root.iter():
            if element.tag not in placed_tags:
                continue
            x, y, w, h = (element.get(name) for name in ("x", "y", "w", "h"))
            assert x is not None and y is not None and w is not None and h is not None
            style = element.get("style")
            assert style is not None
            expected = f"left:{x}mm;top:{y}mm;width:{w}mm;height:{h}mm"
            assert style == expected
            checked += 1
        assert checked >= 3  # frame, graticule, scalebar, text all present

    def test_numbers_have_no_float_noise(self) -> None:
        frame = FrameSpec(
            id="main",
            box=Box(10.1, 24.0, 150.0, 117.23999999999999),
            mode="static",
            crs="EPSG:32648",
            crs_def="+proj=utm",
            corners=((0, 1), (1, 1), (1, 0), (0, 0)),
            image_bytes=b"x",
        )
        spec = CartographSpec(cartograph_id="cg", size="A4", items=(frame,))
        doc = write_cartograph_html(spec, _runtime())
        root = _parse_sheet(doc)
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        assert om_frame.get("h") == "117.24"
        assert "999999" not in doc


class TestStaticFrame:
    def test_mode_attribute_written(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime()))
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        assert om_frame.get("mode") == "static"

    def test_live_mode_is_not_written(self) -> None:
        root = _parse_sheet(
            write_cartograph_html(_live_spec(), _runtime(with_core=True))
        )
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        assert om_frame.get("mode") is None

    def test_crs_def_is_always_written(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime()))
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        assert om_frame.get("crs-def") == "+proj=utm +zone=48 +datum=WGS84"

    def test_corners_are_a_json_array_in_tl_tr_br_bl_order(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime()))
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        import json

        corners = json.loads(om_frame.get("corners") or "")
        assert corners == [[103.8, 1.32], [103.82, 1.32], [103.82, 1.3], [103.8, 1.3]]

    def test_image_bytes_become_a_data_url(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime()))
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        img = om_frame.find("img")
        assert img is not None
        assert img.get("src", "").startswith("data:image/png;base64,")

    def test_image_path_used_when_no_bytes(self) -> None:
        frame = FrameSpec(
            id="main",
            box=Box(0, 0, 10, 10),
            mode="static",
            crs="EPSG:3857",
            corners=((0, 1), (1, 1), (1, 0), (0, 0)),
            image_path="main.frames/main.png",
        )
        spec = CartographSpec(cartograph_id="cg", size="A4", items=(frame,))
        root = _parse_sheet(write_cartograph_html(spec, _runtime()))
        img = root.find(".//om-frame/img")
        assert img is not None
        assert img.get("src") == "main.frames/main.png"


class TestLiveFrame:
    def test_src_attribute_written_when_given(self) -> None:
        main = FrameSpec(
            id="main",
            box=Box(10, 24, 150, 150),
            mode="live",
            src="../maps/map-abc.html",
            center=(103.81, 1.31),
            zoom=14,
        )
        spec = CartographSpec(cartograph_id="cg", size="A4", items=(main,))
        root = _parse_sheet(write_cartograph_html(spec, _runtime(with_core=True)))
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        assert om_frame.get("src") == "../maps/map-abc.html"

    def test_center_is_a_json_lonlat_pair(self) -> None:
        root = _parse_sheet(
            write_cartograph_html(_live_spec(), _runtime(with_core=True))
        )
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        assert om_frame.get("center") == "[103.81, 1.31]"

    def test_prerendered_om_map_markup_is_embedded(self) -> None:
        doc = write_cartograph_html(
            _live_spec(),
            _runtime(with_core=True),
            live_frame_markup={"main": '<om-map center="[0, 0]" zoom="2"></om-map>'},
        )
        root = _parse_sheet(doc)
        om_map = root.find(".//om-frame/om-map")
        assert om_map is not None
        assert om_map.get("zoom") == "2"

    def test_embedded_om_map_may_carry_gzipped_layer_data_verbatim(self) -> None:
        # The writer never decodes this -- it is opaque markup from the
        # reused OnlyMapWriter. Only the bootstrap script (checked above)
        # knows how to inflate it, at runtime, from inside the fragment.
        import gzip as gzip_module

        gz = base64.b64encode(gzip_module.compress(b'[{"x": 1}]', 9, mtime=0)).decode()
        markup = (
            '<om-map center="[0, 0]" zoom="2">'
            f'<om-layer id="pts" type="ScatterplotLayer">'
            f'<script type="{GZIP_SCRIPT_TYPE}">{gz}</script>'
            "</om-layer>"
            "</om-map>"
        )
        doc = write_cartograph_html(
            _live_spec(), _runtime(with_core=True), live_frame_markup={"main": markup}
        )
        assert markup in doc

    def test_no_child_when_no_markup_given(self) -> None:
        root = _parse_sheet(
            write_cartograph_html(_live_spec(), _runtime(with_core=True))
        )
        om_frame = root.find(".//om-frame")
        assert om_frame is not None
        assert len(list(om_frame)) == 0


class TestLegend:
    def test_derived_default_omits_attribute(self) -> None:
        legend = LegendSpec(id="lg", box=Box(165, 24, 35, 60), for_="main")
        spec = CartographSpec(
            cartograph_id="cg", size="A4", items=(_static_spec().items[0], legend)
        )
        root = _parse_sheet(write_cartograph_html(spec, _runtime()))
        om_legend = root.find(".//om-legend")
        assert om_legend is not None
        assert om_legend.get("derived") is None

    def test_non_derived_writes_false_and_literal_rows(self) -> None:
        legend = LegendSpec(
            id="lg",
            box=Box(165, 24, 35, 60),
            for_="main",
            derived=False,
            rows=(LegendRowSpec(label="Parcels", color="#eba941"),),
        )
        spec = CartographSpec(
            cartograph_id="cg", size="A4", items=(_static_spec().items[0], legend)
        )
        root = _parse_sheet(write_cartograph_html(spec, _runtime()))
        om_legend = root.find(".//om-legend")
        assert om_legend is not None
        assert om_legend.get("derived") == "false"
        row = om_legend.find("om-legend-row")
        assert row is not None
        assert row.get("label") == "Parcels"
        assert row.get("color") == "#eba941"


class TestAtlas:
    def test_atlas_element_written(self) -> None:
        frame = _static_spec().items[0]
        atlas = AtlasSpec(for_="main", layer="parcels", filename="lot-{{atlas.lot}}")
        spec = CartographSpec(
            cartograph_id="cg", size="A4", items=(frame,), atlas=atlas
        )
        root = _parse_sheet(write_cartograph_html(spec, _runtime()))
        om_atlas = root.find(".//om-atlas")
        assert om_atlas is not None
        assert om_atlas.get("layer") == "parcels"
        assert om_atlas.get("filename") == "lot-{{atlas.lot}}"


class TestTextAndTokens:
    def test_tokens_in_body_pass_through_unescaped(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime()))
        credit = root.find(".//om-text[@id='credit']")
        assert credit is not None
        assert credit.text is not None
        assert "{{frame.main.scale}}" in credit.text
        assert "{{frame.main.crs}}" in credit.text


class TestShape:
    def test_default_kind_omitted(self) -> None:
        shape = ShapeSpec(id="box", box=Box(0, 0, 10, 10))
        spec = CartographSpec(cartograph_id="cg", size="A4", items=(shape,))
        root = _parse_sheet(write_cartograph_html(spec, _runtime()))
        om_shape = root.find(".//om-shape")
        assert om_shape is not None
        assert om_shape.get("kind") is None

    def test_non_default_kind_written(self) -> None:
        shape = ShapeSpec(id="box", box=Box(0, 0, 10, 10), kind="arrow")
        spec = CartographSpec(cartograph_id="cg", size="A4", items=(shape,))
        root = _parse_sheet(write_cartograph_html(spec, _runtime()))
        om_shape = root.find(".//om-shape")
        assert om_shape is not None
        assert om_shape.get("kind") == "arrow"


class TestEscaping:
    def test_title_with_script_tag_is_escaped(self) -> None:
        spec = _static_spec(title="<script>alert(1)</script>")
        doc = write_cartograph_html(spec, _runtime())
        assert "<script>alert(1)</script>" not in doc
        assert "&lt;script&gt;" in doc
        # The HTML <title> element must still be well-formed.
        assert "<title>&lt;script&gt;alert(1)&lt;/script&gt;</title>" in doc

    def test_attribute_values_with_quotes_are_escaped(self) -> None:
        spec = _static_spec()
        attributed = CartographSpec(
            cartograph_id=spec.cartograph_id,
            title=spec.title,
            attribution='Survey "Dept" & Co.',
            size="A4",
            items=spec.items,
        )
        doc = write_cartograph_html(attributed, _runtime())
        root = _parse_sheet(doc)  # fails to parse if mis-escaped
        cartograph = root.find("om-cartograph")
        assert cartograph is not None
        assert cartograph.get("attribution") == 'Survey "Dept" & Co.'


class TestDeterminism:
    def test_same_input_produces_identical_bytes(self) -> None:
        spec = _static_spec()
        runtime = _runtime()
        first = write_cartograph_html(spec, runtime)
        second = write_cartograph_html(spec, runtime)
        assert first == second

    def test_live_frame_same_input_produces_identical_bytes(self) -> None:
        spec = _live_spec()
        runtime = _runtime(with_core=True)
        first = write_cartograph_html(spec, runtime)
        second = write_cartograph_html(spec, runtime)
        assert first == second


class TestPrintShopAttributes:
    def test_print_controls_are_written_on_the_root(self) -> None:
        frame = _static_spec().items[0]
        spec = CartographSpec(
            cartograph_id="cg",
            size="A3",
            items=(frame,),
            bleed=3.0,
            crop_marks=True,
            safe_zone=8.0,
            dpi=300,
            flatten=True,
            color_profile="sRGB",
            cvd="deuteranopia",
        )
        root = _parse_sheet(write_cartograph_html(spec, _runtime())).find(
            "om-cartograph"
        )
        assert root is not None
        assert root.get("bleed") == "3"
        assert root.get("crop-marks") == ""
        assert root.get("safe-zone") == "8"
        assert root.get("dpi") == "300"
        assert root.get("flatten") == ""
        assert root.get("color-profile") == "sRGB"
        assert root.get("cvd") == "deuteranopia"

    def test_defaults_write_none_of_them(self) -> None:
        root = _parse_sheet(write_cartograph_html(_static_spec(), _runtime())).find(
            "om-cartograph"
        )
        assert root is not None
        for name in ("bleed", "crop-marks", "safe-zone", "dpi", "flatten", "cvd"):
            assert root.get(name) is None, name

    def test_crop_marks_without_a_bleed_are_refused(self) -> None:
        frame = _static_spec().items[0]
        with pytest.raises(ValueError, match="bleed"):
            CartographSpec(
                cartograph_id="cg", size="A3", items=(frame,), crop_marks=True
            )

    def test_an_unknown_cvd_mode_is_refused(self) -> None:
        frame = _static_spec().items[0]
        with pytest.raises(ValueError):
            CartographSpec(cartograph_id="cg", size="A3", items=(frame,), cvd="sepia")
