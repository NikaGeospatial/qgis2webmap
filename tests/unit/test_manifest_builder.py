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
from typing import ClassVar

import pytest

from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    ElevationSpec,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    GraduatedClassSpec,
    IconAtlasSpec,
    LabelingSpec,
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
    WIDGET_POSITIONS,
    build_label_element,
    build_layer_element,
    build_legend_widget,
    build_manifest,
    build_popup_elements,
    build_popup_reset_behaviors,
    build_widget_elements,
    collect_attributions,
    color_literal,
    dash_attribute,
    escape_attr,
    fill_expression,
    icon_expression,
    json_for_script,
    needs_image_legend,
    numeric_expression,
    scale_to_zoom,
    terrain_note,
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
        layers=tuple([make_layer()] if layers is None else layers),
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

    def test_categorized_line_reads_the_stroke_color(self) -> None:
        """A line symbol's colour is its stroke; its fill is `None`.

        Reading only the fill exported every categorized line layer - an MRT
        network in official line colours, say - as the '#888888' placeholder.
        """
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="line",
            categories=(
                CategorySpec("NS", "North-South", SymbolSpec(stroke_color=RED)),
                CategorySpec("EW", "East-West", SymbolSpec(stroke_color=BLUE)),
            ),
        )
        expression = fill_expression(renderer, GeometryKind.LINE)
        assert "$line == 'NS' ? '#ff0000'" in expression
        assert "$line == 'EW' ? '#0000ff'" in expression
        assert "'#888888' :" not in expression

    def test_graduated_line_reads_the_stroke_color(self) -> None:
        """Same split as categorized: class colours must survive on lines."""
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="flow",
            classes=(
                GraduatedClassSpec(0.0, 50.0, "0-50", SymbolSpec(stroke_color=RED)),
                GraduatedClassSpec(50.0, 100.0, "50+", SymbolSpec(stroke_color=BLUE)),
            ),
        )
        expression = fill_expression(renderer, GeometryKind.LINE)
        assert (
            expression == "scale($flow, threshold, ['#ff0000', '#0000ff'], domain=[50])"
        )

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

    def test_a_rounded_line_carries_its_caps_and_joins(self) -> None:
        layer = make_layer(
            geometry_kind=GeometryKind.LINE,
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(
                    stroke_color=RED,
                    stroke_width=2.0,
                    cap_rounded=True,
                    join_rounded=True,
                ),
            ),
        )
        element = build_layer_element(layer)
        assert 'line-cap-rounded="true"' in element
        assert 'line-joint-rounded="true"' in element

    def test_a_default_line_emits_no_cap_attributes(self) -> None:
        """A default QGIS line is square-capped and bevel-joined, like deck.gl."""
        layer = make_layer(
            geometry_kind=GeometryKind.LINE,
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(stroke_color=RED, stroke_width=2.0),
            ),
        )
        element = build_layer_element(layer)
        assert "line-cap-rounded" not in element
        assert "line-joint-rounded" not in element

    def test_graduated_size_becomes_a_threshold_scale(self) -> None:
        """Graduated-by-size used to export every class at one radius."""
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="pop",
            classes=(
                GraduatedClassSpec(0.0, 50.0, "0-50", SymbolSpec(radius=4.0)),
                GraduatedClassSpec(50.0, 100.0, "50-100", SymbolSpec(radius=12.0)),
            ),
        )
        assert (
            numeric_expression(renderer, "radius")
            == "scale($pop, threshold, [4, 12], domain=[50])"
        )

    def test_categorized_size_becomes_a_ternary_chain(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="kind",
            categories=(
                CategorySpec("civil", "Civil", SymbolSpec(radius=3.0)),
                CategorySpec("military", "Military", SymbolSpec(radius=9.0)),
            ),
        )
        expression = numeric_expression(renderer, "radius", fallback=5.0)
        assert expression == "$kind == 'civil' ? 3 : $kind == 'military' ? 9 : 5"

    def test_uniform_classes_stay_a_plain_scalar(self) -> None:
        """An expression yielding one number for every feature is pure noise."""
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="pop",
            classes=(
                GraduatedClassSpec(0.0, 50.0, "0-50", SymbolSpec(radius=4.0)),
                GraduatedClassSpec(50.0, 100.0, "50-100", SymbolSpec(radius=4.0)),
            ),
        )
        assert numeric_expression(renderer, "radius") is None

    def test_single_symbol_has_no_per_class_expression(self) -> None:
        renderer = RendererSpec(kind=RendererKind.SINGLE, symbol=SymbolSpec(radius=4.0))
        assert numeric_expression(renderer, "radius") is None

    def test_a_class_missing_the_value_falls_back_to_the_scalar(self) -> None:
        """Half an expression would draw the missing class at deck.gl's default."""
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="pop",
            classes=(
                GraduatedClassSpec(0.0, 50.0, "0-50", SymbolSpec(radius=4.0)),
                GraduatedClassSpec(50.0, 100.0, "50-100", SymbolSpec(radius=None)),
            ),
        )
        assert numeric_expression(renderer, "radius") is None

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


