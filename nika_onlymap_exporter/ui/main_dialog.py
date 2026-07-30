"""The single export dialog.

One window owns the whole export, grouped by **task rather than by scope**. The
incumbent splits per-layer settings (a checkbox grid) from global settings (a
Setting/Value table) into two panels with two interaction models, and puts
related options on opposite sides, so one conceptual job spans both.

Four rules this file holds:

1. **Nothing configured is silently discarded.** Seven of the incumbent's
   Appearance options default to `"None"`, so a project title set in Project
   Properties never reaches the map.
2. **State is visible.** No control whose current value cannot be read off it.
3. **Never offer an export we know is broken.** Export is disabled with the
   reason beside it.
4. **Never close QGIS.** Every entry point is wrapped; a failure is a message.

Built in Python rather than a `.ui` file on purpose: the tab set is stable and
small, and hand-written widgets avoid a `pyuic` build step plus the Qt5/Qt6 `.ui`
compatibility questions that come with it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import Qgis, QgsMessageLog, QgsProject
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.export_ir import FidelityStatus, OutputMode
from ..core.fidelity_report import FidelityReportBuilder
from ..core.project_reader import read_project, resolve_title
from ..core.settings import DialogState, load_state, save_state
from ..packaging.artifact_builder import build_artifact
from ..writers.onlymap_writer import ExportBlockedError
from .layer_watcher import LayerTreeWatcher
from .preview import write_preview

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgisInterface

LOG_TAG = "QGIS2WebMap"

COMPANY_URL = "https://nikaplanet.com"
DOCS_URL = "https://docs.nikaplanet.com"
REPO_URL = "https://github.com/NikaGeospatial/qgis2webmap"

MODE_LABELS = {
    OutputMode.STANDALONE_HTML: "Standalone HTML - one file, opens by double-click",
    OutputMode.SHARE_ZIP: "Share ZIP - a zip to email or upload",
    OutputMode.FOLDER: "Folder - for publishing to a web server",
}

STATUS_LABELS = {
    FidelityStatus.PRESERVED: "Kept",
    FidelityStatus.APPROXIMATED: "Changed",
    FidelityStatus.RASTER_FALLBACK: "Rasterised",
    FidelityStatus.UNSUPPORTED: "Not exported",
    FidelityStatus.BLOCKED: "Blocked",
}

# Problems first. A report opening on a wall of "Kept" buries what matters.
STATUS_ORDER = {
    FidelityStatus.BLOCKED: 0,
    FidelityStatus.UNSUPPORTED: 1,
    FidelityStatus.APPROXIMATED: 2,
    FidelityStatus.RASTER_FALLBACK: 3,
    FidelityStatus.PRESERVED: 4,
}

HELP_HTML = f"""
<h2>QGIS2WebMap by NIKA</h2>
<p><b>Built by NIKA, powered by OnlyMap.</b></p>

<h3>What it produces</h3>
<p>The default export is a <b>single HTML file</b> another person can
double-click and use without QGIS installed.</p>

<h3>Privacy</h3>
<p>Exported maps contain <b>no tracking</b> and make <b>no network requests</b>.
An exported file works with no internet connection.</p>

<h3>Links</h3>
<ul>
  <li><a href="{DOCS_URL}">NIKA Documentation</a></li>
  <li><a href="{COMPANY_URL}">NIKA</a></li>
  <li><a href="{REPO_URL}">Source code</a> (GPL-2.0-or-later)</li>
  <li><a href="{REPO_URL}/issues">Report an issue</a></li>
</ul>

