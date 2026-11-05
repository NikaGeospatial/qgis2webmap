"""The legend's and layer switcher's size, as set on the Appearance tab.

What a reader sees: a panel given a width and height is that size, a panel
left on Auto is exactly as before (as wide as its rows, at most half the map
tall), and no size, however big, makes the page scroll or runs off a narrow
map. Measured in the page, not read off the stylesheet.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_exported_map import next_frames, open_map

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
    TileSpec,
)
from nika_onlymap_exporter.ui.live_server import PreviewServer
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

LEGEND = 'om-widget[type="legend"]'
SWITCHER = 'om-widget[type="layer-switcher"]'

POINT = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 51.0]},
            "properties": {"name": "Here"},
        }
    ],
}

PANELS = """
() => {
  const root = document.documentElement;
  const out = { page: [root.scrollWidth, root.scrollHeight, innerWidth, innerHeight] };
  const map = document.querySelector('om-map').getBoundingClientRect();
  out.map = { left: map.left, top: map.top, right: map.right, bottom: map.bottom };
  for (const [name, selector] of [['legend', 'om-widget[type="legend"]'],
                                  ['switcher', 'om-widget[type="layer-switcher"]']]) {
    const host = document.querySelector(selector);
    if (!host) continue;
    const r = host.getBoundingClientRect();
    const panel = host.shadowRoot.querySelector('.omni-panel');
    out[name] = { left: r.left, top: r.top, right: r.right, bottom: r.bottom,
                  width: r.width, height: r.height,
                  scrolls: host.scrollHeight - host.clientHeight,
                  sideways: host.scrollWidth - host.clientWidth,
                  content: panel.scrollWidth };
  }
  return out;
}
"""


# A tile layer makes the legend our own static one (`needs_static_legend`),
# which Appearance > Size does not scale. Its tiles never load: nothing listens
# on port 9, and the legend does not wait for them.
TILES = ExportLayer(
    layer_id="tiles",
    name="Tiles",
    geometry_kind=GeometryKind.RASTER,
    source_kind=SourceKind.SERVICE,
    renderer=RendererSpec(kind=RendererKind.UNSUPPORTED),
    tiles=TileSpec("http://127.0.0.1:9/{z}/{x}/{y}.png"),
)
STATIC_LEGEND = 'om-widget[position="top-right"]:not([type])'


def _export(runtime, directory, layers: int, tiles: bool = False, **settings):
    project = ExportProject(
        title="Panel sizes",
        layers=(TILES,) * tiles
        + tuple(
            ExportLayer(
                layer_id=f"layer{i}",
                name=f"Layer {i}",
                geometry_kind=GeometryKind.POINT,
                source_kind=SourceKind.FILE,
                feature_count=1,
                geojson=POINT,
                renderer=RendererSpec(
                    kind=RendererKind.SINGLE,
                    symbol=SymbolSpec(fill_color=Color(r=200, g=40, b=40), radius=6.0),
                ),
            )
            for i in range(layers)
        ),
        extent=Extent(west=-0.5, south=50.7, east=0.5, north=51.3),
        settings=ExportSettings(show_title=False, **settings),
    )
    return OnlyMapWriter(runtime_provider=runtime).write(project, directory).entry_path


def _panels(
    page, entry, width: int = 1280, height: int = 700, url: str = "", switcher=True
):
    page.set_viewport_size({"width": width, "height": height})
    if url:
        page.goto(url)
        page.wait_for_function(
            "() => customElements.get('om-map') !== undefined", timeout=30_000
        )
    else:
        open_map(page, entry)
    for selector in (LEGEND, SWITCHER) if switcher else (LEGEND,):
        page.wait_for_function(
            """(selector) => document.querySelector(selector)
                 ?.shadowRoot?.querySelector('.omni-panel')""",
            arg=selector,
            timeout=30_000,
        )
    # After the short-window fold, which runs on the frame after they arrive.
    next_frames(page)
    return page.evaluate(PANELS)


class TestASetSize:
    def test_the_panels_are_the_size_set(self, page, runtime, tmp_path) -> None:
        """Thirty layers, so both have more rows than fit and scroll inside."""
        entry = _export(
            runtime,
            tmp_path,
            30,
            legend_width=320,
            legend_height=300,
            layer_switcher_width=260,
            layer_switcher_height=420,
        )
        state = _panels(page, entry)
        assert (state["legend"]["width"], state["legend"]["height"]) == (320, 300)
        assert (state["switcher"]["width"], state["switcher"]["height"]) == (260, 420)
        assert state["legend"]["scrolls"] > 0
        assert state["switcher"]["scrolls"] > 0

    def test_a_short_list_fills_the_height_set(self, page, runtime, tmp_path) -> None:
        """Three rows in a 400px panel: the panel is 400px, its own colour to
        the bottom, not three rows over a transparent gap."""
        entry = _export(runtime, tmp_path, 3, legend_height=400)
        state = _panels(page, entry)
        assert state["legend"]["height"] == 400
        background = page.locator(LEGEND).evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        assert background == "rgb(255, 255, 255)"

    def test_it_holds_when_the_map_is_served(self, page, runtime, tmp_path) -> None:
        entry = _export(runtime, tmp_path, 30, legend_width=300, legend_height=250)
        server = PreviewServer(entry.parent)
        server.start()
        try:
            state = _panels(
                page, entry, url=f"http://127.0.0.1:{server.port}/{entry.name}"
            )
        finally:
            server.stop()
        assert (state["legend"]["width"], state["legend"]["height"]) == (300, 250)


class TestTheSmallestSize:
    @pytest.mark.parametrize("tiles", [False, True], ids=["legend", "static-legend"])
    def test_at_105_by_50_it_scrolls_down_not_sideways(
        self, page, runtime, tmp_path, tiles
    ) -> None:
        """Narrower than the panels' own 140px minimum: their rows take the
        width set rather than run past it, so the panel scrolls one way only,
        and nothing leaves the map or covers another control. The legend is
        the runtime's or, for a tile layer, our own."""
        entry = _export(
            runtime,
            tmp_path,
            30,
            tiles=tiles,
            legend_width=105,
            legend_height=50,
            layer_switcher_width=105,
            layer_switcher_height=50,
        )
        legend = STATIC_LEGEND if tiles else LEGEND
        page.set_viewport_size({"width": 1280, "height": 700})
        open_map(page, entry)
        for selector in (legend, SWITCHER):
            page.wait_for_function(
                """(selector) => document.querySelector(selector)
                     ?.shadowRoot?.querySelector('.omni-panel')""",
                arg=selector,
                timeout=30_000,
            )
        next_frames(page)
        state = page.evaluate(PANELS.replace(LEGEND, legend))
        page_width, page_height, inner_width, inner_height = state["page"]
        assert page_width <= inner_width and page_height <= inner_height, state
        for name in ("legend", "switcher"):
            panel = state[name]
            assert (panel["width"], panel["height"]) == (105, 50), name
            assert panel["sideways"] == 0, (name, panel)
            assert panel["scrolls"] > 0, (name, panel)
        boxes = page.evaluate(
            """() => [...document.querySelectorAll(
                  'om-widget, .om-credit, [data-om-badge]')]
                .map((e) => { const r = e.getBoundingClientRect();
                  return [r.left, r.top, r.right, r.bottom]; })
                .filter((r) => r[2] > r[0] && r[3] > r[1])"""
        )
        for place, box in enumerate(boxes):
            for other in boxes[place + 1 :]:
                apart = (
                    box[2] <= other[0]
                    or other[2] <= box[0]
                    or box[3] <= other[1]
                    or other[3] <= box[1]
                )
                assert apart, (box, other)


