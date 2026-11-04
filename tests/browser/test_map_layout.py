"""Where the exported map opens, and where its chrome sits, measured in the page.

What a reader sees on opening a map, each checked in a real browser from
a file and over http: the whole data extent is on screen, tall or wide, in a
desktop window and a phone's; the legend lists the top layer first, as QGIS
does; the runtime's licence badge and our credit chip never cover each other
down to a 320px phone; and the measure tool, at every Size, stays on the
screen and off the other controls - folded into a drawer where the map is too
narrow for it; and the bottom-left corner narrows upward, the widest control
at the bottom, and folds away rather than reach the top-left corner on a short
window.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_exported_map import require_webgl

from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    LabelingSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.ui.live_server import PreviewServer
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

HOW = ("file", "http")


def _camera_script() -> str:
    """The Preview's camera keeper, read from its module's source: importing
    `ui.preview` needs QGIS, and this tier does not."""
    source = (
        Path(__file__).resolve().parents[2]
        / "nika_onlymap_exporter"
        / "ui"
        / "preview.py"
    ).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CAMERA_SCRIPT"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("ui/preview.py has no CAMERA_SCRIPT")


# Twenty degrees of latitude in half a degree of longitude, and the reverse.
TALL = Extent(west=10.0, south=40.0, east=10.5, north=60.0)
WIDE = Extent(west=-10.0, south=50.0, east=10.0, north=52.0)
RED = Color(r=200, g=30, b=30)
BLUE = Color(r=30, g=30, b=200)
# A desktop window and a phone held upright.
WINDOWS = ((1200, 800), (390, 844))


def _layer(layer_id: str, name: str, points, **extra) -> ExportLayer:
    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=len(points),
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": list(point)},
                    "properties": {"name": f"Point {place}"},
                }
                for place, point in enumerate(points)
            ],
        },
        **{
            "renderer": RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(fill_color=RED, radius=6.0),
            ),
            **extra,
        },
    )


def _write(
    runtime, destination: Path, project: ExportProject, preview_hook: str = ""
) -> Path:
    return (
        OnlyMapWriter(runtime_provider=runtime)
        .write(project, destination, preview_hook=preview_hook)
        .entry_path
    )


@pytest.fixture
def opener(page) -> Iterator:
    """Opens an export from its file or over http, in a window of a size."""
    servers: list[PreviewServer] = []

    def open_it(entry: Path, how: str, size: tuple[int, int], fragment: str = ""):
        page.set_viewport_size({"width": size[0], "height": size[1]})
        if how == "file":
            page.goto(entry.as_uri() + fragment)
        else:
            server = PreviewServer(entry.parent)
            server.start()
            servers.append(server)
            page.goto(f"http://127.0.0.1:{server.port}/{entry.name}{fragment}")
        page.wait_for_function(
            "() => customElements.get('om-map') !== undefined", timeout=30_000
        )
        require_webgl(page)
        page.wait_for_function(
            "() => document.querySelector('om-map').ready.then(() => true)",
            timeout=30_000,
        )
        # Two frames, so the runtime's own layout pass (slots, folding, the
        # badge) has run before anything is measured.
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => "
            "requestAnimationFrame(r)))"
        )
        return page

    yield open_it
    for server in servers:
        server.stop()


def _overlap(a: dict, b: dict) -> bool:
    return (
        a["left"] < b["right"]
        and b["left"] < a["right"]
        and a["top"] < b["bottom"]
        and b["top"] < a["bottom"]
    )


BOXES = """(selector) => [...document.querySelectorAll(selector)]
  .map(e => { const r = e.getBoundingClientRect();
    return {name: e.getAttribute('type')
                  || (e.hasAttribute('data-om-badge') ? 'badge' : '')
                  || (e.getAttribute('data-om-fold-toggle')
                      ? 'fold-' + e.getAttribute('data-om-fold-toggle') : '')
                  || e.className,
            left: r.left, top: r.top, right: r.right, bottom: r.bottom}; })
  .filter(b => b.right > b.left && b.bottom > b.top)"""


class TestTheMapOpensOnTheWholeExtent:
    @pytest.mark.parametrize("how", HOW)
    @pytest.mark.parametrize("size", WINDOWS, ids=lambda s: f"{s[0]}x{s[1]}")
    @pytest.mark.parametrize("extent", (TALL, WIDE), ids=("tall", "wide"))
    def test_every_corner_is_on_screen(
        self, opener, runtime, tmp_path, extent, size, how
    ) -> None:
        corners = [
            [extent.west, extent.south],
            [extent.east, extent.south],
            [extent.west, extent.north],
            [extent.east, extent.north],
        ]
        entry = _write(
            runtime,
            tmp_path,
            ExportProject(
                title="Extent",
                layers=(_layer("data", "Data", corners),),
                extent=extent,
            ),
        )
        page = opener(entry, how, size)

        box = page.locator("om-map").bounding_box()
        assert box is not None
        for corner in corners:
            x, y = page.evaluate(
                "(c) => document.querySelector('om-map').project(c)", corner
            )
            assert 0 <= x <= box["width"] and 0 <= y <= box["height"], (
                f"{corner} opens off the {size[0]}x{size[1]} map, at {x:.0f},{y:.0f}"
            )

    def test_wide_data_that_fits_keeps_its_written_zoom(
        self, opener, runtime, tmp_path
    ) -> None:
        """On a desktop window wide data already fitted; its view is unchanged."""
        entry = _write(
            runtime,
            tmp_path,
            ExportProject(
                title="Extent",
                layers=(_layer("data", "Data", [(-10.0, 50.0), (10.0, 52.0)]),),
                extent=WIDE,
            ),
        )
        written = re.search(
            r'<om-map\s[^>]*?\szoom="([^"]+)"', entry.read_text(encoding="utf-8")
        )
        assert written is not None
        page = opener(entry, "file", (1200, 800))
        assert page.locator("om-map").get_attribute("zoom") == written.group(1)

    def test_the_previews_saved_camera_is_kept(self, page, runtime, tmp_path) -> None:
        """A camera kept in the address is the one the map opens on, and the
        fit never moves the map first."""
        entry = _write(
            runtime,
            tmp_path,
            ExportProject(
                title="Extent",
                layers=(_layer("data", "Data", [(10.0, 40.0), (10.5, 60.0)]),),
                extent=TALL,
            ),
            preview_hook=_camera_script(),
        )
        # Every zoom the map is given, from before the page's own scripts run.
        page.add_init_script(
            """window.zoomsSet = [];
            new MutationObserver((records) => {
              for (const r of records) {
                if (r.target.localName === 'om-map' && r.attributeName === 'zoom') {
                  window.zoomsSet.push(r.target.getAttribute('zoom'));
                }
              }
            }).observe(document, {attributes: true, subtree: true,
                                  attributeFilter: ['zoom']});"""
        )
        page.set_viewport_size({"width": 1200, "height": 800})
        page.goto(entry.as_uri() + "#camera=10.250000,50.000000,9.00")
        page.wait_for_function(
            "() => window.zoomsSet && window.zoomsSet.includes('9')",
            timeout=30_000,
        )
        assert page.evaluate("() => window.zoomsSet") == ["9"]


class TestTheLegendListsTheTopLayerFirst:
    @pytest.mark.parametrize("how", HOW)
    def test_rows_read_top_down_as_in_qgis(self, opener, runtime, tmp_path, how):
        """Draw order is bottom first; the legend reads top first, and a
        layer's labels sit under it."""
        layers = (
            _layer("bottom", "Bottom", [(0.0, 51.0)]),
            _layer(
                "middle",
                "Middle",
                [(0.2, 51.2)],
                labeling=LabelingSpec(enabled=True, field_name="name"),
            ),
            _layer("top", "Top", [(0.4, 51.4)]),
        )
        entry = _write(
            runtime,
            tmp_path,
            ExportProject(
                title="Order",
                layers=layers,
                extent=Extent(west=-1.0, south=50.9, east=1.0, north=51.9),
            ),
        )
        page = opener(entry, how, (1000, 800))
        page.wait_for_function(
            """() => document.querySelector('om-widget[type="legend"]')
                 .shadowRoot.querySelectorAll('.omni-row').length === 4""",
            timeout=10_000,
        )
        rows = page.evaluate(
            """() => [...document.querySelector('om-widget[type="legend"]')
                 .shadowRoot.querySelectorAll('.omni-row')]
               .sort((a, b) => a.getBoundingClientRect().top
                             - b.getBoundingClientRect().top)
               .map(r => r.textContent.trim())"""
        )
        assert rows == ["Top", "Middle", "Middle (labels)", "Bottom"]

    def test_the_reordered_legend_is_as_tall_as_before(
        self, opener, runtime, tmp_path
    ) -> None:
        """Reordering moves rows, not the gaps between them - a layer's class
        list included."""
        classes = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="name",
            categories=(
                CategorySpec("Point 0", "First", SymbolSpec(fill_color=RED)),
                CategorySpec("Point 1", "Second", SymbolSpec(fill_color=BLUE)),
            ),
        )
        layers = (
            _layer("bottom", "Bottom", [(0.0, 51.0), (0.1, 51.1)], renderer=classes),
            _layer("middle", "Middle", [(0.2, 51.2)]),
            _layer("top", "Top", [(0.4, 51.4), (0.5, 51.5)], renderer=classes),
        )
        entry = _write(
            runtime,
            tmp_path,
            ExportProject(
                title="Order",
                layers=layers,
                extent=Extent(west=-1.0, south=50.9, east=1.0, north=51.9),
            ),
        )
        page = opener(entry, "file", (1000, 800))
        heights = page.evaluate(
            """() => {
              const root = document.querySelector('om-widget[type="legend"]')
                .shadowRoot;
              const panel = () => root.querySelector('.omni-panel')
                .getBoundingClientRect().height;
              if (root.querySelectorAll('.omni-legend-sub').length !== 2) return null;
              const reordered = panel();
              [...root.querySelectorAll('style')]
                .find(s => s.textContent.includes('order: 1')).remove();
              return [reordered, panel()];
            }"""
        )
        assert heights is not None, "both layers' class lists are in the legend"
        assert heights[0] == heights[1]


