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

    def test_it_prints_at_true_a3_size(
        self, page, browser_name: str, fixtures: Path, expected
    ) -> None:
        _needs_pdf(browser_name)
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
        w, h = want["size_mm"]
        frame_corners = ([0, 0], [w, 0], [w, h], [0, h])

        def corners_landed() -> list[list[float]]:
            return page.evaluate(
                """([frameId, corners]) => {
                  const georef = document.getElementById(frameId).georef;
                  return corners.map((c) => {
                    const p = georef.project(c);
                    return p && [p.xMm, p.yMm];
                  });
                }""",
                [want["main_frame"], want["corners"]],
            )

        def on_the_corners(landed: list[list[float]]) -> bool:
            return all(
                got == pytest.approx(wanted, abs=0.05)
                for got, wanted in zip(landed, frame_corners)
            )

        # A live frame reports a georeference as soon as its map has a camera,
        # but only lands on QGIS's corners once the map has settled there. A
        # slow software-GL browser takes seconds to get that far, so give it
        # a bounded time instead of reading the first view.
        landed = corners_landed()
        deadline = time.monotonic() + 30
        while not on_the_corners(landed) and time.monotonic() < deadline:
            page.wait_for_timeout(250)
            landed = corners_landed()
        for got, wanted in zip(landed, frame_corners):
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

    _GATE_STUB = (
        "let real; Object.defineProperty(window, 'qgis2webmapRasters', {"
        " configurable: true, get: () => real && new Proxy(real, { get: (t, k) =>"
        " (%s) }), set: (v) => { real = v; } });"
        "window.print = () => { window.__printed = true; };"
    )

    def test_cancel_stops_the_wait_instead_of_restarting_it(
        self, page, fixtures: Path
    ) -> None:
        """Cancel used to reload with `?print=1` still in the address, which
        started the same wait again. A raster that is read all the time keeps
        the wait open until the reader cancels."""
        _needs_webgl(page)
        page.add_init_script(
            self._GATE_STUB % "k === 'lastRead' ? performance.now() : t[k]"
        )
        page.goto((fixtures / "raster-live.html").as_uri() + "?print=1&keep=1#frame")
        page.evaluate("() => { window.__same = true; }")
        page.get_by_role("button", name="Cancel").click()
        page.wait_for_function(
            "() => !document.querySelector('[data-qgis2webmap-atlas-notice]')"
        )
        page.wait_for_timeout(1500)
        assert page.evaluate("() => location.search + location.hash") == "?keep=1#frame"
        assert page.evaluate("() => window.__same") is True
        assert page.evaluate("() => window.__printed === true") is False

    def test_a_wait_that_throws_still_ends_in_a_notice_and_a_live_page(
        self, page, fixtures: Path
    ) -> None:
        """A rejected wait aborted the whole module: the notice stayed and the
        URL actions after it never ran."""
        _needs_webgl(page)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(
            self._GATE_STUB
            % "k === 'read' ? (() => { throw new Error('boom'); })() : t[k]"
        )
        page.goto((fixtures / "raster-live.html").as_uri() + "?print=1&keep=1#frame")
        alert = page.get_by_role("alert")
        alert.wait_for(timeout=90_000)
        assert "boom" in alert.inner_text()
        assert page.evaluate("() => location.search + location.hash") == "?keep=1#frame"
        page.wait_for_timeout(1000)
        assert errors == []
        assert page.evaluate("() => window.__printed === true") is False


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


def _needs_pdf(browser_name: str) -> None:
    """Skip where Playwright cannot print a page to PDF (Chromium only)."""
    if browser_name != "chromium":
        pytest.skip("Playwright's page.pdf() is only supported by Chromium")


def _needs_webgl(page) -> None:
    page.goto("about:blank")
    if not page.evaluate(
        "() => !!document.createElement('canvas').getContext('webgl2')"
    ):
        pytest.skip("this browser has no WebGL2, so a live frame cannot draw")