class TestPopupFieldModes:
    """Each mode has to produce its own markup shape, or the choice is a no-op.

    The `*_WITH_DATA` behaviour itself is not testable here: emptiness is a
    per-feature fact and this markup is one template shared by every feature,
    so all these tests can assert is that the row is left *hideable* - the
    stylesheet the overlay carries does the rest.
    """

    @staticmethod
    def _row(mode: PopupFieldMode) -> str:
        layer = make_layer(
            popup=PopupSpec(
                enabled=True, fields=(PopupFieldSpec("name", alias="Name", mode=mode),)
            )
        )
        markup = build_popup_elements(layer)
        # Matched on the opening tag: the overlay also carries the stylesheet,
        # which names every one of these classes in its selectors.
        (row,) = [
            line.strip()
            for line in markup.splitlines()
            if '<div class="om-popup-row' in line
        ]
        return row

    def test_default_mode_markup_is_unchanged(self) -> None:
        """The default must stay byte-identical, or every artifact test churns."""
        assert self._row(PopupFieldMode.INLINE_WITH_DATA) == (
            '<div class="om-popup-row">'
            '<span class="om-popup-label">Name</span>'
            '<span class="om-popup-value">{{name}}</span></div>'
        )

    def test_no_label_emits_the_value_alone(self) -> None:
        row = self._row(PopupFieldMode.NO_LABEL)
        assert "om-popup-label" not in row
        assert '<span class="om-popup-value">{{name}}</span>' in row

    def test_header_modes_are_marked_for_stacked_layout(self) -> None:
        for mode in (PopupFieldMode.HEADER_ALWAYS, PopupFieldMode.HEADER_WITH_DATA):
            assert "om-popup-row-header" in self._row(mode)

    def test_always_modes_opt_out_of_empty_hiding(self) -> None:
        for mode in (
            PopupFieldMode.NO_LABEL,
            PopupFieldMode.INLINE_ALWAYS,
            PopupFieldMode.HEADER_ALWAYS,
        ):
            assert "om-popup-always" in self._row(mode)

    def test_with_data_modes_stay_hideable(self) -> None:
        for mode in (PopupFieldMode.INLINE_WITH_DATA, PopupFieldMode.HEADER_WITH_DATA):
            assert "om-popup-always" not in self._row(mode)

    def test_hidden_fields_produce_no_row_at_all(self) -> None:
        layer = make_layer(
            popup=PopupSpec(
                enabled=True,
                fields=(
                    PopupFieldSpec("name", mode=PopupFieldMode.HIDDEN),
                    PopupFieldSpec("kind", mode=PopupFieldMode.INLINE_ALWAYS),
                ),
            )
        )
        markup = build_popup_elements(layer)
        assert "{{name}}" not in markup
        assert "{{kind}}" in markup

    def test_every_visible_mode_still_interpolates_its_field(self) -> None:
        for mode in PopupFieldMode:
            if mode is PopupFieldMode.HIDDEN:
                continue
            assert "{{name}}" in self._row(mode)


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
    def test_emits_no_basemap_and_leaves_telemetry_to_the_runtime_default(self) -> None:
        markup = build_manifest(make_project())
        assert 'basemap="none"' in markup
        # No `telemetry` attribute at all, so the runtime's default (on) applies -
        # exported maps send the anonymous usage report described in
        # `docs/privacy.md` unless the person exporting opts out.
        assert "telemetry" not in markup
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

    def test_no_export_carries_the_runtime_error_panel(self) -> None:
        """Neither a clean project nor one over the caps mounts `validate`.

        The over-cap half used to assert the opposite. See
        `CapVerdict.needs_runtime_validation` for why it flipped: the panel's
        success badge sat on the legend of maps that had nothing wrong with them.
        """
        clean = make_project()
        verdict = FreeTierPolicy().evaluate(clean)
        assert "validate" not in build_manifest(clean, verdict)

        over_cap = make_project([make_layer(layer_id=f"l{i}") for i in range(6)])
        verdict = FreeTierPolicy().evaluate(over_cap)
        assert verdict.has_violations, "the breach is still detected"
        assert "validate" not in build_manifest(over_cap, verdict)


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

    # Order matters, and the pinned build wins. A local mirror checkout is
    # whatever someone last pulled: `~/Nika/onlymap-js` was three releases behind
    # when `selection-type` was adopted, so it reported a real 0.6.1 attribute as
    # absent from the schema. Checking the *pinned* runtime first is the same
    # rule that applies to reading the bundle - verify against what the export
    # will actually run, never against the dev tree.
    runtime_dir = os.environ.get("ONLYMAP_RUNTIME_DIR")
    candidates = [
        *([Path(runtime_dir) / "onlymapjs.html-data.json"] if runtime_dir else []),
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


class TestLegendSwatch:
    """The legend reads `color`, not the accessor expression.

    Its swatch source is `layer.color ?? <derived legend entry> ?? "#999"`. A
    single symbol compiles to a bare colour literal, which it cannot pull
    structure from - so without the shorthand every single-symbol layer showed
    a grey swatch next to correctly-coloured geometry.
    """

    # `get-fill-color` ends in the same six characters, so the shorthand has to
    # be matched as a whole attribute rather than as a substring.
    SHORTHAND = re.compile(r'(?<![-\w])color="([^"]+)"')

    def test_a_single_symbol_layer_carries_the_shorthand(self) -> None:
        match = self.SHORTHAND.search(build_layer_element(make_layer()))
        assert match is not None
        assert match.group(1) == "#ff0000"

    def test_a_line_layer_uses_its_stroke(self) -> None:
        layer = make_layer(
            geometry_kind=GeometryKind.LINE,
            renderer=RendererSpec(
                kind=RendererKind.SINGLE, symbol=SymbolSpec(stroke_color=BLUE)
            ),
        )
        match = self.SHORTHAND.search(build_layer_element(layer))
        assert match is not None
        assert match.group(1) == "#0000ff"

    def test_a_categorized_layer_does_not_carry_it(self) -> None:
        """Its derived entries are richer than one colour; this would win."""
        layer = make_layer(
            renderer=RendererSpec(
                kind=RendererKind.CATEGORIZED,
                field_name="kind",
                categories=(
                    CategorySpec("civil", "Civil", SymbolSpec(fill_color=RED)),
                ),
            )
        )
        assert self.SHORTHAND.search(build_layer_element(layer)) is None


class TestPopupTrigger:
    def test_click_is_the_default(self) -> None:
        layer = make_layer(
            popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("name"),))
        )
        assert 'on="click"' in build_popup_elements(layer)

    def test_hover_replaces_click_rather_than_adding_to_it(self) -> None:
        """Both bound at once leaves a click on an open popup doing nothing."""
        layer = make_layer(
            popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("name"),))
        )
        markup = build_popup_elements(layer, hover=True)
        assert 'on="hover"' in markup
        assert 'on="click"' not in markup


