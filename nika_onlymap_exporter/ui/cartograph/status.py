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
    # No seconds: the line is redrawn every 30 s, so a count of seconds would
    # always be out of date by the time it was read.
    if seconds < 60:
        return "just now"
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
    has_path: bool = True,
    phase: str = "",
) -> str:
    """The Cartograph tab's status line.

    `translate` is `ui.i18n.tr`, passed in rather than imported so this stays
    testable without a translator installed - the same reason `status.py`
    exists apart from Qt at all.

    `phase` is "pending" (an edit waits out the autosave debounce) or
    "saving" (the cartograph file is being rebuilt); either outranks a
    "Saved" or error line left over from an earlier build, since it no longer
    describes the file the edit will produce. Before the first save "pending"
    stays "Not saved yet", which already says so. The words name the cartograph,
    never the QGIS project, which this plugin does not save.
    """
    if phase == "saving":
        return translate("Saving the cartograph...")
    if phase == "pending" and saved_at is not None:
        return translate("Changes pending")
    if error:
        return error
    if saved_at is None:
        if not has_path:
            return translate("Not saved yet - choose where to save it")
        return translate("Not saved yet")
    elapsed = format_elapsed(now - saved_at)
    size = format_bytes(saved_bytes or 0)
    return translate("Saved {elapsed} · {size}").format(elapsed=elapsed, size=size)


#: ISO 216 A sizes in mm, portrait (width, height), for naming a page.
_ISO_A_SIZES = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
}


def page_label(spec: object) -> str | None:
    """ "A3 landscape", or "500 x 300 mm landscape" for a custom page.

    The hand-off prompt quotes it so a print critique knows the sheet.
    """
    width = getattr(spec, "width", None)
    height = getattr(spec, "height", None)
    size = getattr(spec, "size", None)
    orientation = getattr(spec, "orientation", None) or ""
    if size:
        return f"{size} {orientation}".strip()
    if not width or not height:
        return None
    short, long = sorted((float(width), float(height)))
    for name, (w, h) in _ISO_A_SIZES.items():
        if abs(short - w) < 1.0 and abs(long - h) < 1.0:
            return f"{name} {orientation}".strip()
    return f"{width:g} x {height:g} mm {orientation}".strip()
