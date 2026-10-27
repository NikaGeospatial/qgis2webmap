"""A label's line breaks, in a real browser.

QGIS draws a multi-line label on as many lines as it has; the runtime sets no
`white-space` on `<om-text>`, so the browser folds the breaks into one run-on
paragraph unless the page asks for them. Only a browser can tell.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nika_onlymap_exporter.core.cartograph_ir import Box, CartographSpec, TextSpec
from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

_LINES = ("Roads: 94.5 km", "Trails: 372.1 km", "Viewpoints: 29")


@pytest.fixture(scope="module")
def text_page(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = discover_runtime_dir()
    if directory is None or not (directory / "cartograph.standalone.js").exists():
        pytest.skip("OnlyMap runtime with a cartograph build not available")
    runtime = CartographRuntimeBundle(
        cartograph_js=(directory / "cartograph.standalone.js").read_bytes(),
        cartograph_css=(directory / "cartograph.css").read_text(encoding="utf-8"),
    )
    items = (
        TextSpec(id="broken", box=Box(10, 10, 120, 40), body="\n".join(_LINES)),
        TextSpec(id="plain", box=Box(10, 60, 120, 40), body=_LINES[0]),
    )
    spec = CartographSpec(cartograph_id="cg-text", size="A4", items=items)
    destination = tmp_path_factory.mktemp("cartograph-text") / "text.html"
    destination.write_text(write_cartograph_html(spec, runtime), encoding="utf-8")
    return destination


_LINE_COUNT = """
(id) => {
  const element = document.getElementById(id);
  const range = document.createRange();
  range.selectNodeContents(element);
  return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
}
"""


class TestLabelLineBreaks:
    def test_each_qgis_line_is_drawn_on_its_own_line(
        self, page, text_page: Path
    ) -> None:
        page.goto(text_page.as_uri())
        page.wait_for_selector("#broken", timeout=30_000)
        assert page.evaluate(_LINE_COUNT, "broken") == len(_LINES)

    def test_a_one_line_label_stays_on_one_line(self, page, text_page: Path) -> None:
        page.goto(text_page.as_uri())
        page.wait_for_selector("#plain", timeout=30_000)
        assert page.evaluate(_LINE_COUNT, "plain") == 1