class TestAtlasWithRasters:
    """An atlas page change moves the live frame to a new extent, and the
    raster must be in every page's capture, not just the first, with no
    network."""

    #: Counts the pixels of an image that are close to each wanted colour.
    _COUNT_COLOURS = """async ([source, colours]) => {
      const bitmap = await createImageBitmap(await (await fetch(source)).blob());
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width; canvas.height = bitmap.height;
      const context = canvas.getContext('2d');
      context.drawImage(bitmap, 0, 0);
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const counts = colours.map(() => 0);
      for (let i = 0; i < data.length; i += 4) {
        colours.forEach((colour, n) => {
          if (colour.every((v, c) => Math.abs(data[i + c] - v) <= 12)) counts[n]++;
        });
      }
      return { counts, pixels: canvas.width * canvas.height };
    }"""

    @staticmethod
    def _offline(page) -> None:
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.url.startswith("http")
                else route.continue_()
            ),
        )

    def _assert_page_has_its_raster(
        self, page, source: str, want: dict, number: int
    ) -> None:
        result = page.evaluate(self._COUNT_COLOURS, [source, want["colours"]])
        counts = result["counts"]
        # Page n shows parcel n's colour (the narrowest parcel is a few
        # hundred pixels at 72 dpi) and no other parcel's (they are 5 km
        # apart).
        assert counts[number] > 200, (number, counts)
        assert [c for n, c in enumerate(counts) if n != number] == [0, 0], counts

    def test_the_zip_has_the_raster_on_every_page(
        self, page, fixtures: Path, expected
    ) -> None:
        import base64
        import zipfile

        _needs_webgl(page)
        want = expected["atlas_raster"]
        self._offline(page)
        with page.expect_download(timeout=180_000) as download_info:
            page.goto((fixtures / "atlas-raster.html").as_uri() + "?atlas=zip&dpi=72")
        with zipfile.ZipFile(download_info.value.path()) as archive:
            images = {name: archive.read(name) for name in archive.namelist()}
        assert len(images) == want["pages"] == 3
        # Entries are named by the atlas's file name expression, 'lot_<lot>'.
        for lot, number in (("C-3", 0), ("A-1", 1), ("B-2", 2)):
            (name,) = [n for n in images if f"lot_{lot}" in n]
            source = "data:image/png;base64," + base64.b64encode(images[name]).decode()
            self._assert_page_has_its_raster(page, source, want, number)

    def test_the_print_has_the_raster_on_every_page(
        self, page, fixtures: Path, expected
    ) -> None:
        _needs_webgl(page)
        want = expected["atlas_raster"]
        self._offline(page)
        page.add_init_script(
            "window.print = () => { window.__printedSources = [...document"
            ".querySelectorAll('[data-qgis2webmap-atlas-print] img')]"
            ".map((image) => image.src); };"
        )
        page.goto((fixtures / "atlas-raster.html").as_uri() + "?atlas=print&dpi=72")
        page.wait_for_function(
            "() => window.__printedSources !== undefined", timeout=180_000
        )
        sources = page.evaluate("() => window.__printedSources")
        assert len(sources) == want["pages"]
        for number, source in enumerate(sources):
            self._assert_page_has_its_raster(page, source, want, number)


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

    @pytest.mark.parametrize("which", ["parcels", "places"])
    def test_a_map_series_prints_and_zips_every_page(
        self, page, fixtures: Path, expected, which: str
    ) -> None:
        """The series the Atlas tab sets up: Print series sends one sheet per
        feature to the print, and Export series (images) downloads one PNG per
        feature."""
        import zipfile

        _needs_webgl(page)
        pages = expected["atlas_series"][which]
        assert pages == {"parcels": 3, "places": 4}[which]
        target = (fixtures / f"atlas-series-{which}.html").as_uri()
        page.add_init_script(
            "window.print = () => { window.__printedSheets = document"
            ".querySelectorAll('[data-qgis2webmap-atlas-print] img').length; };"
        )
        page.goto(target + "?atlas=print&dpi=72")
        page.wait_for_function(
            "() => window.__printedSheets !== undefined", timeout=120_000
        )
        assert page.evaluate("() => window.__printedSheets") == pages
        with page.expect_download(timeout=120_000) as download_info:
            page.goto(target + "?atlas=zip&dpi=72")
        with zipfile.ZipFile(download_info.value.path()) as archive:
            names = archive.namelist()
        assert len(names) == pages and all(n.endswith(".png") for n in names)

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


