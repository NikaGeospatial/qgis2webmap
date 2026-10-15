"""Issue #1's v0.1 acceptance, checked in a real browser on real QGIS output.

"`survey-utm` exports to one HTML that opens from `file://`, prints to A3 at
true size, shows a graticule that matches QGIS's within 1 mm, and a legend
with the same rows." Plus the parts a recipient relies on that no unit test
can see: the runtime's own validator, a live frame mounting and resolving its
scale token, and the `?export=png` / `?print=1` host triggers the Share menu
uses.

The fixtures come from `cartograph_fixture_builder.py`, run with QGIS's
Python (named by `QGIS_PYTHON`); this tier's interpreter has Playwright but no
PyQGIS. Without `QGIS_PYTHON` or the runtime, the whole module skips.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import re
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
MM_PER_PT = 25.4 / 72


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    qgis_python = os.environ.get("QGIS_PYTHON")
    if not qgis_python:
        pytest.skip("set QGIS_PYTHON to QGIS's interpreter to build the fixtures")
    if discover_runtime_dir() is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
    out_dir = tmp_path_factory.mktemp("cartograph-acceptance")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    completed = subprocess.run(
        [qgis_python, str(HERE / "cartograph_fixture_builder.py"), str(out_dir)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if not (out_dir / "expected.json").exists():
        sys.stderr.write(completed.stdout + completed.stderr)
        pytest.fail("the fixture builder wrote nothing; its output is above")
    return out_dir


@pytest.fixture(scope="module")
def expected(fixtures: Path) -> dict:
    return json.loads((fixtures / "expected.json").read_text())


@pytest.fixture(scope="module")
def cartograph_source() -> str:
    directory = discover_runtime_dir()
    assert directory is not None
    return (directory / "cartograph.standalone.js").read_text(encoding="utf-8")


_VALIDATE = """
async (source) => {
  const blob = new Blob([source], { type: "text/javascript" });
  const carto = await import(URL.createObjectURL(blob));
  for (const frame of document.querySelectorAll("om-frame")) await frame.ready;
  const result = carto.validateCartograph(document.querySelector("om-cartograph"));
  return { valid: result.valid, errors: result.errors, warnings: result.warnings };
}
"""


def _open(page, path: Path, query: str = "") -> None:
    page.goto(path.as_uri() + query)
    page.wait_for_selector("om-cartograph om-frame", timeout=30_000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('om-frame')].every(f => f.georef)",
        timeout=60_000,
    )


def _pdf_page_size_mm(pdf: bytes) -> tuple[float, float]:
    """The first MediaBox, in mm. Chromium writes it uncompressed."""
    match = re.search(
        rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", pdf
    )
    if match is None:  # pragma: no cover - would mean an unreadable PDF
        raise AssertionError("no MediaBox in the printed PDF")
    x0, y0, x1, y1 = (float(v) for v in match.groups())
    return (x1 - x0) * MM_PER_PT, (y1 - y0) * MM_PER_PT


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


class TestSurveyCartograph:
    def test_the_runtime_validates_it_with_no_errors(
        self, page, fixtures: Path, cartograph_source: str
    ) -> None:
        _open(page, fixtures / "survey-static.html")
        result = page.evaluate(_VALIDATE, cartograph_source)
        assert result["errors"] == []
        assert result["valid"] is True

    def test_it_prints_at_true_a3_size(self, page, fixtures: Path, expected) -> None:
        _open(page, fixtures / "survey-static.html")
        pdf = page.pdf(prefer_css_page_size=True, print_background=True)
        width, height = _pdf_page_size_mm(pdf)
        page_w, page_h = expected["survey"]["page_mm"]
        assert width == pytest.approx(page_w, abs=0.5)
        assert height == pytest.approx(page_h, abs=0.5)

    def test_the_graticule_matches_qgis_within_a_millimetre(
        self, page, fixtures: Path, expected
    ) -> None:
        _open(page, fixtures / "survey-static.html")
        drawn = page.evaluate(
            """() => [...document.querySelectorAll('om-graticule svg polyline')]
                 .map(line => line.getAttribute('points').trim().split(/\\s+/)
                   .map(pair => pair.split(',').map(Number)))
                 .filter(points => Math.abs(points[0][0] - points.at(-1)[0]) < 1e-6)
                 .map(points => points[0][0])"""
        )
        want = expected["survey"]["grid_x_mm"]
        assert drawn, "the runtime drew no vertical graticule lines"
        assert len(drawn) == len(want)
        for got, wanted in zip(sorted(drawn), sorted(want)):
            assert got == pytest.approx(wanted, abs=1.0)

    def test_the_legend_has_the_same_rows_as_qgis(
        self, page, fixtures: Path, expected
    ) -> None:
        _open(page, fixtures / "survey-static.html")
        rows = page.evaluate(
            "() => [...document.querySelectorAll('om-legend om-legend-row')]"
            ".map(row => row.getAttribute('label'))"
        )
        assert rows == expected["survey"]["legend_rows"]

    def test_export_png_downloads_the_page_at_the_asked_dpi(
        self, page, fixtures: Path, expected
    ) -> None:
        with page.expect_download(timeout=60_000) as download_info:
            page.goto((fixtures / "survey-static.html").as_uri() + "?export=png&dpi=96")
        data = Path(download_info.value.path()).read_bytes()
        width, height = _png_size(data)
        page_w, page_h = expected["survey"]["page_mm"]
        assert width == pytest.approx(page_w / 25.4 * 96, abs=2)
        assert height == pytest.approx(page_h / 25.4 * 96, abs=2)

    def test_print_trigger_opens_the_print_dialog(self, page, fixtures: Path) -> None:
        page.add_init_script("window.print = () => { window.__printed = true; };")
        _open(page, fixtures / "survey-static.html", "?print=1")
        page.wait_for_function("() => window.__printed === true", timeout=30_000)


class TestLiveCartograph:
    def test_the_live_frame_mounts_and_resolves_its_scale(
        self, page, fixtures: Path, cartograph_source: str
    ) -> None:
        if not page.evaluate(
            "() => !!document.createElement('canvas').getContext('webgl2')"
        ):
            pytest.skip("this browser has no WebGL2, so a live frame cannot draw")
        _open(page, fixtures / "story-live.html")
        assert page.locator("om-map").count() == 1
        # Tokens resolve once the map has mounted, which is after georef.
        page.wait_for_function(
            "() => !document.querySelector('om-text').innerText.includes('{{')",
            timeout=60_000,
        )
        text = page.locator("om-text").first.inner_text()
        assert "{{" not in text
        assert re.search(r"Scale 1:\s?\d[\d\s,.]*", text)
        result = page.evaluate(_VALIDATE, cartograph_source)
        assert result["errors"] == []

    def test_a_live_page_is_one_file(self, fixtures: Path) -> None:
        html = (fixtures / "story-live.html").read_text(encoding="utf-8")
        # The core rides gzip+base64 inside the file; nothing is fetched.
        assert "const CORE_GZ" in html
        assert 'src="http' not in html


def _needs_webgl(page) -> None:
    page.goto("about:blank")
    if not page.evaluate(
        "() => !!document.createElement('canvas').getContext('webgl2')"
    ):
        pytest.skip("this browser has no WebGL2, so a live frame cannot draw")


class TestAtlasCartograph:
    """The whole series, not one sheet per URL: the two triggers this plugin
    adds for an atlas."""

    def test_atlas_zip_downloads_every_page(
        self, page, fixtures: Path, expected
    ) -> None:
        import zipfile

        _needs_webgl(page)
        with page.expect_download(timeout=120_000) as download_info:
            page.goto((fixtures / "atlas-live.html").as_uri() + "?atlas=zip&dpi=72")
        download = download_info.value
        assert download.suggested_filename.endswith("-atlas.zip")
        with zipfile.ZipFile(download.path()) as archive:
            names = archive.namelist()
        assert len(names) == expected["atlas"]["pages"] == 3
        assert all(name.endswith(".png") for name in names)

    def test_atlas_print_sends_every_page_to_one_print(
        self, page, fixtures: Path, expected
    ) -> None:
        _needs_webgl(page)
        page.add_init_script(
            "window.print = () => { window.__printedSheets = document"
            ".querySelectorAll('[data-qgis2webmap-atlas-print] img').length; };"
        )
        page.goto((fixtures / "atlas-live.html").as_uri() + "?atlas=print&dpi=72")
        page.wait_for_function(
            "() => window.__printedSheets !== undefined", timeout=120_000
        )
        assert (
            page.evaluate("() => window.__printedSheets") == expected["atlas"]["pages"]
        )
