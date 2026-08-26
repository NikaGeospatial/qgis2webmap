#!/usr/bin/env python3
"""Render the same maps on two OnlyMap runtimes and compare the pixels.

    python scripts/visual_diff_runtimes.py OLD_RUNTIME_DIR NEW_RUNTIME_DIR

Why this exists. The browser tier asserts specific properties -- this line is
dashed, that polygon is raised -- so it only ever catches a regression somebody
already thought to assert. `dash` was silently dropped for **twelve releases**
(0.6.2 through 0.6.13) while staying documented, byte-identical, in the runtime's
own schema. Nothing static caught it, and it was found only because one test
counts pixels on a rendered line.

This is that test generalised: render a scene, render it again on the other
build, and diff the images. It cannot tell you *what* changed, only that
something did -- which is exactly the signal a pin bump needs, because the
expensive failures are the ones nobody predicted.

A non-zero diff is not automatically a regression. Antialiasing on a rotated
label, a deliberate upstream fix, a changed default -- all show up here. The
output is a prompt to look, not a verdict.

Requires Playwright (`pip install playwright && playwright install chromium`).
A runtime directory holds `onlymap.standalone.js`, `onlymapjs.css`,
`onlymapjs.html-data.json` and `LICENSE.md`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import base64
import struct
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nika_onlymap_exporter.core.export_ir import (  # noqa: E402
    Color,
    ElevationSpec,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    IconAtlasSpec,
    LabelingSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.packaging.runtime_manager import LocalRuntime  # noqa: E402
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter  # noqa: E402

VIEWPORT = {"width": 900, "height": 600}

# Long enough for tiles, fonts and the first settled frame. The scenes are
# deliberately offline - no basemap, no terrain - so nothing here waits on a
# network round trip, and a fixed settle beats polling for a readiness signal
# whose own semantics changed across these releases (0.6.18 reworked
# `whenSettled`). Using the runtime's own readiness API would make the harness
# agree with whichever build it is testing, which is the one thing it must not
# do.
SETTLE_MS = 3500


# ---------------------------------------------------------------------------
# Scenes: one per rendering path the plugin actually emits
# ---------------------------------------------------------------------------

POINTS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-0.4, 51.6]},
            "properties": {"name": "Alpha", "kind": "a", "n": 3},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.3, 51.35]},
            "properties": {"name": "Beta", "kind": "b", "n": 17},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.8, 51.65]},
            "properties": {"name": "Gamma", "kind": "a", "n": 42},
        },
    ],
}

LINE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[-0.9, 51.4], [0.1, 51.55], [1.0, 51.4]],
            },
            "properties": {"name": "Route"},
        }
    ],
}

POLYGON = {
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

WIDE = Extent(west=-1.0, south=51.2, east=1.1, north=51.8)
CLOSE = Extent(west=-0.1, south=50.9, east=0.2, north=51.2)


def _solid_png(side: int, rgb: tuple[int, int, int]) -> bytes:
    """A single-colour PNG, built by hand so the harness needs no Pillow."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes(rgb) * side for _ in range(side)
    )  # filter byte per row
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


ICON_CELL = 48
ICON_ATLAS = IconAtlasSpec(
    data_uri="data:image/png;base64,"
    + base64.b64encode(_solid_png(ICON_CELL, (220, 40, 120))).decode(),
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
    swatches={
        "i0": "data:image/png;base64,"
        + base64.b64encode(_solid_png(16, (220, 40, 120))).decode()
    },
    supersample=3,
)


def _points_layer(**kwargs) -> ExportLayer:
    base = dict(
        layer_id="points",
        name="Points",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=3,
        geojson=POINTS,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=9.0),
        ),
        popup=PopupSpec(enabled=False),
    )
    base.update(kwargs)
    return ExportLayer(**base)


def _line_layer(dash: tuple[float, ...]) -> ExportLayer:
    return ExportLayer(
        layer_id="route",
        name="Route",
        geometry_kind=GeometryKind.LINE,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson=LINE,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(
                stroke_color=Color(r=220, g=20, b=20),
                stroke_width=7.0,
                stroke_dash=dash,
            ),
        ),
        popup=PopupSpec(enabled=False),
    )