class TestAtlasCoverageNotDrawn:
    """The map left the coverage layer out; the atlas still pages through it."""

    def test_every_page_turns_with_the_coverage_layer_not_drawn(
        self, page, fixtures: Path, expected
    ) -> None:
        _needs_webgl(page)
        want = expected["atlas_undrawn"]
        _open(page, fixtures / "atlas-undrawn.html")
        page.wait_for_function(
            "() => document.querySelector('om-atlas')?.count > 0", timeout=60_000
        )
        seen = page.evaluate(
            """(frameId) => {
              const atlas = document.querySelector('om-atlas');
              const frame = document.getElementById(frameId);
              const pages = [];
              for (let index = 0; index < atlas.count; index++) {
                atlas.seek(index);
                pages.push({
                  id: Number(atlas.current.id),
                  center: frame.getAttribute('center'),
                });
              }
              return pages;
            }""",
            want["main_frame"],
        )
        # The identities of the pages are QGIS's own, in its order, and each
        # page moved the frame to its own parcel.
        assert [p["id"] for p in seen] == want["pages"]
        assert len({p["center"] for p in seen}) == len(want["pages"])


class TestAtlasFilterOutcomes:
    """An invalid atlas filter must never become an unfiltered atlas: the
    cartograph has no atlas at all. A valid filter pages exactly the parcels
    QGIS pages (none, some), and correcting the filter brings them back."""

    _PAGED_LOTS = """() => {
      const atlas = document.querySelector('om-atlas');
      const seen = [];
      for (let index = 0; index < atlas.count; index++) {
        atlas.seek(index);
        seen.push(atlas.current.properties.lot);
      }
      return seen;
    }"""

    def _paged_lots(
        self, page, fixtures: Path, name: str, expected_pages: int
    ) -> list[str]:
        _needs_webgl(page)
        _open(page, fixtures / f"atlas-filter-{name}.html")
        if expected_pages:
            page.wait_for_function(
                "(n) => document.querySelector('om-atlas')?.count === n",
                arg=expected_pages,
                timeout=60_000,
            )
        else:
            # Nothing to wait for but the atlas having had time to count.
            page.wait_for_function(
                "() => document.querySelector('om-atlas')", timeout=60_000
            )
            page.wait_for_timeout(2000)
        return page.evaluate(self._PAGED_LOTS)

    def test_an_invalid_filter_leaves_no_atlas_to_page(
        self, page, fixtures: Path, expected
    ) -> None:
        assert expected["atlas_filters"]["invalid"] is None
        html = (fixtures / "atlas-filter-invalid.html").read_text(encoding="utf-8")
        assert "<om-atlas" not in html
        _needs_webgl(page)
        # Its all-pages trigger finds nothing to export, rather than all three.
        page.goto((fixtures / "atlas-filter-invalid.html").as_uri() + "?atlas=zip")
        alert = page.locator("[data-qgis2webmap-atlas-notice][role=alert]")
        alert.wait_for(timeout=60_000)
        assert "no atlas" in alert.inner_text()
        assert page.evaluate("() => document.querySelector('om-atlas')") is None

    def test_a_filter_matching_nothing_is_an_atlas_with_no_pages(
        self, page, fixtures: Path, expected
    ) -> None:
        assert expected["atlas_filters"]["zero"] == []
        assert self._paged_lots(page, fixtures, "zero", 0) == []
        page.goto((fixtures / "atlas-filter-zero.html").as_uri() + "?atlas=print")
        alert = page.locator("[data-qgis2webmap-atlas-notice][role=alert]")
        alert.wait_for(timeout=60_000)
        assert "no pages" in alert.inner_text()

    def test_a_subset_pages_exactly_the_parcels_qgis_pages(
        self, page, fixtures: Path, expected
    ) -> None:
        want = expected["atlas_filters"]["subset"]
        assert want == ["C-3", "B-2"]
        assert self._paged_lots(page, fixtures, "subset", 2) == want

    def test_correcting_the_filter_pages_the_corrected_parcel(
        self, page, fixtures: Path, expected
    ) -> None:
        want = expected["atlas_filters"]["corrected"]
        assert want == ["A-1"]
        assert self._paged_lots(page, fixtures, "corrected", 1) == want


class TestAtlasResolution:
    """Print, PNG and ZIP of an atlas all render at the cartograph's saved
    resolution, not at a figure of their own: the same small sheet saved at
    72 and at 144 dpi gives images of one size and of twice that, on every
    route."""

    DPIS = (72, 144)

    @staticmethod
    def _want(expected, dpi: int) -> tuple[float, float]:
        width_mm, height_mm = expected["atlas_dpi"]["page_mm"]
        return width_mm / 25.4 * dpi, height_mm / 25.4 * dpi

    def _zip_sizes(self, page, fixtures: Path, name: str, query: str = ""):
        import zipfile

        _needs_webgl(page)
        with page.expect_download(timeout=180_000) as download_info:
            page.goto((fixtures / name).as_uri() + "?atlas=zip" + query)
        with zipfile.ZipFile(download_info.value.path()) as archive:
            return [_png_size(archive.read(entry)) for entry in archive.namelist()]

    def _print_sizes(self, page, fixtures: Path, name: str, query: str = ""):
        _needs_webgl(page)
        page.add_init_script(
            "window.print = () => { window.__printedSizes = [...document"
            ".querySelectorAll('[data-qgis2webmap-atlas-print] img')]"
            ".map((image) => [image.naturalWidth, image.naturalHeight]); };"
        )
        page.goto((fixtures / name).as_uri() + "?atlas=print" + query)
        page.wait_for_function(
            "() => window.__printedSizes !== undefined", timeout=180_000
        )
        return page.evaluate("() => window.__printedSizes")

    def test_zip_and_print_follow_the_saved_resolution(
        self, page, fixtures: Path, expected
    ) -> None:
        pages = expected["atlas_dpi"]["pages"]
        zip_widths = {}
        for dpi in self.DPIS:
            want_w, want_h = self._want(expected, dpi)
            name = f"atlas-dpi-{dpi}.html"
            for route in (self._zip_sizes, self._print_sizes):
                sizes = route(page, fixtures, name)
                assert len(sizes) == pages
                for width, height in sizes:
                    assert width == pytest.approx(want_w, abs=2), (route, dpi)
                    assert height == pytest.approx(want_h, abs=2), (route, dpi)
                zip_widths.setdefault(route.__name__, {})[dpi] = sizes[0][0]
        for widths in zip_widths.values():
            assert widths[144] / widths[72] == pytest.approx(2, abs=0.02)

    def test_png_export_follows_the_saved_resolution(
        self, page, fixtures: Path, expected
    ) -> None:
        for dpi in self.DPIS:
            want_w, want_h = self._want(expected, dpi)
            with page.expect_download(timeout=120_000) as download_info:
                # The very address the plugin's Export PNG opens.
                page.goto((fixtures / f"atlas-dpi-{dpi}.html").as_uri() + "?export=png")
            width, height = _png_size(Path(download_info.value.path()).read_bytes())
            assert width == pytest.approx(want_w, abs=2), dpi
            assert height == pytest.approx(want_h, abs=2), dpi

    def test_an_address_that_names_a_resolution_overrides_the_saved_one(
        self, page, fixtures: Path, expected
    ) -> None:
        want_w, _ = self._want(expected, 72)
        sizes = self._zip_sizes(page, fixtures, "atlas-dpi-144.html", "&dpi=72")
        assert all(width == pytest.approx(want_w, abs=2) for width, _ in sizes)


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


_SHEET_RECT = """() => {
  const r = document.querySelector('om-cartograph').getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height, kx: r.width /
    document.querySelector('om-cartograph').offsetWidth };
}"""