@pytest.fixture(scope="module")
def relief_map(runtime, tmp_path_factory) -> Path:
    """A relief map carries the imagery credit, and a layer adds its own."""
    return _write(
        runtime,
        tmp_path_factory.mktemp("relief"),
        ExportProject(
            title="Relief",
            layers=(
                _layer(
                    "sites",
                    "Sites",
                    [(0.1, 51.1), (0.5, 51.4)],
                    attribution="© Fixture Survey",
                ),
            ),
            extent=Extent(west=-1.0, south=50.9, east=1.0, north=51.9),
            settings=ExportSettings(terrain="terrarium", basemap="osm"),
        ),
    )


class TestTheBadgeAndTheCreditChip:
    @pytest.mark.parametrize("how", HOW)
    @pytest.mark.parametrize("width", (640, 540, 480, 430, 400, 375, 360, 320))
    def test_they_never_cover_each_other(self, opener, relief_map, width, how):
        page = opener(relief_map, how, (width, 700))
        badge = page.evaluate(BOXES, "[data-om-badge]")
        chip = page.evaluate(BOXES, ".om-credit")
        assert badge and chip, "the badge and the data credit are both on the map"
        assert not _overlap(badge[0], chip[0]), (
            f"at {width}px the credit chip {chip[0]} covers the badge {badge[0]}"
        )
        # Nor anything else in the bottom row, nor the attribution above it.
        others = page.evaluate(
            BOXES,
            '[data-om-widget-slot="bottom-start"] > *, '
            '[data-om-widget-slot="bottom-center"] > *, '
            '[data-om-widget-slot="bottom-end"] > *',
        )
        for other in others:
            assert not _overlap(chip[0], other), (
                f"at {width}px the credit chip covers {other['name']}"
            )
        assert chip[0]["right"] <= width and chip[0]["bottom"] <= 700


