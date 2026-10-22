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
from collections.abc import Iterator
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


@contextlib.contextmanager
def staged_beside(path: Path) -> Iterator[Path]:
    """Yield a temp path beside `path`; on a clean exit `os.replace` it onto
    `path`, on any failure delete it. Whatever was at `path` before survives a
    failed or cancelled write untouched. The caller writes the whole file to
    the yielded path (it exists, empty, so a writer may open it in any mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        yield tmp
        # mkstemp makes the file owner-only; give it the mode a plain copy
        # would have had, or the one the file being replaced already has.
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