FIT_VIEWPORTS = [(1366, 768), (1024, 600), (800, 900), (390, 844)]


def _fit_open(page, path: Path, size: tuple[int, int], query: str = "") -> None:
    page.set_viewport_size({"width": size[0], "height": size[1]})
    _open(page, path, query)
    page.wait_for_timeout(500)


class TestPageFitsTheWindow:
    """The page opens whole in the window, like "Fit page", on screen only."""

    @pytest.mark.parametrize("size", FIT_VIEWPORTS)
    @pytest.mark.parametrize(
        "name", ["survey-static.html", "survey-a4-static.html", "atlas-live.html"]
    )
    def test_the_whole_page_is_in_view(
        self, page, fixtures: Path, name: str, size: tuple[int, int]
    ) -> None:
        if "live" in name:
            _needs_webgl(page)
        _fit_open(page, fixtures / name, size)
        rect = page.evaluate(_SHEET_RECT)
        assert rect["x"] >= -0.5 and rect["y"] >= -0.5
        assert rect["x"] + rect["w"] <= size[0] + 0.5
        assert rect["y"] + rect["h"] <= size[1] + 0.5
        # Not shrunk by more than the window needs: one side touches.
        assert max(rect["w"] / size[0], rect["h"] / size[1]) > 0.97 or rect["kx"] == 1
        # Nothing to scroll to: the page stays put wherever the document is
        # sent, and no scrollbar takes width from the window.
        page.evaluate("() => window.scrollTo(0, 99999)")
        assert page.evaluate(_SHEET_RECT) == rect
        assert page.evaluate(
            "() => innerWidth === document.documentElement.clientWidth"
        )

    def test_a_window_bigger_than_the_page_leaves_it_at_true_size(
        self, page, fixtures: Path
    ) -> None:
        _fit_open(page, fixtures / "survey-static.html", (2400, 1800))
        assert page.evaluate(_SHEET_RECT)["kx"] == pytest.approx(1, abs=1e-3)
        assert page.locator("[data-qgis2webmap-fit-toggle]").is_hidden()

    def test_the_button_toggles_actual_size_and_back(
        self, page, fixtures: Path
    ) -> None:
        _fit_open(page, fixtures / "survey-static.html", (1024, 600))
        button = page.locator("[data-qgis2webmap-fit-toggle]")
        assert button.inner_text() == "Actual size"
        button.click()
        assert page.evaluate(_SHEET_RECT)["kx"] == pytest.approx(1, abs=1e-3)
        assert button.inner_text() == "Fit page"
        button.click()
        assert page.evaluate(_SHEET_RECT)["kx"] < 1

    @pytest.mark.parametrize(
        ("name", "key"),
        [("survey-static.html", "survey"), ("survey-a4-static.html", "survey_a4")],
    )
    def test_png_export_keeps_the_true_page_pixels_in_a_small_window(
        self, page, fixtures: Path, expected, name: str, key: str
    ) -> None:
        page.set_viewport_size({"width": 390, "height": 844})
        with page.expect_download(timeout=60_000) as download_info:
            page.goto((fixtures / name).as_uri() + "?export=png&dpi=96")
        width, height = _png_size(Path(download_info.value.path()).read_bytes())
        page_w, page_h = expected[key]["page_mm"]
        assert width == pytest.approx(page_w / 25.4 * 96, abs=2)
        assert height == pytest.approx(page_h / 25.4 * 96, abs=2)
        assert (
            page.evaluate(
                "() => document.documentElement.hasAttribute('data-qgis2webmap-fit')"
            )
            is False
        )

    @pytest.mark.parametrize(
        ("name", "key"),
        [("survey-static.html", "survey"), ("survey-a4-static.html", "survey_a4")],
    )
    def test_print_is_unscaled_in_a_small_window(
        self, page, browser_name: str, fixtures: Path, expected, name: str, key: str
    ) -> None:
        _needs_pdf(browser_name)
        _fit_open(page, fixtures / name, (390, 844))
        assert page.evaluate(_SHEET_RECT)["kx"] < 1
        page.emulate_media(media="print")
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('om-cartograph'))"
                ".transform"
            )
            == "none"
        )
        assert page.locator("[data-qgis2webmap-fit-toggle]").is_hidden()
        pdf = page.pdf(prefer_css_page_size=True, print_background=True)
        width, height = _pdf_page_size_mm(pdf)
        page_w, page_h = expected[key]["page_mm"]
        assert width == pytest.approx(page_w, abs=0.5)
        assert height == pytest.approx(page_h, abs=0.5)
        assert pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 1

    def test_dragging_a_live_frame_moves_the_map_by_the_pointer_distance(
        self, page, fixtures: Path, expected
    ) -> None:
        """Under the scale, a drag of d screen pixels is d / k page pixels."""
        _needs_webgl(page)
        frame_id = expected["survey_live"]["main_frame"]
        _fit_open(page, fixtures / "survey-live.html", (1024, 600))
        k = page.evaluate(_SHEET_RECT)["kx"]
        assert k < 1
        lonlat = expected["survey_live"]["corners"][0]
        read_x = """([frameId, lonlat]) => document.getElementById(frameId)
            .georef.project(lonlat).xMm"""
        before = page.evaluate(read_x, [frame_id, lonlat])
        box = page.locator(f"#{frame_id}").bounding_box()
        assert box is not None
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        distance = 60 * k
        page.mouse.move(x, y)
        page.mouse.down()
        for step in range(1, 21):
            page.mouse.move(x + distance * step / 20, y)
        page.mouse.up()
        page.wait_for_timeout(1500)
        moved_mm = page.evaluate(read_x, [frame_id, lonlat]) - before
        assert moved_mm == pytest.approx(60 * 25.4 / 96, rel=0.06)

    def test_atlas_pages_still_turn_and_stay_in_view(
        self, page, fixtures: Path
    ) -> None:
        _needs_webgl(page)
        _fit_open(page, fixtures / "atlas-live.html", (1024, 600))
        page.wait_for_function(
            "() => document.querySelector('om-atlas')?.count > 0", timeout=60_000
        )
        ids = []
        for index in range(3):
            ids.append(
                page.evaluate(
                    "(i) => { const a = document.querySelector('om-atlas');"
                    " a.seek(i); return Number(a.current.id); }",
                    index,
                )
            )
            page.wait_for_timeout(300)
            rect = page.evaluate(_SHEET_RECT)
            assert rect["y"] + rect["h"] <= 600.5
            assert rect["x"] + rect["w"] <= 1024.5
        assert len(set(ids)) == 3