class TestTheMeasureToolAtEverySize:
    @pytest.mark.parametrize("how", HOW)
    @pytest.mark.parametrize("scale", (0.85, 1.0, 1.25, 1.5, 2.0))
    def test_it_stays_on_screen_and_off_the_other_controls(
        self, opener, runtime, tmp_path, scale, how
    ) -> None:
        entry = _write(
            runtime,
            tmp_path,
            ExportProject(
                title="Measure",
                layers=(_layer("sites", "Sites", [(0.1, 51.1)]),),
                extent=Extent(west=-1.0, south=50.9, east=1.0, north=51.9),
                settings=ExportSettings(chrome_scale=scale),
            ),
        )
        for width, height in ((640, 800), (600, 800), (430, 932), (390, 844)):
            page = opener(entry, how, (width, height))
            toggle = page.locator('[data-om-fold-toggle="bottom"]')
            if toggle.count():
                # Folded: the tool is in the drawer, so open it, as a reader would.
                toggle.click(timeout=10_000)
                page.wait_for_timeout(200)
            boxes = page.evaluate(
                BOXES, "om-widget, .om-credit, [data-om-badge], [data-om-fold-toggle]"
            )
            tool = next((b for b in boxes if b["name"] == "measure"), None)
            assert tool is not None, f"no measure tool at {width}px, size {scale}"
            assert (
                tool["left"] >= 0
                and tool["top"] >= 0
                and tool["right"] <= width
                and tool["bottom"] <= height
            ), f"at {width}px and size {scale} the measure tool leaves the map: {tool}"
            for other in boxes:
                if other is not tool:
                    assert not _overlap(tool, other), (
                        f"at {width}px and size {scale} it covers {other['name']}"
                    )


