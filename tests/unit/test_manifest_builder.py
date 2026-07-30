"""Manifest generation - pure, so it runs in CI without QGIS.

The most valuable test here is `TestAttributeContract`, which checks every
attribute we emit against `onlymapjs.html-data.json` shipped by the runtime. That
turns an upstream rename from a silently broken map into a failing build.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    GraduatedClassSpec,
    PopupFieldMode,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    ScaleRange,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.license_policy import FreeTierPolicy
from nika_onlymap_exporter.core.manifest_builder import (
    SCALE_DENOMINATOR_AT_ZOOM_0,
    build_layer_element,
    build_manifest,
    color_literal,
    fill_expression,
    scale_to_zoom,
)

RED = Color(r=255, g=0, b=0)
BLUE = Color(r=0, g=0, b=255)
GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            "properties": {"name": "a", "kind": "civil"},
        }
    ],
}


def make_layer(**overrides) -> ExportLayer:
    defaults = dict(
        layer_id="layer1",
        name="Test layer",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson=GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE, symbol=SymbolSpec(fill_color=RED)
        ),
        popup=PopupSpec(enabled=False),
    )
    defaults.update(overrides)
    return ExportLayer(**defaults)


def make_project(layers=None, **overrides) -> ExportProject:
    defaults = dict(
        title="Test map",
        layers=tuple(layers or [make_layer()]),
        extent=Extent(west=0.0, south=0.0, east=10.0, north=10.0),
    )
    defaults.update(overrides)
    return ExportProject(**defaults)


class TestColorLiteral:
    def test_opaque_colour_is_plain_hex(self) -> None:
        assert color_literal(RED) == "'#ff0000'"

    def test_translucent_colour_carries_alpha(self) -> None:
        assert color_literal(Color(r=255, g=0, b=0, a=0.5)) == "'#ff000080'"

    def test_missing_colour_falls_back(self) -> None:
        assert color_literal(None) == "'#888888'"


class TestScaleToZoom:
    def test_zoom_zero_is_the_reference_denominator(self) -> None:
        assert scale_to_zoom(SCALE_DENOMINATOR_AT_ZOOM_0) == pytest.approx(0.0)

    def test_halving_the_denominator_adds_one_zoom_level(self) -> None:
        assert scale_to_zoom(SCALE_DENOMINATOR_AT_ZOOM_0 / 2) == pytest.approx(1.0)

    def test_conversion_is_inverted_not_proportional(self) -> None:
        """A smaller denominator means more zoomed in, so a higher zoom."""
        assert scale_to_zoom(1_000) > scale_to_zoom(1_000_000)

    def test_degenerate_input_is_clamped(self) -> None:
        assert scale_to_zoom(0) == 0.0


class TestFillExpression:
    def test_single_symbol_is_a_literal(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.SINGLE, symbol=SymbolSpec(fill_color=RED)
        )
        assert fill_expression(renderer, GeometryKind.POLYGON) == "'#ff0000'"

    def test_categorized_is_an_equality_ternary_chain(self) -> None:
        """The shape the legend renders as a category palette."""
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="kind",
            categories=(
                CategorySpec("civil", "Civil", SymbolSpec(fill_color=RED)),
                CategorySpec("military", "Military", SymbolSpec(fill_color=BLUE)),
            ),
        )
        expression = fill_expression(renderer, GeometryKind.POINT)
        assert expression.startswith("$kind == 'civil' ? '#ff0000'")
        assert "$kind == 'military' ? '#0000ff'" in expression
        # The trailing fallback becomes the legend's "other" row.
        assert expression.rstrip().endswith("'#888888cc'")

    def test_graduated_is_a_threshold_scale(self) -> None:
        """Threshold, not sequential - QGIS classes are discrete, not a ramp."""
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="pop",
            classes=(
                GraduatedClassSpec(0.0, 50.0, "0-50", SymbolSpec(fill_color=RED)),
                GraduatedClassSpec(50.0, 100.0, "50-100", SymbolSpec(fill_color=BLUE)),
            ),
        )
        expression = fill_expression(renderer, GeometryKind.POLYGON)
        assert (
            expression == "scale($pop, threshold, ['#ff0000', '#0000ff'], domain=[50])"
        )

    def test_threshold_breaks_are_one_fewer_than_colours(self) -> None:
        """A d3 threshold scale takes N colours and N-1 interior breaks."""
        classes = tuple(
            GraduatedClassSpec(
                float(i * 10), float((i + 1) * 10), f"c{i}", SymbolSpec(fill_color=RED)
            )
            for i in range(4)
        )
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED, field_name="v", classes=classes
        )
        expression = fill_expression(renderer, GeometryKind.POLYGON)
        colours = expression.count("'#")
        breaks = expression.split("domain=[")[1].rstrip("])").split(", ")
        assert colours == 4
        assert len(breaks) == 3

    def test_category_value_with_a_quote_is_escaped(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="name",
            categories=(CategorySpec("O'Hare", "OHare", SymbolSpec(fill_color=RED)),),
        )
        assert "O\\'Hare" in fill_expression(renderer, GeometryKind.POINT)


class TestLayerElement:
    def test_data_is_inline_as_a_direct_child(self) -> None:
        """`file://` blocks fetch of siblings, so a data URL cannot work."""
        markup = build_layer_element(make_layer())
        assert '<script type="application/json">' in markup
        assert "data=" not in markup

    def test_scale_visibility_becomes_zoom_bounds(self) -> None:
        layer = make_layer(scale_range=ScaleRange(min_scale=1_000_000, max_scale=1_000))
        markup = build_layer_element(layer)
        assert "visible-min-zoom=" in markup
        assert "visible-max-zoom=" in markup

    def test_popup_layer_is_pickable_and_highlights(self) -> None:
        layer = make_layer(
            popup=PopupSpec(
                enabled=True,
                fields=(PopupFieldSpec("name", mode=PopupFieldMode.INLINE_WITH_DATA),),
            )
        )
        markup = build_layer_element(layer)
        assert 'pickable="true"' in markup
        assert 'auto-highlight="true"' in markup

    def test_hidden_layer_is_marked_invisible(self) -> None:
        assert 'visible="false"' in build_layer_element(make_layer(visible=False))

    def test_layer_name_is_escaped(self) -> None:
        markup = build_layer_element(make_layer(name='Roads & "Paths"'))
        assert "&amp;" in markup
        assert 'label="Roads &amp; &quot;Paths&quot;"' in markup


class TestManifest:
    def test_emits_no_basemap_and_no_telemetry(self) -> None:
        markup = build_manifest(make_project())
        assert 'basemap="none"' in markup
        assert 'telemetry="off"' in markup
        assert "map-id" not in markup

    def test_includes_a_fallback_for_previewers(self) -> None:
        markup = build_manifest(make_project())
        assert "<om-fallback>" in markup
        assert "needs JavaScript" in markup

    def test_widgets_are_on_by_default(self) -> None:
        markup = build_manifest(make_project())
        assert 'type="legend"' in markup
        assert 'type="zoom-controls"' in markup
        assert 'type="scale-bar"' in markup

    def test_widgets_can_be_switched_off(self) -> None:
        project = make_project(
            settings=ExportSettings(show_legend=False, show_scale_bar=False)
        )
        markup = build_manifest(project)
        assert 'type="legend"' not in markup
        assert 'type="scale-bar"' not in markup

    def test_layer_switcher_only_when_there_is_a_choice(self) -> None:
        one = build_manifest(make_project())
        assert 'type="layer-switcher"' not in one

        two = build_manifest(
            make_project([make_layer(layer_id="a"), make_layer(layer_id="b")])
        )
        assert 'type="layer-switcher"' in two

    def test_layers_keep_model_order(self) -> None:
        project = make_project(
            [make_layer(layer_id="bottom"), make_layer(layer_id="top")]
        )
        markup = build_manifest(project)
        assert markup.index('id="bottom"') < markup.index('id="top"')

    def test_camera_centres_on_the_extent(self) -> None:
        project = make_project(
            extent=Extent(west=10.0, south=40.0, east=20.0, north=50.0)
        )
        markup = build_manifest(project)
        assert 'center="[15.000000, 45.000000]"' in markup

    def test_antimeridian_extent_centres_on_the_data(self) -> None:
        project = make_project(
            extent=Extent(
                west=170.0,
                south=0.0,
                east=-170.0,
                north=10.0,
                crosses_antimeridian=True,
            )
        )
        markup = build_manifest(project)
        centre = re.search(r'center="\[([-0-9.]+),', markup)
        assert centre is not None
        # Near the dateline, not at longitude zero.
        assert abs(float(centre.group(1))) > 170.0

    def test_validate_only_appears_when_layers_will_be_dropped(self) -> None:
        clean = make_project()
        verdict = FreeTierPolicy().evaluate(clean)
        assert "validate" not in build_manifest(clean, verdict)

        over_cap = make_project([make_layer(layer_id=f"l{i}") for i in range(6)])
        verdict = FreeTierPolicy().evaluate(over_cap)
        assert "validate" in build_manifest(over_cap, verdict)


def find_onlymap_schema() -> Path | None:
    """Locate `onlymapjs.html-data.json`, the runtime's attribute contract.

    Not vendored into this repository: it ships inside the separately-licensed
    OnlyMap package, and copying it here would put licensed material in a GPL
    tree for no benefit. Instead it is discovered, with an env var for anyone
    whose checkout differs.

    Once the plugin carries the runtime (the `RuntimeProvider` work), this should
    read the schema from whatever that provides, and the discovery below goes
    away.
    """
    override = os.environ.get("ONLYMAP_HTML_DATA")
    if override:
        path = Path(override)
        return path if path.exists() else None

    candidates = [
        Path.home() / "Nika/onlymap-js/onlymapjs.html-data.json",
        Path.home()
        / "Nika/nika-agent/node_modules/@nika-js/onlymap/onlymapjs.html-data.json",
        Path("node_modules/@nika-js/onlymap/onlymapjs.html-data.json"),
    ]
    return next((c for c in candidates if c.exists()), None)


@pytest.fixture(scope="module")
def known_attributes() -> dict[str, set[str]]:
    schema = find_onlymap_schema()
    if schema is None:
        pytest.skip(
            "onlymapjs.html-data.json not found - set ONLYMAP_HTML_DATA to run "
            "the attribute-contract tests"
        )
    data = json.loads(schema.read_text())
    return {
        tag["name"]: {a["name"] for a in tag.get("attributes", [])}
        for tag in data["tags"]
    }


class TestAttributeContract:
    """Every attribute we emit must exist in the runtime's own schema.

    `onlymapjs.html-data.json` enumerates all eight elements and their
    attributes. Checking against it turns an upstream rename into a failing
    build here, rather than a map that silently ignores an attribute.
    """

    def test_every_emitted_attribute_exists(self, known_attributes) -> None:
        project = make_project(
            [
                make_layer(
                    layer_id="styled",
                    scale_range=ScaleRange(min_scale=1_000_000, max_scale=1_000),
                    opacity=0.5,
                    visible=False,
                    renderer=RendererSpec(
                        kind=RendererKind.SINGLE,
                        symbol=SymbolSpec(
                            fill_color=RED,
                            stroke_color=BLUE,
                            stroke_width=2.0,
                            radius=5.0,
                        ),
                    ),
                    popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("name"),)),
                )
            ]
        )
        markup = build_manifest(project)

        unknown: list[str] = []
        for element, attributes in re.findall(r"<(om-[a-z]+)\b([^>]*)>", markup):
            for name in re.findall(r'(?:^|\s)([a-z][a-z0-9-]*)="', attributes):
                if name not in known_attributes.get(element, set()):
                    unknown.append(f"{element}[{name}]")

        assert not unknown, f"attributes absent from the OnlyMap schema: {unknown}"

    def test_every_element_we_emit_exists(self, known_attributes) -> None:
        markup = build_manifest(make_project())
        for element in set(re.findall(r"<(om-[a-z]+)", markup)):
            assert element in known_attributes, f"{element} is not an OnlyMap element"