class TestBrowserPreviewFollowsTheLayout:
    """An open browser preview shows the layout on screen after every save."""

    @pytest.fixture
    def served(self, fixtures: Path, tmp_path: Path):
        from nika_onlymap_exporter.ui.live_server import NO_FILES, PreviewServer

        source = (fixtures / "survey-static.html").read_text(encoding="utf-8")
        first = tmp_path / "first-layout.html"
        second = tmp_path / "second-layout.html"
        first.write_text(source, encoding="utf-8")
        second.write_text(source.replace("Survey UTM", "Second layout"), "utf-8")
        server = PreviewServer(tmp_path, only=NO_FILES)
        server.start()
        try:
            yield server, first, second
        finally:
            server.stop()

    @staticmethod
    def _title(page) -> str:
        return page.evaluate("() => document.querySelector('om-text').innerText")

    def _open(self, page, server) -> None:
        from nika_onlymap_exporter.ui.live_server import PAGE_PATH

        page.goto(f"http://127.0.0.1:{server.port}{PAGE_PATH}")
        page.wait_for_selector("om-cartograph om-text", timeout=30_000)

    def test_switching_layout_updates_the_open_tab(self, page, served) -> None:
        server, first, second = served
        server.show(first)
        self._open(page, server)
        assert "Survey UTM" in self._title(page)
        server.show(second)
        page.wait_for_function(
            "() => document.querySelector('om-text')?.innerText"
            ".includes('Second layout')",
            timeout=8_000,
        )

    def test_saving_the_same_layout_again_updates_the_open_tab(
        self, page, served
    ) -> None:
        server, first, _second = served
        server.show(first)
        self._open(page, server)
        first.write_text(
            first.read_text(encoding="utf-8").replace("Survey UTM", "Edited title"),
            "utf-8",
        )
        server.show(first)
        page.wait_for_function(
            "() => document.querySelector('om-text')?.innerText"
            ".includes('Edited title')",
            timeout=8_000,
        )

    def test_the_fit_choice_survives_the_reload(self, page, served) -> None:
        server, first, second = served
        page.set_viewport_size({"width": 800, "height": 500})
        server.show(first)
        self._open(page, server)
        page.locator("[data-qgis2webmap-fit-toggle]").click()
        server.show(second)
        page.wait_for_function(
            "() => document.querySelector('om-text')?.innerText"
            ".includes('Second layout')",
            timeout=8_000,
        )
        page.wait_for_timeout(500)
        assert page.evaluate(_SHEET_RECT)["kx"] == pytest.approx(1, abs=1e-3)

    def test_an_export_through_the_server_is_not_hooked_and_keeps_its_size(
        self, page, served, expected
    ) -> None:
        from nika_onlymap_exporter.ui.live_server import PAGE_PATH

        server, first, _second = served
        server.show(first)
        with page.expect_download(timeout=60_000) as download_info:
            page.goto(f"http://127.0.0.1:{server.port}{PAGE_PATH}?export=png&dpi=96")
        width, height = _png_size(Path(download_info.value.path()).read_bytes())
        page_w, page_h = expected["survey"]["page_mm"]
        assert width == pytest.approx(page_w / 25.4 * 96, abs=2)
        assert height == pytest.approx(page_h / 25.4 * 96, abs=2)
        assert "EventSource" not in first.read_text(encoding="utf-8")
        import urllib.request

        url = f"http://127.0.0.1:{server.port}{PAGE_PATH}?export=png&dpi=96"
        with urllib.request.urlopen(url, timeout=10) as response:
            assert "EventSource" not in response.read().decode()


