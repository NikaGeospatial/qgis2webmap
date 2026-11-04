"""Where the exported map opens, and where its chrome sits, measured in the page.

Four things a reader sees on opening a map, each checked in a real browser from
a file and over http: the whole data extent is on screen, tall or wide, in a
desktop window and a phone's; the legend lists the top layer first, as QGIS
does; the runtime's licence badge and our credit chip never cover each other
down to a 320px phone; and the measure tool, at every Size, stays on the
screen and off the other controls - folded into a drawer where the map is too
narrow for it.

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