BOTTOM_LEFT = ("measure", "basemap-switcher", "scale-bar", "zoom-controls")


def _corner_map(runtime, destination: Path, scale: float) -> Path:
    """Every bottom-left control, and a layer switcher over three layers in the
    top-left corner for the stack to stay clear of."""
    return _write(
        runtime,
        destination,
        ExportProject(
            title="Corner",
            layers=tuple(
                _layer(f"layer{n}", f"Layer {n}", [(0.1 * n, 51.1)]) for n in range(3)
            ),
            extent=Extent(west=-1.0, south=50.9, east=1.0, north=51.9),
            settings=ExportSettings(
                basemap="positron", basemap_switcher=True, chrome_scale=scale
            ),
        ),
    )


class TestTheBottomLeftCorner:
    @pytest.mark.parametrize("how", HOW)
    @pytest.mark.parametrize("scale", (0.85, 1.0, 1.25, 1.5, 2.0))
    def test_it_narrows_upward_and_covers_nothing(
        self, opener, runtime, tmp_path, scale, how
    ) -> None:
        entry = _corner_map(runtime, tmp_path, scale)
        for height in (900, 700, 480):
            page = opener(entry, how, (1200, height))
            boxes = page.evaluate(
                BOXES,
                "om-widget, .om-credit, [data-om-badge], [data-om-fold-toggle]",
            )
            where = f"at size {scale}, 1200x{height}"
            for box in boxes:
                assert box["left"] >= 0 and box["top"] >= 0, f"{where}: {box}"
                assert box["right"] <= 1200 and box["bottom"] <= height, (
                    f"{where}: {box}"
                )
            for place, box in enumerate(boxes):
                for other in boxes[place + 1 :]:
                    assert not _overlap(box, other), (
                        f"{where}: {box['name']} covers {other['name']}"
                    )
            corner = [b for b in boxes if b["name"] in BOTTOM_LEFT]
            if not corner:
                # Folded for room: the drawer holds them, and it fits.
                assert page.locator("[data-om-fold-toggle]").count() > 0, where
                continue
            assert len(corner) == len(BOTTOM_LEFT), f"{where}: {corner}"
            badge = next(b for b in boxes if b["name"] == "badge")
            rising = sorted(corner, key=lambda b: -b["bottom"])
            assert all(b["bottom"] <= badge["top"] for b in corner), where
            widths = [round(b["right"] - b["left"]) for b in rising]
            assert widths == sorted(widths, reverse=True), (
                f"{where}: bottom to top {[b['name'] for b in rising]} {widths}"
            )

    @pytest.mark.parametrize("scale", (1.0, 1.25))
    def test_the_area_hint_takes_its_room_in_the_stack(
        self, opener, runtime, tmp_path, scale
    ) -> None:
        """While an area is drawn the measure tool carries its Ctrl + click
        hint above it, inside its own box, so the stack makes room for it as
        for any taller control - and a narrow map's drawer holds it too."""
        entry = _corner_map(runtime, tmp_path, scale)
        selector = "om-widget, .om-credit, [data-om-badge], [data-om-fold-toggle]"
        for size in ((1280, 800), (390, 844)):
            page = opener(entry, "file", size)
            where = f"at size {scale}, {size[0]}x{size[1]}"
            toggle = page.locator('[data-om-fold-toggle="bottom"]')
            if toggle.count():
                toggle.click(timeout=10_000)
                page.wait_for_timeout(200)
            page.locator('om-widget[type="measure"] button[data-mode="area"]').click(
                timeout=10_000
            )
            hint = page.locator('om-widget[type="measure"] .q2w-measure-hint')
            hint.wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(300)
            boxes = page.evaluate(BOXES, selector)
            tool = next(b for b in boxes if b["name"] == "measure")
            spot = hint.bounding_box()
            assert spot is not None
            assert (
                tool["left"] <= spot["x"]
                and spot["x"] + spot["width"] <= tool["right"] + 0.5
                and tool["top"] <= spot["y"] + 0.5
            ), f"{where}: the hint is outside the tool: {spot} {tool}"
            for box in boxes:
                assert box["left"] >= 0 and box["top"] >= 0, f"{where}: {box}"
                assert box["right"] <= size[0] and box["bottom"] <= size[1], (
                    f"{where}: {box}"
                )
            for place, box in enumerate(boxes):
                for other in boxes[place + 1 :]:
                    assert not _overlap(box, other), (
                        f"{where}: {box['name']} covers {other['name']}"
                    )
            assert page.evaluate(
                "() => document.scrollingElement.scrollHeight <= innerHeight"
            ), f"{where}: the page scrolls"
            corner = [b for b in boxes if b["name"] in BOTTOM_LEFT]
            if size[0] == 1280 and len(corner) == len(BOTTOM_LEFT):
                rising = sorted(corner, key=lambda b: -b["bottom"])
                widths = [round(b["right"] - b["left"]) for b in rising]
                assert widths == sorted(widths, reverse=True), f"{where}: {widths}"

    def test_a_taller_window_gets_the_controls_back(
        self, opener, runtime, tmp_path
    ) -> None:
        entry = _corner_map(runtime, tmp_path, 2.0)
        page = opener(entry, "file", (1400, 480))
        page.wait_for_selector('[data-om-fold-toggle="bottom"]', timeout=10_000)
        page.set_viewport_size({"width": 1400, "height": 1100})
        page.wait_for_function(
            """() => !document.querySelector('[data-om-widgets-folded]')
                 && document.querySelector('om-widget[type="measure"]')
                      .closest('[data-om-widget-slot="bottom-start"]')""",
            timeout=10_000,
        )