class TestAuto:
    def test_auto_is_as_before(self, page, runtime, tmp_path) -> None:
        """As wide as the rows, at most half the map tall: what
        `test_exported_map.TestTallControls` holds a default map to."""
        state = _panels(page, _export(runtime, tmp_path, 30))
        for name in ("legend", "switcher"):
            panel = state[name]
            assert panel["width"] == panel["content"], name
            assert panel["height"] <= 700 / 2 + 1, name
            assert panel["scrolls"] > 100, name


class TestNeverBiggerThanTheMap:
    @pytest.mark.parametrize(("width", "height"), [(1280, 700), (900, 420)])
    def test_the_largest_size_never_scrolls_the_page(
        self, page, runtime, tmp_path, width, height
    ) -> None:
        entry = _export(
            runtime,
            tmp_path,
            30,
            legend_width=600,
            legend_height=1000,
            layer_switcher_width=600,
            layer_switcher_height=1000,
        )
        state = _panels(page, entry, width, height)
        page_width, page_height, inner_width, inner_height = state["page"]
        assert page_width <= inner_width and page_height <= inner_height, state
        for name in ("legend", "switcher"):
            panel = state[name]
            # Inside the map, clear of its edges by the corner inset.
            assert panel["top"] >= state["map"]["top"] + 11, name
            assert panel["bottom"] <= state["map"]["bottom"] - 11, name
            assert panel["left"] >= state["map"]["left"] + 11, name
            assert panel["right"] <= state["map"]["right"] - 11, name

    def test_on_a_narrow_map_the_width_yields(self, page, runtime, tmp_path) -> None:
        """One layer, so the legend has its row to itself and is not folded
        into the drawer a narrow map makes for a crowded one."""
        entry = _export(runtime, tmp_path, 1, legend_width=600, legend_height=1000)
        state = _panels(page, entry, 500, 400, switcher=False)
        assert state["page"][0] <= state["page"][2]
        assert state["page"][1] <= state["page"][3]
        assert state["legend"]["width"] == 500 - 24
        assert state["legend"]["height"] == 400 - 24

    def test_a_scaled_panel_still_fits(self, page, runtime, tmp_path) -> None:
        """The Size setting's transform grows the panel after layout, so the
        cap allows for it - the set legend's, and the switcher's on Auto."""
        entry = _export(runtime, tmp_path, 30, legend_height=1000, chrome_scale=2.0)
        # The big screen Largest is for: a laptop window folds both panels
        # into the drawer, where there is no corner to fit.
        state = _panels(page, entry, 1920, 1200)
        assert page.locator("[data-om-fold-toggle]").count() == 0
        assert state["legend"]["height"] > 0 and state["switcher"]["height"] > 0
        assert state["page"][1] <= state["page"][3]
        assert state["legend"]["bottom"] <= state["map"]["bottom"] - 11
        assert state["switcher"]["bottom"] <= state["map"]["bottom"] - 11


class TestAStaticLegend:
    # Largest on the big screen it is for, with rows enough to fill half of
    # it: on a laptop window it folds the controls into their drawers
    # (`test_folded_at_largest_it_is_in_the_drawer`).
    @pytest.mark.parametrize(
        ("scale", "width", "height", "layers"),
        [(1.0, 1280, 700, 30), (2.0, 1920, 1200, 60)],
    )
    def test_at_any_size_it_takes_up_to_half_the_map(
        self, page, runtime, tmp_path, scale, width, height, layers
    ) -> None:
        """Our own legend, for a tile layer, is not grown by the Size's
        transform, so its corner keeps the half-the-map cap: divided by the
        Size, a long legend stopped at a quarter of the map at 2x."""
        entry = _export(runtime, tmp_path, layers, tiles=True, chrome_scale=scale)
        page.set_viewport_size({"width": width, "height": height})
        open_map(page, entry)
        page.wait_for_function(
            """(selector) => document.querySelector(selector)
                 ?.shadowRoot?.querySelector('.omni-panel')""",
            arg=STATIC_LEGEND,
            timeout=30_000,
        )
        next_frames(page)
        assert page.locator("[data-om-fold-toggle]").count() == 0
        legend = page.locator(STATIC_LEGEND).bounding_box()
        map_box = page.locator("om-map").bounding_box()
        assert legend is not None and map_box is not None
        # Half the map, near enough: a quarter is what the bug left it.
        assert map_box["height"] * 0.45 <= legend["height"] <= map_box["height"] / 2 + 1
        assert legend["y"] + legend["height"] <= map_box["y"] + map_box["height"] - 11
        overflow = page.locator(STATIC_LEGEND).evaluate(
            "el => el.scrollHeight - el.clientHeight"
        )
        assert overflow > 100

    def test_folded_at_largest_it_is_in_the_drawer(
        self, page, runtime, tmp_path
    ) -> None:
        """At Largest a 1280x700 window folds the controls; the drawer opens
        on the legend, inside the map, and the page does not scroll."""
        entry = _export(runtime, tmp_path, 30, tiles=True, chrome_scale=2.0)
        page.set_viewport_size({"width": 1280, "height": 700})
        open_map(page, entry)
        page.locator('[data-om-fold-toggle="top"]').click(timeout=30_000)
        drawer = page.locator('[data-om-fold-panel="top"]')
        drawer.wait_for(state="visible", timeout=10_000)
        legend = drawer.locator(STATIC_LEGEND)
        legend.scroll_into_view_if_needed(timeout=10_000)
        box = legend.bounding_box()
        drawer_box = drawer.bounding_box()
        assert box is not None and drawer_box is not None
        assert box["height"] > 100
        assert box["y"] < drawer_box["y"] + drawer_box["height"]
        assert drawer_box["y"] + drawer_box["height"] <= 700
        page_size = page.evaluate(
            "() => [document.documentElement.scrollHeight, innerHeight]"
        )
        assert page_size[0] <= page_size[1]