class TestWidgetPositions:
    """The one place a value is asserted, not just an attribute name.

    The schema documents eight logical slots plus four legacy corner aliases,
    but the shipped runtime implements only the corners - it looks `position`
    up in a four-entry table and silently falls back to `top-left`. Emitting
    `top-end` therefore does not place the legend on the right; it stacks every
    widget in one corner, on top of each other. The schema alone cannot catch
    that, which is why these values are pinned here.
    """

    RUNTIME_CORNERS = frozenset(
        {"top-left", "top-right", "bottom-left", "bottom-right"}
    )

    def test_every_position_is_one_the_runtime_implements(self) -> None:
        assert set(WIDGET_POSITIONS.values()) <= self.RUNTIME_CORNERS

    def test_the_manifest_only_emits_those_corners(self) -> None:
        markup = build_manifest(
            make_project([make_layer(), make_layer(layer_id="second")])
        )
        emitted = re.findall(r'<om-widget[^>]*position="([^"]+)"', markup)
        assert emitted, "the default export should carry widgets"
        assert set(emitted) <= self.RUNTIME_CORNERS

    def test_the_chrome_does_not_all_land_in_one_corner(self) -> None:
        """The actual defect: four widgets, one corner, piled up."""
        markup = build_manifest(
            make_project([make_layer(), make_layer(layer_id="second")])
        )
        emitted = re.findall(r'<om-widget[^>]*position="([^"]+)"', markup)
        assert len(set(emitted)) >= 3

    def test_the_legend_and_the_credit_chip_do_not_share_a_corner(self) -> None:
        """`map.html` pins the credit component to the bottom-right."""
        assert WIDGET_POSITIONS["legend"] != "bottom-right"
        assert "bottom-right" not in set(WIDGET_POSITIONS.values())

    def test_the_legend_drops_its_heading_when_the_caption_shows_the_title(
        self,
    ) -> None:
        """Otherwise the title is on screen twice, which testing found."""
        from nika_onlymap_exporter.core.export_ir import ExportSettings

        markup = build_manifest(
            make_project([make_layer()], settings=ExportSettings(show_title=True))
        )
        legend = re.search(r'<om-widget type="legend"[^>]*>', markup)
        assert legend is not None, "the legend should still be emitted"
        assert "title=" not in legend.group(0)

    def test_the_legend_keeps_its_heading_when_the_caption_is_off(self) -> None:
        """With no caption the legend is the only thing that can name the map."""
        from nika_onlymap_exporter.core.export_ir import ExportSettings

        markup = build_manifest(
            make_project([make_layer()], settings=ExportSettings(show_title=False))
        )
        legend = re.search(r'<om-widget type="legend"[^>]*>', markup)
        assert legend is not None
        assert "title=" in legend.group(0)


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
                ),
                # A labelled layer too, so the companion TextLayer's attributes
                # are checked against the schema rather than assumed.
                make_layer(
                    layer_id="labelled",
                    labeling=LabelingSpec(
                        enabled=True,
                        field_name="name",
                        font_family="Inter",
                        font_size=14.0,
                        color=RED,
                        halo_color=BLUE,
                        halo_width=1.5,
                        character_set="Zü",
                    ),
                ),
                # And a raised one, so `extruded`, `get-elevation`, `wireframe`
                # and the map's `pitch` are checked too.
                make_layer(
                    layer_id="raised",
                    geometry_kind=GeometryKind.POLYGON,
                    elevation=ElevationSpec(
                        extruded=True, height_field="height", wireframe=True
                    ),
                ),
            ],
            settings=ExportSettings(terrain="terrarium"),
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


class TestRepresentativeSymbol:
    """Regression: categorized and graduated layers lost width and radius.

    `renderer.symbol` is None for those kinds, so reading it directly meant the
    layer drew at defaults with nothing reporting why - the exact class of
    silent symbology loss this project exists to avoid.
    """

    STYLED = SymbolSpec(fill_color=RED, stroke_width=3.0, radius=8.0)

    def test_single_symbol_is_its_own_representative(self) -> None:
        renderer = RendererSpec(kind=RendererKind.SINGLE, symbol=self.STYLED)
        assert renderer.representative_symbol is self.STYLED

    def test_categorized_uses_its_first_class(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="k",
            categories=(CategorySpec("a", "A", self.STYLED),),
        )
        assert renderer.representative_symbol is self.STYLED

    def test_graduated_uses_its_first_class(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="v",
            classes=(GraduatedClassSpec(0.0, 1.0, "a", self.STYLED),),
        )
        assert renderer.representative_symbol is self.STYLED

    def test_unsupported_renderer_has_none(self) -> None:
        assert RendererSpec(kind=RendererKind.UNSUPPORTED).representative_symbol is None

    def test_categorized_point_layer_keeps_radius_and_width(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="k",
            categories=(CategorySpec("a", "A", self.STYLED),),
        )
        markup = build_layer_element(make_layer(renderer=renderer))
        assert "get-point-radius=" in markup
        assert "get-line-width=" in markup

    def test_graduated_polygon_layer_keeps_stroke_width(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="v",
            classes=(GraduatedClassSpec(0.0, 1.0, "a", self.STYLED),),
        )
        layer = make_layer(geometry_kind=GeometryKind.POLYGON, renderer=renderer)
        assert "get-line-width=" in build_layer_element(layer)


class TestTemplateTokenSafety:
    """Regression: sequential token replacement could inject the runtime.

    Replacing tokens one after another rescans already-inserted content, so a
    layer named `@RUNTIME_JS@` had five megabytes of library pasted into its
    label. A single-pass substitution can only match the template's own tokens.
    """

    def test_layer_named_like_a_token_is_left_alone(self, tmp_path) -> None:
        from nika_onlymap_exporter.packaging.runtime_manager import (
            RuntimeBundle,
            sha256_of,
        )
        from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

        class FakeRuntime:
            def load(self) -> RuntimeBundle:
                js = b"/* RUNTIME BODY */"
                return RuntimeBundle(
                    javascript=js, css=b"", version="t", sha256=sha256_of(js)
                )

        hostile = make_project([make_layer(name="@RUNTIME_JS@")])
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            hostile, tmp_path, compress=False
        )
        html = result.entry_path.read_text()

        # The label keeps the literal text; the runtime appears exactly once.
        assert 'label="@RUNTIME_JS@"' in html
        assert html.count("/* RUNTIME BODY */") == 1


class TestScriptEmbeddingSafety:
    """Regression: a feature attribute could close the inline JSON block.

    `json.dumps` leaves `<` literal, so a value containing `</script>` ended the
    script element early - breaking the map, and turning the rest of the
    attribute into live markup. Reachable with ordinary QGIS data.
    """

    HOSTILE: ClassVar[dict] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {"note": "</script><img src=x onerror=alert(1)>"},
            }
        ],
    }

    def test_closing_tag_cannot_survive(self) -> None:
        payload = json_for_script(self.HOSTILE)
        assert "</script>" not in payload
        assert "<" not in payload

    def test_escaping_round_trips(self) -> None:
        """The data must be unchanged for the map that reads it."""
        assert json.loads(json_for_script(self.HOSTILE)) == self.HOSTILE

    def test_javascript_line_terminators_are_escaped(self) -> None:
        """U+2028/9 are legal in JSON strings but break JavaScript source.

        Written as escape sequences, not literals: the characters are
        invisible in an editor, which is precisely why they cause trouble.
        """
        line_sep, para_sep = "\u2028", "\u2029"
        original = f"x{line_sep}y{para_sep}z"
        payload = json_for_script({"a": original})

        # The raw characters must not reach the script body...
        assert line_sep not in payload
        assert para_sep not in payload
        # ...but the data is unchanged for whatever reads it back.
        assert json.loads(payload)["a"] == original

    def test_a_hostile_layer_produces_a_well_formed_element(self) -> None:
        markup = build_layer_element(make_layer(geojson=self.HOSTILE))
        # Exactly one opening and one closing script tag for the data block.
        assert markup.count("<script") == 1
        assert markup.count("</script>") == 1


