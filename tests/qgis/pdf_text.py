"""Read the text of an exported PDF, page by page, with poppler's `pdftotext`.

The tests assert on what a person reading the PDF sees, so the text is taken
from the file itself. `pdftotext` is looked for on PATH and then in the Nix
store (where this project's development machine keeps it); a machine with
neither skips the tests that need it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import glob
import shutil
import subprocess
from pathlib import Path

import pytest


def _find_pdftotext() -> str | None:
    found = shutil.which("pdftotext")
    if found:
        return found
    candidates = sorted(glob.glob("/nix/store/*-poppler-utils-*/bin/pdftotext"))
    return candidates[-1] if candidates else None


def pdf_pages_text(path: Path) -> list[str]:
    """The text of each page of the PDF at `path`, in page order, with the
    page's layout (words on one line stay on one line)."""
    tool = _find_pdftotext()
    if tool is None:
        pytest.skip("pdftotext (poppler) is not available")
    done = subprocess.run(
        [tool, "-layout", str(path), "-"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    pages = done.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()  # the form feed after the last page
    return pages
