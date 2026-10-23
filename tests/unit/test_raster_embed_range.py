"""The embedded-raster fetch stand-in answers range requests as a server would.

Run in Node, which has `fetch`, `Response` and `Headers`; skipped without it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from nika_onlymap_exporter.packaging.raster_embed import build_raster_embed
from nika_onlymap_exporter.packaging.web_mercator_projjson import (
    WEB_MERCATOR_PROJJSON,
)

NODE = shutil.which("node")


def _status_for(range_header: str) -> dict[str, object]:
    return _run_stand_in(
        f"""fetch("r.tif", {{ headers: {{ Range: {json.dumps(range_header)} }} }})
  .then(async (r) => console.log(JSON.stringify({{
    status: r.status, body: await r.text(),
    range: r.headers.get("Content-Range") }})));"""
    )


def _run_stand_in(request: str) -> dict[str, object]:
    """Run the stand-in in Node, then `request`, which prints one JSON line."""
    markup = build_raster_embed([("r.tif", b"0123456789")])
    data = re.search(r'data-src="r.tif">([^<]*)<', markup)
    stand_in = re.search(r"<script>\n(.*)\n\s*</script>", markup, re.S)
    assert data and stand_in
    script = f"""
const blocks = [{{ textContent: {json.dumps(data.group(1))},
  dataset: {{ src: "r.tif" }}, remove() {{}} }}];
globalThis.window = globalThis;
globalThis.addEventListener = () => {{}};
globalThis.document = {{ querySelectorAll: () => blocks }};
globalThis.location = {{ protocol: "http:" }};
{stand_in.group(1)}
{request}
"""
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_a_range_inside_the_file_is_served() -> None:
    assert _status_for("bytes=2-4") == {
        "status": 206,
        "body": "234",
        "range": "bytes 2-4/10",
    }


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_a_range_past_the_end_is_refused() -> None:
    """An empty 206 reads as a zero-length tile, not as a bad request."""
    answer = _status_for("bytes=20-30")
    assert answer["status"] == 416
    assert answer["range"] == "bytes */10"


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_web_mercator_is_answered_from_the_page_not_epsg_io() -> None:
    """The runtime's COG reader asks epsg.io for its file's CRS; offline that
    request failed and the raster drew nothing."""
    answer = _run_stand_in(
        """fetch("https://epsg.io/3857.json").then(async (r) => console.log(
  JSON.stringify({ status: r.status, crs: await r.json() })));"""
    )
    assert answer["status"] == 200
    assert answer["crs"] == json.loads(WEB_MERCATOR_PROJJSON)


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_each_raster_read_is_noted_for_the_print_gate() -> None:
    answer = _run_stand_in(
        """const before = window.qgis2webmapRasters.read.size;
fetch("r.tif").then(() => console.log(JSON.stringify({
  before, read: [...window.qgis2webmapRasters.read],
  names: window.qgis2webmapRasters.names })));"""
    )
    assert answer == {"before": 0, "read": ["r.tif"], "names": ["r.tif"]}
