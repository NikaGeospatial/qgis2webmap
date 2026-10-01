"""What's new in this version, and the research question after it.

Shown once per plugin version: the changelog in one window, then - only when
no choice has been made yet - the research question in a second one, so
neither has to share the screen with the other. When a choice already exists,
the changelog ends with one line saying what it is and where to change it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable

from qgis.PyQt.QtGui import QFontDatabase, QTextDocument
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..research import text


def _markdown_browser(markdown: str, parent: QWidget) -> QTextBrowser:
    browser = QTextBrowser(parent)
    feature = getattr(QTextDocument, "MarkdownFeature", QTextDocument)
    browser.document().setMarkdown(
        markdown,
        QTextDocument.MarkdownFeatures(
            feature.MarkdownDialectGitHub | feature.MarkdownNoHTML
        ),
    )
    browser.setOpenExternalLinks(True)
    return browser


def _label(message: str, parent: QWidget) -> QLabel:
    label = QLabel(message, parent)
    label.setWordWrap(True)
    return label


class PayloadDialog(QDialog):
    """The reports, exactly as they would be sent, read-only."""

    def __init__(self, payload: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exactly what is sent")
        self.setMinimumSize(620, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "This is the JSON the plugin would send, built from your project "
                "by the same code that builds the real reports.",
                self,
            )
        )
        view = QPlainTextEdit(self)
        view.setReadOnly(True)
        view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        view.setPlainText(payload)
        self.view = view
        layout.addWidget(view, stretch=1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class WhatsNewDialog(QDialog):
    """The changelog excerpt for this version, in one scrolling view.

    `choice_line`, when given, says where research sharing stands; it is left
    out on the run that asks the question, which `ResearchChoiceDialog` does
    in a window of its own straight after this one.
    """

    def __init__(
        self,
        version: str,
        excerpt: tuple[str, str],
        *,
        choice_line: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"What's new in QGIS2WebMap {version}")
        self.setMinimumSize(620, 560)
        layout = QVBoxLayout(self)

        heading = QLabel(f"<h2>What's new in {version}</h2>", self)
        layout.addWidget(heading)

        # One view, so the whole list scrolls together. The excerpt is stored
        # split where the research question used to sit; joined, it reads in
        # its original order.
        markdown = "\n\n".join(part for part in excerpt if part)
        self.notes = _markdown_browser(markdown, self)
        layout.addWidget(self.notes, stretch=1)

        if choice_line:
            layout.addWidget(_label(choice_line, self))

        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class ResearchChoiceDialog(QDialog):
    """The research question, asked once, in its own window.

    `choice` is True for Share, False for Don't share, None for neither.

    **Share** and **Don't share** are two plain buttons of equal weight,
    neither pre-selected nor the default, because a window whose Enter key
    opts you in is not a choice (CJEU Planet49). Closing the window without
    pressing either leaves research off.
    """

    def __init__(
        self, payload: Callable[[], str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.choice: bool | None = None
        self._payload = payload
        self.setWindowTitle(text.RESEARCH_HEADING)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h3>{text.RESEARCH_HEADING}</h3>", self))
        layout.addWidget(_label(text.RESEARCH_WHAT, self))
        layout.addWidget(_label(text.RESEARCH_NEVER, self))
        layout.addWidget(_label(text.RESEARCH_CHANGE, self))

        self.see_button = QPushButton(text.SEE_WHAT_IS_SENT, self)
        self.see_button.setAutoDefault(False)
        self.see_button.clicked.connect(self._show_payload)
        layout.addWidget(self.see_button)

        row = QHBoxLayout()
        # Equal weight: same widget, same size, neither default nor autoDefault,
        # so Enter presses neither and no styling singles one out.
        self.share_button = QPushButton("Share", self)
        self.dont_share_button = QPushButton("Don't share", self)
        for button, share in (
            (self.share_button, True),
            (self.dont_share_button, False),
        ):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setMinimumWidth(140)
            button.clicked.connect(lambda _checked=False, s=share: self._choose(s))
            row.addWidget(button)
        layout.addLayout(row)

    def _choose(self, share: bool) -> None:
        self.choice = share
        self.accept()

    def _show_payload(self) -> None:
        PayloadDialog(self._payload(), self).exec()
