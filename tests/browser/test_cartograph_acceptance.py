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

CI's browser job has no QGIS, so the QGIS job builds the fixtures once and
uploads them; the browser job downloads them and names the directory in
`CARTOGRAPH_FIXTURES_DIR`, which is used instead of building. Unset, the
QGIS-backed build above is unchanged.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
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
    prebuilt = os.environ.get("CARTOGRAPH_FIXTURES_DIR")
    if prebuilt:
        directory = Path(prebuilt)
        if not (directory / "expected.json").exists():
            pytest.fail(f"CARTOGRAPH_FIXTURES_DIR has no expected.json: {directory}")
        return directory
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

    def test_the_graticule_is_visible_above_its_frame(
        self, page, fixtures: Path
    ) -> None:
        """Counting lines in the DOM passed while the frame's image covered
        every one of them; this asks what is on top at a grid line."""
        _open(page, fixtures / "survey-static.html")
        # Graticules take no pointer events, so a hit test sees through them;
        # what decides painting is z-index, then document order.
        on_top = page.evaluate(
            """() => {
              const grid = document.querySelector('om-graticule');
              const frame = document.getElementById(grid.getAttribute('for'));
              const z = (e) => Number(getComputedStyle(e).zIndex) || 0;
              const after = frame.compareDocumentPosition(grid)
                & Node.DOCUMENT_POSITION_FOLLOWING;
              return z(grid) > z(frame) || (z(grid) === z(frame) && !!after);
            }"""
        )
        assert on_top

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
        # Printed as drawn, even with the browser's Background graphics off.
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('om-cartograph'))"
                ".printColorAdjust"
            )
            == "exact"
        )


class TestLiveFrameInUtm:
    """A live frame in UTM 48N draws in UTM 48N: QGIS's own map corners land
    on the frame's corners, not just near them as a Web Mercator redraw
    would."""

    def test_qgis_corners_land_on_the_frame_corners(
        self, page, fixtures: Path, expected, cartograph_source: str
    ) -> None:
        _needs_webgl(page)
        want = expected["survey_live"]
        _open(page, fixtures / "survey-live.html")
        landed = page.evaluate(
            """([frameId, corners]) => {
              const georef = document.getElementById(frameId).georef;
              return corners.map((c) => {
                const p = georef.project(c);
                return p && [p.xMm, p.yMm];
              });
            }""",
            [want["main_frame"], want["corners"]],
        )
        w, h = want["size_mm"]
        for got, wanted in zip(landed, ([0, 0], [w, 0], [w, h], [0, h])):
            assert got == pytest.approx(wanted, abs=0.05)
        result = page.evaluate(_VALIDATE, cartograph_source)
        assert result["errors"] == []


class TestLiveFrameInWgs84:
    """A live frame of an EPSG:4326 layout map draws as plate carree, as
    QGIS does: at 60N its corners land on the frame's corners, where a Web
    Mercator redraw is off by centimetres. The runtime flags a geographic
    plane as a warning only."""

    def test_qgis_corners_land_on_the_frame_corners(
        self, page, fixtures: Path, expected, cartograph_source: str, tmp_path: Path
    ) -> None:
        _needs_webgl(page)
        want = expected["geo_live"]
        # validate="silent" makes the map run its own checks and report them.
        html = (fixtures / "geo-live.html").read_text(encoding="utf-8")
        checked = tmp_path / "geo-live.html"
        checked.write_text(
            re.sub(r"<om-map\b", '<om-map validate="silent"', html, count=1),
            encoding="utf-8",
        )
        page.add_init_script(
            "window.__omChecks = [];"
            "document.addEventListener('om-validation-error',"
            " (e) => window.__omChecks.push(e.detail), true);"
        )
        _open(page, checked)
        landed = page.evaluate(
            """([frameId, corners]) => {
              const georef = document.getElementById(frameId).georef;
              return corners.map((c) => {
                const p = georef.project(c);
                return p && [p.xMm, p.yMm];
              });
            }""",
            [want["main_frame"], want["corners"]],
        )
        w, h = want["size_mm"]
        for got, wanted in zip(landed, ([0, 0], [w, 0], [w, h], [0, h])):
            assert got == pytest.approx(wanted, abs=0.05)
        assert page.evaluate(_VALIDATE, cartograph_source)["errors"] == []
        checks = page.evaluate("window.__omChecks")
        assert checks, "the map ran no checks of its own"
        assert all(check["errors"] == [] for check in checks)