<hr>
<p><small>QGIS2WebMap is built by NIKA and is not endorsed by QGIS.org.</small></p>
"""


class MainDialog(QDialog):
    """Export dialog. Non-modal, so QGIS stays usable behind it."""

    def __init__(self, iface: QgisInterface, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.iface = iface
        self.project = QgsProject.instance()
        self.state: DialogState = load_state(self.project)

        self.setWindowTitle("QGIS2WebMap by NIKA")
        self.setObjectName("qgis2webmapMainDialog")
        self.resize(960, 640)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_map_tab(), "Map")
        self.tabs.addTab(self._build_layers_tab(), "Layers")
        self.tabs.addTab(self._build_appearance_tab(), "Appearance")
        self.tabs.addTab(self._build_fidelity_tab(), "Fidelity")
        self.tabs.addTab(self._build_help_tab(), "Help")
        layout.addWidget(self.tabs)
        layout.addLayout(self._build_button_row())

        # The list tracks QGIS live; there is no refresh button because there is
        # nothing to refresh.
        self.watcher = LayerTreeWatcher(self.project, self)
        self.watcher.changed.connect(self.refresh_layers)

        self.refresh_layers()

    # ---- Map tab --------------------------------------------------------

    def _build_map_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()

        # First field in the dialog, and the *only* place the exported title is
        # set. The incumbent splits this across two Project Properties tabs and
        # then discards it unless a third setting is also changed.
        self.name_edit = QLineEdit(self.state.map_name, page)
        self.name_edit.setPlaceholderText(resolve_title(self.project, None))
        self.name_edit.setToolTip(
            "The title shown on the exported map. Leave blank to use the project title."
        )
        self.name_edit.textChanged.connect(self._on_name_changed)
        form.addRow("Map name", self.name_edit)
        layout.addLayout(form)

        self.mode_box = QGroupBox("How to share it", page)
        mode_layout = QVBoxLayout(self.mode_box)
        self.mode_checks: dict[OutputMode, QCheckBox] = {}
        for mode, label in MODE_LABELS.items():
            check = QCheckBox(label, self.mode_box)
            check.setChecked(mode is self.state.output_mode)
            check.clicked.connect(lambda _c, m=mode: self._on_mode_selected(m))
            mode_layout.addWidget(check)
            self.mode_checks[mode] = check
        layout.addWidget(self.mode_box)

        layout.addStretch(1)
        return page

    def _on_name_changed(self, text: str) -> None:
        self.state.map_name = text

    def _on_mode_selected(self, mode: OutputMode) -> None:
        self.state.output_mode = mode
        for candidate, check in self.mode_checks.items():
            check.setChecked(candidate is mode)

    # ---- Layers tab -----------------------------------------------------

    def _build_layers_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Layers come from the QGIS Layers panel and follow it live - "
                "reorder or rename them there and this list updates.",
                page,
            )
        )

        self.layer_tree = QTreeWidget(page)
        self.layer_tree.setColumnCount(4)
        self.layer_tree.setHeaderLabels(["Layer", "Include", "Popups", "Labels"])
        self.layer_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.layer_tree.itemChanged.connect(self._on_layer_item_changed)
        layout.addWidget(self.layer_tree)
        return page

    def refresh_layers(self) -> None:
        """Rebuild the list from the project.

        Settings survive because they live in `DialogState`, keyed by layer id -
        never in the widgets being discarded here. That is what lets the list
        follow QGIS without a refresh button that mutates settings.
        """
        if not hasattr(self, "layer_tree"):
            return

        self.layer_tree.blockSignals(True)
        self.layer_tree.clear()

        # Bottom-first, matching draw order and the exported document.
        for tree_layer in reversed(self.project.layerTreeRoot().findLayers()):
            layer = tree_layer.layer()
            if layer is None:
                continue
            settings = self.state.for_layer(layer.id())

            item = QTreeWidgetItem(self.layer_tree)
            item.setText(0, layer.name())
            item.setData(0, Qt.ItemDataRole.UserRole, layer.id())
            for column, value in (
                (1, settings.include),
                (2, settings.popup),
                (3, settings.label),
            ):
                item.setCheckState(
                    column,
                    Qt.CheckState.Checked if value else Qt.CheckState.Unchecked,
                )

        self.layer_tree.blockSignals(False)
        self._update_export_readiness()

    def _on_layer_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        layer_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not layer_id:
            return
        settings = self.state.for_layer(layer_id)
        checked = item.checkState(column) == Qt.CheckState.Checked
        if column == 1:
            settings.include = checked
        elif column == 2:
            settings.popup = checked
        elif column == 3:
            settings.label = checked
        self._update_export_readiness()

    # ---- Appearance tab -------------------------------------------------

    def _build_appearance_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "These are on by default - an exported map should be useful "
                "without configuring anything.",
                page,
            )
        )

        self.widget_checks: dict[str, QCheckBox] = {}
        for key, label, current in (
            ("legend", "Legend", self.state.show_legend),
            ("layerSwitcher", "Layer switcher", self.state.show_layer_switcher),
            ("zoomControls", "Zoom controls", self.state.show_zoom_controls),
            ("scaleBar", "Scale bar", self.state.show_scale_bar),
        ):
            check = QCheckBox(label, page)
            check.setChecked(current)
            check.toggled.connect(lambda v, k=key: self._on_widget_toggled(k, v))
            layout.addWidget(check)
            self.widget_checks[key] = check

        layout.addStretch(1)
        return page

    def _on_widget_toggled(self, key: str, value: bool) -> None:
        attribute = {
            "legend": "show_legend",
            "layerSwitcher": "show_layer_switcher",
            "zoomControls": "show_zoom_controls",
            "scaleBar": "show_scale_bar",
        }[key]
        setattr(self.state, attribute, value)

    # ---- Fidelity tab ---------------------------------------------------

    def _build_fidelity_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "What survives the export, and what does not. Filled in when you "
                "preview or export - nothing is left to discover later.",
                page,
            )
        )
        self.fidelity_tree = QTreeWidget(page)
        self.fidelity_tree.setColumnCount(3)
        self.fidelity_tree.setHeaderLabels(["Item", "Result", "Detail"])
        self.fidelity_tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.fidelity_tree)
        return page

    def _show_fidelity(self, report: FidelityReportBuilder) -> None:
        self.fidelity_tree.clear()
        for entry in sorted(report.items, key=lambda i: STATUS_ORDER[i.status]):
            item = QTreeWidgetItem(self.fidelity_tree)
            item.setText(0, entry.subject)
            item.setText(1, STATUS_LABELS[entry.status])
            item.setText(2, entry.detail)

    # ---- Help tab -------------------------------------------------------

    def _build_help_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        browser = QTextBrowser(page)
        browser.setHtml(HELP_HTML)
        browser.setOpenLinks(False)
        browser.anchorClicked.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(browser)
        return page

    # ---- Buttons --------------------------------------------------------

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)

        self.preview_button = QPushButton("Preview in browser", self)
        self.preview_button.clicked.connect(self.on_preview)
        row.addWidget(self.preview_button)

        self.export_button = QPushButton("Export", self)
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self.on_export)
        row.addWidget(self.export_button)

        close = QPushButton("Close", self)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        return row

    def _available_layer_ids(self) -> list[str]:
        return [
            tree_layer.layer().id()
            for tree_layer in self.project.layerTreeRoot().findLayers()
            if tree_layer.layer() is not None
        ]

    def _update_export_readiness(self) -> None:
        """Disable Export with the reason beside it, never silently."""
        available = self._available_layer_ids()
        selected = self.state.selected_layer_ids(available)

        if not available:
            self._set_ready(False, "Add a vector layer to the project to export.")
        elif not selected:
            self._set_ready(False, "Tick at least one layer to include.")
        else:
            count = len(selected)
            self._set_ready(
                True, f"{count} layer{'s' if count != 1 else ''} will be exported."
            )

    def _set_ready(self, ready: bool, message: str) -> None:
        self.export_button.setEnabled(ready)
        self.preview_button.setEnabled(ready)
        self.status_label.setText(message)

    # ---- Actions --------------------------------------------------------

    def _read_current_project(self):
        report = FidelityReportBuilder()
        export = read_project(
            self.project,
            report,
            settings=self.state.to_export_settings(),
            title_override=self.state.map_name or None,
            selected_layer_ids=self.state.selected_layer_ids(
                self._available_layer_ids()
            ),
        )
        return export, report

    def on_preview(self) -> None:
        """Write a preview and open it in the user's own default browser."""
        try:
            export, report = self._read_current_project()
            self._show_fidelity(report)

            identity = self.project.fileName() or self.project.baseName() or "untitled"
            result = write_preview(export, identity)

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.entry_path)))
            self.status_label.setText(
                "Preview opened. Reload the browser tab after making changes - "
                "your position on the map is kept."
            )
        except Exception as exc:
            self._report_failure("Preview failed", exc)

    def on_export(self) -> None:
        """Write the artifact wherever the user asks for it."""
        try:
            export, report = self._read_current_project()
            self._show_fidelity(report)

            mode = self.state.output_mode
            suggested, filter_text = {
                OutputMode.STANDALONE_HTML: ("map.html", "Web page (*.html)"),
                OutputMode.SHARE_ZIP: ("map.zip", "Zip archive (*.zip)"),
                OutputMode.FOLDER: ("map", ""),
            }[mode]

            if mode is OutputMode.FOLDER:
                chosen = QFileDialog.getExistingDirectory(self, "Export to folder")
            else:
                chosen, _ = QFileDialog.getSaveFileName(
                    self, "Export map", suggested, filter_text
                )
            if not chosen:
                return

            _result, outcome = build_artifact(export, Path(chosen), mode=mode)
            save_state(self.project, self.state)

            self.status_label.setText(outcome.summary())
            QMessageBox.information(self, "Export complete", outcome.summary())

        except ExportBlockedError as exc:
            # Recoverable and the user's to fix, so a warning with the reasons,
            # not a crash report.
            QMessageBox.warning(
                self,
                "Cannot export",
                "This map cannot be exported yet:\n\n"
                + "\n".join(f"- {reason}" for reason in exc.reasons),
            )
        except Exception as exc:
            self._report_failure("Export failed", exc)

    def _report_failure(self, title: str, exc: Exception) -> None:
        """Surface a failure without ever taking QGIS down with it."""
        QgsMessageLog.logMessage(
            f"{title}:\n{traceback.format_exc()}", LOG_TAG, level=Qgis.Critical
        )
        QMessageBox.critical(
            self,
            title,
            f"{exc}\n\nDetails are in the QGIS message log under "
            f'"{LOG_TAG}". Please report this at {REPO_URL}/issues',
        )

    # ---- Lifecycle ------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Persist settings and drop every signal connection.

        A rebuild scheduled against a destroyed dialog is a crash, and the user
        experiences that as QGIS vanishing.
        """
        try:
            save_state(self.project, self.state)
        except Exception:
            QgsMessageLog.logMessage(
                f"Could not save export settings:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.Warning,
            )
        self.watcher.disconnect_all()
        super().closeEvent(event)
