"""Write a text file so that a crash mid-write never tears it.

The cartograph is a document the user keeps (issue #3 S1), rewritten on
every change. A temp file in the same directory, then `os.replace`, means a
reader - or QGIS being killed - only ever sees the old file or the new one.
Same directory, because a rename across filesystems is not atomic.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, content: str) -> None:
    """Replace `path` with `content` (UTF-8). Raises `OSError` on failure,
    leaving whatever was at `path` before untouched and no temp file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise
