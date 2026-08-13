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

import base64

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ElevationSpec,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    IconAtlasSpec,
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


HIGHLIGHT = Color(r=29, g=233, b=200, a=0.5)


@pytest.fixture(scope="session")
def highlighted_map(runtime, tmp_path_factory):
    """A layer carrying an explicit highlight colour.

    Separate from `exported_map` so the assertion can name the exact RGBA it
    expects rather than the default, which is what distinguishes "our colour
    arrived" from "deck.gl fell back to something".
    """
    layer = ExportLayer(
        layer_id="stations",
        name="Stations",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=2,
        geojson=GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=6.0),
        ),
        popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("name"),)),
        highlight_color=HIGHLIGHT,
    )
    project = ExportProject(
        title="Highlighted map",
        layers=(layer,),
        extent=Extent(west=-1.0, south=50.9, east=1.1, north=51.9),
    )

    destination = tmp_path_factory.mktemp("highlighted")
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


@pytest.fixture(scope="session")
def textured_terrain_map(runtime, tmp_path_factory):
    """Relief plus a basemap, so the terrain-texture path is exercised.

    Separate from `exported_map` on purpose: that fixture underwrites
    `test_the_only_external_call_is_telemetry`, and terrain would add
    s3.amazonaws.com and cartocdn.com traffic to it. The terrain test aborts
    those requests instead - the claim is that the attributes reach the
    element, which is the whole contract with the runtime.
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
            extruded=True, height_field="height", source="3d-renderer"
        ),
        popup=PopupSpec(enabled=False),
    )
    project = ExportProject(
        title="Textured terrain map",
        layers=(layer,),
        extent=Extent(west=-0.1, south=50.9, east=0.2, north=51.2),
        settings=ExportSettings(terrain="terrarium", basemap="voyager"),
    )

    destination = tmp_path_factory.mktemp("terrain")
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


# --------------------------------------------------------------------------
# Icon markers
# --------------------------------------------------------------------------


def _solid_png(side: int, rgb: tuple[int, int, int]) -> bytes:
    """A square opaque PNG, built by hand.

    This tier deliberately has no PyQGIS and no imaging library, and the point
    of the icon fixtures is to prove the *browser* accepts what we emit - so the
    bytes have to be a genuinely valid PNG rather than a placeholder string. A
    dozen lines of zlib and CRC is cheaper than a dependency the release matrix
    would then have to install everywhere.
    """
    import struct
    import zlib

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
            # 8-bit RGBA, no interlace.
            chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw, 9)),
            chunk(b"IEND", b""),
        ]
    )


def _png_data_uri(side: int, rgb: tuple[int, int, int]) -> str:
    return "data:image/png;base64," + base64.b64encode(_solid_png(side, rgb)).decode()


ICON_CELL = 48
ICON_ATLAS = IconAtlasSpec(
    data_uri=_png_data_uri(ICON_CELL, (220, 40, 120)),
    mapping={
        "i0": {
            "x": 0,
            "y": 0,
            "width": ICON_CELL,
            "height": ICON_CELL,
            "anchorX": ICON_CELL / 2,
            "anchorY": ICON_CELL / 2,
            "mask": False,
        }
    },
    swatches={"i0": _png_data_uri(16, (220, 40, 120))},
    supersample=3,
)


@pytest.fixture(scope="session")
def icon_map(runtime, tmp_path_factory):
    """A point layer whose markers came from a sprite sheet.

    Markup tests can only say the attributes were written. Whether deck.gl
    accepts `point-type`, resolves a `data:` sprite sheet and finds the named
    cell is a question only a browser can answer - and getting any of it wrong
    draws an empty map, not a broken one, which is precisely the failure mode
    that goes unnoticed.
    """
    layer = ExportLayer(
        layer_id="markers",
        name="Markers",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=2,
        geojson=GEOJSON,
        icon_atlas=ICON_ATLAS,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(
                fill_color=Color(r=220, g=40, b=120),
                marker_shape="star",
                radius=8.0,
                icon_name="i0",
                icon_size=16.0,
            ),
        ),
        popup=PopupSpec(enabled=False),
    )
    project = ExportProject(
        title="Icon map",
        layers=(layer,),
        extent=Extent(west=-1.0, south=50.9, east=1.1, north=51.9),
    )

    destination = tmp_path_factory.mktemp("icons")
    result = OnlyMapWriter(runtime_provider=runtime).write(project, destination)
    return result.entry_path


# --------------------------------------------------------------------------
# Overlapping popups
# --------------------------------------------------------------------------

# Both layers put a feature on the SAME coordinate. That is the reported case:
# three layers overlapping at one spot left three popups stacked on top of each
# other, so only the top one could be read.
STACKED_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 51.0]},
            "properties": {"name": "Same place"},
        }
    ],
}


@pytest.fixture(scope="session")
def stacked_popups_map(runtime, tmp_path_factory):
    """Two hover-popup layers with a feature at one coordinate.

    Whether a popup ever *closes* cannot be seen in the markup: it depends on
    the runtime dispatching behaviours in document order and on `show-overlay`
    leaving `visible="true"` behind. Only a browser can answer it.
    """
    layers = tuple(
        ExportLayer(
            layer_id=f"stack{i}",
            name=f"Stack {i}",
            geometry_kind=GeometryKind.POINT,
            source_kind=SourceKind.FILE,
            feature_count=1,
            geojson=STACKED_GEOJSON,
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(
                    fill_color=Color(r=200, g=40 + 80 * i, b=60), radius=14.0
                ),
            ),
            popup=PopupSpec(
                enabled=True, fields=(PopupFieldSpec("name"),), on_hover=True
            ),
        )
        for i in range(3)
    )
    project = ExportProject(
        title="Stacked popups",
        layers=layers,
        extent=Extent(west=-0.5, south=50.7, east=0.5, north=51.3),
    )
    destination = tmp_path_factory.mktemp("stacked")
    result = OnlyMapWriter(runtime_provider=runtime).write(project, destination)
    return result.entry_path


# Straddles the antimeridian the way the QGIS Alaska sample does (-179.13 to
# +179.78), but tightly, so the map opens centred on the seam itself. Pure red,
# because the pixel check counts it and nothing else on the map is red.
ANTIMERIDIAN_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [179.6, 51.0]},
            "properties": {"name": "West of the seam"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-179.6, 51.0]},
            "properties": {"name": "East of the seam"},
        },
    ],
}


@pytest.fixture(scope="session")
def antimeridian_map(runtime, tmp_path_factory):
    """A map centred on ±180, where the P0 blank-map bug used to appear.

    deck.gl drew exactly one world copy, -180..+180, while MapLibre repeated
    the world sideways - so data past the seam was simply not rendered and a
    `basemap="none"` export went white. Fixed upstream in OnlyMap 0.5.9 by
    setting `MapView({ repeat: true })` for standalone maps; this is what
    proves it, against the runtime we actually pin.
    """
    layer = ExportLayer(
        layer_id="seam",
        name="Seam",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=2,
        geojson=ANTIMERIDIAN_GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=255, g=0, b=0), radius=14.0),
        ),
        popup=PopupSpec(enabled=False),
    )
    project = ExportProject(
        title="Antimeridian map",
        layers=(layer,),
        extent=Extent(
            west=179.0, south=50.0, east=-179.0, north=52.0, crosses_antimeridian=True
        ),
    )

    destination = tmp_path_factory.mktemp("antimeridian")
    result = OnlyMapWriter(runtime_provider=runtime).write(project, destination)
    return result.entry_path


LINE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[-0.9, 51.4], [1.0, 51.4]],
            },
            "properties": {"name": "Route"},
        }
    ],
}


def _line_map(runtime, tmp_path_factory, name, dash):
    layer = ExportLayer(
        layer_id="route",
        name="Route",
        geometry_kind=GeometryKind.LINE,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson=LINE_GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(
                stroke_color=Color(r=255, g=0, b=0), stroke_width=6.0, stroke_dash=dash
            ),
        ),
        popup=PopupSpec(enabled=False),
    )
    project = ExportProject(
        title=name,
        layers=(layer,),
        extent=Extent(west=-1.0, south=51.3, east=1.1, north=51.5),
    )
    destination = tmp_path_factory.mktemp(name)
    return (
        OnlyMapWriter(runtime_provider=runtime).write(project, destination).entry_path
    )


@pytest.fixture(scope="session")
def solid_line_map(runtime, tmp_path_factory):
    """The control for the dashed map: identical but for the dash pattern."""
    return _line_map(runtime, tmp_path_factory, "solid", ())


@pytest.fixture(scope="session")
def dashed_line_map(runtime, tmp_path_factory):
    """24px on, 12px off at 6px wide - `dash="[4, 2]"`, Qt's own DashLine."""
    return _line_map(runtime, tmp_path_factory, "dashed", (24.0, 12.0))


