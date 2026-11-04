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
    OutputMode,
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
from nika_onlymap_exporter.core.widget_colors import (
    RUNTIME_TEXT,
    contrast_between,
    resolve_widget_colors,
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


class TestUnspecifiedPartnerColour:
    """A colour left on Default must not make the other one unreadable."""

    def test_a_dark_background_gets_light_text(self) -> None:
        markup = _widget_color_block(make_project(widget_background=Color(0, 0, 0)))
        assert "--om-widget-fg: #ffffff;" in markup
        assert "--om-widget-muted: #ffffff;" in markup

    def test_white_text_gets_a_dark_background(self) -> None:
        markup = _widget_color_block(
            make_project(widget_foreground=Color(255, 255, 255))
        )
        assert "--om-widget-bg: #000000;" in markup
        assert "--om-widget-hover-bg: #000000;" in markup

    def test_a_readable_default_partner_is_left_alone(self) -> None:
        assert "--om-widget-fg" not in _widget_color_block(
            make_project(widget_background=Color(255, 255, 255))
        )
        assert "--om-widget-bg" not in _widget_color_block(
            make_project(widget_foreground=Color(0, 0, 0))
        )

    def test_a_deliberate_pair_is_never_changed(self) -> None:
        """Even an unreadable one - that is reported, not overridden."""
        markup = _widget_color_block(
            make_project(
                widget_background=Color(255, 255, 255),
                widget_foreground=Color(250, 250, 250),
            )
        )
        assert "--om-widget-bg: #ffffff;" in markup
        assert "--om-widget-fg: #fafafa;" in markup


class TestWidgetColourResolution:
    def test_an_explicit_low_contrast_pair_reports_its_ratio(self) -> None:
        result = resolve_widget_colors(Color(255, 255, 255), Color(200, 200, 200))
        assert result.explicit_contrast is not None
        assert result.explicit_contrast < 4.5
        assert result.derived is None

    def test_a_readable_explicit_pair_reports_nothing(self) -> None:
        result = resolve_widget_colors(Color(0, 0, 0), Color(255, 255, 255))
        assert result.explicit_contrast is None

    def test_the_resulting_text_always_reads(self) -> None:
        for shade in range(0, 256, 15):
            background = Color(shade, shade, shade)
            result = resolve_widget_colors(background, None)
            text = result.foreground or RUNTIME_TEXT
            assert contrast_between(background, text) >= 4.5, shade

    def test_mid_grey_text_gets_a_surface_that_reads(self) -> None:
        result = resolve_widget_colors(None, Color(119, 119, 119))
        assert result.background is not None
        assert result.derived == "background"

    def test_nothing_set_resolves_to_nothing(self) -> None:
        result = resolve_widget_colors(None, None)
        assert (result.background, result.foreground) == (None, None)


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
        assert line == 'highlight-color="#ffffff55"'

    def test_the_colour_is_bare_hex_not_a_quoted_literal(self) -> None:
        """Quoting it made the whole setting inert, silently.

        `highlight-color` is a plain attribute, so the runtime passes its raw
        string to deck.gl, which accepts only an array or a bare hex. With the
        quotes the value arrived as a 12-character string, failed deck's
        `Array.isArray` guard, and every highlight rendered in deck's default
        navy - the setting appeared to work and did nothing. The browser tier
        checks the other end of this; here we pin the shape.
        """
        for line in (
            self._highlight(),
            self._highlight(highlight_color=Color(r=29, g=233, b=200, a=0.5)),
        ):
            assert "'" not in line
            assert '="#' in line

    def test_a_chosen_colour_reaches_the_layer(self) -> None:
        line = self._highlight(highlight_color=Color(r=29, g=233, b=200, a=0.5))
        assert "#1de9c880" in line

    def test_a_chosen_colour_keeps_its_transparency(self) -> None:
        line = self._highlight(highlight_color=Color(r=0, g=0, b=0, a=0.25))
        assert line.endswith('40"')


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


class TestBasemapSwitcher:
    """A panel that lets the reader switch to another keyless basemap."""

    @staticmethod
    def _options(markup: str) -> list[str] | None:
        import re

        found = re.search(
            r'<om-widget type="basemap-switcher" options="([^"]*)"', markup
        )
        return found.group(1).split() if found else None

    def test_it_is_off_by_default(self) -> None:
        assert DialogState().basemap_switcher is False
        assert self._options(build_manifest(make_project(basemap="positron"))) is None

    def test_a_file_opened_map_offers_every_preset_but_osm(self) -> None:
        """OpenStreetMap's servers refuse a page opened from a file."""
        for mode in (OutputMode.STANDALONE_HTML, OutputMode.SHARE_ZIP):
            markup = build_manifest(
                make_project(
                    basemap="positron", basemap_switcher=True, output_mode=mode
                )
            )
            assert self._options(markup) == [
                "positron",
                "dark-matter",
                "voyager",
                "liberty",
                "bright",
            ], mode

    def test_a_folder_offers_osm_too(self) -> None:
        markup = build_manifest(
            make_project(
                basemap="liberty", basemap_switcher=True, output_mode=OutputMode.FOLDER
            )
        )
        assert self._options(markup) == [p for p in BASEMAP_PRESETS if p != "none"]
        assert 'position="top-left"' in markup.split('type="basemap-switcher"')[1]

    def test_a_chosen_osm_stays_in_the_list_from_a_file(self) -> None:
        """The author was warned; the switcher must still show the map's own."""
        markup = build_manifest(make_project(basemap="osm", basemap_switcher=True))
        assert (self._options(markup) or [])[0] == "osm"

    @pytest.mark.parametrize(
        "settings",
        [
            {"basemap": "none"},
            {"basemap": "positron", "terrain": "terrarium"},
            {"basemap": "positron", "map_crs": "EPSG:27700", "map_crs_def": "+x"},
        ],
    )
    def test_it_needs_a_flat_web_map_with_a_basemap(self, settings) -> None:
        markup = build_manifest(make_project(basemap_switcher=True, **settings))
        assert self._options(markup) is None

    def test_the_network_list_is_every_host_offered(self) -> None:
        from nika_onlymap_exporter.core.manifest_builder import settings_network_hosts

        def hosts(**settings) -> set[str]:
            return set(settings_network_hosts(ExportSettings(**settings)))

        assert hosts(basemap="positron") == {"openfreemap.org"}
        assert hosts(basemap="positron", basemap_switcher=True) == {
            "openfreemap.org",
            "cartocdn.com",
        }
        assert hosts(
            basemap="positron", basemap_switcher=True, output_mode=OutputMode.FOLDER
        ) == {"openfreemap.org", "cartocdn.com", "openstreetmap.org"}

    def test_each_host_named_is_the_one_the_runtime_contacts(self) -> None:
        """The privacy and Fidelity notes name these hosts, so each is checked
        against the address the pinned runtime registers for the preset."""
        import os
        import re
        from pathlib import Path
        from urllib.parse import urlsplit

        from nika_onlymap_exporter.core.manifest_builder import BASEMAP_HOSTS

        runtime_dir = os.environ.get("ONLYMAP_RUNTIME_DIR")
        bundle = Path(runtime_dir or "") / "onlymap.standalone.js"
        if not runtime_dir or not bundle.is_file():
            pytest.skip("set ONLYMAP_RUNTIME_DIR to the pinned runtime")
        source = bundle.read_text(encoding="utf-8")
        for preset, host in BASEMAP_HOSTS.items():
            # `register("dark-matter", { style: "https://..."`; OpenStreetMap's
            # style is inline, its raster `tiles` the first address in it.
            registered = re.search(
                rf'\("{re.escape(preset)}",\s*\{{.*?"(https://[^"]+)"', source, re.S
            )
            assert registered is not None, preset
            contacted = urlsplit(registered.group(1)).hostname or ""
            assert contacted == host or contacted.endswith(f".{host}"), preset

    def test_the_note_names_every_host_a_reader_can_reach(self) -> None:
        from nika_onlymap_exporter.core.manifest_builder import basemap_notes

        (note,) = basemap_notes(
            ExportSettings(
                basemap="voyager", basemap_switcher=True, output_mode=OutputMode.FOLDER
            )
        )
        assert "from cartocdn.com every time" in note
        assert "openstreetmap.org or openfreemap.org" in note
        (from_file,) = basemap_notes(
            ExportSettings(basemap="voyager", basemap_switcher=True)
        )
        assert "openstreetmap.org" not in from_file
        assert "openfreemap.org" in from_file

    def test_osm_from_a_file_keeps_its_warning_and_names_the_rest(self) -> None:
        from nika_onlymap_exporter.core.manifest_builder import (
            OSM_FILE_NOTE,
            basemap_notes,
        )

        assert basemap_notes(ExportSettings(basemap="osm")) == (OSM_FILE_NOTE,)
        warning, note = basemap_notes(
            ExportSettings(basemap="osm", basemap_switcher=True)
        )
        assert warning == OSM_FILE_NOTE
        assert "openfreemap.org or cartocdn.com" in note

    def test_scaled_it_grows_with_the_corner_not_over_the_switcher(self) -> None:
        """Each panel scaled alone would grow down over the one under it."""
        block = _widget_color_block(
            make_project(basemap="positron", basemap_switcher=True, chrome_scale=2.0)
        )
        corner = block.split('[data-om-widget-slot="top-start"] {')[1].split("}")[0]
        assert "transform: scale(2.0);" in corner
        assert (
            '[data-om-widget-slot="top-start"] > om-widget {\n        transform: none;'
            in block
        )
        # Without the switcher the corner keeps the rules it always had: its
        # panel scaled alone, the corner itself not.
        alone = _widget_color_block(make_project(basemap="positron", chrome_scale=2.0))
        assert '[data-om-widget-slot="top-start"] {' not in alone
        assert "transform: none;" not in alone


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

    def test_the_measure_tool_is_lifted_clear_of_the_scaled_zoom_controls(
        self,
    ) -> None:
        """A transform moves nothing out of the way, so the zoom controls grew up
        into the measure tool above them. Lifted by their lift plus growth."""
        block = self._block(2.0).split('om-widget[type="measure"] {')[-1]
        assert (
            "bottom: calc(116.0px + 1.0 * (var(--om-widget-size, 30px) * 2 + 8px))"
            " !important" in block
        )

    def test_without_zoom_controls_it_clears_the_scale_bar(self) -> None:
        from nika_onlymap_exporter.writers.onlymap_writer import _widget_color_block

        block = _widget_color_block(
            make_project(chrome_scale=2.0, show_zoom_controls=False)
        ).split('om-widget[type="measure"] {')[-1]
        assert "bottom: 95.0px !important" in block

    def test_no_lift_when_the_measure_tool_is_off(self) -> None:
        from nika_onlymap_exporter.writers.onlymap_writer import _widget_color_block

        block = _widget_color_block(make_project(chrome_scale=2.0, show_measure=False))
        assert 'om-widget[type="measure"] {\n        bottom' not in block

    def test_the_top_corners_cap_allows_for_the_scale(self) -> None:
        """`map.html` caps each top corner at half the map, and the transform
        grows the panel after layout: at 2x a crowded legend ran past the
        map's foot and the page scrolled."""
        from nika_onlymap_exporter.ui.dialog_labels import CHROME_SCALE_LABELS

        for scale in CHROME_SCALE_LABELS:
            if scale != 1.0:
                assert f"max-height: calc(50% / {scale});" in self._block(scale)

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


class TestPanelSizes:
    """The legend's and layer switcher's width and height, from Appearance."""

    @staticmethod
    def _block(**settings) -> str:
        from nika_onlymap_exporter.writers.onlymap_writer import _widget_color_block

        return _widget_color_block(make_project(**settings))

    def test_auto_emits_nothing(self) -> None:
        """Auto is the runtime's own sizing, byte for byte."""
        assert self._block() == ""
        assert "omni-legend-graphic { max" not in build_manifest(make_project())

    def test_a_set_size_is_the_panels_size(self) -> None:
        block = self._block(legend_width=320, legend_height=480)
        rule = block.split('om-widget[type="legend"],')[1].split("}")[0]
        assert "width: 320px;" in rule
        assert "height: 480px;" in rule
        # The static legend a picture swatch needs is matched too.
        assert 'om-widget[position="top-right"]:not([type]) {' in block

    def test_the_map_caps_it_whatever_was_typed(self) -> None:
        """The corner's half-the-map cap becomes the whole map less its insets,
        and the panel yields to it, so the page never scrolls."""
        block = self._block(layer_switcher_width=600, layer_switcher_height=1000)
        slot = block.split('[data-om-widget-slot="top-start"] {')[1].split("}")[0]
        assert "max-height: calc(100% - 24px);" in slot
        assert "max-width: calc(100% - 24px);" in slot
        panel = block.split('om-widget[type="layer-switcher"] {')[1].split("}")[0]
        assert "max-width: 100%;" in panel

    def test_the_cap_allows_for_the_controls_scale(self) -> None:
        """The scale's transform grows the panel after layout."""
        block = self._block(legend_height=900, chrome_scale=2.0)
        assert "max-height: calc((100% - 24px) / 2.0);" in block

    def test_only_the_dimension_set_changes(self) -> None:
        block = self._block(legend_width=300)
        assert "height" not in block
        assert "top-start" not in block

    def test_a_panel_left_off_the_map_gets_no_rule(self) -> None:
        assert self._block(show_legend=False, legend_width=300) == ""

    def test_a_service_legend_picture_follows_a_sized_legend(self) -> None:
        from nika_onlymap_exporter.core.manifest_builder import _legend_style_element

        style = "\n".join(_legend_style_element("", ExportSettings(legend_width=400)))
        assert ".omni-legend-graphic { max-width: none; }" in style
        style = "\n".join(
            _legend_style_element(
                "", ExportSettings(legend_width=400, legend_height=600)
            )
        )
        assert ".omni-legend-graphic { max-width: none; max-height: none; }" in style
        auto = "\n".join(_legend_style_element("", ExportSettings()))
        assert "max-width: none" not in auto
