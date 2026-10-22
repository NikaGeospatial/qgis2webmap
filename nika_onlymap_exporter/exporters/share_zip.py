"""A zip of the artifact.

Two reasons to choose this over a bare `.html`, and the second is easy to miss:

1. The data is too large for one comfortable file.
2. **Deliverability.** Many mail providers and most corporate filters quarantine
   or strip `.html` attachments, because HTML is a phishing vector. A zip passes
   filters that a bare `.html` does not - so this tier is worth offering even
   when size does not demand it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..core.atomic_write import staged_beside
from ..core.export_ir import OutputMode
from ..core.portable_names import avoid_windows_reserved
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome

_UNPORTABLE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


def archive_root(stem: str) -> str:
    """The folder name every member sits in, safe on every OS and never a path
    traversal: `...zip` has the stem `..`, which would write `../index.html`."""
    name = _UNPORTABLE.sub("-", stem).strip(" .")
    if not name:
        return "map"
    return avoid_windows_reserved(name)[:100]


def _member_name(root: str, file_name: str) -> str:
    """`root/file_name`, refusing anything that could leave the root."""
    if file_name in ("", ".", "..") or any(c in file_name for c in "/\\:\x00"):
        raise ValueError(f"unsafe archive member name: {file_name!r}")
    return f"{root}/{file_name}"


class ShareZipExporter:
    @property
    def mode(self) -> OutputMode:
        return OutputMode.SHARE_ZIP

    def export(self, result: ArtifactResult, destination: Path) -> ExportOutcome:
        """`destination` is the target `.zip` path."""
        if destination.suffix.lower() != ".zip":
            destination = destination.with_suffix(".zip")

        source_dir = result.entry_path.parent
        stem = archive_root(destination.stem)

        # Everything sits inside one named folder, so extracting never scatters
        # files across the recipient's Downloads directory.
        # Built beside the destination and renamed over it, so a failure part-way
        # leaves the previous valid zip in place instead of an incomplete one.
        with (
            staged_beside(destination) as staged,
            zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive,
        ):
            for item in sorted(source_dir.iterdir()):
                if item.is_file():
                    archive.write(item, _member_name(stem, item.name))

        return ExportOutcome(
            path=destination,
            mode=self.mode,
            size_bytes=destination.stat().st_size,
            open_instruction=(
                f"Extract it, then open index.html inside the {stem} folder."
            ),
        )
