"""What's new in this version, with the research question in the middle of it.

Shown once per plugin version. The research section asks only when no choice
has been made yet: **Share** and **Don't share** are two plain buttons of equal
weight, neither pre-selected nor the default, because a changelog whose Enter
key opts you in is not a choice (CJEU Planet49). Closing the window without
pressing either leaves research off.

When a choice already exists, the section is one line saying what it is and
where to change it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable

from qgis.PyQt.QtGui import QFontDatabase, QTextDocument
from qgis.PyQt.QtWidgets import (
    QDialog,
    QGroupBox,
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
    """`choice` is True for Share, False for Don't share, None for neither."""

    def __init__(
        self,
        version: str,
        excerpt: tuple[str, str],
        *,
        ask: bool,
        choice_line: str,
        payload: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.choice: bool | None = None
        self._payload = payload
        self.setWindowTitle(f"What's new in QGIS2WebMap {version}")
        self.setMinimumSize(620, 600)
        layout = QVBoxLayout(self)

        heading = QLabel(f"<h2>What's new in {version}</h2>", self)
        layout.addWidget(heading)

        before, after = excerpt
        if before:
            layout.addWidget(_markdown_browser(before, self), stretch=1)

        if ask:
            layout.addWidget(self._build_question())
        else:
            layout.addWidget(_label(choice_line, self))

        if after:
            layout.addWidget(_markdown_browser(after, self), stretch=1)

        if not ask:
            close = QPushButton("Close", self)
            close.clicked.connect(self.accept)
            layout.addWidget(close)

    def _build_question(self) -> QGroupBox:
        box = QGroupBox(text.RESEARCH_HEADING, self)
        inner = QVBoxLayout(box)
        inner.addWidget(_label(text.RESEARCH_WHAT, box))
        inner.addWidget(_label(text.RESEARCH_NEVER, box))
        inner.addWidget(_label(text.RESEARCH_CHANGE, box))

        self.see_button = QPushButton(text.SEE_WHAT_IS_SENT, box)
        self.see_button.setAutoDefault(False)
        self.see_button.clicked.connect(self._show_payload)
        inner.addWidget(self.see_button)

        row = QHBoxLayout()
        # Equal weight: same widget, same size, neither default nor autoDefault,
        # so Enter presses neither and no styling singles one out.
        self.share_button = QPushButton("Share", box)
        self.dont_share_button = QPushButton("Don't share", box)
        for button, share in (
            (self.share_button, True),
            (self.dont_share_button, False),
        ):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setMinimumWidth(140)
            button.clicked.connect(lambda _checked=False, s=share: self._choose(s))
            row.addWidget(button)
        inner.addLayout(row)
        return box

    def _choose(self, share: bool) -> None:
        self.choice = share
        self.accept()

    def _show_payload(self) -> None:
        PayloadDialog(self._payload(), self).exec()
