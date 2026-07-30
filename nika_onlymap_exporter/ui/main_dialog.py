"""The single export dialog.

One window owns the whole export -- layer selection, appearance, output tier,
fidelity report and help. This mirrors the one structural thing qgis2web gets
right (one dialog, one workflow) while avoiding its split between a per-layer
checkbox grid and a separate global Setting/Value table.

At 0.1.0 scaffold stage only the Help tab is populated; the others are declared
so the shape is fixed and later tasks fill them in.

Built in Python rather than a `.ui` file on purpose: the tab set is stable and
small, and hand-written widgets avoid a `pyuic` build step plus the Qt5/Qt6
`.ui` compatibility questions that come with it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgisInterface

COMPANY_URL = "https://nikaplanet.com"
DOCS_URL = "https://docs.nikaplanet.com"
REPO_URL = "https://github.com/NikaGeospatial/qgis2webmap"
ISSUES_URL = f"{REPO_URL}/issues"

# Tabs in workflow order. Help last because it is reference, not a step.
TAB_TITLES = ("Layers", "Appearance", "Output", "Fidelity", "Help")

HELP_HTML = f"""
<h2>QGIS2WebMap by NIKA</h2>
<p><b>Built by NIKA, powered by OnlyMap.</b> Turn a QGIS project into a portable
OnlyMap web map.</p>

<h3>What it produces</h3>
<p>The default export is a <b>single HTML file</b> that another person can
double-click and use without QGIS installed. Larger projects can be exported as a
folder or zip instead.</p>

<h3>Privacy</h3>
<p>Exported maps contain <b>no tracking</b> and make <b>no network requests</b>.
An exported file works with no internet connection. Publishing and AI-assisted
enhancement are separate, explicit actions that you start yourself.</p>

<h3>Links</h3>
<ul>
  <li><a href="{DOCS_URL}">NIKA Documentation</a></li>
  <li><a href="{COMPANY_URL}">NIKA</a></li>
  <li><a href="{REPO_URL}">Source code</a> (GPL-2.0-or-later)</li>
  <li><a href="{ISSUES_URL}">Report an issue</a></li>
</ul>

<hr>
<p><small>QGIS2WebMap is built by NIKA and is not endorsed by QGIS.org.
QGIS&reg; is a trademark of the QGIS project.</small></p>
"""


class MainDialog(QDialog):
    """Export dialog shell. Tabs are filled in by later tasks."""

    def __init__(self, iface: QgisInterface, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.iface = iface

        self.setWindowTitle("QGIS2WebMap by NIKA")
        self.setObjectName("qgis2webmapMainDialog")
        self.resize(940, 620)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget(self)
        for title in TAB_TITLES:
            self.tabs.addTab(self._build_tab(title), title)
        layout.addWidget(self.tabs)

        # Close only for now -- Export arrives with the writer in a later task.
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---- tab construction ----------------------------------------------

    def _build_tab(self, title: str) -> QWidget:
        if title == "Help":
            return self._build_help_tab()
        return self._build_placeholder_tab(title)

    def _build_help_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        browser = QTextBrowser(page)
        browser.setHtml(HELP_HTML)
        browser.setOpenLinks(False)
        # Route links to the system browser rather than rendering them in-widget.
        browser.anchorClicked.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(browser)

        docs_button = QPushButton("Open NIKA Documentation", page)
        docs_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))
        layout.addWidget(docs_button)

        return page

    def _build_placeholder_tab(self, title: str) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        label = QLabel(
            f"<h3>{title}</h3>"
            "<p>Not implemented yet -- this tab is part of the 0.1.0 build-out.</p>"
            f'<p><a href="{REPO_URL}">Follow progress on GitHub</a></p>',
            page,
        )
        label.setOpenExternalLinks(True)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(label)

        return page