def _polygon_layer(elevation: ElevationSpec) -> ExportLayer:
    return ExportLayer(
        layer_id="blocks",
        name="Blocks",
        geometry_kind=GeometryKind.POLYGON,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson=POLYGON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(
                fill_color=Color(r=200, g=80, b=40),
                stroke_color=Color(r=20, g=20, b=20),
                stroke_width=2.0,
            ),
        ),
        elevation=elevation,
        popup=PopupSpec(enabled=False),
    )


def scenes() -> dict[str, ExportProject]:
    """Every rendering path the plugin emits, one scene each.

    Keyed by the attribute family each one is here to protect, so a diff names
    the suspect rather than just a number.
    """
    return {
        # dash / dash-justified / line-width-*: the family that regressed.
        "line-solid": ExportProject(
            title="Solid", layers=(_line_layer(()),), extent=WIDE
        ),
        "line-dashed": ExportProject(
            title="Dashed", layers=(_line_layer((24.0, 12.0)),), extent=WIDE
        ),
        # get-fill-color / point-radius / size-units
        "points-plain": ExportProject(
            title="Points", layers=(_points_layer(),), extent=WIDE
        ),
        # get-text / font-* / background / outline: known-unverified surface,
        # every export up to 0.1.3 shipped these unstyled.
        "points-labelled": ExportProject(
            title="Labelled",
            layers=(
                _points_layer(
                    labeling=LabelingSpec(
                        enabled=True,
                        field_name="name",
                        font_size=15.0,
                        bold=True,
                        halo_width=2.0,
                        halo_color=Color(r=255, g=255, b=255),
                        color=Color(r=10, g=10, b=10),
                        background_color=Color(r=250, g=250, b=210),
                        background_padding=(4.0, 2.0),
                    )
                ),
            ),
            extent=WIDE,
        ),
        # point-type=icon / icon-atlas / icon-mapping / get-icon-size
        "points-icons": ExportProject(
            title="Icons",
            layers=(
                _points_layer(
                    icon_atlas=ICON_ATLAS,
                    renderer=RendererSpec(
                        kind=RendererKind.SINGLE,
                        symbol=SymbolSpec(
                            fill_color=Color(r=220, g=40, b=120),
                            marker_shape="star",
                            radius=8.0,
                            icon_name="i0",
                            icon_size=18.0,
                        ),
                    ),
                ),
            ),
            extent=WIDE,
        ),
        # get-fill-color / get-line-color / get-line-width on a filled shape
        "polygon-flat": ExportProject(
            title="Polygon", layers=(_polygon_layer(ElevationSpec()),), extent=CLOSE
        ),
        # extruded / get-elevation / wireframe / pitch
        "polygon-extruded": ExportProject(
            title="Extruded",
            layers=(
                _polygon_layer(
                    ElevationSpec(
                        extruded=True,
                        height_field="height",
                        wireframe=True,
                        source="3d-renderer",
                    )
                ),
            ),
            extent=CLOSE,
        ),
        # The authored chrome plus every runtime widget, in one frame.
        "chrome-widgets": ExportProject(
            title="Chrome check",
            abstract="Caption, legend, switcher, zoom controls and scale bar.",
            layers=(_points_layer(attribution="© Fixture Survey"), _line_layer(())),
            extent=WIDE,
            settings=ExportSettings(
                show_legend=True,
                show_layer_switcher=True,
                show_zoom_controls=True,
                show_scale_bar=True,
                show_title=True,
                show_abstract=True,
            ),
        ),
        # opacity: a separate compositing path from plain fill.
        "points-translucent": ExportProject(
            title="Translucent",
            layers=(_points_layer(opacity=0.35),),
            extent=WIDE,
        ),
    }


# ---------------------------------------------------------------------------
# Rendering and comparison
# ---------------------------------------------------------------------------


