"""A layout's too-small legend box, fitted: the exported page's legend ends
inside its box, and the QGIS legend item has the very box the page draws.

The layout and its export come from `legend_fit_fixture_builder.py`, run with
QGIS's Python (`QGIS_PYTHON`); the browser tier has no PyQGIS. Needs WebGL.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

HERE = Path(__file__).resolve().parent
_WEBGL2 = "() => !!document.createElement('canvas').getContext('webgl2')"

_MEASURE = """
() => {
  const legend = document.querySelector('om-legend');
  const content = legend && legend.querySelector('[data-om-carto-legend-rows]');
  if (!content || content.children.length < 6) return null;
  const k = legend.getBoundingClientRect().width / parseFloat(legend.getAttribute('w'));
  const bottom = Math.max(...[...content.children].map(
    (e) => e.getBoundingClientRect().bottom));
  return {
    overrun: (bottom - legend.getBoundingClientRect().bottom) / k,
    box: ['x', 'y', 'w', 'h'].map((n) => parseFloat(legend.getAttribute(n))),
    columns: parseInt(legend.getAttribute('columns'), 10),
  };
}
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    qgis_python = os.environ.get("QGIS_PYTHON")
    if not qgis_python:
        pytest.skip("set QGIS_PYTHON to QGIS's interpreter to build the fixture")
    if discover_runtime_dir() is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
    out_dir = tmp_path_factory.mktemp("legend-fit-box")
    completed = subprocess.run(
        [qgis_python, str(HERE / "legend_fit_fixture_builder.py"), str(out_dir)],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if not (out_dir / "qgis.json").exists():
        sys.stderr.write(completed.stdout + completed.stderr)
        pytest.fail("the fixture builder wrote nothing; its output is above")
    return out_dir


def _measure(page, built: Path) -> dict:
    page.goto((built / "page.html").as_uri())
    page.wait_for_selector("om-legend", timeout=30_000)
    if not page.evaluate(_WEBGL2):
        pytest.skip("no WebGL2 context in this browser/environment")
    page.wait_for_function(f"({_MEASURE})() !== null", timeout=60_000)
    return page.evaluate(_MEASURE)


class TestFittedBoxMatchesQgis:
    def test_the_legend_ends_inside_its_box(self, page, built: Path) -> None:
        assert _measure(page, built)["overrun"] <= 0.0

    def test_the_qgis_legend_item_has_the_pages_box_and_columns(
        self, page, built: Path
    ) -> None:
        measured = _measure(page, built)
        qgis = json.loads((built / "qgis.json").read_text())
        assert measured["box"] == pytest.approx(qgis["box"], abs=0.01)
        assert measured["columns"] == qgis["columns"] == 3
        assert qgis["listed"] == 8
