"""Qt5 and Qt6 must both work.

`metadata.txt` claims `qgisMaximumVersion=4.99`, and QGIS 4 ships PyQt6. PyQt6
removed the unscoped enum names (`Qt.RichText`) and the `exec_()` alias, so code
written the PyQt5 way raises `AttributeError` at the moment a dialog opens - on
a user's machine, not in a test that never builds a widget.

This is a source check rather than a widget test on purpose: it runs in the
plain unit tier with no QGIS and no display, so it catches the mistake in CI on
every push instead of only where a Qt6 build happens to be installed.

The bug it exists to prevent shipped once: `Qt.RichText` in the runtime licence
dialog made "type object 'Qt' has no attribute 'RichText'" the response to
pressing Preview, which blocked both preview and export entirely.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "nika_onlymap_exporter"


def python_sources() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def code_only(source: str) -> str:
    """The source with comments and string literals blanked out.

    Scanning raw text flags prose: the comment explaining *why* `Qt.RichText`
    is forbidden contains `Qt.RichText`. Blanking rather than deleting keeps
    line numbers intact, so a real offender still reports where it is.
    """
    lines = source.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover
        return source

    for token in tokens:
        if token.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (start_row, start_col), (end_row, end_col) = token.start, token.end
        for row in range(start_row, end_row + 1):
            line = lines[row - 1]
            begin = start_col if row == start_row else 0
            finish = end_col if row == end_row else len(line.rstrip("\n"))
            lines[row - 1] = line[:begin] + " " * (finish - begin) + line[finish:]

    return "".join(lines)


# Enum scopes PyQt6 requires. The bare names on the right are PyQt5-only, and
# each maps to the scope it must now be reached through.
REQUIRED_SCOPES = {
    "Qt": (
        "RichText",
        "PlainText",
        "AutoText",
        "WindowModal",
        "ApplicationModal",
        "NonModal",
        "Checked",
        "Unchecked",
        "PartiallyChecked",
        "UserRole",
        "DisplayRole",
        "AlignLeft",
        "AlignRight",
        "AlignCenter",
        "AlignTop",
        "Horizontal",
        "Vertical",
    ),
    "QDialogButtonBox": ("Ok", "Cancel", "Close", "Apply", "Save"),
    "QDialog": ("Accepted", "Rejected"),
    "QMessageBox": ("Yes", "No", "Warning", "Critical", "Information", "Question"),
    "QHeaderView": ("Stretch", "ResizeToContents", "Interactive", "Fixed"),
}


class TestNoUnscopedEnums:
    """PyQt6 removed `Qt.RichText` in favour of `Qt.TextFormat.RichText`."""

    def test_every_enum_is_reached_through_its_scope(self) -> None:
        offenders: list[str] = []

        for path in python_sources():
            source = code_only(path.read_text(encoding="utf-8"))
            for owner, names in REQUIRED_SCOPES.items():
                for name in names:
                    # `Owner.Name` where Name is NOT followed by a further
                    # attribute - i.e. the unscoped form. `Qt.CheckState.Checked`
                    # is fine; `Qt.Checked` is not.
                    pattern = rf"\b{owner}\.{name}\b(?!\.)"
                    for match in re.finditer(pattern, source):
                        line = source[: match.start()].count("\n") + 1
                        offenders.append(
                            f"{path.relative_to(PACKAGE.parent)}:{line} {owner}.{name}"
                        )

        assert not offenders, (
            "unscoped Qt enums break on PyQt6, which QGIS 4 ships:\n"
            + "\n".join(offenders)
        )


class TestNoRemovedApi:
    def test_exec_underscore_is_not_used(self) -> None:
        """PyQt6 removed `exec_()`; `exec()` works on both."""
        offenders: list[str] = []
        for path in python_sources():
            source = code_only(path.read_text(encoding="utf-8"))
            for match in re.finditer(r"\.exec_\s*\(", source):
                line = source[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{line}")

        assert not offenders, f"exec_() was removed in PyQt6: {offenders}"

    def test_qregexp_is_not_used(self) -> None:
        """PyQt6 dropped `QRegExp` entirely in favour of `QRegularExpression`."""
        offenders = [
            str(path.relative_to(PACKAGE.parent))
            for path in python_sources()
            if "QRegExp" in code_only(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, f"QRegExp was removed in PyQt6: {offenders}"
