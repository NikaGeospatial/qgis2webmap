"""Browser-tier smoke test for a static-only cartograph.

The release gate this protects (`specs/cartograph/plan.md` "Verification"):
a cartograph produced by `writers/cartograph_writer.py` opens from `file://`,
passes the runtime's own `validateCartograph` with zero errors, and its
static frame's georeference places a known lon/lat at the millimetre
position a producer would expect -- not just "the markup looks right",
which is as far as `tests/unit/test_cartograph_writer.py` can see.

This tier deliberately does **not** need PyQGIS: the fixture is built
directly from `core/cartograph_ir.py`, exactly like
`tests/browser/test_exported_map.py` builds its map from `core/export_ir.py`.
It does need the OnlyMap runtime's cartograph build
(`cartograph.standalone.js` + `cartograph.css`) and a real browser; both are
`pytest.skip`'d cleanly when unavailable, per this project's own convention.

**Why the test does a second module import.** The artifact's own bootstrap
script (the one `write_cartograph_html` writes) keeps its `carto` module
namespace as a script-local variable -- there is nothing for a test to read
off `window`, and there should not be: adding a test hook to production
output is not this project's style. The runtime's own custom-element
registration is idempotent (`customElements.get(tag) || customElements.
define(tag, ...)` in `cartograph.standalone.js`), so a second `import()` of
the same source, done here purely to get at `validateCartograph`, is safe:
it defines nothing a second time and returns a fresh module namespace with
every exported function on it.

**Verified against the real runtime, outside pytest.** This machine's
Python has no `playwright` package installed (only Node's does), so this
file has never executed here. The equivalent check -- an artifact built by
`write_cartograph_html`, opened in real Chromium via the project's own Node
Playwright runner, with the same `validateCartograph` call and the same
`frame.georef.project(...)` call -- was run by hand and confirmed
`valid: true`, zero errors, `degenerate: false`. That run is also what
caught that `project()` returns millimetres **relative to the frame's own
box**, not absolute page position -- the first version of `_EXPECTED_MM`
below assumed the latter and was off by exactly `(box.x, box.y)`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    CartographSpec,
    FrameSpec,
    ScalebarSpec,
)
from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

# The frame's picture: a 2x2 px solid PNG, hand-built the same way
# test_exported_map.py does it -- this tier has no imaging library, and the
# point is that the bytes are a genuinely valid PNG, not a placeholder.
_PNG_SIDE = 2
_PNG_RGB = (235, 169, 65)

# A simple axis-aligned geographic frame in EPSG:4326, where the runtime's
# own docs say crsForward is the identity -- so the fitted homography is a
# plain affine scale+translate and every interior point's placement can be
# checked by hand, with no proj4 and no network involved.
_LON_MIN, _LON_MAX = 0.0, 10.0
_LAT_MIN, _LAT_MAX = 40.0, 48.0
_FRAME_BOX = Box(x=10.0, y=10.0, w=100.0, h=80.0)
# tl, tr, br, bl -- the runtime's required corner order.
_CORNERS = (
    (_LON_MIN, _LAT_MAX),
    (_LON_MAX, _LAT_MAX),
    (_LON_MAX, _LAT_MIN),
    (_LON_MIN, _LAT_MIN),
)
# A point away from every corner and edge, so a transposed or mirrored
# homography would miss it rather than coincidentally agreeing.
_TEST_LONLAT = (2.5, 46.0)
# `FrameGeoref.project()` returns millimetres **relative to the frame's own
# box** (0..w, 0..h), not absolute page position -- confirmed against the
# real runtime (see this test's module docstring for how). The frame's
# `x`/`y` placement is a separate, already-tested concern
# (test_cartograph_writer.py's placement-agreement tests).
_EXPECTED_MM = (
    _FRAME_BOX.w * (_TEST_LONLAT[0] - _LON_MIN) / (_LON_MAX - _LON_MIN),
    _FRAME_BOX.h * (_LAT_MAX - _TEST_LONLAT[1]) / (_LAT_MAX - _LAT_MIN),
)


def _solid_png(side: int, rgb: tuple[int, int, int]) -> bytes:
    red, green, blue = rgb
    raw = b"".join(b"\x00" + bytes([red, green, blue, 255]) * side for _ in range(side))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (
            struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
        )

    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw, 9)),
            chunk(b"IEND", b""),
        ]
    )


@pytest.fixture(scope="session")
def runtime_dir() -> Path:
    directory = discover_runtime_dir()
    if directory is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
    if not (directory / "cartograph.standalone.js").exists():
        pytest.skip("this runtime copy has no cartograph build")
    return directory


@pytest.fixture(scope="session")
def static_cartograph(
    runtime_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """One standalone, static-only artifact, written once and reused."""
    frame = FrameSpec(
        id="main",
        box=_FRAME_BOX,
        mode="static",
        crs="EPSG:4326",
        crs_def="+proj=longlat +datum=WGS84 +no_defs",
        corners=_CORNERS,
        image_bytes=_solid_png(_PNG_SIDE, _PNG_RGB),
    )
    bar = ScalebarSpec(id="bar", box=Box(10, 92, 70, 9), for_="main", kind="double")
    spec = CartographSpec(
        cartograph_id="cg-browser-static",
        title="Browser static smoke",
        size="A4",
        items=(frame, bar),
    )
    runtime = CartographRuntimeBundle(
        cartograph_js=(runtime_dir / "cartograph.standalone.js").read_bytes(),
        cartograph_css=(runtime_dir / "cartograph.css").read_text(encoding="utf-8"),
    )
    html = write_cartograph_html(spec, runtime)

    destination = tmp_path_factory.mktemp("cartograph") / "static.html"
    destination.write_text(html, encoding="utf-8")
    return destination


_CHECK_SCRIPT = """
async (source) => {
  const blob = new Blob([source], { type: "text/javascript" });
  const carto = await import(URL.createObjectURL(blob));
  const frame = document.querySelector("#main");
  await frame.ready;
  const root = document.querySelector("om-cartograph");
  const validation = carto.validateCartograph(root);
  const georef = frame.georef;
  const projected = georef.project(%s);
  return {
    valid: validation.valid,
    errors: validation.errors,
    warnings: validation.warnings,
    degenerate: georef.degenerate,
    projected,
  };
}
"""


class TestStaticCartographInABrowser:
    def test_opens_validates_and_projects_a_known_point(
        self, page, static_cartograph: Path, runtime_dir: Path
    ) -> None:
        page.goto(static_cartograph.as_uri())
        page.wait_for_selector("#main", timeout=30_000)

        cartograph_source = (runtime_dir / "cartograph.standalone.js").read_text(
            encoding="utf-8"
        )
        result = page.evaluate(_CHECK_SCRIPT % list(_TEST_LONLAT), cartograph_source)

        assert result["errors"] == []
        assert result["valid"] is True
        assert result["degenerate"] is False

        projected = result["projected"]
        assert projected is not None
        assert projected["xMm"] == pytest.approx(_EXPECTED_MM[0], abs=0.05)
        assert projected["yMm"] == pytest.approx(_EXPECTED_MM[1], abs=0.05)

    def test_static_only_page_never_mounts_a_map_canvas(
        self, page, static_cartograph: Path
    ) -> None:
        # No live frame, no <om-map>, no WebGL -- the point of the
        # static/live split (docs/cartograph.md, spike-findings.md #1).
        page.goto(static_cartograph.as_uri())
        page.wait_for_selector("#main", timeout=30_000)
        assert page.locator("om-map").count() == 0