# Ghost popups
# --------------------------------------------------------------------------

# Two layers whose popups name *different* fields, at different coordinates.
# That is what made the reported bug legible: the popup kept the layer it was
# opened on in its title while redrawing the other layer's object into its rows,
# so every field it named came out blank.
GHOST_RIVER_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 51.0]},
            "properties": {"river_name": "Test River"},
        }
    ],
}

GHOST_AIRPORT_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.4, 51.0]},
            "properties": {"airport_code": "TST"},
        }
    ],
}


@pytest.fixture(scope="session")
def ghost_popup_map(runtime, tmp_path_factory):
    """Two click-popup layers, for the popup-follows-the-pointer report.

    A click popup used to re-anchor and re-interpolate on every *hover* pick,
    because click and hover picks share one `ctx.selection` and `updatePosition`
    only filtered on the `layer` attribute. Runtime 0.6.1's `selection-type`
    is the fix, and this fixture is what proves we are asking for it.
    """
    layers = (
        ExportLayer(
            layer_id="rivers",
            name="Rivers",
            geometry_kind=GeometryKind.POINT,
            source_kind=SourceKind.FILE,
            feature_count=1,
            geojson=GHOST_RIVER_GEOJSON,
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(fill_color=Color(r=40, g=90, b=200), radius=14.0),
            ),
            popup=PopupSpec(
                enabled=True,
                fields=(PopupFieldSpec("river_name"),),
                on_hover=False,
            ),
        ),
        ExportLayer(
            layer_id="airports",
            name="Airports",
            geometry_kind=GeometryKind.POINT,
            source_kind=SourceKind.FILE,
            feature_count=1,
            geojson=GHOST_AIRPORT_GEOJSON,
            renderer=RendererSpec(
                kind=RendererKind.SINGLE,
                symbol=SymbolSpec(fill_color=Color(r=200, g=90, b=40), radius=14.0),
            ),
            popup=PopupSpec(
                enabled=True,
                fields=(PopupFieldSpec("airport_code"),),
                on_hover=False,
            ),
        ),
    )
    project = ExportProject(
        title="Ghost popups",
        layers=layers,
        extent=Extent(west=-0.5, south=50.7, east=0.9, north=51.3),
    )
    destination = tmp_path_factory.mktemp("ghost")
    result = OnlyMapWriter(runtime_provider=runtime).write(project, destination)
    return result.entry_path
