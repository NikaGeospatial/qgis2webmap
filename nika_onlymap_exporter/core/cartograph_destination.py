"""Where a cartograph may save itself without asking.

A cartograph rewrites its file on every change, unasked. The destination is
text in the project (`DialogState.cartograph_files`), so a shared or stale
`.qgz` can name any file the user can write. Replacing it unasked destroys
it, so before an autosave writes, the destination is checked here:

- it must be an `.html` file in a folder that already exists (the default
  Cartographs folder is the one folder made on demand), not a directory and
  not a symbolic link (replacing a link swaps the link itself, not what it
  points at - never what the user meant);
- an existing file is replaced only when it is provably this cartograph's own
  earlier output, or the user chose it in the Save dialog (which asks before
  replacing).

Own output is a `<meta name="qgis2webmap-layout">` the plugin stamps into the
head, holding the layout's persistent id (`core/layout_identity.py`, stored in
the layout and so in the `.qgz`). A file saved by an earlier build has no
stamp; it counts as ours when it is a `cartograph/2` page whose
`cartograph-id` is the one this layout would write now (its sanitised name).
The first save over it adds the stamp, so that guess is needed once.

Anything else is never written: `resolve_destination` returns a new name
beside it (`name (2).html`) and why, and the caller says so on screen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .cartograph_export import _cartograph_id
from .layout_identity import LayoutKeys

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsPrintLayout, QgsProject

#: The head element that marks a page as one layout's cartograph.
OWNER_META_NAME = "qgis2webmap-layout"

#: How much of a page is searched for the stamp (it is the first head element).
_STAMP_WINDOW = 4096
#: Legacy pages are searched in pieces; the sheet follows the inlined runtime.
_CHUNK = 1024 * 1024
#: A "cartograph" bigger than this is not read looking for a marker.
_MAX_SCAN_BYTES = 1024 * 1024 * 1024
_MAX_CANDIDATES = 1000
_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


class Reason(Enum):
    """Why a destination was not used as it stood."""

    NOT_HTML = "not-html"
    IS_DIRECTORY = "directory"
    IS_SYMLINK = "symlink"
    NO_FOLDER = "no-folder"
    NOT_OURS = "not-ours"


@dataclass(frozen=True)
class Resolution:
    """`path` is where to write; `reason` is None when it is the destination
    asked for, else why a different one was chosen."""

    path: Path
    reason: Reason | None = None
    asked: Path | None = None


def stamp_owner(html: str, layout_key: str) -> str:
    """`html` with the ownership stamp in its head (replacing an older one)."""
    html = re.sub(
        r'<meta name="' + OWNER_META_NAME + r'" content="[^"]*">\n?', "", html
    )
    meta = f'<meta name="{OWNER_META_NAME}" content="{_escape(layout_key)}">\n'
    for anchor in (
        '<meta charset="utf-8">\n',
        "<head>\n",
        "<head>",
        "<html>\n",
        "<html>",
    ):
        index = html.find(anchor)
        if index != -1:
            end = index + len(anchor)
            return html[:end] + meta + html[end:]
    return meta + html


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def is_own_output(path: Path, layout_key: str, legacy_id: str) -> bool:
    """Whether the existing file at `path` was written by this layout's
    cartograph: stamped with `layout_key`, or an unstamped page of an earlier
    build whose sheet carries `legacy_id`."""
    try:
        size = path.stat().st_size
        if size > _MAX_SCAN_BYTES:
            return False
        with path.open("rb") as handle:
            head = handle.read(_STAMP_WINDOW).decode("utf-8", errors="replace")
            stamp = f'<meta name="{OWNER_META_NAME}" content="'
            start = head.find(stamp)
            if start != -1:
                end = head.find('">', start + len(stamp))
                found = head[start + len(stamp) : end] if end != -1 else ""
                return found == _escape(layout_key)
            if not head.lstrip().lower().startswith("<!doctype html"):
                return False
            return _has_legacy_sheet(handle, head, legacy_id)
    except OSError:
        return False


def _has_legacy_sheet(handle: object, first: str, legacy_id: str) -> bool:
    """Whether a page without a stamp has this layout's `<om-cartograph>`
    sheet. Read in pieces, overlapping so the tag is never split unseen."""
    if not _ID_SAFE.match(legacy_id):
        return False
    pattern = re.compile(
        r'<om-cartograph cartograph-id="' + re.escape(legacy_id) + r'" '
        r'format="cartograph/2"'
    )
    tail = first
    if pattern.search(tail):
        return True
    overlap = 200
    while True:
        chunk = handle.read(_CHUNK)  # type: ignore[attr-defined]
        if not chunk:
            return False
        tail = tail[-overlap:] + chunk.decode("utf-8", errors="replace")
        if pattern.search(tail):
            return True


def _may_replace(
    path: Path,
    layout_key: str,
    legacy_id: str,
    approved: Collection[str],
) -> bool:
    return str(path) in approved or is_own_output(path, layout_key, legacy_id)


def _problem(
    path: Path,
    layout_key: str,
    legacy_id: str,
    approved: Collection[str],
    folder_may_be_made: bool,
) -> Reason | None:
    """What stops `path` being written, or None when it may be."""
    if not path.parent.is_dir() and not folder_may_be_made:
        return Reason.NO_FOLDER
    if path.suffix.lower() != ".html":
        return Reason.NOT_HTML
    if path.is_symlink():
        return Reason.IS_SYMLINK
    if path.is_dir():
        return Reason.IS_DIRECTORY
    if path.exists() and not _may_replace(path, layout_key, legacy_id, approved):
        return Reason.NOT_OURS
    return None


def resolve_destination(
    path: Path,
    layout_key: str,
    legacy_id: str,
    default_path: Path,
    approved: Collection[str] = (),
) -> Resolution:
    """The file an autosave may write for the destination `path`.

    `path` itself when it is safe. Otherwise the first of `name.html`,
    `name (2).html`... that is absent or this cartograph's own (so a redirect
    is reused, not repeated), beside `path` - or in `default_path`'s folder
    when `path`'s folder is missing - with the reason. `approved` holds paths
    the user chose in the Save dialog, which asked before replacing.
    """
    reason = _problem(
        path,
        layout_key,
        legacy_id,
        approved,
        folder_may_be_made=_same_place(path.parent, default_path.parent),
    )
    if reason is None:
        return Resolution(path)
    if reason is Reason.NO_FOLDER:
        base = default_path
    elif path.suffix.lower() == ".html":
        base = path
    else:
        base = path.with_suffix(".html")
    for number in range(1, _MAX_CANDIDATES):
        candidate = (
            base
            if number == 1
            else base.with_name(f"{base.stem} ({number}){base.suffix}")
        )
        if (
            _problem(
                candidate,
                layout_key,
                legacy_id,
                (),
                folder_may_be_made=_same_place(candidate.parent, default_path.parent),
            )
            is None
        ):
            return Resolution(candidate, reason, path)
    raise OSError(f"No free file name beside {base}")


def _same_place(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return first == second


def layout_identity(layout: QgsPrintLayout, project: QgsProject) -> tuple[str, str]:
    """(persistent id, legacy sheet id) of `layout`: what its own output
    carries now, and what an earlier build's output carries."""
    return LayoutKeys(project).key_for(layout.name()), _cartograph_id(layout)


def destination_for_layout(
    path: Path,
    layout: QgsPrintLayout,
    project: QgsProject,
    default_path: Path,
    approved: Collection[str] = (),
) -> Resolution:
    """`resolve_destination` for `layout`'s cartograph."""
    key, legacy_id = layout_identity(layout, project)
    return resolve_destination(path, key, legacy_id, default_path, approved)


def stamp_for_layout(html: str, layout: QgsPrintLayout, project: QgsProject) -> str:
    """`html` stamped as `layout`'s own output."""
    return stamp_owner(html, layout_identity(layout, project)[0])
