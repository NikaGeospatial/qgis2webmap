"""A live frame's derived legend, fitted to its box, in a real browser.

The runtime lays a derived legend out at its own size and clips what runs past
the box's bottom, mid-row. `core/runtime_legend_fit.py` models that layout; only
a browser can say whether the model holds: the fitted legend has to end inside
its box, and the same legend left as QGIS planned it has to overrun (or the
test would prove nothing).

Needs WebGL, like every live frame: skipped where the browser has none.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    CartographSpec,
    FrameSpec,
    LegendRowSpec,
    LegendSpec,
)
from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.manifest_builder import build_manifest
from nika_onlymap_exporter.core.runtime_legend import fit_frame_legends
from nika_onlymap_exporter.core.runtime_legend_fit import fit_frame_legend_boxes
from nika_onlymap_exporter.packaging.runtime_manager import LocalRuntime
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

_WEBGL2 = "() => !!document.createElement('canvas').getContext('webgl2')"
_BOX = Box(10, 100, 190, 52)
_VALUES = ("Asphalt", "Gravel", "Cinder", "Natural", "Unrecorded")


def _line_layer(number: int) -> ExportLayer:
    categories = tuple(
        CategorySpec(
            value=value,
            label=f"{value} road",
            symbol=SymbolSpec(stroke_color=Color(r=40 * (i + 1), g=60, b=90)),
        )
        for i, value in enumerate(_VALUES)
    )
    return ExportLayer(
        layer_id=f"roads{number}",
        name=f"Road network number {number} · base 2001",
        geometry_kind=GeometryKind.LINE,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[0.0, 51.0], [0.5, 51.4]],
                    },
                    "properties": {"f": "Asphalt"},
                }
            ],
        },
        renderer=RendererSpec(
            kind=RendererKind.CATEGORIZED, field_name="f", categories=categories
        ),
    )


def _page(runtime: LocalRuntime, directory: Path, *, fit: bool) -> Path:
    loaded = runtime.load()
    layers = tuple(_line_layer(n) for n in range(1, 7))
    project = ExportProject(
        title="Legend fit",
        layers=layers,
        extent=Extent(west=-1.0, south=50.5, east=1.0, north=51.8),
    )
    frame = FrameSpec(
        id="main", box=Box(10, 10, 190, 80), mode="live", center=(0.0, 51.2), zoom=8
    )
    legend = LegendSpec(
        id="legend_main",
        box=_BOX,
        for_="main",
        columns=3,
        rows=tuple(
            LegendRowSpec(layer=layer.layer_id, order=i)
            for i, layer in enumerate(layers)
        ),
    )
    spec = CartographSpec(cartograph_id="cg-fit", size="A4", items=(frame, legend))
    spec = fit_frame_legends(spec, "main", layers)
    if fit:
        spec = fit_frame_legend_boxes(spec, "main", layers, FidelityReportBuilder())
    bundle = CartographRuntimeBundle(
        cartograph_js=loaded.cartograph_javascript,
        cartograph_css=loaded.cartograph_css.decode("utf-8"),
        core_js=loaded.javascript,
        core_css=loaded.css.decode("utf-8"),
    )
    html = write_cartograph_html(spec, bundle, {"main": build_manifest(project)})
    destination = directory / ("fitted.html" if fit else "unfitted.html")
    destination.write_text(html, encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def pages(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir

    directory = discover_runtime_dir()
    if directory is None or not (directory / "cartograph.standalone.js").exists():
        pytest.skip("OnlyMap runtime with a cartograph build not available")
    runtime = LocalRuntime(directory)
    out = tmp_path_factory.mktemp("legend-fit")
    return {
        "fitted": _page(runtime, out, fit=True),
        "plain": _page(runtime, out, fit=False),
    }


_OVERRUN_MM = """
() => {
  const legend = document.querySelector('om-legend');
  const content = legend && legend.querySelector('[data-om-carto-legend-rows]');
  if (!content || content.children.length < 6) return null;
  const k = legend.getBoundingClientRect().width / parseFloat(legend.getAttribute('w'));
  const bottom = Math.max(...[...content.children].map(
    (e) => e.getBoundingClientRect().bottom));
  return (bottom - legend.getBoundingClientRect().bottom) / k;
}
"""


def _overrun(page, path: Path) -> float:
    page.goto(path.as_uri())
    page.wait_for_selector("om-legend", timeout=30_000)
    if not page.evaluate(_WEBGL2):
        pytest.skip("no WebGL2 context in this browser/environment")
    page.wait_for_function(f"({_OVERRUN_MM})() !== null", timeout=60_000)
    return float(page.evaluate(_OVERRUN_MM))


class TestLegendFitsItsBox:
    def test_the_legend_as_qgis_planned_it_overruns_the_box(
        self, page, pages: dict[str, Path]
    ) -> None:
        assert _overrun(page, pages["plain"]) > 1.0

    def test_the_fitted_legend_ends_inside_the_box(
        self, page, pages: dict[str, Path]
    ) -> None:
        assert _overrun(page, pages["fitted"]) <= 0.0