class TestRasterInALiveFrame:
    """A raster in a live frame draws from the page itself: its source file
    is gone, and the page has no server to fetch from."""

    def test_it_draws_where_qgis_puts_it(self, page, fixtures: Path, expected) -> None:
        _needs_webgl(page)
        want = expected["raster_live"]
        _open(page, fixtures / "raster-live.html")

        def colour(lonlat: list[float]) -> list[int]:
            return _colour_at(page, want["main_frame"], lonlat)

        deadline = time.monotonic() + 30
        red = colour(want["red"])
        while red[:3] != [255, 0, 0] and time.monotonic() < deadline:
            page.wait_for_timeout(500)
            red = colour(want["red"])
        assert red[:3] == pytest.approx([255, 0, 0], abs=8)
        assert colour(want["white"])[:3] == pytest.approx([255, 255, 255], abs=8)

    def test_the_first_automatic_print_has_it_with_no_network(
        self, page, fixtures: Path, expected
    ) -> None:
        """The runtime called a raster frame ready before its file was read,
        so the first `?print=1` capture had no raster; offline, the COG reader
        could not even look up EPSG:3857. The capture the print dialog gets
        must already show it."""
        _needs_webgl(page)
        want = expected["raster_live"]
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.url.startswith("http")
                else route.continue_()
            ),
        )
        page.add_init_script("window.print = () => { window.__printed = true; };")
        page.goto((fixtures / "raster-live.html").as_uri() + "?print=1")
        page.wait_for_function("() => window.__printed === true", timeout=90_000)
        rasters = page.evaluate(
            "() => { const r = window.qgis2webmapRasters;"
            " return { unread: r.names.filter((n) => !r.read.has(n)),"
            " failed: r.failed }; }"
        )
        assert rasters == {"unread": [], "failed": []}
        # What the print dialog captured: the frame's print-time stand-in.
        red = _colour_at(page, want["main_frame"], want["red"])
        assert red[:3] == pytest.approx([255, 0, 0], abs=8)


class TestWithoutWebgl:
    """A browser without WebGL cannot draw a live frame, and the runtime then
    never settles it. The page turns the frame static by QGIS's own
    georeference instead: the frame settles, shows a note where the map
    would be, and its corners and scale tokens still read QGIS's map."""

    def test_the_live_frame_turns_static_by_qgis_georeference(
        self, browser_type, fixtures: Path, expected
    ) -> None:
        want = expected["survey_live"]
        browser = browser_type.launch(args=["--disable-webgl", "--disable-3d-apis"])
        try:
            page = browser.new_page()
            page.goto((fixtures / "survey-live.html").as_uri())
            assert not page.evaluate(
                "() => !!document.createElement('canvas').getContext('webgl2')"
            )
            page.wait_for_function(
                "() => [...document.querySelectorAll('om-frame')].every(f => f.georef)",
                timeout=30_000,
            )
            state = page.evaluate(
                """async ([frameId, corners]) => {
                  const frame = document.getElementById(frameId);
                  const timeout = new Promise((r) => setTimeout(() => r(false), 20000));
                  const settled = await Promise.race([
                    Promise.resolve(frame.ready).then(() => true, () => true),
                    timeout,
                  ]);
                  return {
                    settled,
                    mode: frame.getAttribute('mode'),
                    note: frame.querySelector(':scope > img')?.alt ?? null,
                    corners: corners.map((c) => {
                      const p = frame.georef.project(c);
                      return [p.xMm, p.yMm];
                    }),
                    text: [...document.querySelectorAll('om-text')]
                      .map((t) => t.innerText).join(' '),
                    legendHidden: [...document.querySelectorAll(
                      `om-legend[for="${frameId}"]`)].every((l) => l.hidden),
                  };
                }""",
                [want["main_frame"], want["corners"]],
            )
        finally:
            browser.close()
        assert state["settled"]
        assert state["mode"] == "static"
        assert state["note"] and "WebGL" in state["note"]
        w, h = want["size_mm"]
        for got, wanted in zip(state["corners"], ([0, 0], [w, 0], [w, h], [0, h])):
            assert got == pytest.approx(wanted, abs=0.05)
        assert "{{" not in state["text"]
        # Its legend lists the map's layers; with no map it would only wait.
        assert state["legendHidden"]


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


