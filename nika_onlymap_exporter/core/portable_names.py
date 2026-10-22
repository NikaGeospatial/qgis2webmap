"""Names that work as a file or folder name on Windows, macOS and Linux.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

#: Device names Windows will not create as a file or folder, with or without
#: an extension: `CON`, `con.txt`, `LPT1.html`.
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)


def avoid_windows_reserved(name: str) -> str:
    """`name` unchanged, unless Windows reserves it: then `-map` is added, so
    `CON` becomes `CON-map` and `con.txt` becomes `con.txt-map`."""
    if _WINDOWS_RESERVED.match(name):
        return f"{name}-map"
    return name
