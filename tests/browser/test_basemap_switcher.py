"""The basemap switcher, as a reader finds it.

Ticked on the Map tab, the exported map carries the runtime's own switcher:
one choice per keyless basemap, the map's own ticked, and OpenStreetMap left
out of a map opened from a file, whose readers its servers refuse. A choice
switches the map's basemap. Every request off this machine is refused here,
so no tile server is contacted: what is checked is which basemap the map is
told to draw, not how its tiles look.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    OutputMode,
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

SWITCHER = 'om-widget[type="basemap-switcher"]'

FROM_A_FILE = ["Positron", "Dark Matter", "Voyager", "Liberty", "Bright"]

CHOICES = """
() => [...document.querySelector('om-widget[type="basemap-switcher"]')
          .shadowRoot.querySelectorAll('label')].map((label) => ({
    text: label.textContent.trim(),
    checked: label.querySelector('input').checked,
}))
"""


def _project(mode: OutputMode, basemap: str = "positron") -> ExportProject:
    layer = ExportLayer(
        layer_id="sites",
        name="Sites",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0.1, 51.1]},
                    "properties": {"name": "Wye"},
                }
            ],
        },
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=8.0),
        ),
    )
    return ExportProject(
        title="Basemap switcher map",
        layers=(layer,),
        extent=Extent(west=-1.0, south=50.9, east=1.0, north=51.9),
        settings=ExportSettings(
            output_mode=mode, basemap=basemap, basemap_switcher=True
        ),
    )


@pytest.fixture
def offline(page):
    """Refuse every request that would leave this machine, and keep a list."""
    refused: list[str] = []

    def route(handled) -> None:
        url = handled.request.url
        if url.startswith(("http://127.0.0.1", "file:", "data:", "blob:")):
            handled.continue_()
            return
        refused.append(url)
        handled.abort()

    page.route("**/*", route)
    return refused


def _choices(page, url: str) -> list[dict]:
    page.goto(url)
    page.wait_for_function(
        """(selector) => document.querySelector(selector)
             ?.shadowRoot?.querySelectorAll('label').length > 0""",
        arg=SWITCHER,
        timeout=30_000,
    )
    return page.evaluate(CHOICES)


class TestWhatItOffers:
    def test_a_map_opened_from_a_file_has_no_osm(
        self, page, offline, runtime, tmp_path
    ) -> None:
        """Share ZIP is opened from a file too; its markup is the same
        (`tests/unit/test_export_options.py`)."""
        entry = (
            OnlyMapWriter(runtime_provider=runtime)
            .write(_project(OutputMode.STANDALONE_HTML), tmp_path)
            .entry_path
        )
        choices = _choices(page, entry.as_uri())
        assert [choice["text"] for choice in choices] == FROM_A_FILE
        assert [choice["text"] for choice in choices if choice["checked"]] == [
            "Positron"
        ]

    def test_a_folder_on_a_website_offers_osm_too(
        self, page, offline, runtime, tmp_path
    ) -> None:
        entry = (
            OnlyMapWriter(runtime_provider=runtime)
            .write(_project(OutputMode.FOLDER), tmp_path, mode=OutputMode.FOLDER)
            .entry_path
        )
        server = PreviewServer(entry.parent)
        server.start()
        try:
            choices = _choices(page, f"http://127.0.0.1:{server.port}/{entry.name}")
        finally:
            server.stop()
        assert [choice["text"] for choice in choices] == ["OpenStreetMap", *FROM_A_FILE]


class TestAChoiceSwitchesTheMap:
    def test_picking_one_switches_the_basemap(
        self, page, offline, runtime, tmp_path
    ) -> None:
        entry = (
            OnlyMapWriter(runtime_provider=runtime)
            .write(_project(OutputMode.STANDALONE_HTML), tmp_path)
            .entry_path
        )
        _choices(page, entry.as_uri())
        page.locator(SWITCHER).locator("label", has_text="Dark Matter").click()
        page.wait_for_function(
            "() => document.querySelector('om-map').getAttribute('basemap')"
            " === 'dark-matter'",
            timeout=10_000,
        )
        choices = page.evaluate(CHOICES)
        assert [choice["text"] for choice in choices if choice["checked"]] == [
            "Dark Matter"
        ]