def render(runtime_dir: Path, out_dir: Path, page) -> dict[str, Path]:
    """Write every scene against one runtime and screenshot each."""
    provider = LocalRuntime(runtime_dir)
    writer = OnlyMapWriter(runtime_provider=provider)
    shots: dict[str, Path] = {}
    for name, project in scenes().items():
        staging = out_dir / name
        staging.mkdir(parents=True, exist_ok=True)
        result = writer.write(project, staging, compress=False)
        page.goto(result.entry_path.as_uri())
        page.wait_for_timeout(SETTLE_MS)
        shot = out_dir / f"{name}.png"
        page.screenshot(path=str(shot))
        shots[name] = shot
    return shots


def decode_png(path: Path) -> tuple[int, int, bytes]:
    """Minimal PNG reader: 8-bit RGB/RGBA, no interlace. Enough for Chromium.

    Hand-rolled rather than adding Pillow: this script has to be runnable in
    the same bare environment as the browser tier, and a pin bump blocked on a
    missing image library is a bump that quietly does not happen.
    """
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, width, height, channels = 8, bytearray(), 0, 0, 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
            if depth != 8 or colour not in (2, 6) or body[12] != 0:
                raise ValueError(f"{path}: unsupported PNG (depth/colour/interlace)")
            channels = 3 if colour == 2 else 4
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    src = 0
    for row in range(height):
        filt = raw[src]
        src += 1
        line = bytearray(raw[src : src + stride])
        src += stride
        # PNG filters, per the spec. Reconstruction is sequential by necessity.
        if filt == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif filt != 0:
            raise ValueError(f"{path}: unknown PNG filter {filt}")
        out[row * stride : (row + 1) * stride] = line
        prev = line
    return width, height, bytes(out)


def compare(old: Path, new: Path) -> tuple[int, int, int]:
    """Return (differing pixels, total pixels, worst channel delta)."""
    w1, h1, a = decode_png(old)
    w2, h2, b = decode_png(new)
    if (w1, h1) != (w2, h2):
        raise ValueError(f"size mismatch: {w1}x{h1} vs {w2}x{h2}")
    channels = len(a) // (w1 * h1)
    differing = worst = 0
    for i in range(0, len(a), channels):
        px_a = a[i : i + channels]
        px_b = b[i : i + channels]
        if px_a != px_b:
            differing += 1
            worst = max(worst, max(abs(x - y) for x, y in zip(px_a, px_b)))
    return differing, w1 * h1, worst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="baseline runtime directory")
    parser.add_argument("new", type=Path, help="candidate runtime directory")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/onlymap-visual-diff"),
        help="where screenshots are written (default: /tmp/onlymap-visual-diff)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.001,
        help="fraction of differing pixels tolerated per scene (default 0.1%%)",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is required: pip install playwright", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        print(f"rendering {len(scenes())} scenes on {args.old.name} ...")
        old_shots = render(args.old, args.out / "old", page)
        print(f"rendering {len(scenes())} scenes on {args.new.name} ...")
        new_shots = render(args.new, args.out / "new", page)
        browser.close()

    print()
    worst_scene, failures = 0.0, []
    for name in scenes():
        differing, total, delta = compare(old_shots[name], new_shots[name])
        fraction = differing / total
        worst_scene = max(worst_scene, fraction)
        flag = "DIFF" if fraction > args.threshold else "ok  "
        print(
            f"  {flag} {name:20} {differing:7d}/{total} px "
            f"({fraction:7.4%})  max channel delta {delta}"
        )
        if fraction > args.threshold:
            failures.append(name)

    print()
    if failures:
        print(f"{len(failures)} scene(s) differ beyond {args.threshold:.2%}:")
        for name in failures:
            print(f"  {name}: {args.out}/old/{name}.png vs {args.out}/new/{name}.png")
        print("\nA diff is a prompt to look, not a verdict - an upstream fix and a")
        print("regression look identical from here. Open both images.")
        return 1

    print(f"every scene within {args.threshold:.2%} (worst {worst_scene:.4%}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