def _colour_at(page, frame_id: str, lonlat: list[float]) -> list[int]:
    """The screen colour where `lonlat` falls in frame `frame_id`."""
    x, y = page.evaluate(
        """([frameId, at]) => {
          const frame = document.getElementById(frameId);
          const box = frame.getBoundingClientRect();
          const p = frame.georef.project(at);
          const perMm = box.width / Number(frame.getAttribute('w'));
          return [box.left + p.xMm * perMm, box.top + p.yMm * perMm];
        }""",
        [frame_id, lonlat],
    )
    shot = base64.b64encode(page.screenshot()).decode()
    return page.evaluate(
        """async ([png, x, y]) => {
          const image = new Image();
          image.src = 'data:image/png;base64,' + png;
          await image.decode();
          const canvas = document.createElement('canvas');
          canvas.width = image.width;
          canvas.height = image.height;
          const context = canvas.getContext('2d');
          context.drawImage(image, 0, 0);
          const at = [Math.round(x), Math.round(y)];
          return [...context.getImageData(...at, 1, 1).data];
        }""",
        [shot, x, y],
    )


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

    def test_a_page_that_fails_to_print_releases_the_earlier_ones(
        self, page, fixtures: Path, expected
    ) -> None:
        """Page 3's image fails: every address made for pages 1 to 3 is
        revoked, nothing reaches the print dialog, and the reader is told
        which page failed and what to do."""
        _needs_webgl(page)
        page.add_init_script(
            """
            window.__printCalls = 0;
            window.print = () => { window.__printCalls += 1; };
            window.__printImages = [];
            window.__revoked = [];
            const revoke = URL.revokeObjectURL.bind(URL);
            URL.revokeObjectURL = (url) => { window.__revoked.push(url); revoke(url); };
            const decode = HTMLImageElement.prototype.decode;
            HTMLImageElement.prototype.decode = function () {
              if (!this.style.cssText.includes('break-after')) return decode.call(this);
              window.__printImages.push(this.src);
              if (window.__printImages.length === 3) {
                return Promise.reject(new Error('injected decode failure'));
              }
              return decode.call(this);
            };
            """
        )
        page.goto((fixtures / "atlas-live.html").as_uri() + "?atlas=print&dpi=72")
        alert = page.locator("[data-qgis2webmap-atlas-notice][role=alert]")
        alert.wait_for(timeout=120_000)
        message = alert.inner_text()
        assert "Page 3 of 3" in message
        assert "injected decode failure" in message
        images = page.evaluate("() => window.__printImages")
        assert len(images) == 3
        assert set(images) <= set(page.evaluate("() => window.__revoked"))
        assert page.evaluate("() => window.__printCalls") == 0
        assert page.locator("[data-qgis2webmap-atlas-print]").count() == 0

    def test_preparing_pages_shows_progress(
        self, page, fixtures: Path, expected
    ) -> None:
        _needs_webgl(page)
        page.add_init_script(
            """
            window.__printed = false;
            window.print = () => { window.__printed = true; };
            window.__noticeLog = [];
            new MutationObserver(() => {
              const notice = document.querySelector('[data-qgis2webmap-atlas-notice]');
              const text = notice?.textContent ?? '';
              const log = window.__noticeLog;
              if (text && text !== log[log.length - 1]) log.push(text);
            }).observe(document, {
              subtree: true, childList: true, characterData: true,
            });
            """
        )
        page.goto((fixtures / "atlas-live.html").as_uri() + "?atlas=print&dpi=72")
        page.wait_for_function("() => window.__printed", timeout=120_000)
        log = page.evaluate("() => window.__noticeLog")
        assert [entry.split("Cancel")[0] for entry in log] == [
            f"Preparing page {number} of 3" for number in (1, 2, 3)
        ]
        assert page.locator("[data-qgis2webmap-atlas-notice]").count() == 0


