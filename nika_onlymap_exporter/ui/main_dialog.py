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
from qgis.PyQt.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QPalette,
    QTextDocument,
)
from qgis.PyQt.QtWidgets import (
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
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
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
from ..core.license_policy import (
    default_policy,
    describe_license_key,
    detect_violations,
    looks_like_license_key,
    report_verdict,
)
from ..core.manifest_builder import basemap_note, terrain_note
from ..core.popup_translator import hidden_field_names, popup_field_names
from ..core.project_reader import extent_from_canvas, read_project, resolve_title
from ..core.settings import (
    MAX_PRECISION,
    MIN_PRECISION,
    PRECISION_FULL,
    DialogState,
    LayerSettings,
    load_license_key,
    load_state,
    save_license_key,
    save_state,
)
from ..packaging.artifact_builder import build_artifact
from ..packaging.dependency_scanner import (
    SINGLE_FILE_WARN_BYTES,
    measure_data_bytes,
    standalone_ineligible_reason,
)
from ..writers.onlymap_writer import ExportBlockedError, OnlyMapWriter
from .background_job import BackgroundJob, Progress
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

# The folder the last export was written to. QSettings for the same reason as
# the live-preview flag: "where I keep my maps" is a property of the person, not
# of the project, so it should follow them into the next one.
LAST_EXPORT_DIR_KEY = "qgis2webmap/lastExportDir"

# Small enough that the dialog fits a laptop screen in landscape. Without an
# explicit floor Qt takes the tallest tab's size hint as the minimum, and on
# Windows that made the window impossible to shorten - it could be grown and
# never shrunk. The tabs scroll, so a short window loses nothing.
MINIMUM_DIALOG_HEIGHT = 320
MINIMUM_DIALOG_WIDTH = 720

# What the dialog opens at when the screen has room for it.
DEFAULT_DIALOG_WIDTH = 960
DEFAULT_DIALOG_HEIGHT = 640

# How much of the progress bar the reading stage owns. Reading every feature of
# every layer dominates the wall clock; writing and packaging share the rest, so
# the bar keeps moving rather than sitting at 100% through the slow part.
READ_SHARE = 70

# How long closing the dialog waits for a running job to notice it was
# cancelled. Long enough for a layer of ordinary size to finish, short enough
# that a wedged data source cannot make Close appear to do nothing.
SHUTDOWN_WAIT_MS = 5000

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

# Relief, not a backdrop, so it sits in the same group as the basemap but on its
# own row. One entry beyond "off": the runtime's other preset needs a MapTiler
# key, which an exported file would have to carry where anyone can read it.
TERRAIN_LABELS = {
    "none": "Flat - no elevation",
    "terrarium": "Global relief - tilts the map so it shows",
}

# A short list rather than a spinner: the useful range is narrow, and naming the
# sizes means nobody has to reason about what 1.35 looks like.
CHROME_SCALE_LABELS = {
    1.0: "Normal",
    0.85: "Small",
    1.25: "Large",
    1.5: "Extra large",
    2.0: "Largest - for presentations and big screens",
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


def _scrollable(page: QWidget) -> QScrollArea:
    """Wrap a tab so the window can be shorter than its contents.

    The Map and Appearance tabs stack several group boxes, and Qt makes a
    dialog's minimum height the largest of its pages' size hints - so those two
    set a floor the user could not get below, and the window would grow but
    never shrink. Scrolling the page instead means the window is free to be any
    height and nothing is unreachable at the small end.
    """
    area = QScrollArea()
    area.setWidget(page)
    area.setWidgetResizable(True)
    # No frame: a sunken border around a whole tab reads as a nested panel, and
    # the scroll area is meant to be invisible until it is needed.
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    return area


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
        self.setMinimumSize(MINIMUM_DIALOG_WIDTH, MINIMUM_DIALOG_HEIGHT)
        self._resize_to_fit_screen()

        # ---- Background work ---------------------------------------------
        # Set up before the tabs, because building a tab can already ask for a
        # read. One job at a time: the pipeline reads the live QGIS project, and
        # two reads racing over the same layers is exactly the crash this
        # dialog's fourth rule exists to prevent.
        self._job: BackgroundJob | None = None
        self._cached_export = None
        self._cached_report = None
        self._cached_signature: str | None = None
        # Captured on this thread before each job starts; see `_ensure_export`.
        self._pending_canvas_extent = None
        self._pending_license_key: str | None = None

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(_scrollable(self._build_map_tab()), "Map")
        self.tabs.addTab(self._build_layers_tab(), "Layers")
        self.tabs.addTab(_scrollable(self._build_appearance_tab()), "Appearance")
        self.tabs.addTab(self._build_fidelity_tab(), "Fidelity")
        self.tabs.addTab(self._build_help_tab(), "Help")
        self._fidelity_is_stale = True
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)
        layout.addWidget(self._build_fidelity_strip())
        layout.addWidget(self._build_progress_row())
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

    def _resize_to_fit_screen(self) -> None:
        """Open at a comfortable size, but never taller than the screen allows.

        A fixed 960x640 put the foot of the dialog - the Export button - behind
        the Windows taskbar on a laptop screen, and because the window could not
        be shortened either, there was no way to reach it. `availableGeometry`
        excludes the taskbar and any other reserved strip, so this asks for what
        actually fits.
        """
        width, height = DEFAULT_DIALOG_WIDTH, DEFAULT_DIALOG_HEIGHT

        available = None
        with contextlib.suppress(Exception):
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()

        if available is not None:
            # A margin so the window is visibly inside the work area rather than
            # flush against its edges, which reads as clipped.
            width = min(width, max(MINIMUM_DIALOG_WIDTH, available.width() - 80))
            height = min(height, max(MINIMUM_DIALOG_HEIGHT, available.height() - 80))

        self.resize(width, height)

    # ---- Map tab --------------------------------------------------------

    def _build_map_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        # Where the data comes from, said out loud. There is no source picker
        # because there is no source to pick - the export is always of the
        # project already open - but "no control" and "the control is missing"
        # look identical to someone opening the dialog for the first time, and
        # testing had people hunting the tabs for a file chooser.
        source_note = QLabel(
            "<b>Source:</b> the layers already open in this QGIS project. There "
            "is nothing to browse for - choose which of them to include on the "
            "Layers tab.",
            page,
        )
        source_note.setWordWrap(True)
        layout.addWidget(source_note)

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
        form.addRow(
            "",
            _help_label(
                "Shown on the map and in the browser tab. Leave blank to use "
                "the project title.",
                page,
            ),
        )
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

        # The size rule, next to the choice it constrains. It used to appear
        # only as a modal at the end of an export, which is both too late to
        # inform the decision and - because the modal switched the selection
        # without the radio buttons following - left the dialog claiming
        # Standalone HTML while it built a zip.
        self.size_note = QLabel("", self.mode_box)
        self.size_note.setWordWrap(True)
        self.size_note.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        mode_layout.addWidget(self.size_note)
        layout.addWidget(self.mode_box)

        # ---- Destination -------------------------------------------------
        # Previously the only way to choose a location was a file dialog that
        # appeared after pressing Export, so until then the dialog showed no
        # sign that the location was the user's to pick at all. Visible field,
        # remembered between exports, Browse for the picker.
        destination_box = QGroupBox("Where to save it", page)
        destination_layout = QVBoxLayout(destination_box)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(destination_box)
        self.path_edit.setPlaceholderText("Choose where the exported map is written")
        self.path_edit.textChanged.connect(self._on_path_edited)
        path_row.addWidget(self.path_edit, 1)

        browse = QPushButton("Browse...", destination_box)
        browse.clicked.connect(self._on_browse_destination)
        path_row.addWidget(browse)
        destination_layout.addLayout(path_row)

        destination_layout.addWidget(
            _help_label(
                "Remembered for next time. Leave it blank and Export asks where "
                "to put the file, as before.",
                destination_box,
            )
        )
        layout.addWidget(destination_box)
        layout.addWidget(self._build_license_group(page))

        # Its own group rather than a row inside "Data": a basemap is not the
        # project's data, it is the backdrop behind it - and it is the only
        # setting whose consequence lands on the recipient, so it should be
        # found rather than come across.
        basemap_box = QGroupBox("Basemap", page)
        basemap_form = QFormLayout(basemap_box)

        self.basemap_combo = QComboBox(basemap_box)
        for value, label in BASEMAP_LABELS.items():
            self.basemap_combo.addItem(label, value)
        basemap_index = self.basemap_combo.findData(self.state.basemap)
        self.basemap_combo.setCurrentIndex(basemap_index if basemap_index >= 0 else 0)
        self.basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        basemap_form.addRow("Show behind the map", self.basemap_combo)

        # Warning rather than help: this is the only setting that changes what
        # the *recipient's* machine does, and it cannot be undone after sending.
        self.basemap_warning = QLabel("", basemap_box)
        self.basemap_warning.setWordWrap(True)
        basemap_form.addRow("", self.basemap_warning)
        self._update_basemap_warning()

        # In the same group because it costs the recipient the same thing -
        # tiles fetched from a third party on every open - and separating them
        # would mean explaining that cost twice.
        self.terrain_combo = QComboBox(basemap_box)
        for value, label in TERRAIN_LABELS.items():
            self.terrain_combo.addItem(label, value)
        terrain_index = self.terrain_combo.findData(self.state.terrain)
        self.terrain_combo.setCurrentIndex(terrain_index if terrain_index >= 0 else 0)
        self.terrain_combo.currentIndexChanged.connect(self._on_terrain_changed)
        basemap_form.addRow("Ground surface", self.terrain_combo)

        self.terrain_warning = QLabel("", basemap_box)
        self.terrain_warning.setWordWrap(True)
        basemap_form.addRow("", self.terrain_warning)
        self._update_terrain_warning()
        layout.addWidget(basemap_box)

        data_box = QGroupBox("Data", page)
        data_form = QFormLayout(data_box)

        self.extent_combo = QComboBox(data_box)
        for source, label in EXTENT_LABELS.items():
            self.extent_combo.addItem(label, source.value)
        extent_index = self.extent_combo.findData(self.state.extent_source.value)
        self.extent_combo.setCurrentIndex(extent_index if extent_index >= 0 else 0)
        self.extent_combo.currentIndexChanged.connect(self._on_extent_changed)
        data_form.addRow("Open the map on", self.extent_combo)
        data_form.addRow(
            "",
            _help_label(
                "Where the map is positioned when someone opens it. They can "
                "still pan and zoom anywhere afterwards.",
                data_box,
            ),
        )

        # A separate control from the one above, because they are separate
        # decisions: that one frames the map, this one decides what is in it.
        # Folding them together would mean a user who wanted to open on their
        # working view silently shipped a map missing everything outside it.
        self.clip_check = QCheckBox("Export only the features in this view", data_box)
        self.clip_check.setChecked(self.state.clip_to_extent)
        self.clip_check.toggled.connect(self._on_clip_toggled)
        data_form.addRow("", self.clip_check)
        data_form.addRow(
            "",
            _help_label(
                "Leaves out everything outside the current QGIS view. This is "
                "the practical way to bring a very large layer under the free "
                "plan's 25,000-feature limit. The Fidelity tab reports how many "
                "features each layer loses.",
                data_box,
            ),
        )

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

        # Last, because filling the path field emits `textChanged`, whose slot
        # updates the summary label created just above. An exception raised in a
        # Qt slot does not propagate - PyQt aborts the process - so a handler
        # that runs before the widget it touches exists takes QGIS down.
        self._suggest_destination()
        self._update_size_note()
        return page

    # ---- Licence ---------------------------------------------------------

    def _build_license_group(self, page: QWidget) -> QWidget:
        """Somewhere to put a key the user has already paid for.

        Without this the plugin could only ever produce free-plan maps, which
        for a paying customer means their five-layer, 25,000-row limits stay in
        force in a product they bought their way out of. The plumbing already
        existed all the way to the `license-key` attribute on `<om-map>` - the
        only missing piece was a field.
        """
        box = QGroupBox("OnlyMap licence", page)
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.license_edit = QLineEdit(load_license_key(), box)
        self.license_edit.setPlaceholderText(
            "om_live_...  (leave blank for the free plan)"
        )
        # Not a password field. The key is signed and domain-locked, is meant to
        # be served to browsers, and is written into every map exported with it -
        # so masking it would imply a secrecy it does not have, while making it
        # impossible to check a paste against the invoice it came from.
        self.license_edit.textChanged.connect(self._on_license_changed)
        row.addWidget(self.license_edit, 1)
        layout.addLayout(row)

        self.license_note = QLabel("", box)
        self.license_note.setWordWrap(True)
        layout.addWidget(self.license_note)

        layout.addWidget(
            _help_label(
                "Lifts the 5-layer and 25,000-feature limits and removes the "
                "on-map badge. Stored on this computer, not in the project file, "
                "so it follows you between projects and is never sent with one.",
                box,
            )
        )
        self._update_license_note()
        return box

    def _on_license_changed(self, text: str) -> None:
        save_license_key(text)
        self._update_license_note()
        # The key changes what the runtime will render, so a cached read's
        # verdict - and the fidelity report built from it - is no longer right.
        self._invalidate_export_cache()
        self._fidelity_is_stale = True

    def _license_key(self) -> str | None:
        """The key to export with, or None for the free plan."""
        text = self.license_edit.text().strip() if hasattr(self, "license_edit") else ""
        return text or None

    def _writer(self) -> OnlyMapWriter:
        """A writer carrying the user's licence policy.

        Built per use rather than held: the key can change between one export
        and the next, and a writer holding a stale policy would keep writing
        free-plan maps for someone who had just pasted a key.
        """
        return OnlyMapWriter(license_policy=default_policy(self._license_key()))

    def _update_license_note(self) -> None:
        """Say what the pasted key claims, and where it will not work.

        Read from the payload, never verified - we have no private key and the
        runtime does the real check. Saying "valid" would be a promise we cannot
        keep; saying what the key says about itself is one we can.
        """
        text = self.license_edit.text().strip()
        if not text:
            self.license_note.setText(
                "No key - exports run on the free plan: 5 layers, 25,000 "
                "features per layer."
            )
            self.license_note.setStyleSheet("")
            return

        if not looks_like_license_key(text):
            self.license_note.setText(
                "That does not look like an OnlyMap key. They start with "
                "om_live_ and contain a full stop. The map would fall back to "
                "the free plan."
            )
            self.license_note.setStyleSheet("color: #c0392b;")
            return

        info = describe_license_key(text)
        if info.malformed:
            self.license_note.setText(
                "The key is the right shape but its contents could not be read. "
                "Check it was pasted in full."
            )
            self.license_note.setStyleSheet("color: #c0392b;")
            return

        if info.is_expired:
            self.license_note.setText(
                "This key has expired, so exports will run on the free plan. "
                "Renew it at nikaplanet.com/onlymap."
            )
            self.license_note.setStyleSheet("color: #c0392b;")
            return

        parts = []
        if info.plan:
            parts.append(f"{info.plan} plan")
        if info.domains:
            parts.append("valid on " + ", ".join(info.domains))
        summary = "Key accepted - " + ("; ".join(parts) if parts else "limits lifted")

        # The one thing that surprises everybody: a key issued for a domain does
        # nothing for a file someone double-clicks, because a file:// page has
        # no hostname to match against.
        if not info.covers_local_files:
            self.license_note.setText(
                f"{summary}.\nThis key only applies where the map is served from "
                "one of those domains. A Standalone HTML file opened by "
                "double-clicking has no domain, so it falls back to the free "
                "plan - host the map, or ask NIKA for a key that covers local "
                "files."
            )
            self.license_note.setStyleSheet("color: #c0392b;")
            return

        self.license_note.setText(f"{summary}, including local files.")
        self.license_note.setStyleSheet("")

    def _update_export_summary(self, selected_count: int) -> None:
        """Name the artifact in the same words the buttons use.

        Guarded because it is reachable from a `textChanged` slot, which can
        fire while the Map tab is still being built. See `_build_map_tab`.
        """
        if not hasattr(self, "export_summary"):
            return
        if not selected_count:
            self.export_summary.setText("")
            return

        produced = {
            OutputMode.STANDALONE_HTML: "one HTML file that opens by double-click",
            OutputMode.SHARE_ZIP: "a zip to email or upload",
            OutputMode.FOLDER: "a folder to publish to a web server",
        }[self.state.output_mode]
        layers = f"{selected_count} layer{'s' if selected_count != 1 else ''}"
        sentence = f"Export writes {produced}, carrying {layers}."

        # Naming the destination here is what makes the path field's effect
        # visible without pressing anything.
        destination = getattr(self, "path_edit", None)
        chosen = destination.text().strip() if destination is not None else ""
        if chosen:
            sentence += f"\nTo: {chosen}"
        self.export_summary.setText(sentence)

    def _on_extent_changed(self) -> None:
        value = self.extent_combo.currentData()
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                self.state.extent_source = ExtentSource(value)
        self._fidelity_is_stale = True

    def _on_chrome_scale_changed(self) -> None:
        value = self.scale_combo.currentData()
        self.state.chrome_scale = float(value) if value is not None else 1.0

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

    def _on_terrain_changed(self) -> None:
        value = self.terrain_combo.currentData()
        self.state.terrain = value if isinstance(value, str) else "none"
        self._update_terrain_warning()
        self._fidelity_is_stale = True

    def _update_terrain_warning(self) -> None:
        """The same warning shape as the basemap, for the same reason."""
        note = terrain_note(self.state.terrain)
        if note is None:
            self.terrain_warning.setText("")
            self.terrain_warning.setStyleSheet("")
            return

        self.terrain_warning.setText(note)
        self.terrain_warning.setStyleSheet("color: #c0392b;")

    def _on_clip_toggled(self, value: bool) -> None:
        self.state.clip_to_extent = value
        self._fidelity_is_stale = True

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
        # Keep the radio buttons honest even when the mode was changed in code
        # rather than by a click - the mismatch between "Standalone HTML is
        # selected" and "a zip was written" came from exactly this gap.
        button = self.mode_checks.get(mode)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self._suggest_destination()
        self._update_size_note()
        self._update_export_readiness()

    # ---- Destination ----------------------------------------------------

    def _mode_suffix(self, mode: OutputMode) -> str:
        """The extension the chosen packaging produces. A folder has none."""
        return {
            OutputMode.STANDALONE_HTML: ".html",
            OutputMode.SHARE_ZIP: ".zip",
            OutputMode.FOLDER: "",
        }[mode]

    def _default_basename(self) -> str:
        """Name the file after the map, falling back to the project."""
        name = (self.state.map_name or "").strip() or Path(
            self._project_identity()
        ).stem
        # Anything a file system will argue about becomes an underscore. A map
        # called "Site A / B" must not silently write into a subdirectory.
        cleaned = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
        return cleaned or "map"

    def _on_path_edited(self, _text: str) -> None:
        """Nothing to store on the state - the folder is saved after an export.

        Kept as a slot so the summary at the foot of the tab follows what is
        typed rather than only what was browsed to.
        """
        self._update_export_summary(
            len(self.state.selected_layer_ids(self._available_layer_ids()))
        )

    def _suggest_destination(self) -> None:
        """Fill the path field with the last folder used and a matching name.

        Only ever *suggests*: a path the user typed or browsed to during this
        session keeps its directory, and only the extension is corrected to
        match the packaging they chose. Rewriting their choice because they
        clicked a different radio button would be the dialog arguing with them.
        """
        if not hasattr(self, "path_edit"):
            return

        suffix = self._mode_suffix(self.state.output_mode)
        current = self.path_edit.text().strip()

        if current:
            path = Path(current)
            # A folder export names a directory, so any extension is dropped.
            updated = path.with_suffix(suffix) if suffix else path.with_suffix("")
            if str(updated) != current:
                self.path_edit.setText(str(updated))
            return

        directory = QSettings().value(LAST_EXPORT_DIR_KEY, "", type=str)
        if not directory or not Path(directory).is_dir():
            directory = str(Path.home())
        self.path_edit.setText(
            str(Path(directory) / f"{self._default_basename()}{suffix}")
        )

    def _on_browse_destination(self) -> None:
        """The file picker, now reachable before pressing Export."""
        mode = self.state.output_mode
        current = self.path_edit.text().strip()
        start = current or str(Path.home())

        if mode is OutputMode.FOLDER:
            chosen = QFileDialog.getExistingDirectory(self, "Export to folder", start)
        else:
            filter_text = {
                OutputMode.STANDALONE_HTML: "Web page (*.html)",
                OutputMode.SHARE_ZIP: "Zip archive (*.zip)",
            }[mode]
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Export map", start, filter_text
            )
        if chosen:
            self.path_edit.setText(chosen)

    def _remember_destination(self, path: Path) -> None:
        """Store the folder, never the file name.

        The next export is usually a different map into the same place, so
        carrying the whole path forward would mean the default is always the
        name of the map before it.
        """
        directory = path if path.is_dir() else path.parent
        with contextlib.suppress(Exception):
            QSettings().setValue(LAST_EXPORT_DIR_KEY, str(directory))

    # ---- Size ------------------------------------------------------------

    def _update_size_note(self) -> None:
        """Say what the chosen packaging costs, using a real figure when we have one.

        A standalone file inlines every feature as base64 in the HTML, so its
        size is the data's size plus the runtime - there is no sibling folder to
        offload to, which is the whole point of the format and also why it grows
        so fast. Without a completed read there is no number to show, so the
        rule is stated on its own rather than guessed at.
        """
        if not hasattr(self, "size_note"):
            return

        limit_mb = SINGLE_FILE_WARN_BYTES // 1024 // 1024
        if self.state.output_mode is not OutputMode.STANDALONE_HTML:
            self.size_note.setText("")
            self.size_note.setStyleSheet("")
            return

        base = (
            f"Standalone HTML inlines every feature into the one file, so it is "
            f"the largest of the three. Over about {limit_mb} MB most mail "
            f"services reject it as an attachment."
        )

        export = self._cached_export
        if export is None:
            self.size_note.setText(base)
            self.size_note.setStyleSheet("")
            return

        data_bytes = measure_data_bytes(export)
        megabytes = data_bytes / 1024 / 1024
        if data_bytes > SINGLE_FILE_WARN_BYTES:
            self.size_note.setText(
                f"This map's data is {megabytes:.0f} MB, over the {limit_mb} MB "
                "an attachment usually survives. Export still writes the single "
                "file if you want it - Share ZIP is the practical choice."
            )
            self.size_note.setStyleSheet("color: #c0392b;")
        else:
            self.size_note.setText(f"{base}\nThis map's data is {megabytes:.1f} MB.")
            self.size_note.setStyleSheet("")

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

        # What the three columns do, in terms of what the *recipient* loses.
        # The headers name them but not their consequence, and testing found
        # nobody could say what unticking one would produce - so the boxes went
        # untouched rather than used. Stated on screen rather than in tooltips:
        # a tooltip is only read by someone who already suspects it is there.
        layout.addWidget(
            _help_label(
                "<b>Include</b> - untick to leave the layer out of the map "
                "entirely. Its data is not written, so the file gets smaller.<br>"
                "<b>Popups</b> - untick and clicking a feature does nothing. "
                "The attribute values are left out of the file entirely, so "
                "this is also how you keep data out of a map you are sending "
                "someone.<br>"
                "<b>Labels</b> - untick to drop the text QGIS draws beside "
                "features. The features themselves still appear.<br>"
                "Unticking Popups or Labels only affects that layer; the map "
                "still works, with less on it. Expand a layer to hide single "
                "fields instead - those values leave the file too, unless the "
                "map draws with them.",
                page,
            )
        )

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

        # The cache is keyed on this dialog's settings, which a change in QGIS
        # does not touch - so a layer added, removed or restyled over there
        # would otherwise be served from a read taken before it happened.
        self._invalidate_export_cache()

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
        self.scale_combo = QComboBox(box)
        for value, label in CHROME_SCALE_LABELS.items():
            self.scale_combo.addItem(label, value)
        scale_index = self.scale_combo.findData(self.state.chrome_scale)
        self.scale_combo.setCurrentIndex(scale_index if scale_index >= 0 else 0)
        self.scale_combo.currentIndexChanged.connect(self._on_chrome_scale_changed)
        form.addRow("Size", self.scale_combo)
        form.addRow(
            "",
            _help_label(
                "Scales the legend, layer switcher, zoom controls, scale bar "
                "and title together. The credit stays fixed so attribution "
                "cannot be shrunk out of legibility.",
                box,
            ),
        )
        form.addRow(
            "",
            _help_label(
                "Applies to the legend, layer switcher, zoom controls and "
                "scale bar. Leave both unset to keep the map's own styling.",
                box,
            ),
        )
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

    def _show_fidelity_error(self, message: str) -> None:
        """Leave the tab saying why it is empty, never just empty."""
        self.fidelity_tree.clear()
        item = QTreeWidgetItem(self.fidelity_tree)
        item.setText(0, "Report unavailable")
        item.setText(1, STATUS_LABELS[FidelityStatus.BLOCKED])
        item.setText(2, f"The project could not be read: {message}")

    def _on_tab_changed(self, index: int) -> None:
        """Fill the Fidelity tab when the user opens it.

        The report used to appear only after a preview or an export, so a user
        checking what their map would lose *before* committing to one found an
        empty table - which reads as "nothing to report" rather than "not
        computed yet", the exact inversion this tab exists to prevent.

        Computed on open rather than on every layer change because building it
        reads every feature of every layer, which is far too expensive to run
        on each tick of a checkbox.

        On a worker thread since the tab was reported taking 10-15 seconds with
        the whole window frozen behind it - long enough that the plugin looked
        hung. The tab now fills when the read lands, with the bar showing which
        layer it is on.
        """
        if self.tabs.tabText(index) != "Fidelity" or not self._fidelity_is_stale:
            return

        # A placeholder rather than an empty table: the whole reason this tab
        # computes on open is that "empty" reads as "nothing to report".
        self.fidelity_tree.clear()
        pending = QTreeWidgetItem(self.fidelity_tree)
        pending.setText(0, "Checking...")
        pending.setText(2, "Reading the project to see what the export changes.")

        self._ensure_export(lambda _export, _report: None, "Checking the project...")

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

    # ---- Progress -------------------------------------------------------

    def _build_progress_row(self) -> QWidget:
        """A bar and a way out, for work that takes longer than a blink.

        Hidden until something is running. An always-present bar sitting at zero
        is noise, and the point of this row is that its appearance means "this
        is working, it has not crashed" - which is precisely what the dialog
        could not say while it froze.
        """
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        self.progress_bar = QProgressBar(row)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar, 1)

        self.cancel_button = QPushButton("Cancel", row)
        self.cancel_button.clicked.connect(self._on_cancel_job)
        layout.addWidget(self.cancel_button)

        row.setVisible(False)
        self.progress_row = row
        return row

    def _start_job(
        self,
        work,
        on_success,
        label: str,
        quiet: bool = False,
    ) -> bool:
        """Run `work` on a worker thread, `on_success` back on this one.

        Returns False if something is already running. One job at a time is a
        hard rule: the work reads the live QGIS project, and two threads walking
        the same layers is the kind of fault that takes QGIS down rather than
        just failing.

        `quiet` reports a failure on the status line instead of in a modal. The
        live preview rebuilds on every edit, so a modal there would mean a
        dialog per keystroke for a user who is mid-edit and whose next change
        may well fix it.
        """
        if self._job is not None:
            self.status_label.setText("Still working on the last request.")
            return False

        job = BackgroundJob(work, self)
        self._job = job
        job.progressed.connect(self._on_job_progress)
        job.failed.connect(
            self._on_job_failed_quietly if quiet else self._on_job_failed
        )
        job.cancelled.connect(self._on_job_cancelled)
        job.succeeded.connect(lambda result: self._on_job_succeeded(result, on_success))
        # Qt frees the thread object once it has actually stopped; doing it any
        # earlier destroys a QThread that is still running.
        job.finished.connect(job.deleteLater)

        self._set_busy(True, label)
        job.start()
        return True

    def _set_busy(self, busy: bool, label: str = "") -> None:
        """Show the work, and stop the user starting more of it.

        Export and Preview go dark rather than queueing: pressing Export twice
        should not mean exporting twice, and a disabled button says why the
        second press did nothing.
        """
        self.progress_row.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)  # indeterminate until told otherwise
            self.progress_bar.setFormat(label)
            self.status_label.setText(label)
            self.export_button.setEnabled(False)
            self.preview_button.setEnabled(False)
        else:
            self.progress_bar.reset()
            self._update_export_readiness()

    def _on_job_progress(self, percent: int, message: str) -> None:
        if percent < 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(message)
        self.status_label.setText(message)

    def _on_cancel_job(self) -> None:
        if self._job is None:
            return
        self._job.cancel()
        self.cancel_button.setEnabled(False)
        self.progress_bar.setFormat("Stopping...")

    def _on_job_succeeded(self, result, on_success) -> None:
        # Cleared *before* the callback, so a callback that starts the next
        # stage - reading then writing - is not refused by the one-job rule.
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        try:
            on_success(result)
        except Exception as exc:
            self._report_failure("Export failed", exc)

    def _on_job_cancelled(self) -> None:
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        self.status_label.setText("Stopped. Nothing was written.")

    def _on_job_failed_quietly(self, message: str, details: str) -> None:
        """A background failure the user did not ask about. Log and say so once."""
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        QgsMessageLog.logMessage(details, LOG_TAG, level=Qgis.Warning)
        self.status_label.setText(
            f"Live preview could not be updated ({message}) - see the QGIS log."
        )

    def _on_job_failed(self, message: str, details: str) -> None:
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        QgsMessageLog.logMessage(details, LOG_TAG, level=Qgis.Critical)
        if self._fidelity_is_stale:
            self._show_fidelity_error(message)
        QMessageBox.critical(
            self,
            "Something went wrong",
            f"{message}\n\nDetails are in the QGIS message log under "
            f'"{LOG_TAG}". Please report this at {REPO_URL}/issues',
        )

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

        This is what made unticking a map control freeze the window: with live
        preview on - and it is on by default - every settings change ran a full
        read of every layer plus the production writer, on the GUI thread.
        Neither half was needed for a chrome checkbox. Now the read is skipped
        whenever the data cannot have changed, and what is left runs on a
        worker.

        Reentrancy still matters: a rebuild is slower than the debounce on a
        large project, so an in-flight one defers the next rather than stacking.
        """
        if self._shut_down or self._server is None:
            return

        if self._job is not None:
            # Something is already running - either this rebuild or an export.
            # Come back after it, so the last edit is never the dropped one.
            self._rebuild_timer.start()
            return

        def then(export, _report) -> None:
            # Built here, on the GUI thread, because it reads the licence field.
            writer = self._writer()

            def work(progress: Progress):
                progress.step(READ_SHARE, "Updating the preview...")
                write_preview(
                    export, self._project_identity(), writer=writer, live=True
                )
                return None

            def on_written(_result) -> None:
                if self._shut_down or self._server is None:
                    return
                self._server.notify_reload()
                self.status_label.setText("Live preview updated.")

            self._start_job(work, on_written, "Updating the preview...", quiet=True)

        self._ensure_export(then, "Updating the preview...", quiet=True)

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
        # Two settings need it now, and for different reasons: framing the map
        # on the current view, and clipping the data to it. Reading it only for
        # the first meant ticking only the second silently clipped to nothing.
        if (
            self.state.extent_source is not ExtentSource.CANVAS
            and not self.state.clip_to_extent
        ):
            return None
        canvas = getattr(self.iface, "mapCanvas", None)
        if canvas is None:
            return None
        try:
            return extent_from_canvas(canvas())
        except Exception:  # never let a canvas quirk block an export
            return None

    def _read_current_project(self, progress: Progress | None = None):
        """Read the project into the export model. **Runs on a worker thread.**

        Everything it touches is either a plain value captured before the job
        started, or the PyQGIS pipeline that the Processing algorithm already
        runs off the main thread. Nothing here may touch a widget.
        """
        report = FidelityReportBuilder()

        def on_layer(done: int, total: int, name: str) -> None:
            if progress is None:
                return
            # Reading is most of the wait, so it owns most of the bar and the
            # writing stages continue from where it stops.
            percent = int(READ_SHARE * done / total) if total else 0
            progress.step(percent, f"Reading '{name}' ({done + 1} of {total})")

        export = read_project(
            self.project,
            report,
            settings=self.state.to_export_settings(),
            title_override=self.state.map_name or None,
            selected_layer_ids=self.state.selected_layer_ids(
                self._available_layer_ids()
            ),
            layer_settings=self.state.layers,
            canvas_extent=self._pending_canvas_extent,
            progress=on_layer,
        )
        # Licence caps are evaluated here, not left to the writer, because the
        # writer's verdict arrives after the file is on disk. A layer past the
        # free-tier cap renders nothing for the recipient, so the Fidelity tab
        # has to name it while the user can still do something about it.
        report_verdict(
            default_policy(self._pending_license_key).evaluate(export), report
        )
        return export, report

    def _ensure_export(self, then, label: str, quiet: bool = False) -> bool:
        """Get a current read to `then`, from cache or from a worker thread.

        The cache is the other half of the responsiveness fix. Reading is
        expensive and most settings cannot change its result: unticking "Legend"
        restyles the map's chrome and touches not one feature, yet it used to
        trigger a full re-read of every layer through the live preview. Now a
        chrome change reuses the last read and goes straight to writing.

        `then(export, report)` runs on the GUI thread either way, so callers do
        not have to care which happened.
        """
        signature = self.state.data_snapshot()
        if self._cached_export is not None and self._cached_signature == signature:
            then(self._cached_export, self._cached_report)
            return True

        # Read on the GUI thread, where the canvas lives, and hand the plain
        # value to the worker. Calling `iface.mapCanvas()` from the thread would
        # be touching a widget from the wrong side.
        self._pending_canvas_extent = self._canvas_extent()
        self._pending_license_key = self._license_key()

        def work(progress: Progress):
            progress.step(0, "Reading the project...")
            return self._read_current_project(progress)

        def on_success(result) -> None:
            export, report = result
            self._cached_export = export
            self._cached_report = report
            self._cached_signature = signature
            self._show_fidelity(report)
            self._update_size_note()
            then(export, report)

        return self._start_job(work, on_success, label, quiet=quiet)

    def _invalidate_export_cache(self) -> None:
        """Forget the last read. Called whenever the data could have changed."""
        self._cached_export = None
        self._cached_report = None
        self._cached_signature = None

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
        """Open the preview: served from this machine, or as a file.

        Three stages, and only the middle one is slow. Reading happens first and
        on a worker; the runtime check has to sit between them because it can
        put a licence prompt on screen, which is a GUI-thread thing to do.
        """
        # Read and report *before* asking for the runtime. Reading the project
        # is what fills the Fidelity tab, and it needs no runtime - so gating it
        # behind the download left the tab blank whenever the runtime was
        # missing or the user declined, hiding the one thing that would have
        # told them what their map was going to lose.
        self._ensure_export(self._preview_with, "Reading the project...")

    def _preview_with(self, export, _report) -> None:
        """Write and open the preview. Called once a read is in hand."""
        if not self._runtime_ready():
            return

        identity = self._project_identity()
        live = self.live_check.isChecked()
        writer = self._writer()

        def work(progress: Progress):
            progress.step(READ_SHARE, "Building the preview...")
            return write_preview(export, identity, writer=writer, live=live)

        def on_written(result) -> None:
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

        self._start_job(work, on_written, "Building the preview...")

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
        # Reading fills the Fidelity tab and needs no runtime; see on_preview.
        self._ensure_export(self._export_with, "Reading the project...")

    def _export_with(self, export, _report) -> None:
        """Everything after the read: the checks, the location, the write."""
        # A blocked item means a layer could not be read at all. Exporting
        # anyway writes a map that is quietly missing data while the dialog
        # reports success, and the Processing algorithm already refuses on
        # the same input - the two entry points must not disagree.
        if not export.is_exportable:
            self._warn_not_exportable(export)
            return

        mode = self.state.output_mode

        # Issue #29: never quietly hand over a single file that will not travel.
        # This used to switch the selection to Share ZIP itself, which was worse
        # than the problem: the radio buttons did not follow, so the dialog said
        # Standalone HTML while it wrote a zip. The rule now lives on the Map
        # tab where it can inform the choice, and this is the last reminder
        # before it costs anything. The user's choice stands either way.
        if mode is OutputMode.STANDALONE_HTML:
            reason = standalone_ineligible_reason(export)
            if reason is not None and not self._confirm_oversized_single_file(reason):
                return

        # Advisory, not a gate. A layer past the runtime's free-tier cap renders
        # nothing for the recipient, and that is worth interrupting for once -
        # but the export goes ahead, because the alternative is refusing to
        # write a map the user knowingly asked for.
        self._notify_license_caps(export)

        # After the blocking checks: no point downloading the runtime for a
        # project that was never going to export.
        if not self._runtime_ready():
            return

        destination = self._resolve_destination(mode)
        if destination is None:
            return

        writer = self._writer()

        def work(progress: Progress):
            progress.step(READ_SHARE, "Writing the map...")
            try:
                result, outcome = build_artifact(
                    export, destination, mode=mode, writer=writer
                )
            except ExportBlockedError as exc:
                # Returned rather than raised: a blocked export is the user's to
                # fix, and raising would route it to the crash reporter with a
                # "please report this" that has nothing to report.
                return exc
            progress.step(100, "Finishing...")
            return result, outcome

        def on_written(built) -> None:
            if isinstance(built, ExportBlockedError):
                QMessageBox.warning(
                    self,
                    "Cannot export",
                    "This map cannot be exported yet:\n\n"
                    + "\n".join(f"- {reason}" for reason in built.reasons),
                )
                return

            result, outcome = built
            save_state(self.project, self.state)
            self._remember_destination(destination)

            # The file:// check happens here, against the bytes that ship.
            self._last_export_path = Path(outcome.path)
            self.open_export_button.setEnabled(True)

            summary = outcome.summary()
            self.status_label.setText(summary)
            # The writer's warnings (licence caps, a runtime that does not match
            # the lock, an oversized single file) are things the recipient will
            # experience. Dropping them here is how a user ends up handing over
            # a map with layers missing and no idea it happened.
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

        self._start_job(work, on_written, "Writing the map...")

    def _confirm_oversized_single_file(self, reason: str) -> bool:
        """Warn about a single file too big to email, and let them have it anyway.

        A standalone export inlines every feature, so there is no sibling folder
        to carry the weight - the size is inherent to the format rather than a
        fault. That makes this the user's call, not ours.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("This will be a large single file")
        box.setText(reason)
        box.setInformativeText(
            "A standalone export inlines all the data into the one HTML file, "
            "which is why it is this big. Share ZIP packages the same map to "
            "send more easily."
        )
        export_anyway = box.addButton(
            "Export anyway", QMessageBox.ButtonRole.AcceptRole
        )
        switch = box.addButton("Use Share ZIP", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(switch)
        box.exec()

        clicked = box.clickedButton()
        if clicked is export_anyway:
            return True
        if clicked is switch:
            # Change the selection *and* the radio buttons, then stop so the
            # user sees the new state and presses Export themselves.
            self._on_mode_selected(OutputMode.SHARE_ZIP)
            self.status_label.setText(
                "Switched to Share ZIP. Press Export again to write it."
            )
        return False

    def _notify_license_caps(self, export) -> None:
        """Say what the runtime's free tier will drop, then carry on exporting.

        The caps are enforced inside the exported file on the recipient's
        machine, so a breach is invisible to the person doing the exporting
        until someone tells them the map is empty. The message bar is the right
        weight: impossible to miss, impossible to be blocked by.
        """
        violations = detect_violations(export)
        if not violations:
            return

        subjects = "; ".join(v.subject for v in violations)
        key = self._license_key()

        if key is None:
            message = (
                f"{len(violations)} free-tier limit(s) exceeded ({subjects}). "
                "Layers past the fifth will not render, and an over-size layer "
                "shows only its first 25,000 features while looking complete. "
                "Exporting anyway - the Fidelity tab has the detail."
            )
        elif self.state.output_mode is OutputMode.STANDALONE_HTML and not (
            describe_license_key(key).covers_local_files
        ):
            # The combination that silently fails: a real key, an export that
            # will be opened as a file, and a domain check that cannot match.
            message = (
                f"Your licence key is domain-locked, and a Standalone HTML file "
                f"opened by double-clicking has no domain - so the free-tier "
                f"limits still apply to it ({subjects}). Host the map, or ask "
                "NIKA for a key covering local files."
            )
        else:
            # Licensed and plausibly hosted: no cap applies, so nothing to say.
            return
        bar = getattr(self.iface, "messageBar", None)
        if bar is not None:
            with contextlib.suppress(Exception):
                bar().pushMessage(
                    "QGIS2WebMap", message, level=Qgis.Warning, duration=0
                )
                return
        # No message bar (a test harness, or a stripped iface): the status line
        # is worse but it is not silence.
        self.status_label.setText(message)

    def _resolve_destination(self, mode: OutputMode) -> Path | None:
        """Where to write, from the path field or from a picker.

        The field is the normal route now, so pressing Export does not
        interrupt with a file dialog the user already answered. An empty field
        falls back to the picker, which is what the dialog always did.
        """
        typed = self.path_edit.text().strip()
        if not typed:
            self._on_browse_destination()
            typed = self.path_edit.text().strip()
            if not typed:
                return None
            # Chosen through the picker, which already asked about overwriting.
            return Path(typed)

        destination = Path(typed)
        parent = destination if mode is OutputMode.FOLDER else destination.parent
        if not parent.exists():
            QMessageBox.warning(
                self,
                "Folder not found",
                f"There is no folder at {parent}.\n\nPick somewhere that exists, "
                "or create it first.",
            )
            return None

        # Typed paths skip the file dialog, and with it the overwrite prompt it
        # would have shown. Asking here is what keeps Export from silently
        # replacing yesterday's map.
        if mode is not OutputMode.FOLDER and destination.exists():
            answer = QMessageBox.question(
                self,
                "Replace the existing file?",
                f"{destination.name} already exists in {destination.parent}.\n\n"
                "Exporting replaces it.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None

        return destination

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

        # A running job holds a reference to this dialog and will emit into its
        # slots. Ask it to stop, then wait: a thread still running when Python
        # frees the widgets underneath it is a crash, and the user experiences
        # that as QGIS vanishing. The wait is bounded by the current layer, and
        # capped so a wedged provider cannot hang the close.
        job, self._job = self._job, None
        if job is not None:
            job.cancel()
            with contextlib.suppress(Exception):
                job.wait(SHUTDOWN_WAIT_MS)

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
