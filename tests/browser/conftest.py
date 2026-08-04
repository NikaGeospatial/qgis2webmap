"""A real exported artifact, opened in a real browser.

This tier deliberately does **not** need PyQGIS. It builds its project through
the normalized model directly, so it tests what a recipient receives rather than
what QGIS produced -- and it runs anywhere Playwright does, which is what makes
the Chrome/Firefox/WebKit matrix in issue #29's release gates achievable.

It does need the OnlyMap runtime: the whole point is that the map actually
mounts, and a fake runtime cannot mount anything.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ElevationSpec,
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    LabelingSpec,
    PopupFieldMode,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.packaging.runtime_manager import (
    LocalRuntime,
    discover_runtime_dir,
)
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.87, 51.15]},
            "properties": {"name": "Ashford", "kind": "civil"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-0.2, 51.65]},
            "properties": {"name": "Barnet", "kind": "civil"},
        },
    ],
}


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, browser_name):
    """Turn WebGL on in headless Firefox.

    OnlyMap draws through deck.gl, so a browser without a WebGL2 context cannot
    render the map at all. Headless Firefox ships with WebGL off
    (`AllowWebgl2:false`), which is a property of the harness rather than of the
    export -- left unset, this tier reports a WebGL failure on every run and the
    signal that matters gets lost in it.

    Chromium and WebKit need nothing here.
    """
    if browser_name != "firefox":
        return browser_type_launch_args

    return {
        **browser_type_launch_args,
        "firefox_user_prefs": {
            "webgl.force-enabled": True,
            "webgl.disabled": False,
            "gfx.webrender.all": True,
        },
    }


@pytest.fixture(scope="session")
def runtime():
    directory = discover_runtime_dir()
    if directory is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
    return LocalRuntime(directory)


@pytest.fixture(scope="session")
def exported_map(runtime, tmp_path_factory):
    """One standalone artifact, written once and reused by every browser test."""
    layer = ExportLayer(
        layer_id="stations",
        name="Stations",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=2,
        geojson=GEOJSON,
        attribution="© Fixture Survey",
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=6.0),
        ),
        labeling=LabelingSpec(enabled=True, field_name="name", font_size=13.0),
        popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("name"),)),
    )
    project = ExportProject(
        title="Browser tier map",
        layers=(layer,),
        extent=Extent(west=-1.0, south=50.9, east=1.1, north=51.9),
    )

    destination = tmp_path_factory.mktemp("artifact")
    result = OnlyMapWriter(runtime_provider=runtime).write(project, destination)
    return result.entry_path


EXTRUDED_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[0.0, 51.0], [0.1, 51.0], [0.1, 51.1], [0.0, 51.1], [0.0, 51.0]]
                ],
            },
            "properties": {"name": "Block", "height": 400.0},
        }
    ],
}


@pytest.fixture(scope="session")
def extruded_map(runtime, tmp_path_factory):
    """A raised polygon, so the extrusion path is checked by a real renderer.

    Markup tests can only say the attributes were written. deck.gl silently
    ignores a prop it does not understand, so whether `extruded` actually
    reaches the layer is a question only a browser can answer.
    """
    layer = ExportLayer(
        layer_id="blocks",
        name="Blocks",
        geometry_kind=GeometryKind.POLYGON,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson=EXTRUDED_GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=200, g=80, b=40)),
        ),
        elevation=ElevationSpec(
            extruded=True, height_field="height", wireframe=True, source="3d-renderer"
        ),
        popup=PopupSpec(enabled=False),
    )
    project = ExportProject(
        title="Extruded map",
        layers=(layer,),
        extent=Extent(west=-0.1, south=50.9, east=0.2, north=51.2),
    )

    destination = tmp_path_factory.mktemp("extruded")
    result = OnlyMapWriter(runtime_provider=runtime).write(project, destination)
    return result.entry_path


# One field per popup mode, and two features: the first has a value for every
# field, the second has none. That second feature is the entire subject of the
# `*_WITH_DATA` modes, and it has to be real data rather than a hand-fed picking
# payload -- the runtime materializes the picked row from the layer's own
# columns, so a popup only ever shows what the data actually holds.
POPUP_MODE_FIELDS = (
    "inline_with_data",
    "header_with_data",
    "inline_always",
    "header_always",
    "no_label",
)

MODES_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.87, 51.15]},
            "properties": dict.fromkeys(POPUP_MODE_FIELDS, "Ashford"),
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-0.2, 51.65]},
            "properties": dict.fromkeys(POPUP_MODE_FIELDS, ""),
        },
    ],
}


@pytest.fixture(scope="session")
def popup_modes_map(runtime, tmp_path_factory):
    """An artifact carrying one field in each popup mode.

    The `*_WITH_DATA` modes are the only part of this feature that is not
    decided when the file is written -- the stylesheet resolves them in the
    browser -- so this tier is the only place their behaviour can be checked
    at all.
    """
    layer = ExportLayer(
        layer_id="stations",
        name="Stations",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=2,
        geojson=MODES_GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=6.0),
        ),
        popup=PopupSpec(
            enabled=True,
            fields=(
                PopupFieldSpec(
                    "inline_with_data", mode=PopupFieldMode.INLINE_WITH_DATA
                ),
                PopupFieldSpec(
                    "header_with_data", mode=PopupFieldMode.HEADER_WITH_DATA
                ),
                PopupFieldSpec("inline_always", mode=PopupFieldMode.INLINE_ALWAYS),
                PopupFieldSpec("header_always", mode=PopupFieldMode.HEADER_ALWAYS),
                PopupFieldSpec("no_label", mode=PopupFieldMode.NO_LABEL),
            ),
        ),
    )
    project = ExportProject(
        title="Popup modes",
        layers=(layer,),
        extent=Extent(west=-1.0, south=50.9, east=1.1, north=51.9),
    )

    destination = tmp_path_factory.mktemp("popup-modes")
    result = OnlyMapWriter(runtime_provider=runtime).write(project, destination)
    return result.entry_path


@pytest.fixture
def page_with_network_log(page):
    """A page that records every request the document makes.

    Recorded rather than blocked: the gate is "makes no unwanted network
    request", and a blocked request would let a map that tries and fails still
    look clean.
    """
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.requests_made = requests
    return page