class TestAtlasFixedScale:
    """QGIS's Fixed atlas scale, which the runtime cannot do: every page
    keeps the map's scale and centres its parcel where QGIS does."""

    def test_every_page_is_centred_like_qgis_at_one_scale(
        self, page, fixtures: Path, expected
    ) -> None:
        _needs_webgl(page)
        want = expected["atlas_fixed"]
        _open(page, fixtures / "atlas-fixed.html")
        page.wait_for_function(
            "() => document.querySelector('om-atlas')?.count > 0", timeout=60_000
        )
        cameras = page.evaluate(
            """(frameId) => {
              const atlas = document.querySelector('om-atlas');
              const frame = document.getElementById(frameId);
              const seen = [];
              for (let index = 0; index < atlas.count; index++) {
                atlas.seek(index);
                seen.push({
                  id: String(atlas.current.id),
                  center: JSON.parse(frame.getAttribute('center')),
                  zoom: Number(frame.getAttribute('zoom')),
                });
              }
              return seen;
            }""",
            want["main_frame"],
        )
        assert sorted(c["id"] for c in cameras) == sorted(want["centres"])
        zooms = {round(c["zoom"], 3) for c in cameras}
        assert len(zooms) == 1, f"the scale changed between pages: {zooms}"
        for camera in cameras:
            assert camera["center"] == pytest.approx(
                want["centres"][camera["id"]], abs=1e-6
            )


class TestAtlasFilterAndOrder:
    """The runtime's own filter and sort differ from QGIS on NULL (and on
    text order): the export writes QGIS's answer, which the runtime must page
    exactly -- the filter as a test on feature ids, the order as a number
    carried by each feature."""

    def test_pages_are_the_features_and_order_qgis_pages(
        self, page, fixtures: Path, expected
    ) -> None:
        _needs_webgl(page)
        _open(page, fixtures / "atlas-ranked.html")
        page.wait_for_function(
            "() => document.querySelector('om-atlas')?.count > 0", timeout=60_000
        )
        paged = page.evaluate(
            """() => {
              const atlas = document.querySelector('om-atlas');
              const seen = [];
              for (let index = 0; index < atlas.count; index++) {
                atlas.seek(index);
                seen.push(Number(atlas.current.id));
              }
              return seen;
            }"""
        )
        assert paged == expected["atlas_ranked"]["pages"]


class TestSharedLayerData:
    def test_a_layer_in_two_live_frames_is_written_once_and_drawn_in_both(
        self, page, fixtures: Path, expected
    ) -> None:
        want = expected["shared"]
        html = (fixtures / "shared-live.html").read_text(encoding="utf-8")
        assert html.count(want["marker"]) == 1
        _needs_webgl(page)
        _open(page, fixtures / "shared-live.html")
        page.wait_for_function(
            "() => [...document.querySelectorAll('om-map')]"
            ".every(map => map.getLayers?.().length > 0)",
            timeout=60_000,
        )
        counts = page.evaluate(
            """() => [...document.querySelectorAll('om-map')].map((map) => {
              const layer = map.getLayers().find((l) => l.data);
              const data = layer?.data;
              return Array.isArray(data) ? data.length
                : (data?.features?.length ?? data?.length ?? 0);
            })"""
        )
        assert counts == [want["features"], want["features"]]