class TestAttributeEscaping:
    def test_single_quotes_stay_readable(self) -> None:
        """Accessors are full of single quotes; entities make them unreadable."""
        assert escape_attr("$k == 'a' ? 'b' : 'c'") == "$k == 'a' ? 'b' : 'c'"

    def test_double_quotes_are_escaped(self) -> None:
        assert escape_attr('say "hi"') == "say &quot;hi&quot;"

    def test_markup_characters_are_escaped(self) -> None:
        assert escape_attr("a & b <c>") == "a &amp; b &lt;c&gt;"

    def test_a_layer_name_cannot_inject_an_attribute(self) -> None:
        markup = build_layer_element(make_layer(name='" onload="evil()'))
        assert 'onload="evil()' not in markup
        assert "&quot;" in markup


class TestLabelLayer:
    """Labels were translated into `LabelingSpec` and then dropped.

    The reader built the spec - font, colour, halo, character set - and the
    manifest builder never referenced it, so a labelled QGIS project exported to
    a map with no labels and nothing in the fidelity report saying so.
    """

    LABELLED: ClassVar[dict] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                "properties": {"name": "Alpha"},
            }
        ],
    }

    def labelled_layer(self, **labeling_overrides) -> ExportLayer:
        defaults = dict(enabled=True, field_name="name")
        defaults.update(labeling_overrides)
        return make_layer(geojson=self.LABELLED, labeling=LabelingSpec(**defaults))

    def test_an_unlabelled_layer_emits_no_text_layer(self) -> None:
        assert build_label_element(make_layer()) == ""

    def test_a_labelled_layer_emits_a_text_layer(self) -> None:
        markup = build_label_element(self.labelled_layer())
        assert 'type="TextLayer"' in markup
        assert 'get-text="$label"' in markup

    def test_placement_quadrant_becomes_anchor_and_baseline(self) -> None:
        """QGIS "above left" pins the text's end and bottom to the point."""
        markup = build_label_element(
            self.labelled_layer(anchor="end", baseline="bottom")
        )
        assert "get-text-anchor=\"'end'\"" in markup
        assert "get-text-alignment-baseline=\"'bottom'\"" in markup

    def test_a_centred_label_emits_no_placement_noise(self) -> None:
        markup = build_label_element(self.labelled_layer())
        assert "get-text-anchor" not in markup
        assert "get-text-alignment-baseline" not in markup

    def test_offset_rotation_and_weight_are_carried(self) -> None:
        markup = build_label_element(
            self.labelled_layer(offset_x=4.0, offset_y=-2.0, rotation=45.0, bold=True)
        )
        assert 'get-text-pixel-offset="[4, -2]"' in markup
        assert 'get-text-angle="45"' in markup
        assert 'text-font-weight="bold"' in markup

    def test_label_background_needs_its_flag(self) -> None:
        """Colour and padding are ignored silently without text-background."""
        markup = build_label_element(
            self.labelled_layer(
                background_color=Color(r=255, g=255, b=255),
                background_padding=(3.0, 2.0),
            )
        )
        assert 'text-background="true"' in markup
        assert "get-text-background-color=\"'#ffffff'\"" in markup
        assert 'text-background-padding="[3, 2]"' in markup

    def test_the_text_layer_carries_only_label_points(self) -> None:
        """Not the source geometry: that would embed the data twice."""
        markup = build_label_element(self.labelled_layer())
        payload = json.loads(
            markup.split('<script type="application/json">')[1].split("</script>")[0]
        )

        (label_feature,) = payload["features"]
        assert label_feature["geometry"] == {
            "type": "Point",
            "coordinates": [1.0, 2.0],
        }
        assert label_feature["properties"] == {"label": "Alpha"}

    def test_labels_are_not_pickable(self) -> None:
        """A pickable text layer swallows the clicks meant for the geometry."""
        markup = build_label_element(self.labelled_layer())
        assert "pickable" not in markup

    def test_font_size_is_in_pixels(self) -> None:
        """In metres, labels balloon on zoom-in and vanish on zoom-out."""
        markup = build_label_element(self.labelled_layer(font_size=18.0))
        assert 'get-text-size="18"' in markup
        assert 'text-size-units="pixels"' in markup

    def test_a_qgis_buffer_becomes_a_deck_outline(self) -> None:
        markup = build_label_element(
            self.labelled_layer(halo_width=2.0, halo_color=BLUE)
        )
        assert 'text-outline-width="2"' in markup
        assert "text-outline-color" in markup

    def test_the_readers_character_set_wins(self) -> None:
        markup = build_label_element(self.labelled_layer(character_set="Zü"))
        assert 'text-character-set="Zü"' in markup

    def test_a_label_field_with_no_values_emits_nothing(self) -> None:
        layer = make_layer(
            geojson=self.LABELLED,
            labeling=LabelingSpec(enabled=True, field_name="gone"),
        )
        assert build_label_element(layer) == ""

    def test_labels_follow_their_layers_scale_visibility(self) -> None:
        """A label outliving the feature it names reads as a rendering bug."""
        layer = make_layer(
            geojson=self.LABELLED,
            labeling=LabelingSpec(enabled=True, field_name="name"),
            scale_range=ScaleRange(min_scale=1_000_000, max_scale=1_000),
        )
        markup = build_label_element(layer)
        assert "visible-min-zoom" in markup
        assert "visible-max-zoom" in markup

    def test_label_layers_are_emitted_after_every_geometry_layer(self) -> None:
        """Otherwise the layer stacked above paints over the labels below it."""
        bottom = self.labelled_layer()
        top = make_layer(layer_id="top", name="Top", geojson=self.LABELLED)
        markup = build_manifest(make_project([bottom, top]))

        assert markup.index('id="top"') < markup.index('id="layer1-labels"')


class TestAttributionCollection:
    """Attribution was read from QGIS, stored, snapshotted - and never rendered.

    OnlyMap has no layer-level attribution: its control credits the *basemap*
    provider, and 0.1.0 ships no basemap. So the artifact must render these
    itself or the credit never reaches the recipient.
    """

    def test_no_credits_yields_an_empty_tuple(self) -> None:
        assert collect_attributions(make_project()) == ()

    def test_credits_are_collected_in_draw_order(self) -> None:
        project = make_project(
            [
                make_layer(layer_id="a", attribution="Statistics Canada"),
                make_layer(layer_id="b", attribution="OSM contributors"),
            ]
        )
        assert collect_attributions(project) == (
            "Statistics Canada",
            "OSM contributors",
        )

    def test_duplicate_credits_appear_once(self) -> None:
        project = make_project(
            [
                make_layer(layer_id="a", attribution="OSM contributors"),
                make_layer(layer_id="b", attribution="OSM contributors"),
            ]
        )
        assert collect_attributions(project) == ("OSM contributors",)

    def test_blank_credits_are_ignored(self) -> None:
        project = make_project(
            [
                make_layer(layer_id="a", attribution="   "),
                make_layer(layer_id="b", attribution=None),
            ]
        )
        assert collect_attributions(project) == ()


