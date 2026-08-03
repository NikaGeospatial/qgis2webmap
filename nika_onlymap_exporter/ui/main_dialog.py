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

import contextlib
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import Qgis, QgsMapLayer, QgsMessageLog, QgsProject
from qgis.gui import QgsColorButton
from qgis.PyQt.QtCore import QSettings, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QPalette, QTextDocument
from qgis.PyQt.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
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
    QRadioButton,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.export_ir import (
    ExtentSource,
    FidelityStatus,
    OutputMode,
    OverlayCorner,
    PopupFieldMode,
)
from ..core.fidelity_report import FidelityReportBuilder
from ..core.license_policy import default_policy, report_verdict
from ..core.manifest_builder import basemap_note
from ..core.popup_translator import hidden_field_names, popup_field_names
from ..core.project_reader import extent_from_canvas, read_project, resolve_title
from ..core.settings import (
    MAX_PRECISION,
    MIN_PRECISION,
    PRECISION_FULL,
    DialogState,
    LayerSettings,
    load_state,
    save_state,
)
from ..packaging.artifact_builder import build_artifact
from ..packaging.dependency_scanner import standalone_ineligible_reason
from ..writers.onlymap_writer import ExportBlockedError
from .layer_watcher import LayerTreeWatcher
from .live_server import PreviewServer
from .preview import preview_directory, write_preview
from .runtime_setup import ensure_runtime

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

# Worded so the choice is readable without knowing the model: "with data" says
# what happens, where qgis2web's "visible with data" needs its manual.
POPUP_MODE_LABELS = {
    PopupFieldMode.NO_LABEL: "Value only, no label",
    PopupFieldMode.INLINE_ALWAYS: "Label beside value - always show",
    PopupFieldMode.INLINE_WITH_DATA: "Label beside value - only if it has data",
    PopupFieldMode.HEADER_ALWAYS: "Label above value - always show",
    PopupFieldMode.HEADER_WITH_DATA: "Label above value - only if it has data",
    PopupFieldMode.HIDDEN: "Do not show this field",
}

# Long enough to say what happened, short enough that every row stays one line.
DETAIL_SUMMARY_LENGTH = 110

# Keeps the mode combos aligned down the list however long the field names are.
FIELD_NAME_COLUMN_WIDTH = 160

# How often the live preview checks whether any setting changed. Comparing one
# short JSON string, so the cost is noise; the interval only bounds how long a
# change waits before the debounce even starts.
LIVE_POLL_MS = 300

# How long a change must settle before a rebuild. Dragging a colour picker emits
# a change per pixel, and each rebuild re-runs the production writer.
LIVE_DEBOUNCE_MS = 400

# Where the live-preview preference lives. QSettings, not the project file: it
# describes how someone likes to work, not what the map is, so it should follow
# the person between projects rather than travel inside one.
LIVE_PREVIEW_KEY = "qgis2webmap/livePreview"

# Marks the per-layer options row, so it is never mistaken for a field row.
LAYER_OPTIONS_ROLE = "__layer_options__"

# Centres first: they are the only positions not already holding map chrome, so
# they are what most people should pick.
CAPTION_CORNER_LABELS = {
    OverlayCorner.TOP_CENTER: "Top centre - clear of the controls",
    OverlayCorner.BOTTOM_CENTER: "Bottom centre - clear of the controls",
    OverlayCorner.TOP_LEFT: "Top left - shared with the layer switcher",
    OverlayCorner.TOP_RIGHT: "Top right - shared with the legend",
    OverlayCorner.BOTTOM_LEFT: "Bottom left - shared with zoom and scale",
    OverlayCorner.BOTTOM_RIGHT: "Bottom right - shared with the credit",
}

# "None" first and default: it is the only choice that keeps the export working
# offline and contacting nobody. The rest are the runtime's registered presets,
# minus the MapTiler ones, which need an API key the file would have to carry in
# plain text for every recipient to read.
BASEMAP_LABELS = {
    "none": "None - the export stays offline and contacts nobody",
    "osm": "OpenStreetMap",
    "positron": "Positron - pale, for data on top",
    "dark-matter": "Dark Matter - dark background",
    "voyager": "Voyager - general purpose",
    "liberty": "Liberty - detailed street map",
    "bright": "Bright - high contrast",
}

EXTENT_LABELS = {
    ExtentSource.DATA: "The data - every feature is visible",
    ExtentSource.CANVAS: "The current QGIS view",
}


def _apply_saved_color(button: QgsColorButton, value: str) -> None:
    """Restore a stored `#rrggbb`, leaving the button null when unset.

    Set before the signal matters: a null button means "the runtime's own
    colour", which is what keeps a default export byte-identical.
    """
    from qgis.PyQt.QtGui import QColor

    if not value:
        button.setToNull()
        return

    # Stored CSS-style with alpha last; QColor's own hex form puts it first.
    text = value.lstrip("#")
    color = QColor(f"#{text[6:8]}{text[0:6]}") if len(text) == 8 else QColor(value)

    if color.isValid():
        button.setColor(color)
    else:
        button.setToNull()


def _help_label(text: str, parent: QWidget) -> QLabel:
    """A quiet line of guidance under a control.

    Several settings explained themselves only in a tooltip, which is invisible
    unless you already suspect there is something to read - and the setting most
    in need of explaining, coordinate precision, is the one that throws data
    away. Colour comes from the palette so it stays legible in a dark theme.
    """
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def _summarise(detail: str) -> str:
    """The one-line form of a fidelity note, cut on a word boundary."""
    if len(detail) <= DETAIL_SUMMARY_LENGTH:
        return detail
    cut = detail[:DETAIL_SUMMARY_LENGTH].rsplit(" ", 1)[0]
    return f"{cut} ..."


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
    ("Overview", "index.md"),
    ("Your first export", "first-export.md"),
    ("Sharing a map", "sharing.md"),
    ("Enhance with AI", "enhance-with-ai.md"),
    ("Host with OnlyMap", "hosting.md"),
    ("What gets exported", "supported-features.md"),
    ("Privacy", "privacy.md"),
)