_LOADING = "[data-qgis2webmap-loading]"

#: Makes the page's frames slow to become ready: every frame's `ready` waits
#: for `window.__releaseSettle()` first, and the page's patience is cut to 1.5 s.
_SLOW_SETTLE = """
window.qgis2webmapLoadingBudgetMs = 1500;
window.__holdSettle = new Promise((resolve) => {
  window.__releaseSettle = resolve;
});
const defineElement = customElements.define.bind(customElements);
customElements.define = (name, constructor, options) => {
  const original = name === 'om-frame'
    && Object.getOwnPropertyDescriptor(constructor.prototype, 'ready');
  if (original && original.get) {
    Object.defineProperty(constructor.prototype, 'ready', {
      configurable: true,
      get() {
        return window.__holdSettle.then(() => original.get.call(this));
      },
    });
  }
  defineElement(name, constructor, options);
};
"""

#: Counts every `whenSettled` call per map. Its first call pays a warm-up of
#: full composites and canvas readbacks that blocks the page for seconds.
_COUNT_SETTLES = """
window.__settleCalls = new Map();
const defineElement = customElements.define.bind(customElements);
customElements.define = (name, constructor, options) => {
  if (name === 'om-map' && constructor.prototype.whenSettled) {
    const original = constructor.prototype.whenSettled;
    constructor.prototype.whenSettled = function (...args) {
      window.__settleCalls.set(this, (window.__settleCalls.get(this) || 0) + 1);
      return original.apply(this, args);
    };
  }
  defineElement(name, constructor, options);
};
"""
_SETTLE_COUNTS = "() => [...window.__settleCalls.values()]"