class TestThinLinesStayClickable:
    """deck.gl picks against what it drew, so drawn width *is* hit width.

    A QGIS hairline exports as a sub-pixel line: visible, and effectively
    impossible to click, which makes its popup unreachable. Testing found this.
    """

    def _line_layer(self, width: float):
        from nika_onlymap_exporter.core.export_ir import (
            Color,
            ExportLayer,
            GeometryKind,
            RendererKind,
            RendererSpec,
            SourceKind,
            SymbolSpec,
        )

        return ExportLayer(
            layer_id="l",
            name="Rivers",
            geometry_kind=GeometryKind.LINE,
            source_kind=SourceKind.FILE,
            feature_count=1,
            geojson={"type": "FeatureCollection", "features": []},
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(
                    stroke_color=Color(r=0, g=0, b=255), stroke_width=width
                ),
            ),
        )

    def test_a_hairline_gets_a_minimum_drawn_width(self) -> None:
        markup = build_layer_element(self._line_layer(0.26))
        assert "line-width-min-pixels" in markup

    def test_the_authored_width_is_still_emitted(self) -> None:
        """The floor must not overwrite what QGIS actually said."""
        markup = build_layer_element(self._line_layer(0.26))
        assert 'get-line-width="0.26"' in markup

    def test_polygons_do_not_get_a_thickened_outline(self) -> None:
        """A polygon is picked by its interior; a fat outline would eat it."""
        from nika_onlymap_exporter.core.export_ir import (
            Color,
            ExportLayer,
            GeometryKind,
            RendererKind,
            RendererSpec,
            SourceKind,
            SymbolSpec,
        )

        layer = ExportLayer(
            layer_id="p",
            name="Parcels",
            geometry_kind=GeometryKind.POLYGON,
            source_kind=SourceKind.FILE,
            feature_count=1,
            geojson={"type": "FeatureCollection", "features": []},
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(
                    fill_color=Color(r=1, g=2, b=3),
                    stroke_color=Color(r=0, g=0, b=0),
                    stroke_width=0.26,
                ),
            ),
        )
        assert "line-width-min-pixels" not in build_layer_element(layer)


class TestExtrusion:
    """Raised polygons, from either place QGIS keeps a height.

    The invariant worth guarding is the pitch: without it the map opens looking
    straight down, where an extruded scene and a flat one are the same picture,
    and the whole feature reads as not working.
    """

    def _raised(self, **elevation):
        return make_layer(
            layer_id="buildings",
            geometry_kind=GeometryKind.POLYGON,
            elevation=ElevationSpec(**elevation),
        )

    def test_a_constant_height_becomes_a_number(self) -> None:
        markup = build_layer_element(self._raised(extruded=True, height=12.0))
        assert 'extruded="true"' in markup
        assert 'get-elevation="12"' in markup

    def test_a_field_driven_height_becomes_an_accessor(self) -> None:
        markup = build_layer_element(self._raised(extruded=True, height_field="floors"))
        assert 'get-elevation="$floors"' in markup

    def test_a_flat_layer_emits_nothing(self) -> None:
        markup = build_layer_element(make_layer())
        assert "extruded" not in markup
        assert "get-elevation" not in markup

    def test_extruded_without_a_height_is_not_extruded(self) -> None:
        # A 3D renderer set to zero height draws nothing in QGIS either; the
        # attribute pair would only cost bytes.
        markup = build_layer_element(self._raised(extruded=True))
        assert "extruded" not in markup

    def test_edges_become_a_wireframe(self) -> None:
        markup = build_layer_element(
            self._raised(extruded=True, height=5.0, wireframe=True)
        )
        assert 'wireframe="true"' in markup

    def test_edges_off_emits_nothing(self) -> None:
        markup = build_layer_element(self._raised(extruded=True, height=5.0))
        assert "wireframe" not in markup

    def test_a_raised_layer_tilts_the_map(self) -> None:
        markup = build_manifest(make_project([self._raised(extruded=True, height=5.0)]))
        assert 'pitch="45"' in markup

    def test_a_flat_map_is_not_tilted(self) -> None:
        assert "pitch" not in build_manifest(make_project())


class TestTerrain:
    def test_a_preset_is_emitted_and_tilts_the_map(self) -> None:
        markup = build_manifest(
            make_project(settings=ExportSettings(terrain="terrarium"))
        )
        assert 'terrain="terrarium"' in markup
        assert 'pitch="45"' in markup

    def test_off_by_default(self) -> None:
        markup = build_manifest(make_project())
        assert "terrain" not in markup

    def test_an_unknown_preset_is_dropped_rather_than_passed_through(self) -> None:
        # Same rule as the basemap: a DEM the runtime cannot resolve gives blank
        # relief, which is worse than the flat map the user could have had.
        markup = build_manifest(
            make_project(settings=ExportSettings(terrain="whatever"))
        )
        assert "terrain=" not in markup
        assert "pitch" not in markup

    def test_the_note_names_the_host_and_the_tilt(self) -> None:
        note = terrain_note("terrarium")
        assert note is not None
        assert "s3.amazonaws.com" in note
        assert "tilted" in note

    def test_no_note_when_flat(self) -> None:
        assert terrain_note("none") is None


# --------------------------------------------------------------------------
# Symbol atlas: markers QGIS had to draw itself
# --------------------------------------------------------------------------

ATLAS = IconAtlasSpec(
    data_uri="data:image/png;base64,SHEET",
    mapping={
        "i0": {
            "x": 0,
            "y": 0,
            "width": 30,
            "height": 30,
            "anchorX": 15,
            "anchorY": 15,
            "mask": False,
        },
        "i1": {
            "x": 30,
            "y": 0,
            "width": 30,
            "height": 30,
            "anchorX": 15,
            "anchorY": 15,
            "mask": False,
        },
    },
    swatches={
        "i0": "data:image/png;base64,SWATCH0",
        "i1": "data:image/png;base64,SWATCH1",
    },
    supersample=3,
)


def icon_symbol(name: str, size: float, **overrides) -> SymbolSpec:
    defaults = dict(fill_color=RED, marker_shape="star", radius=size / 2)
    defaults.update(overrides)
    return SymbolSpec(icon_name=name, icon_size=size, **defaults)