HELP_UNAVAILABLE = (
    "# Help\n\n"
    "The bundled guides could not be found in this installation.\n\n"
    f"They are also online at <{DOCS_URL}>.\n"
)


def load_help_markdown() -> str:
    """Concatenate the bundled guides into one scrollable document.

    Read from disk rather than embedded in this file so the plugin's help and
    the published website are literally the same text.
    """
    directory = help_directory()
    if directory is None:
        return HELP_UNAVAILABLE

    sections: list[str] = []
    for title, filename in HELP_PAGES:
        path = directory / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Strip any YAML front matter, which is for the website only.
        if text.startswith("---"):
            _, _, text = text.partition("---\n")[2].partition("---\n")
        sections.append(f"# {title}\n\n{text.strip()}")

    if not sections:
        return HELP_UNAVAILABLE
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
    feature = getattr(QTextDocument, "MarkdownFeature", QTextDocument)
    return QTextDocument.MarkdownFeatures(
        feature.MarkdownDialectGitHub | feature.MarkdownNoHTML
    )


def render_help_document() -> QTextDocument:
    """The bundled guides as a rendered document."""
    document = QTextDocument()
    document.setMarkdown(load_help_markdown(), markdown_features())
    return document


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
        self._fidelity_is_stale = True
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)
        layout.addWidget(self._build_fidelity_strip())
        layout.addLayout(self._build_button_row())

        # The list tracks QGIS live; there is no refresh button because there is
        # nothing to refresh.
        self.watcher = LayerTreeWatcher(self.project, self)
        self.watcher.changed.connect(self.refresh_layers)

        # `finished` covers every way the dialog can close, including the Close
        # button. `closeEvent` alone does not: on Qt5, `QDialog::done()` hides
        # the dialog without delivering a QCloseEvent, so a user who pressed
        # Close would lose every setting they had just made.
        self._shut_down = False
        self.finished.connect(lambda _code: self._shutdown())

        # ---- Live preview ------------------------------------------------
        # The server is created on first use, not here: a user with live preview
        # switched off must never have a socket opened on their behalf.
        self._server: PreviewServer | None = None
        self._last_snapshot = self.state.snapshot()
        self._rebuilding = False

        # Polls the settings snapshot rather than the widgets. See
        # `DialogState.snapshot` for why signals were the wrong hook.
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(LIVE_POLL_MS)
        self._watch_timer.timeout.connect(self._poll_for_changes)

        # Coalesces a burst of edits - dragging a colour picker emits a change
        # per pixel - into one rebuild.
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(LIVE_DEBOUNCE_MS)
        self._rebuild_timer.timeout.connect(self._rebuild_live_preview)

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

        # Radio buttons, not checkboxes. These three are mutually exclusive, and
        # the previous checkbox version had to unpick the others by hand on every
        # click - which promises multi-select, does not deliver it, and reads
        # wrong to a screen reader. A QButtonGroup enforces the exclusivity that
        # the model already had.
        self.mode_box = QGroupBox("How to share it", page)
        mode_layout = QVBoxLayout(self.mode_box)
        self.mode_group = QButtonGroup(self.mode_box)
        self.mode_group.setExclusive(True)
        self.mode_checks: dict[OutputMode, QRadioButton] = {}
        for mode, label in MODE_LABELS.items():
            button = QRadioButton(label, self.mode_box)
            button.setChecked(mode is self.state.output_mode)
            button.clicked.connect(lambda _c, m=mode: self._on_mode_selected(m))
            self.mode_group.addButton(button)
            mode_layout.addWidget(button)
            self.mode_checks[mode] = button
        layout.addWidget(self.mode_box)

        data_box = QGroupBox("Data", page)
        data_form = QFormLayout(data_box)

        self.extent_combo = QComboBox(data_box)
        for source, label in EXTENT_LABELS.items():
            self.extent_combo.addItem(label, source.value)
        extent_index = self.extent_combo.findData(self.state.extent_source.value)
        self.extent_combo.setCurrentIndex(extent_index if extent_index >= 0 else 0)
        self.extent_combo.currentIndexChanged.connect(self._on_extent_changed)
        data_form.addRow("Open the map on", self.extent_combo)

        self.basemap_combo = QComboBox(data_box)
        for value, label in BASEMAP_LABELS.items():
            self.basemap_combo.addItem(label, value)
        basemap_index = self.basemap_combo.findData(self.state.basemap)
        self.basemap_combo.setCurrentIndex(basemap_index if basemap_index >= 0 else 0)
        self.basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        data_form.addRow("Basemap", self.basemap_combo)

        # Warning rather than help: this is the only setting that changes what
        # the *recipient's* machine does, and it cannot be undone after sending.
        self.basemap_warning = QLabel("", data_box)
        self.basemap_warning.setWordWrap(True)
        data_form.addRow("", self.basemap_warning)
        self._update_basemap_warning()

        # "Maintain" first and selected: rounding coordinates is the only
        # setting in this dialog that throws data away, so it is opt-in and
        # says so in the fidelity report when chosen.
        self.precision_combo = QComboBox(data_box)
        self.precision_combo.addItem("Maintain full precision", None)
        for places in range(MIN_PRECISION, MAX_PRECISION + 1):
            self.precision_combo.addItem(f"{places} decimal place(s)", places)
        precision_index = self.precision_combo.findData(self.state.quantize_precision)
        self.precision_combo.setCurrentIndex(
            precision_index if precision_index >= 0 else 0
        )
        self.precision_combo.setToolTip(
            "Rounding coordinates makes the file smaller and is irreversible. "
            "Around 6 decimal places is roughly 0.1 m at the equator."
        )
        self.precision_combo.currentIndexChanged.connect(self._on_precision_changed)
        data_form.addRow("Coordinate precision", self.precision_combo)
        # The only setting in this dialog that discards data, so it says so on
        # screen rather than only on hover.
        data_form.addRow(
            "",
            _help_label(
                "Rounding makes the file smaller and cannot be undone. "
                "6 places is about 0.1 m at the equator.",
                data_box,
            ),
        )
        layout.addWidget(data_box)

        layout.addStretch(1)

        # The tab's dead space, spent saying what pressing Export will produce.
        # Kept at the bottom so it reads as a consequence of the choices above
        # rather than another setting.
        self.export_summary = QLabel("", page)
        self.export_summary.setWordWrap(True)
        self.export_summary.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(self.export_summary)
        return page

    def _update_export_summary(self, selected_count: int) -> None:
        """Name the artifact in the same words the buttons use."""
        if not selected_count:
            self.export_summary.setText("")
            return

        produced = {
            OutputMode.STANDALONE_HTML: "one HTML file that opens by double-click",
            OutputMode.SHARE_ZIP: "a zip to email or upload",
            OutputMode.FOLDER: "a folder to publish to a web server",
        }[self.state.output_mode]
        layers = f"{selected_count} layer{'s' if selected_count != 1 else ''}"
        self.export_summary.setText(f"Export writes {produced}, carrying {layers}.")

    def _on_extent_changed(self) -> None:
        value = self.extent_combo.currentData()
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                self.state.extent_source = ExtentSource(value)
        self._fidelity_is_stale = True

    def _on_basemap_changed(self) -> None:
        value = self.basemap_combo.currentData()
        self.state.basemap = value if isinstance(value, str) else "none"
        self._update_basemap_warning()
        self._fidelity_is_stale = True

    def _update_basemap_warning(self) -> None:
        """Say what a basemap costs, in the terms it actually costs them.

        Not file size: tiles are streamed, so the export does not grow by a byte.
        What it costs is the offline guarantee and the promise that opening the
        map contacts nobody - and unlike every other setting here, that lands on
        the recipient rather than on the person choosing it.
        """
        note = basemap_note(self.state.basemap)
        if note is None:
            self.basemap_warning.setText("")
            self.basemap_warning.setStyleSheet("")
            return

        self.basemap_warning.setText(note)
        # The one place a fixed colour is right: this is a warning, and warning
        # red has to read as red in both light and dark themes rather than
        # following the palette into something quiet.
        self.basemap_warning.setStyleSheet("color: #c0392b;")

    def _on_precision_changed(self) -> None:
        value = self.precision_combo.currentData()
        self.state.quantize_precision = value if isinstance(value, int) else None
        self._fidelity_is_stale = True

    def _on_name_changed(self, text: str) -> None:
        self.state.map_name = text

    def _on_mode_selected(self, mode: OutputMode) -> None:
        # The button group handles deselecting the others; this only records the
        # choice and refreshes what the summary says will be produced.
        self.state.output_mode = mode
        self._update_export_readiness()

    # ---- Layers tab -----------------------------------------------------

    def _build_layers_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Layers come from the QGIS Layers panel and follow it live - "
                "reorder or rename them there and this list updates. Expand a "
                "layer to choose how each of its fields appears in popups.",
                page,
            )
        )

        self.layer_tree = QTreeWidget(page)
        self.layer_tree.setColumnCount(4)
        self.layer_tree.setHeaderLabels(["Layer", "Include", "Popups", "Labels"])
        self.layer_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.layer_tree.itemChanged.connect(self._on_layer_item_changed)
        layout.addWidget(self.layer_tree)

        # Bulk action. Setting a mode field-by-field across a dozen layers is
        # exactly the tedium qgis2web users report, so the escape hatch is here
        # too - but as a button, because it is the one control on this tab that
        # overwrites every layer at once and should not fire on a stray scroll.
        bulk = QHBoxLayout()
        bulk.addWidget(QLabel("Set every field to", page))
        self.bulk_mode_combo = QComboBox(page)
        for mode, label in POPUP_MODE_LABELS.items():
            self.bulk_mode_combo.addItem(label, mode.value)
        self.bulk_mode_combo.setCurrentIndex(
            self.bulk_mode_combo.findData(PopupFieldMode.INLINE_WITH_DATA.value)
        )
        bulk.addWidget(self.bulk_mode_combo)

        apply_all = QPushButton("Apply to all layers", page)
        apply_all.setToolTip(
            "Give every popup field in every layer this setting, replacing the "
            "choices below."
        )
        apply_all.clicked.connect(self._on_apply_mode_to_all)
        bulk.addWidget(apply_all)
        bulk.addStretch(1)
        layout.addLayout(bulk)
        return page

    def refresh_layers(self) -> None:
        """Rebuild the list from the project.

        Settings survive because they live in `DialogState`, keyed by layer id -
        never in the widgets being discarded here. That is what lets the list
        follow QGIS without a refresh button that mutates settings.
        """
        # The layers changed, so any report already on screen describes a
        # project that no longer exists. Marked rather than recomputed: it is
        # only worth building when the user actually looks at it.
        self._fidelity_is_stale = True

        # Adding or removing a layer in QGIS changes the map, but it changes
        # nothing in `DialogState` unless that layer already had settings - so
        # the snapshot the live preview watches stays identical and the preview
        # silently goes stale. The watcher already coalesces bursts, so this is
        # one rebuild per change rather than one per signal.
        if getattr(self, "_server", None) is not None:
            self._rebuild_timer.start()

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

            self._add_field_rows(item, layer, settings)

        self.layer_tree.blockSignals(False)
        self._update_export_readiness()

    def _add_field_rows(
        self,
        parent: QTreeWidgetItem,
        layer: QgsMapLayer,
        settings: LayerSettings,
    ) -> None:
        """A popup-mode combo per attribute, behind the layer's disclosure arrow.

        One level of nesting, not two. qgis2web puts the same choice behind
        expand-layer *then* expand-"Popups", which is a large part of why its
        users never find it.
        """
        hidden = hidden_field_names(layer)
        layer_id = layer.id()

        self._add_layer_options_row(parent, layer_id, settings)

        for field_name in popup_field_names(layer):
            child = QTreeWidgetItem(parent)
            # The row spans every column and carries its own widgets. Putting the
            # combo in the "Include" column instead crops it to a checkbox's
            # width - the mode labels are sentences, and "Label beside" tells a
            # user nothing about what the rest of it said.
            child.setFirstColumnSpanned(True)
            # The name is drawn by the row widget, not by the item: item text
            # would show *through* the widget's transparent background. Kept in
            # UserRole so the row is still identifiable without reading widgets.
            child.setData(0, Qt.ItemDataRole.UserRole, field_name)
            # No check boxes on a field row: the mode combo is the whole choice,
            # and an inherited tristate box here would mean nothing.
            child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            row = QWidget(self.layer_tree)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            name_label = QLabel(field_name, row)
            name_label.setMinimumWidth(FIELD_NAME_COLUMN_WIDTH)
            row_layout.addWidget(name_label)

            combo = QComboBox(row)
            for mode, label in POPUP_MODE_LABELS.items():
                combo.addItem(label, mode.value)

            # With no explicit choice the combo must show what the export will
            # actually do, which for a column hidden in the QGIS attribute table
            # is "do not show" - not the global default.
            fallback = (
                PopupFieldMode.HIDDEN
                if field_name in hidden
                else PopupFieldMode.INLINE_WITH_DATA
            )
            index = combo.findData(settings.fields.get(field_name, fallback.value))
            combo.setCurrentIndex(index if index >= 0 else 0)

            # Connected only once the value is in place, so restoring saved
            # settings never registers as the user changing one.
            combo.currentIndexChanged.connect(
                lambda _index, lid=layer_id, name=field_name, box=combo: (
                    self._on_field_mode_changed(lid, name, box)
                )
            )
            row_layout.addWidget(combo, 1)
            row_layout.addStretch(1)
            self.layer_tree.setItemWidget(child, 0, row)

    def _add_layer_options_row(
        self, parent: QTreeWidgetItem, layer_id: str, settings: LayerSettings
    ) -> None:
        """Per-layer overrides of three map-wide settings.

        Every control starts on "Same as map", so a project that never touches
        this row behaves exactly as before. qgis2web has carried these three as
        global-only since 2015 - its issues #131, #132 and #133 each ask for
        this and each is still open - and the reason it matters is that "all
        layers alike except one" otherwise means configuring every layer.
        """
        item = QTreeWidgetItem(parent)
        item.setFirstColumnSpanned(True)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setData(0, Qt.ItemDataRole.UserRole, LAYER_OPTIONS_ROLE)

        row = QWidget(self.layer_tree)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        label = QLabel("This layer only", row)
        label.setMinimumWidth(FIELD_NAME_COLUMN_WIDTH)
        layout.addWidget(label)

        hover = QComboBox(row)
        for text, value in (
            ("Popups: same as map", None),
            ("Popups: on hover", True),
            ("Popups: on click", False),
        ):
            hover.addItem(text, value)
        hover.setCurrentIndex(max(0, hover.findData(settings.popup_on_hover)))
        hover.currentIndexChanged.connect(
            lambda _i, lid=layer_id, box=hover: self._on_layer_hover_changed(lid, box)
        )
        layout.addWidget(hover)

        precision = QComboBox(row)
        precision.addItem("Precision: same as map", None)
        precision.addItem("Precision: keep it all", PRECISION_FULL)
        for places in range(MIN_PRECISION, MAX_PRECISION + 1):
            precision.addItem(f"Precision: {places} decimal place(s)", places)
        precision.setCurrentIndex(
            max(0, precision.findData(settings.quantize_precision))
        )
        precision.currentIndexChanged.connect(
            lambda _i, lid=layer_id, box=precision: self._on_layer_precision_changed(
                lid, box
            )
        )
        layout.addWidget(precision)

        highlight = QgsColorButton(row)
        highlight.setAllowOpacity(True)
        highlight.setShowNull(True, "Highlight: same as map")
        highlight.setToolTip("Highlight colour for this layer only.")
        _apply_saved_color(highlight, settings.highlight_color or "")
        highlight.colorChanged.connect(
            lambda _c, lid=layer_id, button=highlight: self._on_layer_highlight_changed(
                lid, button
            )
        )
        layout.addWidget(highlight)

        layout.addStretch(1)
        self.layer_tree.setItemWidget(item, 0, row)

    def _on_layer_hover_changed(self, layer_id: str, combo: QComboBox) -> None:
        self.state.for_layer(layer_id).popup_on_hover = combo.currentData()
        self._fidelity_is_stale = True

    def _on_layer_precision_changed(self, layer_id: str, combo: QComboBox) -> None:
        self.state.for_layer(layer_id).quantize_precision = combo.currentData()
        self._fidelity_is_stale = True

    def _on_layer_highlight_changed(
        self, layer_id: str, button: QgsColorButton
    ) -> None:
        settings = self.state.for_layer(layer_id)
        color = button.color()
        if button.isNull() or color is None or not color.isValid():
            settings.highlight_color = None
            return
        settings.highlight_color = (
            f"#{color.red():02x}{color.green():02x}"
            f"{color.blue():02x}{color.alpha():02x}"
        )
        self._fidelity_is_stale = True

    def _on_field_mode_changed(
        self, layer_id: str, field_name: str, combo: QComboBox
    ) -> None:
        value = combo.currentData()
        if not isinstance(value, str):
            return
        self.state.for_layer(layer_id).fields[field_name] = value
        self._fidelity_is_stale = True

    def _on_apply_mode_to_all(self) -> None:
        value = self.bulk_mode_combo.currentData()
        if not isinstance(value, str):
            return

        for tree_layer in self.project.layerTreeRoot().findLayers():
            layer = tree_layer.layer()
            if layer is None:
                continue
            settings = self.state.for_layer(layer.id())
            for field_name in popup_field_names(layer):
                settings.fields[field_name] = value

        self.refresh_layers()

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

        chrome = QGroupBox("Map controls", page)
        chrome_layout = QVBoxLayout(chrome)
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
            chrome_layout.addWidget(check)
            self.widget_checks[key] = check
        layout.addWidget(chrome)

        layout.addWidget(self._build_caption_group(page))
        layout.addWidget(self._build_colors_group(page))
        layout.addWidget(self._build_behaviour_group(page))

        layout.addStretch(1)
        return page

    def _build_caption_group(self, page: QWidget) -> QWidget:
        """Title and description, drawn over the map.

        The project abstract is read on every export and, until now, thrown
        away - the incumbent does the same thing with a title set in Project
        Properties. Both are off by default because an unwanted caption over
        someone's map is worse than a missing one.
        """
        box = QGroupBox("Caption", page)
        form = QFormLayout(box)

        self.title_check = QCheckBox("Map title", box)
        self.title_check.setChecked(self.state.show_title)
        self.title_check.toggled.connect(self._on_title_toggled)
        form.addRow(self.title_check)
        form.addRow(
            "",
            _help_label(
                "Draws the map name over the map. The legend drops its own "
                "heading while this is on, so the title is not shown twice.",
                box,
            ),
        )

        self.abstract_check = QCheckBox("Project description", box)
        self.abstract_check.setChecked(self.state.show_abstract)
        self.abstract_check.toggled.connect(self._on_abstract_toggled)
        form.addRow(self.abstract_check)
        form.addRow(
            "",
            _help_label(
                "The abstract from Project Properties > Metadata. Nothing "
                "appears if the project has none.",
                box,
            ),
        )

        self.corner_combo = QComboBox(box)
        for corner, label in CAPTION_CORNER_LABELS.items():
            self.corner_combo.addItem(label, corner.value)
        index = self.corner_combo.findData(self.state.title_corner.value)
        self.corner_combo.setCurrentIndex(index if index >= 0 else 0)
        self.corner_combo.currentIndexChanged.connect(self._on_corner_changed)
        form.addRow("Position", self.corner_combo)
        form.addRow(
            "",
            _help_label(
                "All four corners already hold map controls - switcher top "
                "left, legend top right, zoom and scale bottom left, credit "
                "bottom right. The centres are the only clear space.",
                box,
            ),
        )
        return box

    def _build_colors_group(self, page: QWidget) -> QWidget:
        """Widget colours, as CSS custom properties the runtime already reads."""
        box = QGroupBox("Control colours", page)
        form = QFormLayout(box)

        self.background_button = QgsColorButton(box)
        self.background_button.setAllowOpacity(False)
        self.background_button.setShowNull(True, "Default")
        self.background_button.colorChanged.connect(
            lambda color: self._on_widget_color("widget_background", color)
        )
        _apply_saved_color(self.background_button, self.state.widget_background)
        form.addRow("Background", self.background_button)

        self.foreground_button = QgsColorButton(box)
        self.foreground_button.setAllowOpacity(False)
        self.foreground_button.setShowNull(True, "Default")
        self.foreground_button.colorChanged.connect(
            lambda color: self._on_widget_color("widget_foreground", color)
        )
        _apply_saved_color(self.foreground_button, self.state.widget_foreground)
        form.addRow("Text and icons", self.foreground_button)
        return box

    def _build_behaviour_group(self, page: QWidget) -> QWidget:
        box = QGroupBox("Behaviour", page)
        layout = QVBoxLayout(box)

        self.hover_check = QCheckBox("Open popups on hover instead of click", box)
        self.hover_check.setChecked(self.state.popup_on_hover)
        self.hover_check.setToolTip(
            "Hover replaces click rather than adding to it: bound together, a "
            "click on an already-open popup appears to do nothing."
        )
        self.hover_check.toggled.connect(self._on_hover_toggled)
        layout.addWidget(self.hover_check)
        layout.addWidget(
            _help_label(
                "Hover replaces click rather than adding to it.",
                box,
            )
        )

        # qgis2web has no control for this at all: it reuses the QGIS *editing
        # selection* colour as a web hover cue, opaque yellow out of the box, and
        # the only way to change it is Project Properties - outside the plugin.
        # Reported as far back as 2015 (qgis2web#132) and still open.
        highlight_row = QHBoxLayout()
        highlight_row.addWidget(QLabel("Highlight under the cursor", box))
        self.highlight_button = QgsColorButton(box)
        self.highlight_button.setAllowOpacity(True)
        self.highlight_button.setShowNull(True, "Default (white, see-through)")
        self.highlight_button.setToolTip(
            "Shown when the cursor is over a feature. Keep some transparency: a "
            "solid colour hides whatever the feature is drawn on top of."
        )
        self.highlight_button.colorChanged.connect(self._on_highlight_color)
        _apply_saved_color(self.highlight_button, self.state.highlight_color)
        highlight_row.addWidget(self.highlight_button)
        highlight_row.addStretch(1)
        layout.addLayout(highlight_row)
        return box

    def _on_widget_toggled(self, key: str, value: bool) -> None:
        attribute = {
            "legend": "show_legend",
            "layerSwitcher": "show_layer_switcher",
            "zoomControls": "show_zoom_controls",
            "scaleBar": "show_scale_bar",
        }[key]
        setattr(self.state, attribute, value)

    def _on_title_toggled(self, value: bool) -> None:
        self.state.show_title = value

    def _on_abstract_toggled(self, value: bool) -> None:
        self.state.show_abstract = value

    def _on_hover_toggled(self, value: bool) -> None:
        self.state.popup_on_hover = value

    def _on_corner_changed(self) -> None:
        value = self.corner_combo.currentData()
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                self.state.title_corner = OverlayCorner(value)

    def _on_widget_color(self, attribute: str, color: object) -> None:
        """A null colour means "leave the runtime's own default alone"."""
        if color is None or not color.isValid() or color.alpha() == 0:
            setattr(self.state, attribute, "")
            return
        setattr(self.state, attribute, color.name())

    def _on_highlight_color(self, color: object) -> None:
        """Kept in CSS order with alpha last, the form the manifest emits.

        A fully transparent pick is a real choice here - "no visible highlight" -
        so it cannot double as the null the other pickers use. The button's own
        null state carries "unset" instead.
        """
        if color is None or not color.isValid() or self.highlight_button.isNull():
            self.state.highlight_color = ""
            return
        self.state.highlight_color = (
            f"#{color.red():02x}{color.green():02x}"
            f"{color.blue():02x}{color.alpha():02x}"
        )

    # ---- Fidelity tab ---------------------------------------------------

    def _build_fidelity_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "What survives the export, and what does not - checked before "
                "you export, so nothing is left to discover later.",
                page,
            )
        )
        self.fidelity_tree = QTreeWidget(page)
        self.fidelity_tree.setColumnCount(3)
        self.fidelity_tree.setHeaderLabels(["Item", "Result", "Detail"])
        # The item names identify *which* layer a note is about, so eliding them
        # to "Data of 'al..." makes the report unreadable on a project with
        # several similarly-named layers. They size to their content; the detail
        # is what gets shortened, and it expands on click.
        header = self.fidelity_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.fidelity_tree.setWordWrap(True)
        layout.addWidget(self.fidelity_tree)
        return page

    def _show_fidelity(self, report: FidelityReportBuilder) -> None:
        self.fidelity_tree.clear()
        self._fidelity_is_stale = False
        self._update_fidelity_strip(report)
        for entry in sorted(report.items, key=lambda i: STATUS_ORDER[i.status]):
            item = QTreeWidgetItem(self.fidelity_tree)
            item.setText(0, entry.subject)
            item.setText(1, STATUS_LABELS[entry.status])
            item.setText(2, _summarise(entry.detail))
            item.setToolTip(2, entry.detail)

            # The full text goes on a child row rather than wrapping in place:
            # a dozen wrapped paragraphs is a wall, and the point of this tab is
            # that a user can scan it and then read the one that matters.
            if len(entry.detail) > DETAIL_SUMMARY_LENGTH:
                detail = QTreeWidgetItem(item)
                detail.setFirstColumnSpanned(True)
                detail.setText(0, entry.detail)
                detail.setFlags(Qt.ItemFlag.ItemIsEnabled)

    def _on_tab_changed(self, index: int) -> None:
        """Fill the Fidelity tab when the user opens it.

        The report used to appear only after a preview or an export, so a user
        checking what their map would lose *before* committing to one found an
        empty table - which reads as "nothing to report" rather than "not
        computed yet", the exact inversion this tab exists to prevent.

        Computed on open rather than on every layer change because building it
        reads every feature of every layer, which is far too expensive to run
        on each tick of a checkbox.
        """
        if self.tabs.tabText(index) != "Fidelity" or not self._fidelity_is_stale:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            _, report = self._read_current_project()
            self._show_fidelity(report)
        except Exception as exc:  # a broken project must not break the tab
            self.fidelity_tree.clear()
            item = QTreeWidgetItem(self.fidelity_tree)
            item.setText(0, "Report unavailable")
            item.setText(1, STATUS_LABELS[FidelityStatus.BLOCKED])
            item.setText(2, f"The project could not be read: {exc}")
            QgsMessageLog.logMessage(
                f"Fidelity preview failed:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.Warning,
            )
        finally:
            QApplication.restoreOverrideCursor()

    # ---- Help tab -------------------------------------------------------

    def _build_help_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        browser = QTextBrowser(page)
        # Markdown keeps one source of truth: the same files the website serves.
        browser.setDocument(render_help_document())
        browser.setOpenLinks(False)
        browser.anchorClicked.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(browser)

        docs_button = QPushButton("Open the full documentation", page)
        docs_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))
        layout.addWidget(docs_button)
        return page

    # ---- Buttons --------------------------------------------------------

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)

        # Remembered per machine. Ticking it does not open anything on its own -
        # the user asked for nothing to auto-launch a browser - it only decides
        # what Preview does when they press it.
        self.live_check = QCheckBox("Live preview", self)
        self.live_check.setChecked(QSettings().value(LIVE_PREVIEW_KEY, True, type=bool))
        self.live_check.setToolTip(
            "Serve the preview from this machine and reload the open tab as you "
            "change settings. Switch off to open the preview as a file instead."
        )
        self.live_check.toggled.connect(self._on_live_toggled)
        row.addWidget(self.live_check)

        self.preview_button = QPushButton("Preview", self)
        self.preview_button.clicked.connect(self.on_preview)
        row.addWidget(self.preview_button)

        self.export_button = QPushButton("Export", self)
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self.on_export)
        row.addWidget(self.export_button)

        # Enabled only once there is something to open. The file:// path is
        # exercised here, against the artifact that actually ships, rather than
        # against a preview copy of it.
        self.open_export_button = QPushButton("Open exported map", self)
        self.open_export_button.setEnabled(False)
        self.open_export_button.clicked.connect(self._on_open_export)
        self._last_export_path: Path | None = None
        row.addWidget(self.open_export_button)

        close = QPushButton("Close", self)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        return row

    # ---- Fidelity strip -------------------------------------------------

    def _build_fidelity_strip(self) -> QWidget:
        """What the export changes, visible from every tab.

        This plugin's one real advantage over the incumbent is telling you what
        your recipient loses. Putting that behind a tab means it is read after
        the decision it should have informed, if at all - so the count lives
        here, always on screen, and opens the detail when clicked.
        """
        strip = QWidget(self)
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)

        self.fidelity_summary = QLabel("", strip)
        # Derived from the palette rather than a hardcoded grey: this dialog sits
        # inside whatever Qt theme the user runs, light or dark, and a fixed
        # colour is unreadable in one of them.
        self.fidelity_summary.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        row.addWidget(self.fidelity_summary, 1)

        self.fidelity_link = QPushButton("What changes?", strip)
        self.fidelity_link.setFlat(True)
        self.fidelity_link.clicked.connect(self._show_fidelity_tab)
        row.addWidget(self.fidelity_link)
        return strip

    def _show_fidelity_tab(self) -> None:
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == "Fidelity":
                self.tabs.setCurrentIndex(index)
                return

    def _update_fidelity_strip(self, report) -> None:
        """Summarise the report in one line, problems first.

        Says nothing rather than "0 things change" when the export is clean:
        an always-present count trains people to ignore it, and the absence of a
        warning is itself the message.
        """
        items = list(getattr(report, "items", ()) or ())
        notable = [
            item
            for item in items
            if item.status
            in (
                FidelityStatus.BLOCKED,
                FidelityStatus.UNSUPPORTED,
                FidelityStatus.APPROXIMATED,
                FidelityStatus.RASTER_FALLBACK,
            )
        ]
        blocked = sum(1 for item in notable if item.status is FidelityStatus.BLOCKED)

        if blocked:
            self.fidelity_summary.setText(
                f"{blocked} layer{'s' if blocked != 1 else ''} cannot be exported."
            )
        elif notable:
            count = len(notable)
            self.fidelity_summary.setText(
                f"{count} thing{'s' if count != 1 else ''} change on export."
            )
        else:
            self.fidelity_summary.setText("")

        self.fidelity_link.setVisible(bool(items))

    # ---- Live preview ---------------------------------------------------

    def _on_live_toggled(self, enabled: bool) -> None:
        QSettings().setValue(LIVE_PREVIEW_KEY, enabled)
        if not enabled:
            self._stop_live_preview()

    def _on_open_export(self) -> None:
        if self._last_export_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_export_path)))

    def _poll_for_changes(self) -> None:
        """Restart the debounce whenever any setting differs from last build."""
        if self._shut_down or self._server is None:
            return
        current = self.state.snapshot()
        if current == self._last_snapshot:
            return
        self._last_snapshot = current
        self._rebuild_timer.start()

    def _rebuild_live_preview(self) -> None:
        """Rewrite the artifact and tell the open tab to reload.

        Reentrancy matters here: a rebuild runs the full production writer, and
        on a large project that is slower than the debounce. Without the guard a
        burst of edits would stack rebuilds on top of each other.
        """
        if self._shut_down or self._server is None or self._rebuilding:
            # Try again once the in-flight rebuild finishes, so the last edit is
            # never the one that gets dropped.
            if self._rebuilding and not self._shut_down:
                self._rebuild_timer.start()
            return

        self._rebuilding = True
        try:
            export, report = self._read_current_project()
            self._show_fidelity(report)
            write_preview(export, self._project_identity(), live=True)
            self._server.notify_reload()
            self.status_label.setText("Live preview updated.")
        except Exception:
            # A failed rebuild must not switch live preview off or spawn a modal
            # on every keystroke: the user is mid-edit and the next change may
            # well fix it. Report quietly and keep going.
            self.status_label.setText(
                "Live preview could not be updated - see the QGIS log."
            )
            QgsMessageLog.logMessage(
                f"Live preview rebuild failed:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.Warning,
            )
        finally:
            self._rebuilding = False

    def _stop_live_preview(self) -> None:
        self._watch_timer.stop()
        self._rebuild_timer.stop()
        server, self._server = self._server, None
        if server is not None:
            with contextlib.suppress(Exception):
                server.stop()

    def _project_identity(self) -> str:
        return self.project.fileName() or self.project.baseName() or "untitled"

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

        self._update_export_summary(len(selected))

    def _set_ready(self, ready: bool, message: str) -> None:
        self.export_button.setEnabled(ready)
        self.preview_button.setEnabled(ready)
        self.status_label.setText(message)

    # ---- Actions --------------------------------------------------------

    def _canvas_extent(self):
        """The QGIS view, for the "current view" extent choice.

        Read on every project read rather than cached: the user pans between
        opening the dialog and pressing Export, and the extent they meant is the
        one on screen when they pressed it.
        """
        if self.state.extent_source is not ExtentSource.CANVAS:
            return None
        canvas = getattr(self.iface, "mapCanvas", None)
        if canvas is None:
            return None
        try:
            return extent_from_canvas(canvas())
        except Exception:  # never let a canvas quirk block an export
            return None

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
            layer_settings=self.state.layers,
            canvas_extent=self._canvas_extent(),
        )
        # Licence caps are evaluated here, not left to the writer, because the
        # writer's verdict arrives after the file is on disk. A layer past the
        # free-tier cap renders nothing for the recipient, so the Fidelity tab
        # has to name it while the user can still do something about it.
        report_verdict(default_policy().evaluate(export), report)
        return export, report

    def _runtime_ready(self) -> bool:
        """Make sure the OnlyMap runtime is installed before building anything.

        Both preview and export need it: the artifact is inert markup until the
        runtime defines its custom elements, so a preview without one opens a
        blank page rather than a map. Asking here, before any work, means the
        user sees the licence and the download once - not a failure at the end
        of an export they thought had succeeded.
        """
        if ensure_runtime(self) is not None:
            return True
        self.status_label.setText(
            "The OnlyMap runtime is needed to build a map. Nothing was written."
        )
        return False

    def on_preview(self) -> None:
        """Open the preview: served from this machine, or as a file."""
        try:
            # Read and report *before* asking for the runtime. Reading the
            # project is what fills the Fidelity tab, and it needs no runtime -
            # so gating it behind the download left the tab blank whenever the
            # runtime was missing or the user declined, hiding the one thing
            # that would have told them what their map was going to lose.
            export, report = self._read_current_project()
            self._show_fidelity(report)

            if not self._runtime_ready():
                return

            identity = self._project_identity()
            live = self.live_check.isChecked()
            result = write_preview(export, identity, live=live)

            if not live:
                self._stop_live_preview()
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.entry_path)))
                self.status_label.setText(
                    "Preview opened. Reload the browser tab after making changes - "
                    "your position on the map is kept."
                )
                return

            self._last_snapshot = self.state.snapshot()
            try:
                url = self._ensure_server(preview_directory(identity))
            except OSError:
                # A refused port is a local firewall or a sandbox, not a bug in
                # the map. The artifact is already written, so fall back to
                # opening it directly rather than losing the preview entirely.
                self._fall_back_to_file_preview(result.entry_path)
                return

            QDesktopServices.openUrl(QUrl(url))
            self._watch_timer.start()
            self.status_label.setText(
                "Live preview open. Changes here update the tab automatically."
            )
        except Exception as exc:
            self._report_failure("Preview failed", exc)

    def _fall_back_to_file_preview(self, entry_path: Path) -> None:
        """Open the preview as a file, and say why it is not live.

        Deliberately not a modal: the user asked for a preview and there is one,
        so interrupting them to explain a downgrade they did not choose would be
        worse than the downgrade. The checkbox is cleared so the state on screen
        matches what is actually happening.
        """
        QgsMessageLog.logMessage(
            f"Could not start the live preview server:\n{traceback.format_exc()}",
            LOG_TAG,
            level=Qgis.Warning,
        )
        self._stop_live_preview()
        with contextlib.suppress(Exception):
            # Reflect reality without re-entering `_on_live_toggled`'s teardown.
            self.live_check.blockSignals(True)
            self.live_check.setChecked(False)
            self.live_check.blockSignals(False)

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry_path)))
        self.status_label.setText(
            "Live preview could not start on this machine, so the preview opened "
            "as a file. Reload the tab after making changes."
        )

    def _ensure_server(self, root: Path) -> str:
        """Start the preview server if it is not already running.

        Rebound if the project identity changed, because the preview directory
        is keyed by project and a stale root would serve the previous map.
        """
        if self._server is not None:
            if self._server.root == root:
                return self._server.url
            self._stop_live_preview()

        server = PreviewServer(root)
        url = server.start()
        self._server = server
        return url

    def on_export(self) -> None:
        """Write the artifact wherever the user asks for it."""
        try:
            # Reading fills the Fidelity tab and needs no runtime; see on_preview.
            export, report = self._read_current_project()
            self._show_fidelity(report)

            # A blocked item means a layer could not be read at all. Exporting
            # anyway writes a map that is quietly missing data while the dialog
            # reports success, and the Processing algorithm already refuses on
            # the same input - the two entry points must not disagree.
            if not export.is_exportable:
                self._warn_not_exportable(export)
                return

            # After the blocking checks: no point downloading 3 MB for a project
            # that was never going to export.
            if not self._runtime_ready():
                return

            mode = self.state.output_mode
            # Issue #29: never quietly hand over a single file that will not
            # travel. If Standalone HTML is not eligible, say exactly why and
            # move the selection to the next viable tier - the user can still
            # override it, but not by accident.
            if mode is OutputMode.STANDALONE_HTML:
                reason = standalone_ineligible_reason(export)
                if reason is not None:
                    self._on_mode_selected(OutputMode.SHARE_ZIP)
                    QMessageBox.information(
                        self,
                        "Switched to Share ZIP",
                        reason
                        + "\n\nShare ZIP is now selected. Choose Standalone HTML "
                        "again if you want the single file anyway.",
                    )
                    return

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

            result, outcome = build_artifact(export, Path(chosen), mode=mode)
            save_state(self.project, self.state)

            # The file:// check happens here, against the bytes that ship.
            self._last_export_path = Path(outcome.path)
            self.open_export_button.setEnabled(True)

            self.status_label.setText(outcome.summary())
            # The writer's warnings (licence caps, a runtime that does not match
            # the lock, an oversized single file) are things the recipient will
            # experience. Dropping them here is how a user ends up handing over
            # a map with layers missing and no idea it happened.
            summary = outcome.summary()
            if result.warnings:
                QMessageBox.warning(
                    self,
                    "Exported, with warnings",
                    summary
                    + "\n\nThe exported map has known limitations:\n\n"
                    + "\n".join(f"- {warning}" for warning in result.warnings)
                    + "\n\nThe Fidelity tab lists the detail.",
                )
            else:
                QMessageBox.information(self, "Export complete", summary)

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

    def _warn_not_exportable(self, export) -> None:
        """Explain a refusal in terms of what the user has to fix in QGIS."""
        reasons = [item.detail for item in export.blocking_items] or [
            "No layer in the selection could be read."
        ]
        QMessageBox.warning(
            self,
            "Cannot export",
            "This map cannot be exported yet:\n\n"
            + "\n".join(f"- {reason}" for reason in reasons)
            + "\n\nFix these in QGIS, then export again. The Fidelity tab lists "
            "everything that was checked.",
        )

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

    def _shutdown(self) -> None:
        """Persist settings and drop every signal connection.

        A rebuild scheduled against a destroyed dialog is a crash, and the user
        experiences that as QGIS vanishing.

        Idempotent: reached from both `finished` and `closeEvent`, and a
        window-manager close fires both.
        """
        if self._shut_down:
            return
        self._shut_down = True

        try:
            save_state(self.project, self.state)
        except Exception:
            QgsMessageLog.logMessage(
                f"Could not save export settings:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.Warning,
            )
        # Before the watcher, because a rebuild triggered mid-teardown would run
        # against a half-disconnected dialog.
        self._stop_live_preview()
        self.watcher.disconnect_all()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._shutdown()
        super().closeEvent(event)
