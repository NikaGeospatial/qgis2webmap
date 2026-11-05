"""Vector tiles, PMTiles archives and PNG tiles written with the standard library.

The browser tier has no GDAL, so the tiles a test serves are made here: `mvt`
encodes one Mapbox Vector Tile (spec 2.1) of polygons and lines given in
longitude/latitude, `png` one solid-colour image tile, and `pmtiles` packs
tiles into a PMTiles v3 archive (a single root directory, gzip or no
compression) the way `pmtiles convert` lays one out. `tiles_covering` lists the
tiles of a zoom that a box touches, so a test can build exactly the pyramid its
map asks for.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import gzip
import math
import struct
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

EXTENT = 4096

# PMTiles v3 codes (spec v3, "Compression" and "TileType").
COMPRESSION_NONE = 1
COMPRESSION_GZIP = 2
COMPRESSION_BROTLI = 3
COMPRESSION_ZSTD = 4
TILE_MVT = 1
TILE_PNG = 2


@dataclass(frozen=True)
class Feature:
    """One feature: `kind` "polygon" (one ring) or "line", in lon/lat."""

    kind: str
    coordinates: Sequence[tuple[float, float]]
    properties: Mapping[str, str | int | float] = field(default_factory=dict)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _key(number: int, wire: int) -> bytes:
    return _varint((number << 3) | wire)


def _length_delimited(number: int, payload: bytes) -> bytes:
    return _key(number, 2) + _varint(len(payload)) + payload


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 31)


def _tile_xy(lon: float, lat: float, z: int, x: int, y: int) -> tuple[int, int]:
    """A lon/lat position in tile `z/x/y`'s own 0..EXTENT coordinates."""
    scale = 2**z
    world_x = (lon + 180.0) / 360.0 * scale
    sin = math.sin(math.radians(lat))
    world_y = (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * scale
    return round((world_x - x) * EXTENT), round((world_y - y) * EXTENT)


def _geometry(feature: Feature, z: int, x: int, y: int) -> list[int]:
    points = [_tile_xy(lon, lat, z, x, y) for lon, lat in feature.coordinates]
    if feature.kind == "polygon":
        if points[0] == points[-1]:
            points = points[:-1]
        # Exterior rings wind clockwise in tile space (y down): positive area.
        area = sum(
            ax * by - bx * ay
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1], strict=True)
        )
        if area < 0:
            points.reverse()
    commands = [(1 & 0x7) | (1 << 3)]
    cursor = (0, 0)
    for index, (px, py) in enumerate(points):
        if index == 1:
            commands.append((2 & 0x7) | ((len(points) - 1) << 3))
        commands += [_zigzag(px - cursor[0]), _zigzag(py - cursor[1])]
        cursor = (px, py)
    if feature.kind == "polygon":
        commands.append((7 & 0x7) | (1 << 3))
    return commands


def _value(value: str | int | float) -> bytes:
    if isinstance(value, str):
        return _length_delimited(1, value.encode("utf-8"))
    if isinstance(value, bool):
        return _key(7, 0) + _varint(int(value))
    if isinstance(value, int):
        return _key(6, 0) + _varint(_zigzag(value) if value < 0 else value << 1)
    return _key(3, 1) + struct.pack("<d", float(value))


def mvt(layers: Mapping[str, Sequence[Feature]], z: int, x: int, y: int) -> bytes:
    """One vector tile holding `layers`, each a list of features in lon/lat."""
    tile = b""
    for name, features in layers.items():
        keys: list[str] = []
        values: list[str | int | float] = []
        encoded = b""
        for number, feature in enumerate(features, start=1):
            tags: list[int] = []
            for key, value in feature.properties.items():
                if key not in keys:
                    keys.append(key)
                if value not in values:
                    values.append(value)
                tags += [keys.index(key), values.index(value)]
            geometry = _geometry(feature, z, x, y)
            body = _key(1, 0) + _varint(number)
            body += _length_delimited(2, b"".join(_varint(tag) for tag in tags))
            body += _key(3, 0) + _varint(3 if feature.kind == "polygon" else 2)
            body += _length_delimited(4, b"".join(_varint(c) for c in geometry))
            encoded += _length_delimited(2, body)
        layer = _key(15, 0) + _varint(2) + _length_delimited(1, name.encode("utf-8"))
        layer += encoded
        layer += b"".join(_length_delimited(3, key.encode("utf-8")) for key in keys)
        layer += b"".join(_length_delimited(4, _value(value)) for value in values)
        layer += _key(5, 0) + _varint(EXTENT)
        tile += _length_delimited(3, layer)
    return tile