def make_icon_layer(renderer: RendererSpec, **overrides) -> ExportLayer:
    return make_layer(renderer=renderer, icon_atlas=ATLAS, **overrides)


SINGLE_ICON = RendererSpec(kind=RendererKind.SINGLE, symbol=icon_symbol("i0", 12.0))

CATEGORIZED_ICONS = RendererSpec(
    kind=RendererKind.CATEGORIZED,
    field_name="kind",
    categories=(
        CategorySpec(value="civil", label="Civil", symbol=icon_symbol("i0", 12.0)),
        CategorySpec(value="metro", label="Metro", symbol=icon_symbol("i1", 20.0)),
    ),
)

GRADUATED_ICONS = RendererSpec(
    kind=RendererKind.GRADUATED,
    field_name="pop",
    classes=(
        GraduatedClassSpec(
            lower=0, upper=100, label="0 - 100", symbol=icon_symbol("i0", 12.0)
        ),
        GraduatedClassSpec(
            lower=100, upper=500, label="100 - 500", symbol=icon_symbol("i1", 20.0)
        ),
    ),
)


class TestIconLayer:
    """A point layer whose markers were rasterised.

    The whole feature turns on one decision: the layer stays a `GeoJsonLayer`
    and swaps its internal point sublayer via `point-type`, rather than becoming
    an `IconLayer`. An `IconLayer` takes rows with a position accessor, not
    GeoJSON, so that route would ship the coordinates a second time and drop any
    non-point geometry sharing the file.
    """

    def test_the_layer_class_does_not_change(self) -> None:
        markup = build_layer_element(make_icon_layer(SINGLE_ICON))
        assert 'type="GeoJsonLayer"' in markup
        assert 'type="IconLayer"' not in markup

    def test_the_point_sublayer_switches_to_icons(self) -> None:
        markup = build_layer_element(make_icon_layer(SINGLE_ICON))
        assert 'point-type="icon"' in markup

    def test_the_sheet_and_its_mapping_are_emitted(self) -> None:
        markup = build_layer_element(make_icon_layer(SINGLE_ICON))
        assert 'icon-atlas="data:image/png;base64,SHEET"' in markup
        assert "icon-mapping=" in markup
        # The mapping travels as JSON, so its quotes are escaped for the
        # attribute rather than ending it early.
        assert "&quot;anchorX&quot;" in markup

    def test_sizes_are_pixels(self) -> None:
        """Without this deck.gl reads the size as metres: markers balloon on
        zoom in and vanish on zoom out."""
        markup = build_layer_element(make_icon_layer(SINGLE_ICON))
        assert 'icon-size-units="pixels"' in markup

    def test_a_single_symbol_names_its_icon_as_a_literal(self) -> None:
        assert icon_expression(SINGLE_ICON) == "'i0'"

    def test_a_categorized_layer_gets_a_ternary_chain(self) -> None:
        expression = icon_expression(CATEGORIZED_ICONS)
        assert expression == (
            "$kind == 'civil' ? 'i0' : $kind == 'metro' ? 'i1' : 'i0'"
        )

    def test_the_categorized_fallback_is_a_real_icon(self) -> None:
        """deck.gl draws nothing at all for an icon name it cannot resolve, so
        a feature outside every class would silently vanish."""
        expression = icon_expression(CATEGORIZED_ICONS)
        assert expression.rsplit(" : ", 1)[-1] in ("'i0'", "'i1'")

    def test_a_graduated_layer_gets_a_threshold_scale(self) -> None:
        assert icon_expression(GRADUATED_ICONS) == (
            "scale($pop, threshold, ['i0', 'i1'], domain=[100])"
        )

    def test_per_class_sizes_survive(self) -> None:
        """A graduated-by-size layer of icons is the case this rescues: without
        it every class draws at one size and the map's whole point is lost."""
        markup = build_layer_element(make_icon_layer(GRADUATED_ICONS))
        assert (
            'get-icon-size="scale($pop, threshold, [12, 20], domain=[100])"' in markup
        )

    def test_one_size_stays_a_constant(self) -> None:
        markup = build_layer_element(make_icon_layer(SINGLE_ICON))
        assert 'get-icon-size="12"' in markup

    def test_the_circle_radius_is_not_also_emitted(self) -> None:
        """The circle sublayer is not drawn when `point-type` is icon, so a
        radius alongside it is noise that reads as a contradiction."""
        markup = build_layer_element(make_icon_layer(SINGLE_ICON))
        assert "get-point-radius=" not in markup

    def test_a_marker_offset_becomes_a_pixel_offset(self) -> None:
        renderer = RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=icon_symbol("i0", 12.0, offset_x=3.0, offset_y=-4.0),
        )
        markup = build_layer_element(make_icon_layer(renderer))
        assert 'get-icon-pixel-offset="[3, -4]"' in markup

    def test_a_layer_without_an_atlas_is_untouched(self) -> None:
        """Byte-for-byte the output it always had: almost every layer is this
        one, and a change here would be a regression for all of them."""
        markup = build_layer_element(
            make_layer(
                renderer=RendererSpec(
                    kind=RendererKind.SINGLE,
                    symbol=SymbolSpec(fill_color=RED, radius=5.0),
                )
            )
        )
        for attribute in ("point-type", "icon-atlas", "get-icon", "icon-mapping"):
            assert attribute not in markup
        assert "get-point-radius=" in markup

    def test_an_atlas_without_icon_names_emits_nothing(self) -> None:
        """A half-built atlas must not produce `get-icon` pointing at names the
        mapping does not contain - that draws an empty map, not a fallback."""
        renderer = RendererSpec(
            kind=RendererKind.SINGLE, symbol=SymbolSpec(fill_color=RED, radius=5.0)
        )
        markup = build_layer_element(make_icon_layer(renderer))
        assert "point-type=" not in markup

    def test_every_icon_attribute_exists_in_the_schema(self, known_attributes) -> None:
        project = make_project([make_icon_layer(CATEGORIZED_ICONS)])
        markup = build_manifest(project)

        unknown = [
            f"{element}[{name}]"
            for element, attributes in re.findall(r"<(om-[a-z]+)\b([^>]*)>", markup)
            for name in re.findall(r'(?:^|\s)([a-z][a-z0-9-]*)="', attributes)
            if name not in known_attributes.get(element, set())
        ]
        assert not unknown, f"attributes absent from the OnlyMap schema: {unknown}"


