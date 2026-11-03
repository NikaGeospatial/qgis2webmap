"""Popup links and pictures, as a recipient's browser shows them.

The exporter writes each link and picture as markup the template inserts
unescaped, so only a browser can say the parts that matter: that a link can
actually be clicked (the runtime makes a popup transparent to the pointer),
that a carried picture loads and fits the popup, that a picture that is not
carried leaves the rest of the popup alone, and that a hostile value is shown
as its text and runs nothing.

Each check runs on the three ways a map is opened: a single file and an
extracted Share ZIP by double-click (`file://`), and a Folder export served
over http - the pictures travel inside the first and beside the page in the
other two.

The project is built through the normalized model, like the rest of this
tier; the picture's bytes come from a stand-in loader, because reading and
scaling files is the QGIS tier's subject (`tests/qgis/test_popup_media.py`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import functools
import struct
import threading
import zlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    OutputMode,
    PopupAttachment,
    PopupFieldMode,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.popup_media import (
    LoadedPicture,
    PictureProblem,
    attach_popup_media,
)
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

# Wider than any popup, and a panorama, so that only fitting its width (not
# the height cap on tall pictures) keeps it inside.
PICTURE_SIZE = (1200, 300)
HOSTILE_SITE = "<img src=x onerror=\"window.__pwned='site'\">"
HOSTILE_NOTE = "<b onmouseover=\"window.__pwned='note'\">bold</b>"
HOSTILE_PHOTO = "<script>window.__pwned='photo'</script>"
HOSTILE_LINK = "javascript:window.__pwned='link'"

# Feature order in the fixture.
LINKED, MISSING, HOSTILE = 0, 1, 2


def _png(width: int, height: int) -> bytes:
    """An opaque RGB PNG, built by hand: this tier has no imaging library."""
    row = b"\x00" + bytes([200, 60, 30]) * width
    raw = row * height

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (
            struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
        )

    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw, 9)),
            chunk(b"IEND", b""),
        ]
    )


def _project() -> ExportProject:
    picture = LoadedPicture(_png(*PICTURE_SIZE), "image/png", "png")

    def load(path: Path):
        return (
            picture
            if path == Path("/project/photos/site.png")
            else (PictureProblem.MISSING)
        )

    rows = [
        {
            "site": "https://example.org/site?a=1&b=2",
            "photo": "photos/site.png",
            "note": "first",
        },
        {"site": HOSTILE_LINK, "photo": "photos/missing.png", "note": "second"},
        {"site": HOSTILE_SITE, "photo": HOSTILE_PHOTO, "note": HOSTILE_NOTE},
    ]
    layer = ExportLayer(
        layer_id="sites",
        name="Sites",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=len(rows),
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0.1 * i, 51.0]},
                    "properties": row,
                }
                for i, row in enumerate(rows)
            ],
        },
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=6.0),
        ),
        popup=PopupSpec(
            enabled=True,
            fields=(
                PopupFieldSpec("site"),
                PopupFieldSpec(
                    "photo",
                    mode=PopupFieldMode.HEADER_WITH_DATA,
                    attachment=PopupAttachment(picture=True, base_dir="/project"),
                ),
                PopupFieldSpec("note"),
            ),
        ),
    )
    [layer] = attach_popup_media([layer], FidelityReportBuilder(), load)
    return ExportProject(
        title="Popup media",
        layers=(layer,),
        extent=Extent(west=-0.5, south=50.5, east=0.7, north=51.5),
    )


@pytest.fixture(scope="module")
def served_folder(runtime, tmp_path_factory):
    destination = tmp_path_factory.mktemp("popup-media-folder")
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
def opened_from_disk(runtime, tmp_path_factory):
    urls = {}
    for name, mode in (
        ("single file", OutputMode.STANDALONE_HTML),
        ("extracted zip", OutputMode.SHARE_ZIP),
    ):
        destination = tmp_path_factory.mktemp(f"popup-media-{mode.value}")
        result = OnlyMapWriter(runtime_provider=runtime).write(
            _project(), destination, mode=mode
        )
        urls[name] = result.entry_path.as_uri()
    return urls


@pytest.fixture(params=["single file", "extracted zip", "folder over http"])
def map_url(request, opened_from_disk, served_folder) -> str:
    if request.param == "folder over http":
        return served_folder
    return opened_from_disk[request.param]


def open_popup(page, url: str, feature: int) -> None:
    """Show one feature's popup, interpolated from the data the page carries.

    Injected rather than clicked, as `test_exported_map.TestPopupModes` does:
    hitting a 6px point needs its projected pixel, and `injectPickInternal`
    lands on the same path a real pick takes."""
    page.goto(url)
    page.wait_for_function(
        "() => customElements.get('om-map') !== undefined", timeout=30_000
    )
    page.wait_for_function(
        """(index) => {
            const map = document.querySelector('om-map');
            const layer = map.getLayers().find(
                (c) => 'note' in (c?.data?.[index]?.properties ?? {})
            );
            if (!layer) return false;
            map.injectPickInternal({
                layerId: layer.id, object: layer.data[index], index,
                coordinate: [0.1 * index, 51.0], pixel: [200, 200], type: 'click',
            });
            return true;
        }""",
        arg=feature,
        timeout=30_000,
    )
    # Until this feature's values are in, not merely the rows: the overlay
    # renders the bare template first.
    page.wait_for_function(
        """(note) => {
            const root = document.querySelector('om-overlay')?.shadowRoot;
            const values = root?.querySelectorAll('.om-popup-value');
            return values && values.length === 3
                && values[2].textContent === note
                && getComputedStyle(root.querySelector('.om-popup')).display !== 'none'
                && document.querySelector('om-overlay').style.visibility !== 'hidden';
        }""",
        arg=["first", "second", HOSTILE_NOTE][feature],
        timeout=15_000,
    )


def popup_state(page) -> dict:
    return page.evaluate(
        """async () => {
            const root = document.querySelector('om-overlay').shadowRoot;
            const images = Array.from(root.querySelectorAll('img'));
            await Promise.all(images.map((i) => i.decode().catch(() => null)));
            const popup = root.querySelector('.om-popup').getBoundingClientRect();
            return {
                popup: popup.toJSON(),
                values: Array.from(root.querySelectorAll('.om-popup-value')).map(
                    (v) => v.textContent),
                tags: Array.from(root.querySelectorAll('.om-popup *')).map(
                    (e) => e.tagName.toLowerCase()),
                links: Array.from(root.querySelectorAll('a')).map((a) => ({
                    href: a.getAttribute('href'), target: a.getAttribute('target'),
                    rel: a.getAttribute('rel'),
                })),
                images: images.map((i) => ({
                    naturalWidth: i.naturalWidth,
                    box: i.getBoundingClientRect().toJSON(),
                })),
                pwned: window.__pwned ?? null,
            };
        }"""
    )


class TestLinks:
    def test_an_address_is_a_link_to_a_new_tab(self, page, map_url) -> None:
        open_popup(page, map_url, LINKED)
        state = popup_state(page)
        assert state["links"] == [
            {
                "href": "https://example.org/site?a=1&b=2",
                "target": "_blank",
                "rel": "noopener noreferrer",
            }
        ]

    def test_the_link_can_actually_be_clicked(self, page, map_url) -> None:
        """The runtime makes a popup transparent to the pointer, so a link
        drawn in one is only a link if it takes the pointer back. A real click
        on it, at its own position, opens the address in a new tab."""
        page.context.route(
            "https://example.org/**", lambda route: route.fulfill(body="ok")
        )
        open_popup(page, map_url, LINKED)
        link = page.locator("om-overlay a")
        with page.context.expect_page(timeout=10_000) as opened:
            link.click(timeout=5_000)
        tab = opened.value
        tab.wait_for_load_state(timeout=10_000)
        assert tab.url == "https://example.org/site?a=1&b=2"
        tab.close()

    def test_a_value_that_is_not_an_address_stays_text(self, page, map_url) -> None:
        open_popup(page, map_url, MISSING)
        state = popup_state(page)
        assert state["links"] == []
        assert state["values"][0] == HOSTILE_LINK


class TestPictures:
    def test_a_carried_picture_loads_and_fits_the_popup(self, page, map_url) -> None:
        open_popup(page, map_url, LINKED)
        state = popup_state(page)
        [image] = state["images"]
        assert image["naturalWidth"] == PICTURE_SIZE[0]
        popup = state["popup"]
        box = image["box"]
        assert box["width"] > 0
        assert box["left"] >= popup["left"] - 0.5
        assert box["right"] <= popup["right"] + 0.5
        # Its proportions kept while it is shrunk to fit.
        ratio = PICTURE_SIZE[0] / PICTURE_SIZE[1]
        assert box["width"] / box["height"] == pytest.approx(ratio, rel=0.02)

    def test_a_picture_not_carried_shows_its_path_and_breaks_nothing(
        self, page, map_url
    ) -> None:
        open_popup(page, map_url, MISSING)
        state = popup_state(page)
        assert state["images"] == []
        assert state["values"] == [HOSTILE_LINK, "photos/missing.png", "second"]


class TestHostileValues:
    def test_they_are_shown_as_text_and_run_nothing(self, page, map_url) -> None:
        open_popup(page, map_url, HOSTILE)
        state = popup_state(page)
        assert state["values"] == [HOSTILE_SITE, HOSTILE_PHOTO, HOSTILE_NOTE]
        assert state["links"] == [] and state["images"] == []
        assert not {"script", "img", "a", "b"} & set(state["tags"])
        assert state["pwned"] is None