def png(color: tuple[int, int, int], size: int = 256) -> bytes:
    """A `size` x `size` opaque PNG of one colour."""
    row = b"\0" + bytes(color) * size
    raw = zlib.compress(row * size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


def tiles_covering(
    west: float, south: float, east: float, north: float, z: int
) -> list[tuple[int, int, int]]:
    """Every tile `(z, x, y)` of zoom `z` that the box touches."""
    scale = 2**z

    def column(lon: float) -> int:
        return min(scale - 1, max(0, int((lon + 180.0) / 360.0 * scale)))

    def row(lat: float) -> int:
        sin = math.sin(math.radians(lat))
        world = (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * scale
        return min(scale - 1, max(0, int(world)))

    return [
        (z, x, y)
        for x in range(column(west), column(east) + 1)
        for y in range(row(north), row(south) + 1)
    ]


def _hilbert(z: int, x: int, y: int) -> int:
    """Position of (x, y) along zoom `z`'s Hilbert curve."""
    n = 2**z
    distance = 0
    s = n // 2
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        distance += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s //= 2
    return distance


def tile_id(z: int, x: int, y: int) -> int:
    """The PMTiles tile id of z/x/y: every tile of lower zooms, then the curve."""
    return sum(4**level for level in range(z)) + _hilbert(z, x, y)


def pmtiles(
    tiles: Mapping[tuple[int, int, int], bytes],
    tile_type: int,
    bounds: tuple[float, float, float, float],
    *,
    compression: int = COMPRESSION_GZIP,
    tile_compression: int | None = None,
    metadata: str = "{}",
) -> bytes:
    """A PMTiles v3 archive of `tiles` ((z, x, y) -> bytes, uncompressed).

    `compression` is the tiles' and the directory's: gzip is what GDAL and
    `pmtiles convert` write for vector tiles; image tiles are stored as they
    are (`COMPRESSION_NONE` for the tiles is implied by an image type).
    `tile_compression` declares the tiles' alone. Brotli and zstd are only
    declared, never applied - an archive a browser cannot read.
    """

    def squeeze(data: bytes, code: int) -> bytes:
        return gzip.compress(data, mtime=0) if code == COMPRESSION_GZIP else data

    if tile_compression is None:
        tile_compression = COMPRESSION_NONE if tile_type != TILE_MVT else compression
    entries = sorted((tile_id(*index), data) for index, data in tiles.items())
    blob = b""
    rows: list[tuple[int, int, int]] = []
    for identifier, data in entries:
        stored = squeeze(data, tile_compression)
        rows.append((identifier, len(blob), len(stored)))
        blob += stored
    directory = _varint(len(rows))
    previous = 0
    for identifier, _offset, _length in rows:
        directory += _varint(identifier - previous)
        previous = identifier
    directory += b"".join(_varint(1) for _ in rows)
    directory += b"".join(_varint(length) for _i, _o, length in rows)
    for index, (_identifier, offset, _length) in enumerate(rows):
        follows = index > 0 and offset == rows[index - 1][1] + rows[index - 1][2]
        directory += _varint(0 if follows else offset + 1)
    internal = COMPRESSION_GZIP if compression == COMPRESSION_GZIP else compression
    root = squeeze(directory, internal)
    meta = squeeze(metadata.encode("utf-8"), internal)
    zooms = [z for z, _x, _y in tiles]
    west, south, east, north = bounds
    root_at = 127
    meta_at = root_at + len(root)
    data_at = meta_at + len(meta)
    header = b"PMTiles" + bytes([3])
    header += struct.pack(
        "<QQQQQQQQQQQ",
        root_at,
        len(root),
        meta_at,
        len(meta),
        data_at + len(blob),
        0,
        data_at,
        len(blob),
        len(rows),
        len(rows),
        len(rows),
    )
    header += bytes([1, internal, tile_compression, tile_type, min(zooms), max(zooms)])
    header += struct.pack(
        "<iiii",
        round(west * 1e7),
        round(south * 1e7),
        round(east * 1e7),
        round(north * 1e7),
    )
    header += bytes([min(zooms)])
    header += struct.pack(
        "<ii", round((west + east) / 2 * 1e7), round((south + north) / 2 * 1e7)
    )
    assert len(header) == 127
    return header + root + meta + blob


def pyramid(
    build: Iterable[tuple[int, int, int]], tile: object
) -> dict[tuple[int, int, int], bytes]:
    """`{(z, x, y): tile(z, x, y)}` for every index in `build`."""
    assert callable(tile)
    return {index: tile(*index) for index in build}
