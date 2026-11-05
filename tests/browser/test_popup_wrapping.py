"""Long popup values, as a recipient's browser lays them out.

A value with no space in it - an absolute path, a long web address, an ID -
has nowhere to wrap, and used to run past the popup's right edge. Only a
browser can say where a line breaks, so these open a real popup and measure
it: every value stays inside the popup's box, a link as well as plain text,
and an ordinary sentence still breaks between its words.

Checked on a single file opened by double-click (`file://`) and on a Folder
export served over http; the popup's styles are the same text in both.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    OutputMode,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.popup_media import attach_popup_media
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

LONG_PATH = (
    "/home/surveyor/projects/coastal_survey_2026/deliverables/orthophotos/"
    "block_17/tile_0042_0137_rgb_cog_final_v3.tif"
)
LONG_ADDRESS = (
    "https://data.example.org/catalogue/datasets/coastal-survey-2026/"
    "items/0042-0137?format=geotiff&band=rgb&token-free=true"
)
LONG_ID = "urn:uuid:" + "7f3a9c2e" * 8
SENTENCE = (
    "Surveyed by boat at low tide on a calm morning, with the harbour "
    "master's permission and two observers on board"
)
FIELDS = ("path", "address", "identifier", "note")


def _project() -> ExportProject:
    row = {
        "path": LONG_PATH,
        "address": LONG_ADDRESS,
        "identifier": LONG_ID,
        "note": SENTENCE,
    }
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
                    "geometry": {"type": "Point", "coordinates": [0.0, 51.0]},
                    "properties": row,
                }
            ],
        },
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=6.0),
        ),
        popup=PopupSpec(
            enabled=True, fields=tuple(PopupFieldSpec(name) for name in FIELDS)
        ),
    )
    # The pass that turns a web address into link markup, as an export runs it.
    [layer] = attach_popup_media([layer], FidelityReportBuilder())
    return ExportProject(
        title="Popup wrapping",
        layers=(layer,),
        extent=Extent(west=-0.5, south=50.5, east=0.5, north=51.5),
    )


@pytest.fixture(scope="module")
def served_folder(runtime, tmp_path_factory):
    destination = tmp_path_factory.mktemp("popup-wrapping-folder")
    OnlyMapWriter(runtime_provider=runtime).write(
        _project(), destination, mode=OutputMode.FOLDER, unbundle=True
    )
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(destination))
    handler.log_message = lambda *args: None  # type: ignore[attr-defined]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def single_file(runtime, tmp_path_factory) -> str:
    destination = tmp_path_factory.mktemp("popup-wrapping-file")
    result = OnlyMapWriter(runtime_provider=runtime).write(
        _project(), destination, mode=OutputMode.STANDALONE_HTML
    )
    return result.entry_path.as_uri()


@pytest.fixture(params=["single file", "folder over http"])
def map_url(request, single_file, served_folder) -> str:
    return single_file if request.param == "single file" else served_folder


def open_popup(page, url: str) -> None:
    """Show the feature's popup, injected as `test_popup_media` does it."""
    page.goto(url)
    page.wait_for_function(
        "() => customElements.get('om-map') !== undefined", timeout=30_000
    )
    # The popup is placed by the map's projection, which needs a drawn map.
    if not page.evaluate(
        "() => !!document.createElement('canvas').getContext('webgl2')"
    ):
        pytest.skip("no WebGL2 context in this browser/environment")
    page.wait_for_function(
        """() => {
            const map = document.querySelector('om-map');
            const layer = map.getLayers().find(
                (c) => 'note' in (c?.data?.[0]?.properties ?? {})
            );
            if (!layer) return false;
            map.injectPickInternal({
                layerId: layer.id, object: layer.data[0], index: 0,
                coordinate: [0.0, 51.0], pixel: [200, 200], type: 'click',
            });
            return true;
        }""",
        timeout=30_000,
    )
    page.wait_for_function(
        """(note) => {
            const root = document.querySelector('om-overlay')?.shadowRoot;
            const values = root?.querySelectorAll('.om-popup-value');
            return values && values.length === 4
                && values[3].textContent === note
                && getComputedStyle(root.querySelector('.om-popup')).display !== 'none'
                && document.querySelector('om-overlay').style.visibility !== 'hidden';
        }""",
        arg=SENTENCE,
        timeout=15_000,
    )


def layout(page) -> dict:
    """The popup's box, each value's box and line count, and the words of the
    sentence that a line break split."""
    return page.evaluate(
        """() => {
            const root = document.querySelector('om-overlay').shadowRoot;
            const popup = root.querySelector('.om-popup');
            const box = popup.getBoundingClientRect();
            const style = getComputedStyle(popup);
            const inner = {
                left: box.left + parseFloat(style.borderLeftWidth)
                    + parseFloat(style.paddingLeft),
                right: box.right - parseFloat(style.borderRightWidth)
                    - parseFloat(style.paddingRight),
            };
            const lines = (element) => {
                const range = document.createRange();
                range.selectNodeContents(element);
                return new Set(
                    Array.from(range.getClientRects()).map((r) => Math.round(r.top))
                ).size;
            };
            const values = Array.from(root.querySelectorAll('.om-popup-value'));
            const note = values[3].firstChild;
            const split = [];
            let at = 0;
            for (const word of note.textContent.split(' ')) {
                const range = document.createRange();
                range.setStart(note, at);
                range.setEnd(note, at + word.length);
                const tops = new Set(
                    Array.from(range.getClientRects()).map((r) => Math.round(r.top))
                );
                if (tops.size > 1) split.push(word);
                at += word.length + 1;
            }
            return {
                popup: box.toJSON(),
                inner,
                values: values.map((v) => ({
                    text: v.textContent,
                    box: v.getBoundingClientRect().toJSON(),
                    lines: lines(v),
                })),
                links: values.map((v) => v.querySelector('a') !== null),
                split,
            };
        }"""
    )


class TestLongValues:
    def test_every_value_stays_inside_the_popup(self, page, map_url) -> None:
        open_popup(page, map_url)
        state = layout(page)

        # The address is a link, so the link markup is measured too.
        assert state["links"] == [False, True, False, False]
        assert [v["text"] for v in state["values"]] == [
            LONG_PATH,
            LONG_ADDRESS,
            LONG_ID,
            SENTENCE,
        ]
        outside = [
            value["text"]
            for value in state["values"]
            if value["box"]["left"] < state["inner"]["left"] - 0.5
            or value["box"]["right"] > state["inner"]["right"] + 0.5
        ]
        assert outside == []
        # Kept whole by wrapping, not by cutting it off: each long value runs
        # over more than one line.
        assert all(value["lines"] > 1 for value in state["values"][:3])

    def test_a_sentence_still_breaks_between_its_words(self, page, map_url) -> None:
        open_popup(page, map_url)
        state = layout(page)

        sentence = state["values"][3]
        assert sentence["lines"] > 1
        assert state["split"] == []