class TestLoadingNotice:
    """A heavy page says it is loading, then says it is slow, never lingers."""

    def test_it_is_in_the_html_before_the_data(self, fixtures: Path) -> None:
        html = (fixtures / "story-live.html").read_text(encoding="utf-8")
        assert html.index("data-qgis2webmap-loading") < html.index("<template")

    def test_a_static_page_has_none(self, fixtures: Path) -> None:
        html = (fixtures / "survey-static.html").read_text(encoding="utf-8")
        assert "<div data-qgis2webmap-loading" not in html

    def test_a_page_that_settles_shows_no_notice_afterwards(
        self, page, fixtures: Path
    ) -> None:
        _needs_webgl(page)
        _open(page, fixtures / "story-live.html")
        page.wait_for_selector(_LOADING, state="detached", timeout=60_000)
        assert page.locator(_LOADING).count() == 0

    def test_a_slow_page_says_loading_then_that_it_is_slow_then_clears(
        self, page, fixtures: Path
    ) -> None:
        _needs_webgl(page)
        page.add_init_script(_SLOW_SETTLE)
        page.goto((fixtures / "story-live.html").as_uri())
        note = page.locator(_LOADING)
        note.wait_for(state="visible", timeout=30_000)
        assert "Loading map" in note.inner_text()
        assert "taking a long time" not in note.inner_text()
        page.wait_for_function(
            "(s) => document.querySelector(s)?.hasAttribute('data-slow')",
            arg=_LOADING,
            timeout=30_000,
        )
        text = note.inner_text()
        assert "taking a long time to draw" in text
        assert "graphics card" in text
        # An overlay: the sheet does not move when it appears or goes.
        before = page.evaluate(_SHEET_RECT)
        page.evaluate("() => window.__releaseSettle()")
        page.wait_for_selector(_LOADING, state="detached", timeout=60_000)
        assert page.evaluate(_SHEET_RECT) == before

    def test_time_the_page_was_blocked_does_not_count(
        self, page, fixtures: Path
    ) -> None:
        _needs_webgl(page)
        page.add_init_script(_SLOW_SETTLE)
        page.goto((fixtures / "story-live.html").as_uri())
        page.locator(_LOADING).wait_for(state="visible", timeout=30_000)
        # Blocked for longer than the whole budget: that is not free time.
        page.evaluate(
            "() => { const end = Date.now() + 2500; while (Date.now() < end); }"
        )
        assert not page.evaluate(
            "(s) => document.querySelector(s).hasAttribute('data-slow')", _LOADING
        )

    def test_it_is_not_printed(self, page, fixtures: Path) -> None:
        _needs_webgl(page)
        page.add_init_script(_SLOW_SETTLE)
        page.goto((fixtures / "story-live.html").as_uri())
        page.locator(_LOADING).wait_for(state="visible", timeout=30_000)
        page.emulate_media(media="print")
        assert page.locator(_LOADING).is_hidden()

    @pytest.mark.parametrize("query", ["?print=1", "?export=png&dpi=96", "?atlas=zip"])
    def test_the_output_routes_never_have_it(
        self, page, fixtures: Path, query: str
    ) -> None:
        _needs_webgl(page)
        page.add_init_script(_SLOW_SETTLE)
        page.goto((fixtures / "story-live.html").as_uri() + query)
        page.wait_for_selector("om-cartograph", state="attached", timeout=30_000)
        assert page.locator(_LOADING).count() == 0

    def test_a_plain_view_adds_no_settle_call_of_its_own(
        self, page, fixtures: Path
    ) -> None:
        _needs_webgl(page)
        page.add_init_script(_COUNT_SETTLES)
        _open(page, fixtures / "story-live.html")
        page.wait_for_selector(_LOADING, state="detached", timeout=60_000)
        counts = page.evaluate(_SETTLE_COUNTS)
        # The runtime settles each live frame's map once as it mounts it; the
        # page's own script must not call again (the first call per map pays
        # a blocking warm-up, so every extra reader risks it).
        assert counts and max(counts) == 1

    def test_the_export_route_still_settles_its_maps(
        self, page, fixtures: Path
    ) -> None:
        _needs_webgl(page)
        page.add_init_script(_COUNT_SETTLES)
        with page.expect_download(timeout=90_000):
            page.goto((fixtures / "story-live.html").as_uri() + "?export=png&dpi=96")
        assert page.evaluate(_SETTLE_COUNTS)
