"""Fixtures for the print-settings tests: a scripted CMYK ICC profile and a
small PDF reader.

A CMYK output profile is needed to prove the PDF/X-4 output intent, and none
ships with Qt or QGIS; committing a vendor profile (FOGRA39 and friends) is
both a licence question and a binary in the repository. So the profile is
built here, the same way the survey layout is: by script. It is a minimal ICC
v2.1 output ("prtr") profile, CMYK to Lab, with 2-point `mft1` lookup tables
in both directions -- enough for `QColorSpace.fromIccProfile` to accept it as
a valid CMYK colour space, which is all the export path needs. Its colours
are deliberately crude; nothing here judges colour accuracy.

`pypdf` is not a dependency of this project, so the reader below handles just
what these tests check: page boxes, and text searched in the raw file and in
every Flate-compressed stream.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools
import re
import struct
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path

PT_PER_MM = 72 / 25.4
TEST_PROFILE_NAME = "NIKA test CMYK"


def _s15fixed16(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _mft1(
    in_channels: int,
    out_channels: int,
    grid: int,
    clut: Callable[[Sequence[float]], Sequence[int]],
) -> bytes:
    body = b"mft1" + b"\0" * 4 + bytes([in_channels, out_channels, grid, 0])
    body += b"".join(_s15fixed16(v) for v in (1, 0, 0, 0, 1, 0, 0, 0, 1))
    identity = bytes(range(256))
    body += identity * in_channels
    for index in itertools.product(range(grid), repeat=in_channels):
        body += bytes(clut([i / (grid - 1) for i in index]))
    body += identity * out_channels
    return body


def _cmyk_to_lab(cmyk: Sequence[float]) -> list[int]:
    c, m, y, k = cmyk
    lightness = (1 - k) * (1 - (c + m + y) / 3 * 0.8)
    return [
        round(lightness * 255),
        128 + round((m - c) * 40),
        128 + round((y - m) * 40),
    ]


def _lab_to_cmyk(lab: Sequence[float]) -> list[int]:
    return [0, 0, 0, round((1 - lab[0]) * 255)]


def cmyk_icc_profile(name: str = TEST_PROFILE_NAME) -> bytes:
    """A minimal valid CMYK output ICC profile named `name`."""
    text = name.encode("ascii") + b"\0"
    desc = b"desc" + b"\0" * 4 + struct.pack(">I", len(text)) + text + b"\0" * (8 + 70)
    white = (
        b"XYZ " + b"\0" * 4 + b"".join(_s15fixed16(v) for v in (0.9642, 1.0, 0.8249))
    )
    tags = [
        (b"desc", desc),
        (b"wtpt", white),
        (b"A2B0", _mft1(4, 3, 2, _cmyk_to_lab)),
        (b"B2A0", _mft1(3, 4, 2, _lab_to_cmyk)),
        (b"cprt", b"text\0\0\0\0PD\0"),
    ]
    table_size = 4 + 12 * len(tags)
    entries = b""
    data = b""
    for signature, blob in tags:
        while (128 + table_size + len(data)) % 4:
            data += b"\0"
        entries += signature + struct.pack(
            ">II", 128 + table_size + len(data), len(blob)
        )
        data += blob
    while len(data) % 4:
        data += b"\0"
    size = 128 + table_size + len(data)
    header = (
        struct.pack(">I", size)
        + b"none"
        + struct.pack(">I", 0x02100000)
        + b"prtrCMYKLab "
        + struct.pack(">6H", 2026, 1, 1, 0, 0, 0)
        + b"acsp"
        + b"\0" * 28
        + b"".join(_s15fixed16(v) for v in (0.9642, 1.0, 0.8249))
        + b"\0" * 48
    )
    assert len(header) == 128
    return header + struct.pack(">I", len(tags)) + entries + data


def write_cmyk_icc(path: Path, name: str = TEST_PROFILE_NAME) -> Path:
    path.write_bytes(cmyk_icc_profile(name))
    return path


# ---- PDF reading -----------------------------------------------------------


def media_boxes_mm(pdf: bytes) -> list[tuple[float, float]]:
    """Every page's MediaBox as (width, height) in millimetres."""
    boxes = []
    for match in re.finditer(rb"/MediaBox\s*\[([^\]]*)\]", pdf):
        x0, y0, x1, y1 = (float(v) for v in match.group(1).split())
        boxes.append(((x1 - x0) / PT_PER_MM, (y1 - y0) / PT_PER_MM))
    return boxes


def inflated_streams(pdf: bytes) -> list[bytes]:
    """Every stream that inflates as Flate; others are skipped."""
    streams = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            streams.append(zlib.decompress(match.group(1)))
        except zlib.error:
            continue
    return streams


def count_anywhere(pdf: bytes, needle: bytes) -> int:
    """Occurrences of `needle` in the raw file plus every inflated stream."""
    return pdf.count(needle) + sum(s.count(needle) for s in inflated_streams(pdf))
