"""The autosave status line, as plain pure text formatting.

Kept apart from `controller.py` and free of Qt so the wording can be unit
tested (`tests/unit`) without a `QApplication`, the same reason
`core/fidelity_summary.py` exists as its own module.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations


def format_elapsed(seconds: float) -> str:
    """A short "n s/min/h ago" fragment, English only - `status_text` wraps it."""
    seconds = max(0.0, seconds)
    if seconds < 1:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} s ago"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} min ago"
    hours = minutes / 60
    return f"{int(hours)} h ago"


def format_bytes(count: int) -> str:
    """`2.1 MB`, `340 KB`, `12 B` - one significant decimal past kilobytes."""
    if count < 1024:
        return f"{count} B"
    kb = count / 1024
    if kb < 1024:
        return f"{kb:.0f} KB"
    mb = kb / 1024
    return f"{mb:.1f} MB"


def status_text(
    *,
    saved_at: float | None,
    saved_bytes: int | None,
    error: str,
    now: float,
    translate,
) -> str:
    """The Cartograph tab's status line.

    `translate` is `ui.i18n.tr`, passed in rather than imported so this stays
    testable without a translator installed - the same reason `status.py`
    exists apart from Qt at all.
    """
    if error:
        return error
    if saved_at is None:
        return translate("Not saved yet")
    elapsed = format_elapsed(now - saved_at)
    size = format_bytes(saved_bytes or 0)
    return translate("Saved {elapsed} · {size}").format(elapsed=elapsed, size=size)