class TestImageLegend:
    """The legend has to show the markers the map draws, not an approximation.

    A hand-drawn swatch beside a rasterised marker is exactly the divergence
    this widget exists to prevent - and it is why the swatch is a picture cut
    from the same rendering the map uses.
    """

    def test_a_project_without_icons_keeps_the_built_in_widget(self) -> None:
        """The built-in legend is interactive and follows layer visibility. It
        is kept for almost every project, so we do not fork it forever."""
        widgets = build_widget_elements(make_project())
        assert 'type="legend"' in widgets

    def test_a_project_with_icons_needs_its_own(self) -> None:
        assert needs_image_legend(make_project([make_icon_layer(CATEGORIZED_ICONS)]))
        assert not needs_image_legend(make_project())

    def test_the_custom_legend_replaces_the_built_in_one(self) -> None:
        widgets = build_widget_elements(
            make_project([make_icon_layer(CATEGORIZED_ICONS)])
        )
        assert 'type="legend"' not in widgets
        assert "omni-panel" in widgets

    def test_it_carries_no_script(self) -> None:
        """A widget with no type and no `om/widget` script is purely static, so
        this stays markup rather than the generated JavaScript this project
        exists not to write."""
        markup = build_legend_widget(make_project([make_icon_layer(SINGLE_ICON)]))
        assert "<script" not in markup

    def test_swatches_come_from_the_atlas(self) -> None:
        markup = build_legend_widget(make_project([make_icon_layer(CATEGORIZED_ICONS)]))
        assert "data:image/png;base64,SWATCH0" in markup
        assert "data:image/png;base64,SWATCH1" in markup

    def test_class_labels_are_shown(self) -> None:
        markup = build_legend_widget(make_project([make_icon_layer(CATEGORIZED_ICONS)]))
        assert "Civil" in markup and "Metro" in markup

    def test_a_categorized_layer_explains_its_other_row(self) -> None:
        """Features outside every class are drawn in the fallback colour, so
        the legend has to account for them."""
        markup = build_legend_widget(make_project([make_icon_layer(CATEGORIZED_ICONS)]))
        assert ">other<" in markup

    def test_a_graduated_layer_lists_its_ranges(self) -> None:
        markup = build_legend_widget(make_project([make_icon_layer(GRADUATED_ICONS)]))
        assert "0 - 100" in markup and "100 - 500" in markup

    def test_a_colour_layer_beside_an_icon_layer_keeps_a_colour_swatch(self) -> None:
        """A mixed project must not lose the plain layers: once the custom
        legend takes over it is the only legend, so it renders everything."""
        project = make_project(
            [
                make_icon_layer(SINGLE_ICON),
                make_layer(
                    layer_id="plain",
                    name="Plain layer",
                    renderer=RendererSpec(
                        kind=RendererKind.SINGLE, symbol=SymbolSpec(fill_color=BLUE)
                    ),
                ),
            ]
        )
        markup = build_legend_widget(project)
        assert "Plain layer" in markup
        assert "background:#0000ff" in markup

    def test_its_styles_travel_inside_it(self) -> None:
        """`om-widget` moves its children into a shadow root, which document
        rules never reach - the same boundary the popup styles hit."""
        markup = build_legend_widget(make_project([make_icon_layer(SINGLE_ICON)]))
        opening = markup.index("<om-widget")
        closing = markup.index("</om-widget>")
        assert opening < markup.index("<style>") < closing

    def test_it_uses_the_runtime_theming_hooks(self) -> None:
        """Custom properties inherit through a shadow root, so a user theming
        the map still themes this - and a map with SVG markers does not look
        like a different product from one without."""
        markup = build_legend_widget(make_project([make_icon_layer(SINGLE_ICON)]))
        assert "--om-widget-bg" in markup
        assert "--om-widget-fg" in markup

    def test_it_sits_where_the_built_in_legend_sits(self) -> None:
        markup = build_legend_widget(make_project([make_icon_layer(SINGLE_ICON)]))
        assert f'position="{WIDGET_POSITIONS["legend"]}"' in markup

    def test_it_is_suppressed_with_the_legend_setting(self) -> None:
        project = make_project(
            [make_icon_layer(SINGLE_ICON)],
            settings=ExportSettings(show_legend=False),
        )
        assert "omni-panel" not in build_widget_elements(project)

    def test_layer_names_are_escaped(self) -> None:
        """Layer names are user text and reach the artifact as markup."""
        project = make_project([make_icon_layer(SINGLE_ICON, name='<img onerror="x">')])
        markup = build_legend_widget(project)
        assert "<img onerror" not in markup
        assert "&lt;img onerror" in markup


