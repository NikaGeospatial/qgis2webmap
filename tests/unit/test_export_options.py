"""Tranche 1's remaining options: caption, widget colours, precision, extent.

All pure, so they run without QGIS. The recurring assertion is that a *default*
export is unchanged - every one of these is opt-in, and an artifact built with
none of them switched on has to be byte-identical to one built before they
existed, or the fixture tier's release gates become meaningless.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    ExtentSource,
    GeometryKind,
    OverlayCorner,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.manifest_builder import (
    BASEMAP_PRESETS,
    basemap_note,
    build_manifest,
)
from nika_onlymap_exporter.core.settings import (
    MAX_PRECISION,
    PRECISION_FULL,
    DialogState,
    LayerSettings,
    parse_hex_color,
)
from nika_onlymap_exporter.writers.onlymap_writer import (
    _caption_block,
    _widget_color_block,
)

TEAL = Color(r=29, g=233, b=200)


def make_project(**settings) -> ExportProject:
    return ExportProject(
        title="Test map",
        abstract="A description of the map.",
        extent=Extent(west=0.0, south=0.0, east=10.0, north=10.0),
        settings=ExportSettings(**settings),
    )


class TestCaption:
    def test_the_title_is_drawn_by_default(self) -> None:
        """Changed deliberately: a map that does not say what it is has no context.

        Testing found people expected the title on the map and did not think to
        look for a setting. The legend gives up its own heading in exchange, so
        the title still appears exactly once.
        """
        markup = _caption_block(make_project())
        assert "om-caption-title" in markup
        assert "Test map" in markup

    def test_the_default_position_is_clear_of_the_map_controls(self) -> None:
        """All four corners hold chrome; only the centres are free."""
        assert "om-caption-top-center" in _caption_block(make_project())

    def test_nothing_is_emitted_when_both_parts_are_switched_off(self) -> None:
        project = make_project(show_title=False, show_abstract=False)
        assert _caption_block(project) == ""

    def test_the_title_appears_when_asked_for(self) -> None:
        markup = _caption_block(make_project(show_title=True))
        assert "Test map" in markup
        assert "om-caption-title" in markup

    def test_the_abstract_appears_when_asked_for(self) -> None:
        markup = _caption_block(make_project(show_abstract=True))
        assert "A description of the map." in markup

    def test_an_absent_abstract_emits_nothing_rather_than_an_empty_box(self) -> None:
        project = ExportProject(
            title="Test map",
            abstract=None,
            settings=ExportSettings(show_abstract=True, show_title=False),
        )
        assert _caption_block(project) == ""

    def test_the_corner_reaches_the_class_name(self) -> None:
        markup = _caption_block(
            make_project(show_title=True, title_corner=OverlayCorner.BOTTOM_RIGHT)
        )
        assert "om-caption-bottom-right" in markup

    def test_project_metadata_is_escaped_not_trusted(self) -> None:
        """Both strings are author-controlled text, not markup."""
        project = ExportProject(
            title="<script>alert(1)</script>",
            settings=ExportSettings(show_title=True),
        )
        markup = _caption_block(project)
        assert "<script>" not in markup
        assert "&lt;script&gt;" in markup


class TestWidgetColors:
    def test_nothing_is_emitted_by_default(self) -> None:
        """A default export has to stay byte-identical."""
        assert _widget_color_block(make_project()) == ""

    def test_a_background_becomes_a_custom_property(self) -> None:
        markup = _widget_color_block(make_project(widget_background=TEAL))
        assert "--om-widget-bg: #1de9c8;" in markup

    def test_a_foreground_becomes_a_custom_property(self) -> None:
        markup = _widget_color_block(make_project(widget_foreground=TEAL))
        assert "--om-widget-fg: #1de9c8;" in markup

    def test_either_colour_alone_is_enough(self) -> None:
        assert _widget_color_block(make_project(widget_background=TEAL)) != ""
        assert _widget_color_block(make_project(widget_foreground=TEAL)) != ""


class TestHighlightColour:
    """qgis2web has no control for this; ours is the whole improvement.

    Theirs reuses `mapSettings.selectionColor()` - the QGIS *editing selection*
    colour, opaque yellow by default - as a web hover cue, and the only way to
    change it is Project Properties, outside the plugin. Open since 2015 as
    qgis2web#132.
    """

    @staticmethod
    def _highlight(**settings) -> str:
        from nika_onlymap_exporter.core.manifest_builder import build_layer_element

        layer = ExportLayer(
            layer_id="l",
            name="L",
            geometry_kind=GeometryKind.POINT,
            source_kind=SourceKind.FILE,
            feature_count=1,
            geojson={"type": "FeatureCollection", "features": []},
            renderer=RendererSpec(
                kind=RendererKind.SINGLE, symbol=SymbolSpec(fill_color=TEAL)
            ),
            popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("name"),)),
        )
        markup = build_layer_element(layer, **settings)
        (line,) = [ln for ln in markup.splitlines() if "highlight-color" in ln]
        return line.strip()

    def test_the_default_is_translucent_not_opaque(self) -> None:
        """The exact failure mode of the incumbent: a solid fill over the map."""
        line = self._highlight()
        assert line == "highlight-color=\"'#ffffff55'\""

    def test_a_chosen_colour_reaches_the_layer(self) -> None:
        line = self._highlight(highlight_color=Color(r=29, g=233, b=200, a=0.5))
        assert "#1de9c880" in line

    def test_a_chosen_colour_keeps_its_transparency(self) -> None:
        line = self._highlight(highlight_color=Color(r=0, g=0, b=0, a=0.25))
        assert line.endswith("40'\"")


class TestPerLayerOverrides:
    """The three settings qgis2web has kept global-only since 2015.

    Its issues #131 (precision), #132 (highlight) and #133 (hover popups) each
    ask for a per-layer option and each is still open. A tri-state is the point:
    without one, "all layers alike except this one" means configuring every
    layer by hand.
    """

    def test_everything_inherits_by_default(self) -> None:
        settings = LayerSettings()
        assert settings.resolved_hover(True) is True
        assert settings.resolved_hover(False) is False
        assert settings.resolved_precision(6) == 6
        assert settings.resolved_precision(None) is None
        assert settings.resolved_highlight("#1de9c880") == parse_hex_color("#1de9c880")

    def test_an_override_beats_the_map_wide_value(self) -> None:
        settings = LayerSettings(
            popup_on_hover=False, quantize_precision=2, highlight_color="#ff000080"
        )
        assert settings.resolved_hover(True) is False
        assert settings.resolved_precision(6) == 2
        highlight = settings.resolved_highlight("#1de9c880")
        assert highlight is not None
        assert (highlight.r, highlight.g, highlight.b) == (0xFF, 0, 0)

    def test_a_layer_can_keep_full_precision_while_the_map_rounds(self) -> None:
        """Distinct from inheriting: the whole reason 0 is a separate marker."""
        settings = LayerSettings(quantize_precision=PRECISION_FULL)
        assert settings.resolved_precision(4) is None

    def test_overrides_are_absent_from_a_default_layer_s_json(self) -> None:
        """A project saved before overrides existed must round-trip unchanged."""
        data = LayerSettings().to_dict()
        for key in ("popupOnHover", "highlightColor", "quantizePrecision"):
            assert key not in data

    def test_overrides_round_trip(self) -> None:
        original = LayerSettings(
            popup_on_hover=True, quantize_precision=3, highlight_color="#00ff0040"
        )
        restored = LayerSettings.from_dict(original.to_dict())
        assert restored.popup_on_hover is True
        assert restored.quantize_precision == 3
        assert restored.highlight_color == "#00ff0040"

    def test_a_malformed_override_falls_back_to_inheriting(self) -> None:
        restored = LayerSettings.from_dict(
            {"quantizePrecision": "not a number", "popupOnHover": None}
        )
        assert restored.quantize_precision is None
        assert restored.popup_on_hover is None
        assert restored.resolved_precision(5) == 5

    def test_an_out_of_range_precision_inherits_rather_than_rounding_wildly(
        self,
    ) -> None:
        assert LayerSettings.from_dict(
            {"quantizePrecision": 99}
        ).quantize_precision is (None)


class TestHexColorParsing:
    def test_unset_is_none_not_black(self) -> None:
        """The difference between "no choice" and "black" is the whole default."""
        assert parse_hex_color("") is None

    def test_a_hash_prefix_is_optional(self) -> None:
        assert parse_hex_color("#1de9c8") == parse_hex_color("1de9c8")

    def test_channels_are_read_in_order(self) -> None:
        color = parse_hex_color("#1de9c8")
        assert color is not None
        assert (color.r, color.g, color.b) == (0x1D, 0xE9, 0xC8)

    def test_junk_is_none_rather_than_an_exception(self) -> None:
        for value in ("nonsense", "#12", "#gggggg", "#1de9c", None):
            assert parse_hex_color(value) is None

    def test_the_eight_digit_form_carries_alpha_last(self) -> None:
        """CSS order, matching what the manifest emits, so it round-trips."""
        color = parse_hex_color("#1de9c880")
        assert color is not None
        assert (color.r, color.g, color.b) == (0x1D, 0xE9, 0xC8)
        assert color.a == pytest.approx(128 / 255)


class TestLossyPrecision:
    def test_precision_is_off_by_default(self) -> None:
        assert ExportSettings().quantize_precision is None
        assert ExportSettings().has_lossy_transform is False

    def test_setting_precision_marks_the_export_lossy(self) -> None:
        """The fidelity report keys off this - rounding discards data."""
        assert ExportSettings(quantize_precision=4).has_lossy_transform is True

    def test_state_carries_precision_into_the_export_settings(self) -> None:
        state = DialogState(quantize_precision=MAX_PRECISION)
        assert state.to_export_settings().quantize_precision == MAX_PRECISION


class TestExtentSource:
    def test_data_is_the_default(self) -> None:
        """It is antimeridian-aware; the canvas rectangle cannot be."""
        assert ExportSettings().extent_source is ExtentSource.DATA

    def test_the_choice_survives_into_the_export_settings(self) -> None:
        state = DialogState(extent_source=ExtentSource.CANVAS)
        assert state.to_export_settings().extent_source is ExtentSource.CANVAS


class TestBasemap:
    """The one setting whose cost lands on the recipient rather than the author.

    Tiles are streamed, so the file does not grow - what a basemap ends is the
    "opens offline, contacts nobody" guarantee, and that cannot be walked back
    once the map has been sent.
    """

    def test_the_default_contacts_nobody(self) -> None:
        markup = build_manifest(make_project())
        assert 'basemap="none"' in markup

    def test_a_chosen_preset_reaches_the_map(self) -> None:
        markup = build_manifest(make_project(basemap="osm"))
        assert 'basemap="osm"' in markup

    def test_an_unknown_preset_falls_back_to_none(self) -> None:
        """A style the runtime cannot resolve is worse than no basemap at all."""
        markup = build_manifest(make_project(basemap="not-a-real-preset"))
        assert 'basemap="none"' in markup

    def test_maptiler_presets_are_not_offered(self) -> None:
        """They need an API key, which the file would carry in clear text."""
        assert not any(name.startswith("maptiler") for name in BASEMAP_PRESETS)

    def test_no_note_when_there_is_no_basemap(self) -> None:
        assert basemap_note("none") is None
        assert basemap_note("") is None

    def test_the_note_names_the_host_being_depended_on(self) -> None:
        note = basemap_note("osm")
        assert note is not None
        assert "openstreetmap.org" in note

    def test_the_note_corrects_the_size_assumption(self) -> None:
        """The intuitive worry is file size, and it is the wrong one."""
        note = basemap_note("osm")
        assert note is not None
        assert "no larger" in note
        assert "offline" in note

    def test_every_offered_preset_has_a_note(self) -> None:
        for preset in BASEMAP_PRESETS:
            if preset == "none":
                continue
            assert basemap_note(preset) is not None, preset


class TestChromeScale:
    """Sizing the map controls, measured rather than assumed.

    `font-size` on the widget host was the obvious approach and it is not
    enough: it does cross the shadow boundary, but the controls' internals are
    sized in pixels, so the text grew while the buttons stayed 30x60. A
    transform scales the rendered box and everything in it.
    """

    @staticmethod
    def _block(scale: float) -> str:
        from nika_onlymap_exporter.writers.onlymap_writer import _widget_color_block

        return _widget_color_block(make_project(chrome_scale=scale))

    def test_normal_size_emits_nothing(self) -> None:
        """A default export must stay byte-identical."""
        assert self._block(1.0) == ""

    def test_it_scales_with_a_transform_not_a_font_size(self) -> None:
        block = self._block(2.0)
        assert "transform: scale(2.0)" in block
        assert "font-size" not in block.split(".om-caption")[0]

    def test_every_scaled_widget_gets_an_origin(self) -> None:
        """Without one, a widget grows off its own edge instead of into the map."""
        block = self._block(1.5)
        assert block.count("transform: scale(") == block.count("transform-origin:")

    def test_the_stacked_corner_offsets_scale_too(self) -> None:
        """Fixed offsets let a scaled scale-bar land on the zoom controls."""
        block = self._block(2.0)
        assert "bottom: 116.0px !important" in block
        assert "bottom: 60.0px !important" in block

    def test_the_caption_scales_as_type(self) -> None:
        """It is our own element, so it can reflow rather than be stretched."""
        block = self._block(2.0)
        assert "font-size: 30.0px" in block
        assert "font-size: 24.0px" in block

    def test_the_credit_component_is_never_scaled(self) -> None:
        """Attribution carries licence obligations; it must not be shrinkable."""
        for scale in (0.75, 1.5, 2.0):
            assert "om-credit" not in self._block(scale)

    def test_colours_and_scale_can_coexist(self) -> None:
        project = make_project(widget_background=TEAL, chrome_scale=1.5)
        from nika_onlymap_exporter.writers.onlymap_writer import _widget_color_block

        block = _widget_color_block(project)
        assert "--om-widget-bg" in block
        assert "transform: scale(1.5)" in block
