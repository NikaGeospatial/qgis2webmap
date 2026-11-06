"""ISO 8601 dates and times, read the same on every Python QGIS ships.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from datetime import datetime

# A time's fraction of a second, and its zone written without a colon.
_FRACTION = re.compile(r"(T\d{2}:\d{2}:\d{2})\.(\d+)", re.IGNORECASE)
_BARE_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")


def from_iso(text: str) -> datetime | None:
    """`text` as `datetime.fromisoformat` reads it from Python 3.11 on, or
    `None` when it is not a date or a date and time.

    QGIS 3.44 on Ubuntu 22.04 runs Python 3.10, whose `fromisoformat` takes
    only what `isoformat` writes: no `Z` for UTC, a zone only as `+hh:mm`,
    and a fraction of a second only of 3 or 6 digits. Services and QGIS
    write all three otherwise (`2026-10-20T00:00:00Z`, `+0100`, `.5`), so
    they are put in the form every version reads first.
    """
    text = text.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    text = _BARE_OFFSET.sub(r"\1:\2", text) if "T" in text.upper() else text
    text = _FRACTION.sub(
        lambda match: f"{match.group(1)}.{match.group(2)[:6].ljust(6, '0')}", text
    )
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