class TestPopupsDoNotStack:
    """Only one popup may be open at a time.

    `show-overlay` sets `visible="true"` and nothing ever sets it back, and the
    runtime dispatches behaviours only when there *is* a pick - there is no
    unhover event. So every popup ever opened stayed open, and both symptoms
    below came back from a real project.
    """

    @staticmethod
    def _project(hover: bool = True, count: int = 3) -> ExportProject:
        return make_project(
            [
                make_layer(
                    layer_id=f"layer{i}",
                    name=f"Layer {i}",
                    popup=PopupSpec(
                        enabled=True,
                        fields=(PopupFieldSpec("name"),),
                        on_hover=hover,
                    ),
                )
                for i in range(count)
            ]
        )

    def test_every_popup_gets_a_close_behavior(self) -> None:
        markup = build_manifest(self._project())
        for i in range(3):
            assert f'action="hide-overlay" target="layer{i}-popup"' in markup, (
                f"layer{i} has no way to close"
            )

    def test_the_close_behaviors_are_not_scoped_to_a_layer(self) -> None:
        """A pick on *any* layer has to close the others, so these must carry
        no `layer` filter - the runtime skips a scoped behaviour whose layer
        does not match the pick."""
        for line in build_popup_reset_behaviors(self._project()).splitlines():
            assert "layer=" not in line, line

    def test_every_close_precedes_every_open(self) -> None:
        """The load-bearing property. `dispatchToBehaviors` walks the behaviours
        in document order and dispatches synchronously, so a hide emitted after
        a show would close the popup that pick had just opened - turning a
        stacking bug into a map where popups never appear at all."""
        markup = build_manifest(self._project())
        last_hide = markup.rindex('action="hide-overlay"')
        first_show = markup.index('action="show-overlay"')
        assert last_hide < first_show

    def test_a_hover_project_emits_no_click_resets(self) -> None:
        markup = build_popup_reset_behaviors(self._project(hover=True))
        assert 'on="hover"' in markup
        assert 'on="click"' not in markup

    def test_a_click_project_emits_no_hover_resets(self) -> None:
        markup = build_popup_reset_behaviors(self._project(hover=False))
        assert 'on="click"' in markup
        assert 'on="hover"' not in markup

    def test_a_mixed_project_emits_both(self) -> None:
        """A project can set hover per layer, and a pick of either kind has to
        close whatever is open."""
        project = make_project(
            [
                make_layer(
                    layer_id="hovered",
                    popup=PopupSpec(
                        enabled=True, fields=(PopupFieldSpec("name"),), on_hover=True
                    ),
                ),
                make_layer(
                    layer_id="clicked",
                    popup=PopupSpec(
                        enabled=True, fields=(PopupFieldSpec("name"),), on_hover=False
                    ),
                ),
            ]
        )
        markup = build_popup_reset_behaviors(project)
        assert 'on="hover"' in markup and 'on="click"' in markup
        for target in ("hovered-popup", "clicked-popup"):
            assert markup.count(f'target="{target}"') == 2

    def test_a_project_with_no_popups_emits_nothing(self) -> None:
        assert build_popup_reset_behaviors(make_project()) == ""

    def test_a_layer_without_a_popup_gets_no_reset(self) -> None:
        """A layer whose popups the author turned off has no overlay to close,
        and a hide aimed at a missing overlay logs a runtime warning."""
        project = make_project(
            [
                make_layer(
                    layer_id="withpopup",
                    popup=PopupSpec(
                        enabled=True, fields=(PopupFieldSpec("name"),), on_hover=True
                    ),
                ),
                make_layer(layer_id="silent", popup=PopupSpec(enabled=False)),
            ]
        )
        markup = build_popup_reset_behaviors(project)
        assert "withpopup-popup" in markup
        assert "silent" not in markup

    def test_the_reset_attributes_exist_in_the_schema(self, known_attributes) -> None:
        markup = build_manifest(self._project())
        unknown = [
            f"{element}[{name}]"
            for element, attributes in re.findall(r"<(om-[a-z]+)\b([^>]*)>", markup)
            for name in re.findall(r'(?:^|\s)([a-z][a-z0-9-]*)="', attributes)
            if name not in known_attributes.get(element, set())
        ]
        assert not unknown, f"attributes absent from the OnlyMap schema: {unknown}"

    def test_overlays_stay_unscoped(self) -> None:
        """Scoping an overlay with `layer=` is the fix that suggests itself and
        is wrong: an overlay hides because an *unscoped* one follows a null
        selection to nowhere. Scoped, it would stay stranded on screen after the
        cursor left every feature."""
        markup = build_manifest(self._project())
        for overlay in re.findall(r"<om-overlay\b[^>]*>", markup):
            assert "layer=" not in overlay, overlay


class TestLicenseKeyReachesTheMarkup:
    """The last link in the chain a paying customer depends on.

    The dialog collects a key, the policy carries it, the verdict holds it - and
    none of that matters unless it lands on `<om-map>`, which is the only thing
    the runtime reads.
    """

    def test_a_licensed_verdict_emits_the_attribute(self) -> None:
        from nika_onlymap_exporter.core.license_policy import LicensedPolicy

        project = make_project()
        verdict = LicensedPolicy("om_live_eyJhIjoxfQ.c2ln").evaluate(project)

        assert 'license-key="om_live_eyJhIjoxfQ.c2ln"' in build_manifest(
            project, verdict
        )

    def test_the_free_tier_emits_no_attribute(self) -> None:
        """An unlicensed map must not carry an empty or placeholder key."""
        project = make_project()
        verdict = FreeTierPolicy().evaluate(project)
        assert "license-key" not in build_manifest(project, verdict)

    def test_a_licensed_map_does_not_wear_the_error_panel(self) -> None:
        """`validate` mounts a diagnostic badge. A licensed map has nothing to
        report, so it must not arrive wearing one."""
        from nika_onlymap_exporter.core.license_policy import (
            FREE_TIER_MAX_ROWS_PER_LAYER,
            LicensedPolicy,
        )

        over_cap = make_layer(feature_count=FREE_TIER_MAX_ROWS_PER_LAYER + 1)
        project = make_project([over_cap])

        licensed = LicensedPolicy("om_live_eyJhIjoxfQ.c2ln").evaluate(project)
        assert not licensed.needs_runtime_validation

        # Nor does the same project without a key, since runtime 0.6.0 - the
        # caps it breaches do not apply where these artifacts are opened.
        assert not FreeTierPolicy().evaluate(project).needs_runtime_validation


class TestDashedLines:
    """QGIS dash patterns reaching the map.

    They were read from QGIS into the model and then dropped at the writer, so
    every dashed line exported solid. The runtime has taken `dash` since 0.5.10.
    """

    def _symbol(self, **overrides) -> SymbolSpec:
        base = {"stroke_width": 2.0, "stroke_color": Color(r=0, g=0, b=0)}
        base.update(overrides)
        return SymbolSpec(**base)

    def test_a_solid_line_emits_nothing(self) -> None:
        assert dash_attribute(self._symbol()) is None

    def test_pixels_become_line_width_units(self) -> None:
        """deck.gl's dashArray is in widths, not pixels - 8px on a 2px line is 4."""
        assert dash_attribute(self._symbol(stroke_dash=(8.0, 4.0))) == "[4, 2]"

    def test_the_pattern_scales_with_the_line_width(self) -> None:
        """The same pixels on a thicker line must not dash the same."""
        thin = dash_attribute(self._symbol(stroke_width=2.0, stroke_dash=(8.0, 4.0)))
        thick = dash_attribute(self._symbol(stroke_width=4.0, stroke_dash=(8.0, 4.0)))
        assert thin != thick

    def test_a_dash_dot_pattern_is_cut_to_one_pair(self) -> None:
        """deck.gl strokes exactly one on/off pair."""
        symbol = self._symbol(stroke_dash=(8.0, 4.0, 2.0, 4.0))
        assert dash_attribute(symbol) == "[4, 2]"

    def test_a_zero_dash_length_is_refused(self) -> None:
        """The runtime rejects it outright; better to export a solid line."""
        assert dash_attribute(self._symbol(stroke_dash=(0.0, 4.0))) is None

    def test_a_widthless_line_is_refused(self) -> None:
        """Dividing by a zero width is how a NaN reaches the markup."""
        assert (
            dash_attribute(self._symbol(stroke_width=0.0, stroke_dash=(8.0, 4.0)))
            is None
        )

    def test_it_reaches_the_layer_element(self) -> None:
        layer = make_layer(
            geometry_kind=GeometryKind.LINE,
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=self._symbol(stroke_dash=(8.0, 4.0)),
            ),
        )
        assert 'dash="[4, 2]"' in build_layer_element(layer)
