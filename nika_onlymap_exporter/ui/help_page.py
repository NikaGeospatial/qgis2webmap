"""The Help tab's text: the bundled guides, rendered for a `QTextBrowser`.

The guides are `docs/*.md`, shipped as `help/` (see `load_help_markdown`).
`MainDialog` builds the tab and decides when to render; this module is what
it renders and the browser it shows it in.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from pathlib import Path

from qgis.PyQt.QtCore import QObject, Qt
from qgis.PyQt.QtGui import (
    QKeyEvent,
    QTextDocument,
    QTextFrame,
    QTextLength,
    QTextTable,
)
from qgis.PyQt.QtWidgets import QTextBrowser, QWidget

from .i18n import N_, tr
from .links import DOCS_URL

# Installed plugins carry the guides at `help/`, copied there by
# scripts/package_plugin.py. A git checkout has no such directory, so fall back
# to the authored source in `docs/` - which means a contributor running from a
# clone sees exactly what a user sees, with no duplicated copy to drift.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent
HELP_DIRS = (_PACKAGE_DIR / "help", _PACKAGE_DIR.parent / "docs")


def help_directory() -> Path | None:
    return next((d for d in HELP_DIRS if (d / "index.md").is_file()), None)


# Order shown in the Help tab. The same files are served on GitHub Pages, so the
# in-plugin help and the website can never drift apart.
HELP_PAGES = (
    (N_("Overview"), "index.md"),
    (N_("Your first export"), "first-export.md"),
    (N_("The dialog, tab by tab"), "the-dialog.md"),
    (N_("Sharing a map"), "sharing.md"),
    (N_("Enhance a map with AI"), "enhance-with-ai.md"),
    (N_("Putting a map online"), "hosting.md"),
    (N_("What gets exported"), "supported-features.md"),
    (N_("Troubleshooting"), "troubleshooting.md"),
    (N_("Privacy"), "privacy.md"),
    (N_("Your first cartograph"), "cartograph-first-export.md"),
    (N_("The Cartograph dialog, tab by tab"), "cartograph-dialog.md"),
    (N_("Interactive or image frames"), "live-or-static-frames.md"),
    (N_("Atlas cartographs"), "cartograph-atlas.md"),
    (N_("Printing a cartograph"), "printing-a-cartograph.md"),
    (N_("Colour vision"), "colour-vision.md"),
    (N_("What a cartograph keeps"), "cartograph-supported-features.md"),
)

HELP_UNAVAILABLE = N_(
    "# Help\n\n"
    "The bundled guides could not be found in this installation.\n\n"
    "They are also online at <{url}>.\n"
)


_IMAGE_LINE = re.compile(r"^[ \t]*!\[[^\]]*\]\([^)]*\)[ \t]*$\n?", re.MULTILINE)
_IMAGE_INLINE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# An image on its own line sits between two blank lines, so removing the line
# leaves three newlines behind. Markdown treats any run of blank lines as one
# break, so collapsing is safe and keeps the Help text free of ragged gaps.
_BLANK_RUN = re.compile(r"\n{3,}")
# A leading `# Heading`, allowing the blank lines front-matter stripping leaves
# behind. Only `# ` counts: a guide starting at `##` has no title of its own.
_OPENS_WITH_H1 = re.compile(r"\A\s*#[ \t]")


def strip_images(text: str) -> str:
    """Drop Markdown images, which are for the website only.

    The guides are one source of text serving two readers, and only one of them
    can show a picture. The website wants the screenshots - it is the page a
    stranger lands on, and a wall of prose about which button to press is worse
    there than anywhere. The Help tab cannot show them without `setBaseUrl` on
    the document *and* every PNG shipped inside the zip, which would take it
    from 223 KB to about 2.1 MB for a reader who is already looking at the
    dialog the screenshots depict.

    So the pictures are added on the site and removed here. Stripping at read
    time rather than at packaging time keeps `docs/*.md` and `help/*.md`
    byte-identical, which is the property that stops the two drifting apart.

    A whole-line image is removed with its line, so no blank gap is left behind;
    an image used mid-sentence is removed in place. Alt text goes with it: Qt
    would otherwise render it as bare prose that reads as a caption for a
    picture that is not there.
    """
    return _BLANK_RUN.sub("\n\n", _IMAGE_INLINE.sub("", _IMAGE_LINE.sub("", text)))


def load_help_markdown() -> str:
    """Concatenate the bundled guides into one scrollable document.

    Read from disk rather than embedded in this file so the plugin's help and
    the published website are literally the same text.
    """
    directory = help_directory()
    if directory is None:
        return tr(HELP_UNAVAILABLE).format(url=DOCS_URL)

    sections: list[str] = []
    for title, filename in HELP_PAGES:
        path = directory / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Strip any YAML front matter, which is for the website only.
        if text.startswith("---"):
            _, _, text = text.partition("---\n")[2].partition("---\n")
        text = strip_images(text).strip()
        # Most guides open with their own H1, because the website renders them
        # as standalone pages. Prepending the HELP_PAGES title unconditionally
        # printed that heading twice in a row - "The dialog, tab by tab" over
        # "The dialog, tab by tab" - for every guide that has one. The title is
        # only needed for a guide that does not carry its own.
        if not _OPENS_WITH_H1.match(text):
            text = f"# {tr(title)}\n\n{text}"
        sections.append(text)

    if not sections:
        return tr(HELP_UNAVAILABLE).format(url=DOCS_URL)
    return "\n\n---\n\n".join(sections)


def markdown_features():
    """Qt markdown features for the Help tab: GitHub dialect, **HTML off**.

    HTML must be off. The guides are full of literal `<om-map>`, `<om-layer>`
    and `<script>` inside code fences, and with HTML enabled Qt treats those as
    real elements and swallows everything after the first one - the Help tab
    lost more than half its text (5,330 characters rendered out of 12,439) and
    showed empty bullets where the content used to be.

    Built through `MarkdownFeatures(...)` rather than passing the OR'd value
    straight in: PyQt rejects a bare `int` for this argument. The flags are
    reached through their enum scope so this works on PyQt5 and PyQt6 alike.
    """
    feature = QTextDocument.MarkdownFeature
    return QTextDocument.MarkdownFeatures(
        feature.MarkdownDialectGitHub | feature.MarkdownNoHTML
    )


def render_help_document(parent: QObject | None = None) -> QTextDocument:
    """The bundled guides as a rendered document.

    Pass the browser as `parent`: `setDocument` does not take ownership, so a
    parentless document is freed with its last Python reference and the
    browser crashes on the next thing that touches it - a font change.
    """
    document = QTextDocument(parent)
    document.setMarkdown(load_help_markdown(), markdown_features())
    _fit_tables_to_width(document.rootFrame())
    return document


def _fit_tables_to_width(frame: QTextFrame) -> None:
    """Make every table exactly as wide as the page, wrapping its cells.

    Qt sizes a markdown table to its content, so a table with long cells (the
    "What you get" table) could be wider than the viewport and give the Help
    page a sideways scrollbar. A table fixed to 100% wraps its cells instead.
    """
    for child in frame.childFrames():
        if isinstance(child, QTextTable):
            table_format = child.format()
            table_format.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
            child.setFormat(table_format)
        _fit_tables_to_width(child)


class _HelpBrowser(QTextBrowser):
    """The Help text: one Tab stop, links reached with Ctrl+Tab.

    A QTextBrowser makes every link a Tab stop, which put about 126 presses
    between the Help page and the buttons below it. Tab now leaves the text in
    one step; the browser stays focusable, so the arrow and page keys scroll it.
    Links stay reachable from the keyboard: Ctrl+Tab and Ctrl+Shift+Tab step
    through them and Enter opens the one that is focused.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTabChangesFocus(True)

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802 - Qt override
        return QWidget.focusNextPrevChild(self, next)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt override
        if (
            event is not None
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            forward = event.key() == Qt.Key.Key_Tab
            if forward or event.key() == Qt.Key.Key_Backtab:
                QTextBrowser.focusNextPrevChild(self, forward)
                event.accept()
                return
        super().keyPressEvent(event)