def test_a_static_cartograph_reads_without_javascript(browser, fixtures: Path) -> None:
    """Mail and chat previews, QuickLook, locked-down viewers: no script runs,
    and a static-only page still shows its frames where they belong."""
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto((fixtures / "survey-static.html").as_uri())
        images = page.locator("om-frame img")
        assert images.count() == 2
        box = images.first.bounding_box()
        assert box is not None and box["width"] > 100
    finally:
        context.close()


_SAMPLE_PIXELS = """
async ([png, points]) => {
  const image = new Image();
  image.src = 'data:image/png;base64,' + png;
  await image.decode();
  const canvas = document.createElement('canvas');
  canvas.width = image.width;
  canvas.height = image.height;
  const context = canvas.getContext('2d');
  context.drawImage(image, 0, 0);
  return points.map(([x, y]) =>
    [...context.getImageData(Math.round(x), Math.round(y), 1, 1).data].slice(0, 3));
}
"""


class TestItemOpacity:
    """A 50% item draws at half alpha over a known backdrop, as QGIS's own
    render of the layout does: a label, a live map (real WebGL), a static map
    (the raster must not carry the opacity as well), and a group member."""

    def _sample(self, page, png: bytes, points: list[list[float]]) -> list[list[int]]:
        encoded = base64.b64encode(png).decode()
        return page.evaluate(_SAMPLE_PIXELS, [encoded, points])

    def test_it_matches_qgis_at_the_same_pixels(
        self, page, fixtures: Path, expected
    ) -> None:
        _needs_webgl(page)
        want = expected["opacity"]
        page.set_viewport_size({"width": 1500, "height": 1000})
        _open(page, fixtures / "opacity-live.html")
        probes = want["probes"]

        def browser_pixels() -> dict[str, list[int]]:
            geometry = page.evaluate(
                """(frameId) => {
                  const frame = document.getElementById(frameId);
                  const box = frame.getBoundingClientRect();
                  const perMm = box.width / Number(frame.getAttribute('w'));
                  return [box.left - Number(frame.getAttribute('x')) * perMm,
                          box.top - Number(frame.getAttribute('y')) * perMm, perMm];
                }""",
                want["live_frame"],
            )
            left, top, per_mm = geometry
            points = [[left + x * per_mm, top + y * per_mm] for x, y in probes.values()]
            colours = self._sample(page, page.screenshot(), points)
            return dict(zip(probes, colours, strict=True))

        # Blue, then green at half over it: the polygon is drawn when it shows.
        deadline = time.monotonic() + 30
        got = browser_pixels()
        while got["live_map_polygon"][1] < 100 and time.monotonic() < deadline:
            page.wait_for_timeout(500)
            got = browser_pixels()

        qgis_png = (fixtures / "opacity-qgis.png").read_bytes()
        px_per_mm = 96 / 25.4
        from_qgis = dict(
            zip(
                probes,
                self._sample(
                    page,
                    qgis_png,
                    [[x * px_per_mm, y * px_per_mm] for x, y in probes.values()],
                ),
                strict=True,
            )
        )
        # What half alpha of each colour over the blue backdrop must be.
        half = {
            "backdrop": [0, 0, 255],
            "label": [128, 0, 128],
            "live_map_background": [128, 128, 255],
            "live_map_polygon": [0, 128, 128],
            "static_map_background": [128, 128, 255],
            "static_map_polygon": [0, 128, 128],
            "group_member_at_half": [128, 0, 128],
            "group_member_opaque": [255, 0, 0],
        }
        for name, colour in half.items():
            # The runtime's live map canvas is a pale off-white, not QGIS's
            # pure white; at half alpha that is a difference of up to 12.
            slack = 16 if name == "live_map_background" else 10
            assert from_qgis[name] == pytest.approx(colour, abs=3), f"QGIS {name}"
            assert got[name] == pytest.approx(colour, abs=slack), f"browser {name}"
            assert got[name] == pytest.approx(from_qgis[name], abs=slack), name
