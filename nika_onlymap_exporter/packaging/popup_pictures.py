"""Popup pictures, carried the way the export's tier carries everything else.

The reader has already chosen and prepared every picture a popup shows
(`core.popup_media`), and its markup names each one by a file name. Here that
name becomes something the recipient's browser can open:

* **Folder and Share ZIP** write each picture beside `index.html` under that
  name, so the markup's relative address finds it - served, or opened from an
  extracted folder by double-click, since a browser shows a sibling picture
  from `file://` (it is only `fetch` that it refuses).
* **A single file** has nowhere to put one, so each name is replaced with a
  `data:` URI holding the picture: in the page itself, as a cartograph's live
  frames need too.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import dataclasses
from pathlib import Path

from ..core.export_ir import ExportProject, OutputMode
from ..core.popup_media import picture_sources, replace_picture_names

# The tiers with a folder to put a picture in.
_SIBLING_MODES = frozenset({OutputMode.FOLDER, OutputMode.SHARE_ZIP})


def embed_popup_pictures(project: ExportProject) -> ExportProject:
    """`project` with every popup picture inside its markup as a `data:` URI."""
    pictures = picture_sources(project.layers)
    if not pictures:
        return project
    sources = {
        name: f"data:{picture.media_type};base64,"
        + base64.b64encode(picture.data).decode("ascii")
        for name, picture in pictures.items()
    }
    return dataclasses.replace(
        project,
        layers=tuple(replace_picture_names(layer, sources) for layer in project.layers),
    )


def place_popup_pictures(
    project: ExportProject, destination: Path, mode: OutputMode
) -> tuple[ExportProject, tuple[Path, ...]]:
    """Carry `project`'s popup pictures for `mode`; the files written, if any.

    Beside the page for a tier with a folder, inside it otherwise.
    """
    if mode not in _SIBLING_MODES:
        return embed_popup_pictures(project), ()
    written = []
    for name, picture in picture_sources(project.layers).items():
        path = destination / name
        path.write_bytes(picture.data)
        written.append(path)
    return project, tuple(written)
