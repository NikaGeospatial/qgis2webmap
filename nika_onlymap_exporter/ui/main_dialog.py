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
import re
import sys
import time
import traceback
from dataclasses import replace
from html import escape as escape_html
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import Qgis, QgsMapLayer, QgsMessageLog, QgsProject
from qgis.gui import QgsColorButton
from qgis.PyQt.QtCore import QObject, QSettings, QSize, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QPalette,
    QTextDocument,
)
from qgis.PyQt.QtWidgets import (
    QAction,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.export_ir import (
    ExportProject,
    ExtentSource,
    OutputMode,
    OverlayCorner,
    PopupFieldMode,
)
from ..core.fidelity_report import FidelityReportBuilder
from ..core.fidelity_summary import (
    ReportState,
    ReportSummary,
    strip_message,
    summarise,
)
from ..core.license_policy import (
    default_policy,
    report_verdict,
)
from ..core.manifest_builder import (
    OSM_FILE_NOTE,
    osm_from_file,
    terrain_note,
)
from ..core.popup_translator import hidden_field_names, popup_field_names
from ..core.project_reader import extent_from_canvas, read_project, resolve_title
from ..core.report_facts import ReportFacts, report_facts
from ..core.settings import (
    MAX_PRECISION,
    MIN_PRECISION,
    PRECISION_FULL,
    DialogState,
    LayerSettings,
    load_state,
    resolve_license_key,
    save_state,
)
from ..packaging.artifact_builder import build_artifact
from ..packaging.dependency_scanner import (
    SINGLE_FILE_WARN_BYTES,
    data_bytes_by_layer,
    measure_data_bytes,
    standalone_ineligible_reason,
    standalone_raster_reason,
)
from ..packaging.runtime_manager import read_lock
from ..writers.onlymap_writer import ExportBlockedError, OnlyMapWriter
from . import languages, theme
from .account_button import AccountButton, PendingHostingBackend
from .background_job import BackgroundJob, Progress
from .fidelity_panel import FidelityPanel, FidelityStrip, Proceed, ask_before
from .fitted_label import FittedLabel
from .i18n import N_, active_language, plural, tr
from .language_picker import LanguagePicker, make_offer
from .layer_watcher import LayerContentWatcher, LayerTreeWatcher
from .links import (  # noqa: F401  - re-exported; imported by name elsewhere
    COMMUNITY_URL,
    COMPANY_URL,
    DISCORD_URL,
    DOCS_URL,
    FEATURE_REQUEST_URL,
    REPO_URL,
)
from .live_server import PreviewServer
from .preview import (
    preview_directory,
    preview_mode,
    prune_stale_previews,
    remove_preview,
    write_preview,
)
from .runtime_setup import ensure_runtime

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgisInterface

LOG_TAG = "QGIS2WebMap"

# The Share rows that act on the map file, so need one to have been chosen.
SHARE_ACTIONS_ON_THE_FILE = frozenset({"copy_path", "reveal"})

# The Map tab's "Export as" choice, worded as 0.1.4 had it: what each one is
# for, not what it is called internally.
MODE_LABELS = {
    OutputMode.STANDALONE_HTML: N_("Standalone HTML - one file, opens by double-click"),
    OutputMode.SHARE_ZIP: N_("Share ZIP - a zip to email or upload"),
    OutputMode.FOLDER: N_("Folder - for publishing to a web server"),
}

# Worded so the choice is readable without knowing the model: "with data" says
# what happens, where qgis2web's "visible with data" needs its manual.
POPUP_MODE_LABELS = {
    PopupFieldMode.NO_LABEL: N_("Value only, no label"),
    PopupFieldMode.INLINE_ALWAYS: N_("Label beside value - always show"),
    PopupFieldMode.INLINE_WITH_DATA: N_("Label beside value - only if it has data"),
    PopupFieldMode.HEADER_ALWAYS: N_("Label above value - always show"),
    PopupFieldMode.HEADER_WITH_DATA: N_("Label above value - only if it has data"),
    PopupFieldMode.HIDDEN: N_("Do not show this field"),
}

# Keeps the mode combos aligned down the list however long the field names are.
FIELD_NAME_COLUMN_WIDTH = 160

# How often the live preview checks whether any setting changed. Comparing one
# short JSON string, so the cost is noise; the interval only bounds how long a
# change waits before the debounce even starts.
LIVE_POLL_MS = 300

# How often the Fidelity tab and strip check whether the report on screen still
# describes the current settings. Comparing one short JSON string is noise.
FIDELITY_POLL_MS = 500

# How long a change must settle before a rebuild. Dragging a colour picker emits
# a change per pixel, and each rebuild re-runs the production writer.
LIVE_DEBOUNCE_MS = 400

# Whether Preview stays live. Per machine, like the theme: a working habit,
# not a property of the map.
LIVE_PREVIEW_KEY = "qgis2webmap/livePreview"

# How long every preview page must be gone before the server shuts itself down.
#
# Generous on purpose. A reload drops the page's `EventSource` and opens a new
# one, so this has to outlast a full reload of a page carrying a multi-megabyte
# inlined runtime on a slow machine - and the cost of waiting too long is one
# idle loopback socket, while the cost of being too eager is the server dying
# under a user who is still working.
PREVIEW_IDLE_STOP_SECONDS = 20.0

# Where the live-preview preference lives. QSettings, not the project file: it
# describes how someone likes to work, not what the map is, so it should follow
# the person between projects rather than travel inside one.

# The folder the last export was written to. QSettings for the same reason as
# the live-preview flag: "where I keep my maps" is a property of the person, not
# of the project, so it should follow them into the next one.
LAST_EXPORT_DIR_KEY = "qgis2webmap/lastExportDir"

# Which QGIS theme icon each Share row wears. A table rather than nine literals
# at the call sites, so a test can check every name against the icons this QGIS
# build actually has - which is the only part of an icon that can be verified
# without a screen. See `TestShareMenuIcons` for why `isNull` cannot be used.
SHARE_MENU_ICONS = {
    "copy_path": "mActionEditCopy.svg",
    "reveal": "mIconFolderOpen.svg",
    "publish": "mActionSharingExport.svg",
    "enhance": "mActionShowAllLayers.svg",
    "critique": "mActionShowAllLayers.svg",
    "cartograph": "mActionSaveAsPDF.svg",
}


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
    OverlayCorner.TOP_CENTER: N_("Top centre - clear of the controls"),
    OverlayCorner.BOTTOM_CENTER: N_("Bottom centre - clear of the controls"),
    OverlayCorner.TOP_LEFT: N_("Top left - shared with the layer switcher"),
    OverlayCorner.TOP_RIGHT: N_("Top right - shared with the legend"),
    OverlayCorner.BOTTOM_LEFT: N_("Bottom left - shared with zoom and scale"),
    OverlayCorner.BOTTOM_RIGHT: N_("Bottom right - shared with the credit"),
}

# "None" first and default: it is the only choice that keeps the export working
# offline and contacting nobody. The rest are the runtime's registered presets,
# minus the MapTiler ones, which need an API key the file would have to carry in
# plain text for every recipient to read.
BASEMAP_LABELS = {
    "none": N_("None - the export stays offline and contacts nobody"),
    "osm": N_("OpenStreetMap"),
    "positron": N_("Positron - pale, for data on top"),
    "dark-matter": N_("Dark Matter - dark background"),
    "voyager": N_("Voyager - general purpose"),
    "liberty": N_("Liberty - detailed street map"),
    "bright": N_("Bright - high contrast"),
}

# Relief, not a backdrop, so it sits in the same group as the basemap but on its
# own row. One entry beyond "off": the runtime's other preset needs a MapTiler
# key, which an exported file would have to carry where anyone can read it.
TERRAIN_LABELS = {
    "none": N_("Flat - no elevation"),
    "terrarium": N_("Global relief - tilts the map so it shows"),
}

# A short list rather than a spinner: the useful range is narrow, and naming the
# sizes means nobody has to reason about what 1.35 looks like.
CHROME_SCALE_LABELS = {
    1.0: N_("Normal"),
    0.85: N_("Small"),
    1.25: N_("Large"),
    1.5: N_("Extra large"),
    2.0: N_("Largest - for presentations and big screens"),
}

EXTENT_LABELS = {
    ExtentSource.DATA: N_("The data - every feature is visible"),
    ExtentSource.CANVAS: N_("The current QGIS view"),
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


def show_failure(parent: QWidget, title: str, message: str) -> None:
    """Report a failure, with the two ways out actually clickable.

    These boxes used to be plain text, so the URL in them rendered as
    characters the reader had to retype into a browser - at the exact moment
    they were already annoyed, which is how a reportable bug turns into an
    abandoned one. `QMessageBox` opens links in a rich-text body by itself, so
    this only has to escape the message and hand over the markup.

    Discord leads. Most people using this plugin are not carrying a GitHub
    account, and asking in a channel is a far lower bar than filing an issue
    with a title and a reproduction.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.RichText)
    # `message` carries exception text, which routinely contains angle brackets
    # and ampersands. Unescaped, Qt would silently swallow them as tags.
    box.setText(
        f"<p>{escape_html(message).replace(chr(10), '<br>')}</p>"
        + tr(
            "<p>Details are in the QGIS message log, under <b>{tag}</b>.</p>"
            "<p>Tell us about it in the QGIS2WebMap channel on "
            '<a href="{discord}">our Discord</a>, or '
            '<a href="{issues}">open a GitHub issue</a>. '
            "Please include the log.</p>"
        ).format(
            tag=escape_html(LOG_TAG), discord=DISCORD_URL, issues=f"{REPO_URL}/issues"
        )
    )
    box.exec()


def _theme_icon(name: str):
    """A QGIS theme icon, or an empty one if this build has not got it.

    Icon names are resource paths compiled into QGIS, not files on disk, and
    they come and go between releases. `getThemeIcon` returns a null `QIcon` for
    a name that has gone rather than raising, so a menu built against a newer
    QGIS still opens on an older one - it just loses a picture.

    Wrapped rather than called inline so that the failure is stated once, here,
    instead of being invisible at nine call sites.
    """
    from qgis.core import QgsApplication

    with contextlib.suppress(Exception):
        return QgsApplication.getThemeIcon(f"/{name}")
    return QIcon()


def _reveal_label() -> str:
    """What "show me this file" is called on the platform in front of you.

    Not cosmetic. Someone looking for a file reaches for the name their own
    system uses, and "Reveal in Finder" on Windows reads as a different feature
    rather than the same one worded oddly.
    """
    if sys.platform == "darwin":
        return tr("Reveal in Finder")
    if sys.platform.startswith("win"):
        return tr("Show in Explorer")
    return tr("Show in file manager")


def _ago(when: float) -> str:
    """How long ago a timestamp was, in the coarsest unit that is still true.

    Coarse on purpose. The point of the line this feeds is "is my file current",
    which "2 minutes ago" answers and "2 minutes 14 seconds ago" does not - and
    a label that changes every second draws the eye away from the map.

    A timestamp in the future reads as "just now" rather than a negative
    duration: clock skew on a network share is somebody else's problem, and it
    must not be reported as though the user did something wrong.
    """
    seconds = max(0.0, time.time() - when)
    if seconds < 10:
        return tr("just now")
    if seconds < 90:
        return tr("{n} s ago").format(n=int(seconds))
    minutes = seconds / 60
    if minutes < 90:
        return tr("{n} min ago").format(n=int(minutes))
    hours = minutes / 60
    if hours < 36:
        return plural(int(hours), "{n} hour ago", "{n} hours ago")
    return plural(int(hours / 24), "{n} day ago", "{n} days ago")


def _fit_row_to_widget(item: QTreeWidgetItem, widget: QWidget) -> None:
    """Make a tree row as tall as the controls placed in it.

    A tree sizes a row for its text, and an item widget is squeezed into that
    rect: the themed combo boxes are taller than a line of text, so their
    bottom edge was cut off. Polished first, so the height is the themed one.

    The width is real because Qt drops a size hint with a negative width. The
    extra height is the theme's `QTreeView::item` padding (3 px above and
    below), which the tree takes out of the widget's rect.
    """
    widget.ensurePolished()
    for child in widget.findChildren(QWidget):
        child.ensurePolished()
    hint = widget.sizeHint()
    item.setSizeHint(0, QSize(hint.width(), hint.height() + 8))


def _info_button(text: str, parent: QWidget) -> QToolButton:
    """An "i" that shows `text` on hover, and on click for touch and keyboard."""
    button = QToolButton(parent)
    button.setProperty("lineIcon", "info")
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(text)
    button.setAccessibleName(tr("What these columns do"))
    button.clicked.connect(
        lambda: QToolTip.showText(
            button.mapToGlobal(button.rect().bottomLeft()), text, button
        )
    )
    return button


def _help_label(text: str, parent: QWidget) -> QLabel:
    """A quiet line of guidance under a control.

    Several settings explained themselves only in a tooltip, which is invisible
    unless you already suspect there is something to read - and the setting most
    in need of explaining, coordinate precision, is the one that throws data
    away. Colour comes from the palette so it stays legible in a dark theme.
    """
    label = FittedLabel(text, parent)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    # Pinned to the top of its row. A form can hand a wrapped label more
    # height than it needs, and centred text then floats away from the
    # control it explains.
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return label


def _set_form_row_shown(field: QWidget, shown: bool) -> None:
    """Show or hide a form row, label included.

    An empty warning label still took a row's height plus the form's spacing,
    which left a visible hole under a setting with nothing to warn about.
    `QFormLayout` skips a row only once both of its widgets are hidden.
    """
    field.setVisible(shown)
    parent = field.parentWidget()
    form = parent.layout() if parent is not None else None
    if isinstance(form, QFormLayout):
        label = form.labelForField(field)
        if label is not None:
            label.setVisible(shown)


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
    feature = getattr(QTextDocument, "MarkdownFeature", QTextDocument)
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
    return document


class MainDialog(QDialog):
    """Export dialog. Non-modal, so QGIS stays usable behind it."""

    # The user picked a language that differs from the one on screen. The
    # plugin reopens the dialog in it; every label is set at build time.
    languageChanged = pyqtSignal()  # noqa: N815 - Qt signal naming

    def __init__(self, iface: QgisInterface, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.iface = iface
        self.project = QgsProject.instance()
        self.state: DialogState = load_state(self.project)

        self.setWindowTitle(tr("QGIS2WebMap by NIKA"))
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

        # Which tier the write in flight is producing, and what to do once it
        # lands. Both belong to one press rather than to the dialog: packaging
        # stopped being a remembered setting when the mode radio group went.
        self._pending_mode: OutputMode = OutputMode.STANDALONE_HTML
        self._export_then = None
        # The state the map file on disk was last written from. `None` means
        # "not by us, not this session" - so the first Share action rebuilds
        # rather than trusting a file it has never seen written.
        self._written_signature: str | None = None
        self._pending_signature: str | None = None
        self._last_export_path: Path | None = None

        # What the Fidelity tab and strip are showing. See `_fidelity_is_stale`.
        self._fidelity_dirty = True
        self._fidelity_signature: str | None = None
        self._fidelity_state = ReportState.NOT_CHECKED
        self._fidelity_error = ""
        self._fidelity_summary: ReportSummary | None = None
        self._data_bytes_cache = None

        layout = QVBoxLayout(self)
        if active_language() in languages.RIGHT_TO_LEFT:
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        # Only when QGIS speaks a language we have and its pack is not here.
        self.language_offer = make_offer(self)
        if self.language_offer is not None:
            self.language_offer.changed.connect(self.languageChanged)
            layout.addWidget(self.language_offer)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(_scrollable(self._build_map_tab()), tr("Map"))
        self.tabs.addTab(self._build_layers_tab(), tr("Layers"))
        self.tabs.addTab(_scrollable(self._build_appearance_tab()), tr("Appearance"))
        # Held, not found by title: the title is translated.
        self._fidelity_page = self._build_fidelity_tab()
        self.tabs.addTab(self._fidelity_page, tr("Fidelity"))
        self._help_page = self._build_help_tab()
        self.tabs.addTab(self._help_page, tr("Help"))
        # Tabs by their English names, for jumps that must survive translation.
        self._pages = {
            "Map": self.tabs.widget(0),
            "Layers": self.tabs.widget(1),
            "Appearance": self.tabs.widget(2),
            "Fidelity": self._fidelity_page,
            "Help": self._help_page,
        }
        self.tabs.setCornerWidget(self._build_tab_corner(), Qt.Corner.TopRightCorner)
        # Before Help, where people look for settings.
        self.tabs.insertTab(
            self.tabs.indexOf(self._help_page),
            _scrollable(self._build_settings_tab()),
            tr("Settings"),
        )
        self._fidelity_is_stale = True
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)
        layout.addWidget(self._build_fidelity_strip())
        layout.addWidget(self._build_progress_row())
        layout.addLayout(self._build_button_row())

        # Last, so the stylesheet meets every widget the tabs built. See
        # `theme` for why it is scoped to this dialog and never to QGIS.
        self.theme = theme.apply(self)
        theme.recolour_document(self.help_browser.document(), self.theme)
        self.account_button.refresh()
        self._update_basemap_warning()
        self._update_terrain_warning()

        # The list tracks QGIS live; there is no refresh button because there is
        # nothing to refresh.
        self.watcher = LayerTreeWatcher(self.project, self)
        self.watcher.changed.connect(self.refresh_layers)
        # What the layers LOOK like, which the tree does not carry: a restyle
        # (or saved edits) changes the map without moving anything above.
        self.content_watcher = LayerContentWatcher(self.project, self)
        self.content_watcher.changed.connect(self._project_content_changed)

        # Notices a report going out of date whatever changed it. Polled, like
        # the live preview, because a signal per setting is what let Include,
        # Popup, Label and the map name change the export without marking the
        # report stale.
        self._fidelity_timer = QTimer(self)
        self._fidelity_timer.setInterval(FIDELITY_POLL_MS)
        self._fidelity_timer.timeout.connect(self._refresh_fidelity_freshness)
        self._fidelity_timer.start()

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

    def _warn_style(self) -> str:
        """Warning text in the theme's warn colour.

        Warnings can be set while the tabs are still being built, before the
        theme exists; those fall back to the old fixed red and are redrawn once
        `theme.apply` has run.
        """
        palette = getattr(self, "theme", None)
        return f"color: {palette.warn if palette else '#c0392b'};"

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
        source_note = FittedLabel(
            tr(
                "<b>Source:</b> the layers already open in this QGIS project. There "
                "is nothing to browse for - choose which of them to include on the "
                "Layers tab."
            ),
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
            tr(
                "The title shown on the exported map. Leave blank to use the "
                "project title."
            )
        )
        self.name_edit.textChanged.connect(self._on_name_changed)
        form.addRow(tr("Map name"), self.name_edit)
        form.addRow(
            "",
            _help_label(
                tr(
                    "Shown on the map and in the browser tab. Leave blank to use "
                    "the project title."
                ),
                page,
            ),
        )
        layout.addLayout(form)

        # ---- The map file ------------------------------------------------
        # There is no packaging choice here any more. 0.1.x asked "how to share
        # it" - standalone HTML, zip or folder - before the user had shared
        # anything, which is the wrong moment to ask: the answer depends on who
        # they are sending it to, and they decide that afterwards. The map is
        # now one HTML document, and the zip and folder tiers are produced from
        # the Share menu at the point of sharing.
        destination_box = QGroupBox(tr("Export"), page)
        destination_layout = QVBoxLayout(destination_box)

        # Radio buttons, not checkboxes: the three are mutually exclusive, and
        # the group enforces it. Restored from 0.1.4, where choosing the type
        # before pressing Export is the whole flow.
        destination_layout.addWidget(QLabel(tr("Export as"), destination_box))
        self.mode_group = QButtonGroup(destination_box)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[OutputMode, QRadioButton] = {}
        for mode, label in MODE_LABELS.items():
            button = QRadioButton(tr(label), destination_box)
            button.setChecked(mode is self.state.output_mode)
            button.toggled.connect(lambda on, m=mode: on and self._on_mode_selected(m))
            self.mode_group.addButton(button)
            destination_layout.addWidget(button)
            self.mode_buttons[mode] = button

        destination_layout.addWidget(QLabel(tr("Save to"), destination_box))
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(destination_box)
        self.path_edit.setPlaceholderText(tr("Choose where the export is written"))
        self.path_edit.textChanged.connect(self._on_path_edited)
        path_row.addWidget(self.path_edit, 1)

        browse = QPushButton(tr("Choose..."), destination_box)
        browse.clicked.connect(self._on_choose_map_file)
        path_row.addWidget(browse)
        destination_layout.addLayout(path_row)

        # What the file on disk is doing right now, directly under the field
        # that names it. Separate from the status line in the bottom bar, which
        # reports work in progress: this one reports the file, and the two must
        # never describe the same thing.
        self.save_status = FittedLabel("", destination_box)
        self.save_status.setWordWrap(True)
        self.save_status.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        destination_layout.addWidget(self.save_status)

        # The size rule, beneath the file it constrains. It used to appear only
        # as a modal at the end of an export, which is both too late to inform
        # the decision and - because the modal switched the selection without
        # the radio buttons following - left the dialog claiming Standalone
        # HTML while it built a zip. There is no selection to disagree with any
        # more, but the number is still worth saying before anyone emails it.
        self.size_note = FittedLabel("", destination_box)
        self.size_note.setWordWrap(True)
        self.size_note.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        destination_layout.addWidget(self.size_note)

        layout.addWidget(destination_box)

        # Its own group rather than a row inside "Data": a basemap is not the
        # project's data, it is the backdrop behind it - and it is the only
        # setting whose consequence lands on the recipient, so it should be
        # found rather than come across.
        basemap_box = QGroupBox(tr("Basemap"), page)
        basemap_form = QFormLayout(basemap_box)

        self.basemap_combo = QComboBox(basemap_box)
        for value, label in BASEMAP_LABELS.items():
            self.basemap_combo.addItem(tr(label), value)
        basemap_index = self.basemap_combo.findData(self.state.basemap)
        self.basemap_combo.setCurrentIndex(basemap_index if basemap_index >= 0 else 0)
        self.basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        basemap_form.addRow(tr("Show behind the map"), self.basemap_combo)

        # Warning rather than help: this is the only setting that changes what
        # the *recipient's* machine does, and it cannot be undone after sending.
        self.basemap_warning = FittedLabel("", basemap_box)
        self.basemap_warning.setWordWrap(True)
        basemap_form.addRow("", self.basemap_warning)
        self._update_basemap_warning()

        # In the same group because it costs the recipient the same thing -
        # tiles fetched from a third party on every open - and separating them
        # would mean explaining that cost twice.
        self.terrain_combo = QComboBox(basemap_box)
        for value, label in TERRAIN_LABELS.items():
            self.terrain_combo.addItem(tr(label), value)
        terrain_index = self.terrain_combo.findData(self.state.terrain)
        self.terrain_combo.setCurrentIndex(terrain_index if terrain_index >= 0 else 0)
        self.terrain_combo.currentIndexChanged.connect(self._on_terrain_changed)
        basemap_form.addRow(tr("Ground surface"), self.terrain_combo)

        self.terrain_warning = FittedLabel("", basemap_box)
        self.terrain_warning.setWordWrap(True)
        basemap_form.addRow("", self.terrain_warning)
        self._update_terrain_warning()
        layout.addWidget(basemap_box)

        data_box = QGroupBox(tr("Data"), page)
        data_form = QFormLayout(data_box)

        self.extent_combo = QComboBox(data_box)
        for source, label in EXTENT_LABELS.items():
            self.extent_combo.addItem(tr(label), source.value)
        extent_index = self.extent_combo.findData(self.state.extent_source.value)
        self.extent_combo.setCurrentIndex(extent_index if extent_index >= 0 else 0)
        self.extent_combo.currentIndexChanged.connect(self._on_extent_changed)
        data_form.addRow(tr("Open the map on"), self.extent_combo)
        data_form.addRow(
            "",
            _help_label(
                tr(
                    "Where the map is positioned when someone opens it. They can "
                    "still pan and zoom anywhere afterwards."
                ),
                data_box,
            ),
        )

        # A separate control from the one above, because they are separate
        # decisions: that one frames the map, this one decides what is in it.
        # Folding them together would mean a user who wanted to open on their
        # working view silently shipped a map missing everything outside it.
        self.clip_check = QCheckBox(
            tr("Export only the features in this view"), data_box
        )
        self.clip_check.setChecked(self.state.clip_to_extent)
        self.clip_check.toggled.connect(self._on_clip_toggled)
        data_form.addRow("", self.clip_check)
        data_form.addRow(
            "",
            _help_label(
                tr(
                    "Leaves out everything outside the current QGIS view. This is "
                    "the practical way to bring a very large layer under the free "
                    "plan's 25,000-feature limit. The Fidelity tab reports how many "
                    "features each layer loses."
                ),
                data_box,
            ),
        )

        # "Maintain" first and selected: rounding coordinates is the only
        # setting in this dialog that throws data away, so it is opt-in and
        # says so in the fidelity report when chosen.
        self.precision_combo = QComboBox(data_box)
        self.precision_combo.addItem(tr("Maintain full precision"), None)
        for places in range(MIN_PRECISION, MAX_PRECISION + 1):
            self.precision_combo.addItem(
                plural(places, "{n} decimal place", "{n} decimal places"), places
            )
        precision_index = self.precision_combo.findData(self.state.quantize_precision)
        self.precision_combo.setCurrentIndex(
            precision_index if precision_index >= 0 else 0
        )
        self.precision_combo.setToolTip(
            tr(
                "Rounding coordinates makes the file smaller and is irreversible. "
                "Around 6 decimal places is roughly 0.1 m at the equator."
            )
        )
        self.precision_combo.currentIndexChanged.connect(self._on_precision_changed)
        data_form.addRow(tr("Coordinate precision"), self.precision_combo)
        # The only setting in this dialog that discards data, so it says so on
        # screen rather than only on hover.
        data_form.addRow(
            "",
            _help_label(
                tr(
                    "Rounding makes the file smaller and cannot be undone. "
                    "6 places is about 0.1 m at the equator."
                ),
                data_box,
            ),
        )
        layout.addWidget(data_box)

        layout.addStretch(1)

        # The tab's dead space, spent saying what pressing Export will produce.
        # Kept at the bottom so it reads as a consequence of the choices above
        # rather than another setting.
        self.export_summary = FittedLabel("", page)
        self.export_summary.setWordWrap(True)
        self.export_summary.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(self.export_summary)

        # Last, because filling the path field emits `textChanged`, whose slot
        # updates the summary label created just above. An exception raised in a
        # Qt slot does not propagate - PyQt aborts the process - so a handler
        # that runs before the widget it touches exists takes QGIS down.
        #
        # The field is filled from the project or left empty; it is never
        # guessed at. See `DialogState.map_file`.
        if self.state.map_file:
            self.path_edit.setText(self.state.map_file)
        self._update_save_status()
        self._update_size_note()
        return page

    # ---- Licence ---------------------------------------------------------

    def _license_key(self) -> str | None:
        """The key to export with, or None for the free plan.

        There is no field for this on the Map tab. Since runtime 0.6.0 the caps
        apply only on a hosted `http(s)` page, and every artifact this dialog
        produces is opened from a file or from localhost, so a key would have
        changed nothing for almost everyone who saw it.

        Still resolved rather than hardcoded to None: `resolve_license_key`
        reads `ONLYMAP_LICENSE_KEY` and then the stored setting, which is how
        the Processing algorithm supplies one. A user who needs a key for a
        hosted map goes through Processing or the environment variable, and the
        dialog honours it silently instead of ignoring it.
        """
        return resolve_license_key()

    def _writer(self) -> OnlyMapWriter:
        """A writer carrying the user's licence policy.

        Built per use rather than held: the key can change between one export
        and the next, and a writer holding a stale policy would keep writing
        free-plan maps for someone who had just pasted a key.
        """
        return OnlyMapWriter(license_policy=default_policy(self._license_key()))

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

        # Says what Export will make, in the type chosen above.
        mode = self.state.output_mode
        if mode is OutputMode.SHARE_ZIP:
            sentence = plural(
                selected_count,
                "Export makes a zip to email or upload, carrying {n} layer.",
                "Export makes a zip to email or upload, carrying {n} layers.",
            )
        elif mode is OutputMode.FOLDER:
            sentence = plural(
                selected_count,
                "Export makes a folder for a web server, carrying {n} layer.",
                "Export makes a folder for a web server, carrying {n} layers.",
            )
        else:
            sentence = plural(
                selected_count,
                "Export makes one HTML file that opens by double-click, "
                "carrying {n} layer.",
                "Export makes one HTML file that opens by double-click, "
                "carrying {n} layers.",
            )

        # Naming the destination here is what makes the path field's effect
        # visible without pressing anything.
        destination = getattr(self, "path_edit", None)
        chosen = destination.text().strip() if destination is not None else ""
        if chosen:
            sentence += "\n" + tr("To: {path}").format(path=chosen)
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
        # The terrain warning names the imagery draped over the relief, which
        # follows the basemap - so it changes when the basemap does.
        self._update_terrain_warning()
        self._fidelity_is_stale = True

    def _update_basemap_warning(self) -> None:
        """Warn under the basemap when OpenStreetMap will fail its recipients.

        Only that case: what every basemap costs - tiles from a third party,
        no offline use - is in the Fidelity tab, and repeating it here read as
        noise under every choice.
        """
        note = (
            OSM_FILE_NOTE
            if osm_from_file(self.state.basemap, self.state.output_mode)
            else None
        )
        if note is None:
            self.basemap_warning.setText("")
            self.basemap_warning.setStyleSheet("")
            _set_form_row_shown(self.basemap_warning, False)
            return

        _set_form_row_shown(self.basemap_warning, True)

        self.basemap_warning.setText(note)
        # The one place a fixed colour is right: this is a warning, and warning
        # red has to read as red in both light and dark themes rather than
        # following the palette into something quiet.
        self.basemap_warning.setStyleSheet(self._warn_style())

    def _on_terrain_changed(self) -> None:
        value = self.terrain_combo.currentData()
        self.state.terrain = value if isinstance(value, str) else "none"
        self._update_terrain_warning()
        self._fidelity_is_stale = True

    def _update_terrain_warning(self) -> None:
        """The same warning shape as the basemap, for the same reason."""
        note = terrain_note(self.state.terrain, basemap=self.state.basemap)
        if note is None:
            self.terrain_warning.setText("")
            self.terrain_warning.setStyleSheet("")
            _set_form_row_shown(self.terrain_warning, False)
            return

        _set_form_row_shown(self.terrain_warning, True)

        self.terrain_warning.setText(note)
        self.terrain_warning.setStyleSheet(self._warn_style())

    def _on_clip_toggled(self, value: bool) -> None:
        self.state.clip_to_extent = value
        self._fidelity_is_stale = True

    def _on_precision_changed(self) -> None:
        value = self.precision_combo.currentData()
        self.state.quantize_precision = value if isinstance(value, int) else None
        self._fidelity_is_stale = True

    def _on_name_changed(self, text: str) -> None:
        self.state.map_name = text

    # ---- The map file ----------------------------------------------------

    def _mode_suffix(self, mode: OutputMode) -> str:
        """The extension a packaging tier produces. A folder has none.

        Still needed although the dialog no longer offers the choice: the Share
        menu names a tier per action, and the picker it opens has to propose a
        matching extension.
        """
        return {
            OutputMode.STANDALONE_HTML: ".html",
            OutputMode.SHARE_ZIP: ".zip",
            OutputMode.FOLDER: "",
        }[mode]

    def _map_file(self) -> Path | None:
        """The chosen map file, or None while the field is empty."""
        typed = self.path_edit.text().strip() if hasattr(self, "path_edit") else ""
        return Path(typed) if typed else None

    def _default_basename(self) -> str:
        """Name the file after the map, falling back to the project."""
        name = (self.state.map_name or "").strip() or Path(
            self._project_identity()
        ).stem
        # Anything a file system will argue about becomes an underscore. A map
        # called "Site A / B" must not silently write into a subdirectory.
        cleaned = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
        return cleaned or "map"

    def _on_path_edited(self, text: str) -> None:
        """Record the path and refresh everything that depends on having one.

        Reachable from `textChanged`, which fires while the Map tab is still
        being built, so every method called here guards on its own widget
        existing. An exception raised in a Qt slot does not propagate - PyQt
        aborts the process - so a handler that runs too early takes QGIS down.
        """
        self.state.map_file = text.strip()
        self._update_export_summary(
            len(self.state.selected_layer_ids(self._available_layer_ids()))
        )
        self._update_save_status()
        self._update_share_enabled()

    def _picker_start_directory(self) -> str:
        """Where a file picker should open, in order of what it can know.

        The folder beside the project first, because that is where the issue
        says a map belongs and where the user will look for it. Then the folder
        of the last export, which is the only thing 0.1.x remembered - it is a
        useful hint here, in a dialog the user confirms, and a bad default in
        the field itself, where they might not read it.
        """
        project_file = self.project.fileName()
        if project_file:
            parent = Path(project_file).parent
            if parent.is_dir():
                return str(parent)

        remembered = QSettings().value(LAST_EXPORT_DIR_KEY, "", type=str)
        if remembered and Path(remembered).is_dir():
            return remembered
        return str(Path.home())

    def _on_mode_selected(self, mode: OutputMode) -> None:
        """Switch the export type, carrying the chosen name across.

        `peaks.html` becomes `peaks.zip` or a `peaks` folder rather than being
        cleared, so changing the type never costs the user the place they
        picked.
        """
        self.state.output_mode = mode
        self._update_basemap_warning()
        # The report states the output type, and with OpenStreetMap the type
        # decides whether it warns: restate or mark it out of date.
        self._refresh_fidelity_freshness()
        if (
            self._fidelity_state is ReportState.CURRENT
            and self._fidelity_summary is not None
        ):
            self.fidelity_panel.show_summary(
                self._fidelity_summary, self._fidelity_facts(self._fidelity_signature)
            )
        typed = self.path_edit.text().strip()
        if typed:
            path = Path(typed)
            stem = (
                path.with_suffix("")
                if path.suffix.lower() in (".html", ".zip")
                else path
            )
            self.path_edit.setText(str(stem) + self._mode_suffix(mode))
        self._update_size_note()
        self._update_share_enabled()
        self._update_export_summary(
            len(self.state.selected_layer_ids(self._available_layer_ids()))
        )

    def _on_choose_map_file(self) -> None:
        """Pick where the map file is written.

        Always `.html`: the map file is the document, and the other packaging
        tiers are produced from the Share menu into destinations of their own.
        """
        mode = self.state.output_mode
        suffix = self._mode_suffix(mode)
        current = self.path_edit.text().strip()
        start = current or str(
            Path(self._picker_start_directory()) / f"{self._default_basename()}{suffix}"
        )
        if mode is OutputMode.FOLDER:
            chosen = QFileDialog.getExistingDirectory(
                self, tr("Export folder for hosting"), str(Path(start).parent)
            )
            if chosen:
                self.path_edit.setText(chosen)
            return
        file_filter = (
            tr("Web page (*.html)")
            if mode is OutputMode.STANDALONE_HTML
            else tr("Zip archive (*.zip)")
        )
        chosen, _ = QFileDialog.getSaveFileName(self, tr("Save to"), start, file_filter)
        if not chosen:
            return
        # A name typed without an extension means the type chosen above.
        path = Path(chosen)
        if path.suffix.lower() != suffix:
            path = path.with_suffix(suffix)
        self.path_edit.setText(str(path))

    def _update_save_status(self) -> None:
        """Say what the file on disk is, in one line under the field.

        Three states, and the first is the one that matters: with no map file
        chosen, every Share action is disabled, and this is where that is
        explained. A disabled menu with no reason beside it is the failure this
        label exists to prevent.
        """
        if not hasattr(self, "save_status"):
            return

        destination = self._map_file()
        if destination is None:
            self.save_status.setText(tr("Choose where to save, then press Export."))
            return

        if not destination.exists():
            self.save_status.setText(tr("Not written yet."))
            return

        try:
            stat = destination.stat()
        except OSError:
            # The path is real enough to have been chosen but cannot be read -
            # a disconnected network share, most likely. Saying so beats a
            # traceback in a slot.
            self.save_status.setText(tr("The map file cannot be read from here."))
            return

        megabytes = stat.st_size / 1024 / 1024
        layers = len(self.state.selected_layer_ids(self._available_layer_ids()))
        detail = plural(
            layers,
            "{size} MB - {n} layer",
            "{size} MB - {n} layers",
            size=f"{megabytes:.1f}",
        )

        # The two states read differently because they promise different things.
        # While a session is live the file tracks the dialog, so "Saved" is a
        # fact about now. With no session it is a fact about the past, and the
        # sentence has to say what will close the gap - otherwise a file written
        # twenty minutes ago looks current simply because it exists.
        if self._map_file_is_stale():
            self.save_status.setText(
                tr(
                    "Last written {ago} - {detail}. "
                    "The project has changed since; Export writes it again."
                ).format(ago=_ago(stat.st_mtime), detail=detail)
            )
        else:
            self.save_status.setText(
                tr("Last written {ago} - {detail}").format(
                    ago=_ago(stat.st_mtime), detail=detail
                )
            )

    def _update_share_enabled(self) -> None:
        """The rows that act on the map file need somewhere to write it.

        Only those: the waitlist and the hand-offs never touch the file, and
        greying them out until a path was chosen hid them from exactly the
        people who had not exported yet.

        Enablement hangs on the *path*, not on the file existing: an action that
        needs the map writes it first. Requiring the file to exist would leave
        the menu dead on first open with nothing to press to wake it.

        **Every action, not just the button.** A disabled `QToolButton` still
        holds an enabled `QMenu`, and an enabled `QAction` keeps its shortcut
        live on the dialog - so a shortcut would fire with no map file set, from
        a button the user can see is greyed out.
        """
        if not hasattr(self, "share_button"):
            return
        ready = self._map_file() is not None and getattr(self, "_export_ready", True)
        for key, action in getattr(self, "share_actions", {}).items():
            if key not in SHARE_ACTIONS_ON_THE_FILE:
                continue
            action.setEnabled(ready)
            # Restored from the originals rather than remembered off the widget:
            # overwriting the tooltip on the way down would lose the real one
            # the first time a map file was cleared, and the action would spend
            # the rest of the session explaining a state it is no longer in.
            action.setToolTip(
                self._share_tooltips[key]
                if ready
                else tr("Choose where to save on the Map tab first")
            )

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
        # The single-file rule says nothing about a zip or a folder.
        if self.state.output_mode is not OutputMode.STANDALONE_HTML:
            self.size_note.setText("")
            self.size_note.setVisible(False)
            return
        self.size_note.setVisible(True)

        limit_mb = SINGLE_FILE_WARN_BYTES // 1024 // 1024
        base = tr(
            "The map file inlines every feature, so it carries all the data "
            "itself. Past about {limit} MB it gets slow to open and awkward "
            "to move around."
        ).format(limit=limit_mb)

        export = self._cached_export
        if export is None:
            self.size_note.setText(base)
            self.size_note.setStyleSheet("")
            return

        data_bytes = measure_data_bytes(export)
        megabytes = data_bytes / 1024 / 1024
        if data_bytes > SINGLE_FILE_WARN_BYTES:
            self.size_note.setText(
                tr(
                    "This map's data is {size} MB, past the {limit} MB "
                    "where a single file starts to be unwieldy. The map file is "
                    "still written - exporting as Share ZIP is the practical way "
                    "to send something this size."
                ).format(size=f"{megabytes:.0f}", limit=limit_mb)
            )
            self.size_note.setStyleSheet(self._warn_style())
        else:
            self.size_note.setText(
                base
                + "\n"
                + tr("This map's data is {size} MB.").format(size=f"{megabytes:.1f}")
            )
            self.size_note.setStyleSheet("")

    # ---- Layers tab -----------------------------------------------------

    def _build_layers_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        intro = FittedLabel(
            tr(
                "Layers come from the QGIS Layers panel and follow it live - "
                "reorder or rename them there and this list updates. Expand a "
                "layer to choose how each of its fields appears in popups."
            ),
            page,
        )
        # Wrapped: on one line it ran off the edge of a normal-sized dialog.
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.layer_tree = QTreeWidget(page)
        self.layer_tree.setColumnCount(4)
        self.layer_tree.setHeaderLabels(
            [tr("Layer"), tr("Include"), tr("Popups"), tr("Labels")]
        )
        self.layer_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.layer_tree.itemChanged.connect(self._on_layer_item_changed)
        layout.addWidget(self.layer_tree)

        # What the three columns do, in terms of what the *recipient* loses.
        # The headers name them but not their consequence, and testing found
        # nobody could say what unticking one would produce - so the boxes went
        # untouched rather than used. Behind a visible "i" button rather than
        # spelled out below the list, where six lines of it crowded the tab;
        # the button is there to be noticed, unlike a bare tooltip.
        self.layers_info_button = _info_button(
            tr(
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
                "map draws with them."
            ),
            page,
        )

        # Bulk action. Setting a mode field-by-field across a dozen layers is
        # exactly the tedium qgis2web users report, so the escape hatch is here
        # too - but as a button, because it is the one control on this tab that
        # overwrites every layer at once and should not fire on a stray scroll.
        bulk = QHBoxLayout()
        bulk.addWidget(QLabel(tr("Set every field to"), page))
        self.bulk_mode_combo = QComboBox(page)
        for mode, label in POPUP_MODE_LABELS.items():
            self.bulk_mode_combo.addItem(tr(label), mode.value)
        self.bulk_mode_combo.setCurrentIndex(
            self.bulk_mode_combo.findData(PopupFieldMode.INLINE_WITH_DATA.value)
        )
        bulk.addWidget(self.bulk_mode_combo)

        apply_all = QPushButton(tr("Apply to all layers"), page)
        apply_all.setToolTip(
            tr(
                "Give every popup field in every layer this setting, replacing the "
                "choices below."
            )
        )
        apply_all.clicked.connect(self._on_apply_mode_to_all)
        bulk.addWidget(apply_all)
        bulk.addStretch(1)
        bulk.addWidget(self.layers_info_button)
        layout.addLayout(bulk)
        return page

    def _project_content_changed(self) -> None:
        """A layer was restyled or its edits saved in QGIS. Everything read
        from the project before is stale."""
        self._fidelity_is_stale = True
        # The cache is keyed on this dialog's settings, which a change in QGIS
        # does not touch - so a restyled layer would otherwise be served from a
        # read taken before it happened.
        self._invalidate_export_cache()
        if self._live_running():
            self._rebuild_timer.start()

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
        if self._live_running():
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
                combo.addItem(tr(label), mode.value)

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
            _fit_row_to_widget(child, row)

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

        label = QLabel(tr("This layer only"), row)
        label.setMinimumWidth(FIELD_NAME_COLUMN_WIDTH)
        layout.addWidget(label)

        hover = QComboBox(row)
        for text, value in (
            (tr("Popups: same as map"), None),
            (tr("Popups: on hover"), True),
            (tr("Popups: on click"), False),
        ):
            hover.addItem(text, value)
        hover.setCurrentIndex(max(0, hover.findData(settings.popup_on_hover)))
        hover.currentIndexChanged.connect(
            lambda _i, lid=layer_id, box=hover: self._on_layer_hover_changed(lid, box)
        )
        layout.addWidget(hover)

        precision = QComboBox(row)
        precision.addItem(tr("Precision: same as map"), None)
        precision.addItem(tr("Precision: keep it all"), PRECISION_FULL)
        for places in range(MIN_PRECISION, MAX_PRECISION + 1):
            precision.addItem(
                plural(
                    places,
                    "Precision: {n} decimal place",
                    "Precision: {n} decimal places",
                ),
                places,
            )
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
        highlight.setShowNull(True, tr("Highlight: same as map"))
        highlight.setToolTip(tr("Highlight colour for this layer only."))
        _apply_saved_color(highlight, settings.highlight_color or "")
        highlight.colorChanged.connect(
            lambda _c, lid=layer_id, button=highlight: self._on_layer_highlight_changed(
                lid, button
            )
        )
        layout.addWidget(highlight)

        layout.addStretch(1)
        self.layer_tree.setItemWidget(item, 0, row)
        _fit_row_to_widget(item, row)

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
                tr(
                    "These are on by default - an exported map should be useful "
                    "without configuring anything."
                ),
                page,
            )
        )

        chrome = QGroupBox(tr("Map controls"), page)
        chrome_layout = QVBoxLayout(chrome)
        self.widget_checks: dict[str, QCheckBox] = {}
        for key, label, current in (
            ("legend", tr("Legend"), self.state.show_legend),
            ("layerSwitcher", tr("Layer switcher"), self.state.show_layer_switcher),
            ("zoomControls", tr("Zoom controls"), self.state.show_zoom_controls),
            ("scaleBar", tr("Scale bar"), self.state.show_scale_bar),
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
        box = QGroupBox(tr("Caption"), page)
        form = QFormLayout(box)

        self.title_check = QCheckBox(tr("Map title"), box)
        self.title_check.setChecked(self.state.show_title)
        self.title_check.toggled.connect(self._on_title_toggled)
        form.addRow(self.title_check)
        form.addRow(
            "",
            _help_label(
                tr(
                    "Draws the map name over the map. The legend drops its own "
                    "heading while this is on, so the title is not shown twice."
                ),
                box,
            ),
        )

        self.abstract_check = QCheckBox(tr("Project description"), box)
        self.abstract_check.setChecked(self.state.show_abstract)
        self.abstract_check.toggled.connect(self._on_abstract_toggled)
        form.addRow(self.abstract_check)
        form.addRow(
            "",
            _help_label(
                tr(
                    "The abstract from Project Properties > Metadata. Nothing "
                    "appears if the project has none."
                ),
                box,
            ),
        )

        self.corner_combo = QComboBox(box)
        for corner, label in CAPTION_CORNER_LABELS.items():
            self.corner_combo.addItem(tr(label), corner.value)
        index = self.corner_combo.findData(self.state.title_corner.value)
        self.corner_combo.setCurrentIndex(index if index >= 0 else 0)
        self.corner_combo.currentIndexChanged.connect(self._on_corner_changed)
        form.addRow(tr("Position"), self.corner_combo)
        form.addRow(
            "",
            _help_label(
                tr(
                    "All four corners already hold map controls - switcher top "
                    "left, legend top right, zoom and scale bottom left, credit "
                    "bottom right. The centres are the only clear space."
                ),
                box,
            ),
        )
        return box

    def _build_colors_group(self, page: QWidget) -> QWidget:
        """Widget colours, as CSS custom properties the runtime already reads."""
        box = QGroupBox(tr("Control colours"), page)
        form = QFormLayout(box)

        self.background_button = QgsColorButton(box)
        self.background_button.setAllowOpacity(False)
        self.background_button.setShowNull(True, tr("Default"))
        self.background_button.colorChanged.connect(
            lambda color: self._on_widget_color("widget_background", color)
        )
        _apply_saved_color(self.background_button, self.state.widget_background)
        form.addRow(tr("Background"), self.background_button)

        self.foreground_button = QgsColorButton(box)
        self.foreground_button.setAllowOpacity(False)
        self.foreground_button.setShowNull(True, tr("Default"))
        self.foreground_button.colorChanged.connect(
            lambda color: self._on_widget_color("widget_foreground", color)
        )
        _apply_saved_color(self.foreground_button, self.state.widget_foreground)
        form.addRow(tr("Text and icons"), self.foreground_button)
        self.scale_combo = QComboBox(box)
        for value, label in CHROME_SCALE_LABELS.items():
            self.scale_combo.addItem(tr(label), value)
        scale_index = self.scale_combo.findData(self.state.chrome_scale)
        self.scale_combo.setCurrentIndex(scale_index if scale_index >= 0 else 0)
        self.scale_combo.currentIndexChanged.connect(self._on_chrome_scale_changed)
        form.addRow(tr("Size"), self.scale_combo)
        form.addRow(
            "",
            _help_label(
                tr(
                    "Scales the legend, layer switcher, zoom controls, scale bar "
                    "and title together. The credit stays fixed so attribution "
                    "cannot be shrunk out of legibility."
                ),
                box,
            ),
        )
        form.addRow(
            "",
            _help_label(
                tr(
                    "Applies to the legend, layer switcher, zoom controls and "
                    "scale bar. Leave both unset to keep the map's own styling."
                ),
                box,
            ),
        )
        return box

    def _build_behaviour_group(self, page: QWidget) -> QWidget:
        box = QGroupBox(tr("Behaviour"), page)
        layout = QVBoxLayout(box)

        self.hover_check = QCheckBox(tr("Open popups on hover instead of click"), box)
        self.hover_check.setChecked(self.state.popup_on_hover)
        self.hover_check.setToolTip(
            tr(
                "Hover replaces click rather than adding to it: bound together, a "
                "click on an already-open popup appears to do nothing."
            )
        )
        self.hover_check.toggled.connect(self._on_hover_toggled)
        layout.addWidget(self.hover_check)
        layout.addWidget(
            _help_label(
                tr("Hover replaces click rather than adding to it."),
                box,
            )
        )

        # qgis2web has no control for this at all: it reuses the QGIS *editing
        # selection* colour as a web hover cue, opaque yellow out of the box, and
        # the only way to change it is Project Properties - outside the plugin.
        # Reported as far back as 2015 (qgis2web#132) and still open.
        highlight_row = QHBoxLayout()
        highlight_row.addWidget(QLabel(tr("Highlight under the cursor"), box))
        self.highlight_button = QgsColorButton(box)
        self.highlight_button.setAllowOpacity(True)
        self.highlight_button.setShowNull(True, tr("Default (white, see-through)"))
        self.highlight_button.setToolTip(
            tr(
                "Shown when the cursor is over a feature. Keep some transparency: a "
                "solid colour hides whatever the feature is drawn on top of."
            )
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
        # The page itself lives in `fidelity_panel`. The dialog owns reading the
        # project and where a jump lands, so those are wired here.
        self.fidelity_panel = FidelityPanel(self)
        self.fidelity_panel.check_requested.connect(self._check_fidelity)
        self.fidelity_panel.show_layer_requested.connect(self._show_layer_in_list)
        self.fidelity_panel.show_tab_requested.connect(self._show_tab)
        return self.fidelity_panel

    @property
    def _fidelity_is_stale(self) -> bool:
        """Whether the report on screen no longer describes the project.

        Derived from the settings as well as flagged: a flag set by hand in each
        handler is how ticking Include, Popup or Label, or renaming the map,
        left an old report looking current. The flag stays for what the
        settings cannot see - layers added, removed, renamed or restyled in QGIS.
        """
        return (
            self._fidelity_dirty
            or self._fidelity_signature is None
            or self._fidelity_signature != self.state.data_snapshot()
        )

    @_fidelity_is_stale.setter
    def _fidelity_is_stale(self, stale: bool) -> None:
        self._fidelity_dirty = stale
        self._refresh_fidelity_freshness()

    def _show_fidelity(
        self, report: FidelityReportBuilder, signature: str | None = None
    ) -> None:
        """Show a report. `signature` is the data snapshot it was read against;
        `None` means the settings as they are now."""
        self._fidelity_signature = (
            signature if signature is not None else self.state.data_snapshot()
        )
        self._fidelity_state = ReportState.CURRENT
        self._fidelity_dirty = False
        self._update_fidelity_strip(report)
        if self._fidelity_summary is not None:
            self.fidelity_panel.show_summary(
                self._fidelity_summary, self._fidelity_facts(self._fidelity_signature)
            )
        self._refresh_fidelity_freshness()

    def _fidelity_facts(self, signature: str | None) -> ReportFacts | None:
        """What the read behind this report knows about each layer.

        Only from the cached read the report came from: a report shown without
        one - or against a different read - gets no facts rather than facts
        about some other version of the project.
        """
        export = self._cached_export
        if not isinstance(export, ExportProject) or signature != self._cached_signature:
            return None
        # The read's own settings are the ones it was taken with; the output
        # type stated beside it is today's.
        current = replace(export, settings=self.state.to_export_settings())
        try:
            return report_facts(current, self._layer_data_bytes(export))
        except Exception:
            # Facts are a courtesy beside the report; never lose the report
            # over one.
            QgsMessageLog.logMessage(
                f"Fidelity facts:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.MessageLevel.Info,
            )
            return None

    def _layer_data_bytes(self, export) -> dict[str, int]:
        """Each layer's share of the file, measured once per read."""
        cached = self._data_bytes_cache
        if cached is not None and cached[0] is export:
            return cached[1]
        sizes = data_bytes_by_layer(export)
        self._data_bytes_cache = (export, sizes)
        return sizes

    def _show_fidelity_error(self, message: str) -> None:
        """Leave the tab saying why it is empty, never just empty."""
        self._fidelity_state = ReportState.FAILED
        self._fidelity_error = message
        self._fidelity_summary = None
        self.fidelity_panel.show_error(message)
        self._show_fidelity_strip()

    def _fidelity_summary_of(self, report: FidelityReportBuilder) -> ReportSummary:
        """Group a report under the names and order the Layers panel shows."""
        tree_layers = self.project.layerTreeRoot().findLayers()
        return summarise(
            getattr(report, "items", ()) or (),
            {tree_layer.layerId(): tree_layer.name() for tree_layer in tree_layers},
            [tree_layer.layerId() for tree_layer in tree_layers],
        )

    def _on_tab_changed(self, index: int) -> None:
        """Fill the Fidelity tab when the user opens it.

        Computed on open rather than on every layer change because building it
        reads every feature of every layer, and on a worker thread so the
        window never freezes while it does.
        """
        if self.tabs.widget(index) is not self._fidelity_page:
            return
        if not self._fidelity_is_stale or self._fidelity_state is ReportState.CHECKING:
            return
        self._check_fidelity()

    def _check_fidelity(self) -> None:
        """Read the project for a fresh report, or show a cached one."""

        def then(_export, report: FidelityReportBuilder) -> None:
            # A cached read skips `_ensure_export`'s own display.
            if self._fidelity_is_stale:
                self._show_fidelity(report, self._cached_signature)

        self._ensure_export(then, tr("Checking the project..."))

    def _begin_fidelity_check(self) -> None:
        # A placeholder rather than an empty table: "empty" reads as "nothing
        # to report".
        self._fidelity_state = ReportState.CHECKING
        self.fidelity_panel.show_checking()
        self._show_fidelity_strip()

    def _end_fidelity_check_without_report(self) -> None:
        """A check that was stopped leaves the last report, marked for what it is."""
        if self._fidelity_state is not ReportState.CHECKING:
            return
        if self._fidelity_summary is None:
            self._fidelity_state = ReportState.NOT_CHECKED
            self.fidelity_panel.show_not_checked()
        else:
            self._fidelity_state = ReportState.OUT_OF_DATE
        self._refresh_fidelity_freshness()

    def _show_layer_in_list(self, layer_id: str) -> None:
        """The Fidelity tab's "Show in Layers": that layer's row, selected."""
        self._show_tab("Layers")
        for index in range(self.layer_tree.topLevelItemCount()):
            item = self.layer_tree.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == layer_id:
                self.layer_tree.setCurrentItem(item)
                self.layer_tree.scrollToItem(item)
                return
        self.status_label.setText(tr("That layer is no longer in the project."))

    def _show_tab(self, title: str) -> None:
        """Open a tab by its English name. Titles on screen are translated, so
        the pages are looked up rather than the text."""
        page = self._pages.get(title)
        if page is not None:
            self.tabs.setCurrentWidget(page)

    # ---- Help tab -------------------------------------------------------

    def _build_tab_corner(self) -> QWidget:
        """Preview and Share, on screen from whichever tab is open.

        The corner used to hold `Get help ↗`, which was put there because "this
        dialog cannot afford another row" - `_resize_to_fit_screen` exists
        because a fixed height once put the Export button behind the Windows
        taskbar with no way to reach it.

        That reasoning still holds and this does not break it. The two actions
        that were at the foot of the dialog move up here, the bottom row empties
        to Discord and Close, and the dialog is no taller than it was. Help goes
        down into the space they left.

        They belong here for the same reason the count does: these are the two
        things a user does *to* the map, and the tab they happen to be reading
        when they decide to do one is not the dialog's business.
        """
        corner = QWidget(self)
        layout = QHBoxLayout(corner)
        layout.setContentsMargins(0, 0, 6, 0)
        # Separate buttons, not one joined control: joined, the filled middle
        # read as the selected one of three choices. These are three acts.
        layout.setSpacing(6)

        # Preview is one pill in two parts, the live switch sitting where
        # Share has its arrow: a dot, red while on. Off, Preview opens the map
        # once. On, it keeps the tab updating, reads "Live" while it does, and
        # a second press stops it - the switch stays on, because the habit
        # outlives one session. Not a menu: a menu of one action and one switch
        # was a click spent on choosing nothing.
        self._preview_group = QFrame(corner)
        self._preview_group.setProperty("role", "cornerGroup")
        group = QHBoxLayout(self._preview_group)
        group.setContentsMargins(0, 0, 0, 0)
        group.setSpacing(0)

        self.preview_button = self._corner_button(
            self._preview_group, tr("Preview"), "preview"
        )
        self.preview_button.setProperty("role", "cornerPart")
        self.preview_button.setProperty("position", "left")
        self.preview_button.clicked.connect(self._on_preview_pressed)
        group.addWidget(self.preview_button)

        self.live_switch = self._corner_button(self._preview_group, "", "live-off")
        self.live_switch.setProperty("role", "cornerPart")
        self.live_switch.setProperty("position", "right")
        self.live_switch.setCheckable(True)
        self.live_switch.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.live_switch.setChecked(
            QSettings().value(LIVE_PREVIEW_KEY, False, type=bool)
        )
        self.live_switch.toggled.connect(self._on_live_toggled)
        group.addWidget(self.live_switch)
        layout.addWidget(self._preview_group)

        self.share_button = self._corner_button(corner, tr("Share"), "share")
        # A plain dropdown for now: pressing it opens the menu. It also holds
        # Discord, which needs no map file, so the button is never disabled;
        # the rows that do need one are.
        self.share_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.share_button.setProperty("hasMenu", True)
        self.share_button.setMenu(self._build_share_menu())
        layout.addWidget(self.share_button)

        # The plugin's main act, so the one filled button, and last - where a
        # dialog's confirming button sits: writes the export in the type and
        # place chosen on the Map tab.
        layout.addSpacing(4)
        self.export_button = self._corner_button(corner, tr("Export"), "export")
        self.export_button.setProperty("accent", True)
        self.export_button.setToolTip(
            tr("Write the map in the type and place chosen on the Map tab")
        )
        self.export_button.clicked.connect(self.on_export)
        layout.addWidget(self.export_button)

        self._show_preview_state()
        return corner

    @staticmethod
    def _corner_button(parent: QWidget, text: str, icon: str) -> QToolButton:
        button = QToolButton(parent)
        button.setText(text)
        button.setProperty("role", "corner")
        button.setProperty("accent", False)
        button.setProperty("hasMenu", False)
        button.setProperty("lineIcon", icon)
        button.setIconSize(QSize(15, 15))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        return button

    def _show_preview_state(self) -> None:
        """Make the switch and the Preview button say what is happening."""
        running = self._live_running()
        live = self.live_switch.isChecked()

        self.live_switch.setProperty("lineIcon", "live" if live else "live-off")
        self.live_switch.setToolTip(
            tr(
                "Live preview is on: Preview keeps the browser tab updating as you "
                "change settings. Click to turn it off."
            )
            if live
            else tr(
                "Live preview is off: Preview opens the map as it is now. Click to "
                "keep the tab updating as you change settings."
            )
        )

        button = self.preview_button
        button.setProperty("live", running)
        button.setText(tr("Live") if running else tr("Preview"))
        button.setToolTip(
            tr("Live preview is running. Press to stop it.")
            if running
            else tr("Open the map in your browser.")
        )
        self._preview_group.setProperty("live", running)
        for widget in (self._preview_group, button, self.live_switch):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if hasattr(self, "theme"):
            theme.paint_line_icons(button.parentWidget(), self.theme)

    def _build_share_menu(self) -> QMenu:
        """Everything that sends the map somewhere, in one list.

        Tooltips rather than descriptions in the labels: one line per action
        keeps the menu scannable, and a style that drops section titles must
        still leave every label readable on its own - which is why each one
        names its object ("Copy file path", not "Copy").
        """
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        self.share_actions: dict[str, QAction] = {}
        # The wording each action carries when it is available. Kept apart from
        # the widgets so `_update_share_enabled` can put it back after having
        # replaced it with the reason the item was greyed out.
        self._share_tooltips: dict[str, str] = {}

        def add(key: str, label: str, tip: str, slot, shortcut: str = "") -> QAction:
            action = QAction(label, self)
            action.setToolTip(tip)
            name = SHARE_MENU_ICONS.get(key, "")
            if name:
                action.setIcon(_theme_icon(name))
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            self.share_actions[key] = action
            self._share_tooltips[key] = tip
            return action

        add(
            "copy_path",
            tr("Copy file path"),
            tr("Copies the map file's path to the clipboard"),
            self.on_copy_map_path,
        )
        add(
            "reveal",
            _reveal_label(),
            tr("Shows the map file in your file manager"),
            self.on_reveal_map_file,
        )

        menu.addSection(tr("Publish"))
        add(
            "publish",
            tr("Publish with NIKA hosting... (waitlist)"),
            tr(
                "Hosting is not live yet. This opens a short form so we can see how "
                "many people want it."
            ),
            self.on_publish,
        )

        # Not wired yet, and present anyway. The menu's shape is part of what is
        # being reviewed, and a row that is missing reads as a decision not to
        # have it - so these say what they will do and admit they do not do it
        # yet, rather than being silently absent or silently inert.
        menu.addSection(tr("NIKA"))
        add(
            "enhance",
            tr("Enhance with NIKA AI..."),
            tr("Opens a hand-off dialog with a prompt to paste into NIKA"),
            lambda: self._not_wired_yet(tr("Enhance with NIKA AI")),
        )
        add(
            "critique",
            tr("Ask NIKA for a critique..."),
            tr(
                "Opens a hand-off dialog asking NIKA to review the map as a "
                "cartographer would"
            ),
            lambda: self._not_wired_yet(tr("Ask NIKA for a critique")),
        )

        add(
            "cartograph",
            tr("Make a cartograph with QGIS2Carto..."),
            tr("Opens QGIS2Carto with this map as a live frame"),
            lambda: self._not_wired_yet(tr("Make a cartograph with QGIS2Carto")),
        )

        menu.addSeparator()
        discord = menu.addAction(tr("Ask on Discord ↗"))
        discord.setToolTip(
            tr(
                "Open the QGIS2WebMap channel on the NIKA Discord in your browser. "
                "Ask a question, suggest something, or report a bug - no GitHub "
                "account needed."
            )
        )
        discord.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(DISCORD_URL)))
        coming = menu.addAction(tr("More ways to share are coming soon"))
        coming.setEnabled(False)
        return menu

    def _not_wired_yet(self, what: str) -> None:
        """Say plainly that a menu row is not built, rather than doing nothing.

        A row that silently does nothing is the worst of the three options - the
        user cannot tell a broken feature from an unfinished one. Naming the
        release it belongs to would be a promise this branch cannot keep, so it
        says only that it is coming.
        """
        QMessageBox.information(
            self,
            tr("{what} is not built yet").format(what=what),
            tr(
                "{what} is coming in a later release.\n\nThe menu entry is here so "
                "the shape of the Share menu can be reviewed; pressing it does "
                "nothing else yet."
            ).format(what=what),
        )

    def _build_settings_tab(self) -> QWidget:
        """The person's own preferences, apart from the map's.

        Everything here is true of every project - who is signed in, the
        language, the theme - so it lives in one familiar place rather than in
        the footer. Live preview stays on the Preview button's menu, beside
        the thing it changes. The map's own options stay on the Map, Layers and
        Appearance tabs.
        """
        page = QWidget(self)
        layout = QVBoxLayout(page)

        account_box = QGroupBox(tr("NIKA account"), page)
        account_form = QFormLayout(account_box)
        self.account_button = AccountButton(PendingHostingBackend(), account_box)
        # The widget itself, not a row layout: a form centres its label only
        # against a widget, and the chip keeps its own width either way.
        account_form.addRow(tr("Account"), self.account_button)
        account_form.addRow(
            "",
            _help_label(
                tr(
                    "Publishing maps to NIKA needs an account. Exporting a file "
                    "never does."
                ),
                account_box,
            ),
        )
        layout.addWidget(account_box)

        language_box = QGroupBox(tr("Language"), page)
        language_form = QFormLayout(language_box)
        self.language_picker = LanguagePicker(language_box)
        self.language_picker.changed.connect(self.languageChanged)
        language_form.addRow(tr("Show this dialog in"), self.language_picker)
        language_form.addRow(
            "",
            _help_label(
                tr(
                    "English is built in. Other languages are a small download "
                    "the first time you pick one. Follow QGIS uses the language "
                    "QGIS is set to, when it is one of these."
                ),
                language_box,
            ),
        )
        layout.addWidget(language_box)

        appearance_box = QGroupBox(tr("Appearance"), page)
        appearance_form = QFormLayout(appearance_box)
        self.theme_picker = QComboBox(appearance_box)
        for value, label in (
            ("auto", tr("Follow QGIS")),
            ("light", tr("Light")),
            ("dark", tr("Dark")),
        ):
            self.theme_picker.addItem(label, value)
        self.theme_picker.setCurrentIndex(
            max(0, self.theme_picker.findData(theme.chosen_theme()))
        )
        self.theme_picker.currentIndexChanged.connect(self._on_theme_changed)
        appearance_form.addRow(tr("Theme"), self.theme_picker)
        layout.addWidget(appearance_box)

        about_box = QGroupBox(tr("About"), page)
        about_form = QFormLayout(about_box)
        about_form.addRow(
            tr("Plugin version"), QLabel(languages.plugin_version(), about_box)
        )
        about_form.addRow(
            tr("OnlyMap runtime"),
            QLabel(str(read_lock().get("version") or tr("not pinned")), about_box),
        )
        layout.addWidget(about_box)

        layout.addStretch(1)
        return page

    def _on_theme_changed(self) -> None:
        """Restyle in place: colours are not baked into labels the way text is."""
        QSettings().setValue(theme.THEME_KEY, self.theme_picker.currentData())
        self.theme = theme.apply(self)
        theme.recolour_document(self.help_browser.document(), self.theme)
        self.account_button.refresh()
        self._update_basemap_warning()
        self._update_terrain_warning()
        self._update_size_note()

    def _build_help_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        browser = QTextBrowser(page)
        self.help_browser = browser
        # Markdown keeps one source of truth: the same files the website serves.
        browser.setDocument(render_help_document(browser))
        browser.setOpenLinks(False)
        browser.anchorClicked.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(browser)

        # Read something, or ask someone. The pair is the point - the guides
        # above answer what is already known to go wrong, and the second button
        # is for everything else.
        #
        # Stacked rather than side by side: they are not alternatives to choose
        # between, they are two things to do next, and a full-width target is
        # the easier one to hit on the small dialog this has to survive at.
        docs_button = QPushButton(tr("Open the full documentation"), page)
        docs_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))
        layout.addWidget(docs_button)

        community_button = QPushButton(tr("Join the community"), page)
        community_button.setToolTip(
            tr(
                "Open the NIKA Discord in your browser. Problems, suggestions and "
                "bug reports all go to the QGIS2WebMap channel - no GitHub account "
                "needed."
            )
        )
        community_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(COMMUNITY_URL))
        )
        layout.addWidget(community_button)
        return page

    # ---- Buttons --------------------------------------------------------

    def _build_button_row(self) -> QHBoxLayout:
        """Chrome only: the running commentary, and a way to leave.

        Everything that acts on the map is in the tab corner, the account and
        language are on the Settings tab, and Discord is in the Share menu.
        """
        row = QHBoxLayout()

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)

        close = QPushButton(tr("Close"), self)
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

        self.cancel_button = QPushButton(tr("Cancel"), row)
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
            self.status_label.setText(tr("Still working on the last request."))
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
            self.preview_button.setEnabled(False)
            self.export_button.setEnabled(False)
            for action in getattr(self, "share_actions", {}).values():
                action.setEnabled(False)
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
        self.progress_bar.setFormat(tr("Stopping..."))

    def _on_job_succeeded(self, result, on_success) -> None:
        # Cleared *before* the callback, so a callback that starts the next
        # stage - reading then writing - is not refused by the one-job rule.
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        try:
            on_success(result)
        except Exception as exc:
            self._report_failure(tr("Export failed"), exc)

    def _on_job_cancelled(self) -> None:
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        self._end_fidelity_check_without_report()
        self.status_label.setText(tr("Stopped. Nothing was written."))

    def _on_job_failed_quietly(self, message: str, details: str) -> None:
        """A background failure the user did not ask about. Log and say so once."""
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        QgsMessageLog.logMessage(details, LOG_TAG, level=Qgis.MessageLevel.Warning)
        if self._fidelity_state is ReportState.CHECKING:
            self._show_fidelity_error(message)
        self.status_label.setText(
            tr(
                "Live preview could not be updated ({message}) - see the QGIS log."
            ).format(message=message)
        )

    def _on_job_failed(self, message: str, details: str) -> None:
        self._job = None
        self._set_busy(False)
        self.cancel_button.setEnabled(True)
        QgsMessageLog.logMessage(details, LOG_TAG, level=Qgis.MessageLevel.Critical)
        # Only a failed read is a failed check. A write failing says nothing
        # about what the map contains.
        if self._fidelity_state is ReportState.CHECKING:
            self._show_fidelity_error(message)
        show_failure(self, tr("Something went wrong"), message)

    # ---- Fidelity strip -------------------------------------------------

    def _build_fidelity_strip(self) -> QWidget:
        """What the export changes, visible from every tab.

        This plugin's one real advantage over the incumbent is telling you what
        your recipient loses, so the count lives here, always on screen, and
        opens the detail when clicked.
        """
        self.fidelity_strip = FidelityStrip(self)
        # Under their old names too, for the tests that read them directly.
        self.fidelity_summary = self.fidelity_strip.label
        self.fidelity_link = self.fidelity_strip.button
        self.fidelity_link.setProperty("role", "link")
        self.fidelity_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fidelity_strip.button_clicked.connect(self._on_fidelity_strip_clicked)
        return self.fidelity_strip

    def _on_fidelity_strip_clicked(self) -> None:
        # "Check now" and "Check again" check where the user is; everything
        # else the strip offers is reading, which happens on the Fidelity tab.
        if self._fidelity_state in (ReportState.NOT_CHECKED, ReportState.OUT_OF_DATE):
            self._check_fidelity()
        else:
            self._show_fidelity_tab()

    def _show_fidelity_tab(self) -> None:
        self.tabs.setCurrentWidget(self._fidelity_page)

    def _update_fidelity_strip(self, report) -> None:
        """Summarise the report in one line, problems first."""
        self._fidelity_summary = self._fidelity_summary_of(report)
        self.fidelity_strip.show_message(
            strip_message(ReportState.CURRENT, self._fidelity_summary)
        )

    def _show_fidelity_strip(self) -> None:
        self.fidelity_strip.show_message(
            strip_message(
                self._fidelity_state, self._fidelity_summary, self._fidelity_error
            )
        )

    def _refresh_fidelity_freshness(self) -> None:
        """Mark the report out of date the moment it stops describing the map."""
        if getattr(self, "_shut_down", False) or not hasattr(self, "fidelity_panel"):
            return
        if not hasattr(self, "fidelity_strip"):
            return
        if self._fidelity_state in (ReportState.CURRENT, ReportState.OUT_OF_DATE):
            self._fidelity_state = (
                ReportState.OUT_OF_DATE
                if self._fidelity_is_stale
                else ReportState.CURRENT
            )
        self.fidelity_panel.set_out_of_date(
            self._fidelity_state is ReportState.OUT_OF_DATE
        )
        self._show_fidelity_strip()

    # ---- Live preview ---------------------------------------------------

    def _live_running(self) -> bool:
        """Whether a preview is open and following the dialog.

        Both halves, because a raster map's one-off preview is served too - a
        browser will not read the raster from a page opened off the disk - and
        that preview is not live: nothing rebuilds it.
        """
        timer = getattr(self, "_watch_timer", None)
        return (
            getattr(self, "_server", None) is not None
            and timer is not None
            and timer.isActive()
        )

    def _on_live_toggled(self, live: bool) -> None:
        QSettings().setValue(LIVE_PREVIEW_KEY, live)
        if not live and self._live_running():
            self._stop_live_preview()
            self.status_label.setText(tr("Live preview stopped."))
        self._show_preview_state()

    def _on_preview_pressed(self) -> None:
        """Preview, or stop the live preview that is running."""
        if not self._live_running():
            self.on_preview()
            return
        self._stop_live_preview()
        self.status_label.setText(tr("Live preview stopped."))

    def _poll_for_changes(self) -> None:
        """Restart the debounce whenever any setting differs from last build."""
        if self._shut_down or self._server is None:
            return

        if self._preview_tab_was_closed():
            self._stop_live_preview()
            self.status_label.setText(
                tr(
                    "Preview tab closed, so the preview server stopped. "
                    "Press Preview to start it again."
                )
            )
            self._update_save_status()
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
        # Live, not merely served: a raster map's one-off preview is served
        # too, and rewriting it would make it half-live behind the user's back.
        if self._shut_down or not self._live_running():
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
                progress.step(READ_SHARE, tr("Updating the preview..."))
                write_preview(
                    export, self._project_identity(), writer=writer, live=True
                )

            def on_written(_result) -> None:
                if self._shut_down or self._server is None:
                    return
                self._server.notify_reload()
                self.status_label.setText(tr("Live preview updated."))

            self._start_job(work, on_written, tr("Updating the preview..."), quiet=True)

        self._ensure_export(then, tr("Updating the preview..."), quiet=True)

    def _preview_tab_was_closed(self) -> bool:
        """Whether the browser has gone away and is not coming back.

        The server used to outlive its audience: it ran until the dialog closed
        or the checkbox was unticked, with nothing on screen saying a socket was
        open. Closing the tab is the clearest statement a user can make that
        they are done looking, so it is treated as one.

        **Why a grace period rather than a count of zero.** `notify_reload` tells
        the page to reload itself, which drops its `EventSource` and opens a new
        one, so the client count dips to zero on every single rebuild. Acting on
        that dip would kill the server on the user's first edit - the grace has
        to be comfortably longer than a reload takes, and a reload here re-parses
        a multi-megabyte inlined runtime.
        """
        if self._job is not None:
            # A rebuild is in flight, so the tab is mid-reload by definition.
            return False
        idle = self._server.idle_seconds if self._server is not None else None
        return idle is not None and idle > PREVIEW_IDLE_STOP_SECONDS

    def _stop_live_preview(self) -> None:
        self._watch_timer.stop()
        self._rebuild_timer.stop()
        server, self._server = self._server, None
        if server is not None:
            with contextlib.suppress(Exception):
                server.stop()
        if hasattr(self, "preview_button"):
            self._show_preview_state()

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
            self._set_ready(
                False, tr("Add a vector or raster layer to the project to export.")
            )
        elif not selected:
            self._set_ready(False, tr("Tick at least one layer to include."))
        else:
            count = len(selected)
            self._set_ready(
                True,
                plural(
                    count, "{n} layer will be exported.", "{n} layers will be exported."
                ),
            )

        self._update_export_summary(len(selected))

    def _set_ready(self, ready: bool, message: str) -> None:
        self.preview_button.setEnabled(ready)
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(ready)
        # Share's rows need a destination as well as something to put in it,
        # so their own rule is applied on top rather than instead.
        self._export_ready = ready
        self._update_share_enabled()
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
            progress.step(
                percent,
                tr("Reading '{name}' ({done} of {total})").format(
                    name=name, done=done + 1, total=total
                ),
            )

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
            # The read is reused, but the settings it carries are the ones it
            # was taken with: a colour or control changed since would never
            # reach the page. Today's settings replace them.
            current = replace(
                self._cached_export, settings=self.state.to_export_settings()
            )
            then(current, self._cached_report)
            return True

        # Read on the GUI thread, where the canvas lives, and hand the plain
        # value to the worker. Calling `iface.mapCanvas()` from the thread would
        # be touching a widget from the wrong side.
        self._pending_canvas_extent = self._canvas_extent()
        self._pending_license_key = self._license_key()

        def work(progress: Progress):
            progress.step(0, tr("Reading the project..."))
            return self._read_current_project(progress)

        def on_success(result) -> None:
            export, report = result
            self._cached_export = export
            self._cached_report = report
            self._cached_signature = signature
            self._show_fidelity(report, signature)
            self._update_size_note()
            then(export, report)

        # Every read is a check: it is what fills the Fidelity tab, whichever
        # button asked for it.
        started = self._start_job(work, on_success, label, quiet=quiet)
        if started:
            self._begin_fidelity_check()
        return started

    def _invalidate_export_cache(self) -> None:
        """Forget the last read. Called whenever the data could have changed."""
        self._cached_export = None
        self._cached_report = None
        self._cached_signature = None
        # A file written from the old project is as out of date as the read.
        self._written_signature = None
        self._update_save_status()
        self._data_bytes_cache = None

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
            tr("The OnlyMap runtime is needed to build a map. Nothing was written.")
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
        self._ensure_export(self._preview_with, tr("Reading the project..."))

    def _preview_with(self, export, _report) -> None:
        """Write and open the preview. Called once a read is in hand.

        Live means the tab follows the dialog; it never means the export file
        does. Only Export writes the user's map - a preview that rewrote it on
        every edit changed their file without their asking.
        """
        if not self._runtime_ready():
            return

        identity = self._project_identity()
        writer = self._writer()
        live = self.live_switch.isChecked()
        # A raster map is previewed as a folder, which only works served - so
        # even a one-off preview of one goes through the local server. It is
        # not live for that: nothing watches for changes.
        served = live or preview_mode(export) is OutputMode.FOLDER

        def work(progress: Progress):
            progress.step(READ_SHARE, tr("Building the preview..."))
            return write_preview(export, identity, writer=writer, live=live)

        def on_written(result) -> None:
            if not served:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.entry_path)))
                self.status_label.setText(
                    tr(
                        "Preview opened. Press Preview again after making changes - "
                        "your position on the map is kept."
                    )
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
            if not live:
                self.status_label.setText(
                    tr(
                        "Preview opened. Press Preview again after making changes - "
                        "your position on the map is kept."
                    )
                )
                return
            self._watch_timer.start()
            self._show_preview_state()
            self.status_label.setText(
                tr("Live preview open. Changes here update the tab automatically.")
            )

        self._start_job(work, on_written, tr("Building the preview..."))

    def _fall_back_to_file_preview(self, entry_path: Path) -> None:
        """Open the preview as a file, and say why it is not live.

        Deliberately not a modal: the user asked for a preview and there is one,
        so interrupting them to explain a downgrade they did not choose would be
        worse than the downgrade. No server runs, so the Preview button stays as
        it was rather than claiming to be live.
        """
        QgsMessageLog.logMessage(
            f"Could not start the live preview server:\n{traceback.format_exc()}",
            LOG_TAG,
            level=Qgis.MessageLevel.Warning,
        )
        self._stop_live_preview()

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry_path)))
        self.status_label.setText(
            tr(
                "Live preview could not start on this machine, so the preview opened "
                "as a file. Reload the tab after making changes."
            )
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

    # ---- Share -----------------------------------------------------------

    def _with_current_map_file(self, then) -> None:
        """Run `then(path)` once the map file on disk matches the project.

        The one rule the whole Share menu is built on. Every action needs a file
        that is current, and none of them should make the user think about
        producing one: pressing Export before Copy file path was a step that
        existed only because the dialog could not do it itself.

        Rebuilds only when something actually changed - `_ensure_export` keys
        its cache on `data_snapshot`, so a run of Share actions after one write
        costs nothing.
        """
        destination = self._map_file()
        if destination is None:
            self._warn_no_map_file()
            return

        if destination.exists() and not self._map_file_is_stale():
            then(destination)
            return

        self._export_then = then
        self.on_export()

    def _map_file_is_stale(self) -> bool:
        """Whether the project has moved since the map file was last written."""
        return self._written_signature != self.state.snapshot()

    def on_copy_map_path(self) -> None:
        """The path, on the clipboard, for pasting into anything at all.

        Does not build first. This is the one Share action that asks about the
        file's *name* rather than its contents, and making somebody wait through
        a rebuild to learn where a file lives would be absurd.
        """
        destination = self._map_file()
        if destination is None:
            self._warn_no_map_file()
            return
        QGuiApplication.clipboard().setText(str(destination))
        self.status_label.setText(tr("Copied {path}").format(path=destination))

    def on_reveal_map_file(self) -> None:
        """Open the file manager at the folder holding the map file.

        **The folder, not the file selected in it.** Selecting the file needs a
        platform command - `open -R` on macOS, `explorer /select,` on Windows -
        and running system commands is exactly what CONTRIBUTING.md rule 2
        forbids, after qgis2web shelled out to `apt-get` and took QGIS down with
        it on every non-Debian machine. Reaching for `QProcess` instead of
        `subprocess` would dodge the guard rather than honour the rule.

        The folder is enough in practice: the map file is the one the user just
        named in the field above, so they know what they are looking for. Copy
        file path is there for the times they do not.
        """
        self._with_current_map_file(
            lambda destination: (
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination.parent))),
                self.status_label.setText(
                    tr("Opened {path}").format(path=destination.parent)
                ),
            )
        )

    def on_publish(self) -> None:
        """Hosting, for as long as hosting is a form rather than a feature.

        A demand probe, not a feature: this opens the waitlist so the interest
        can be counted before the auth flow, blob storage and expiry job get
        built on a hypothesis. Deliberately one method with one job, so the
        release that makes hosting real replaces a body rather than a menu.
        """
        QDesktopServices.openUrl(QUrl(FEATURE_REQUEST_URL))
        self.status_label.setText(
            tr("Hosting is not live yet - the waitlist form opened in your browser.")
        )

    def on_export(self) -> None:
        """Write the export in the type and place chosen on the Map tab."""
        self._pending_mode = self.state.output_mode
        # Taken before the read, not after the write: a setting changed while
        # this runs is not in the file, so the file must not claim it.
        self._pending_signature = self.state.snapshot()
        # Reading fills the Fidelity tab and needs no runtime; see on_preview.
        self._ensure_export(self._export_with, tr("Reading the project..."))

    def _export_with(self, export, _report) -> None:
        """Everything after the read: the checks, the location, the write."""
        # A blocked item means a layer could not be read at all. Exporting
        # anyway writes a map that is quietly missing data while the dialog
        # reports success, and the Processing algorithm already refuses on
        # the same input - the two entry points must not disagree.
        # An export with no layer in it is refused: there is no map to write.
        # A Blocked item is different - a layer that could not be read - and
        # that is the user's call once they have been told which one, not a
        # refusal. The Processing algorithm still refuses, because a batch run
        # has nobody to ask.
        if not export.exportable_layers:
            self._export_then = None
            self._warn_not_exportable(export)
            return
        if not self._confirm_despite_problems(export, "Export"):
            self._export_then = None
            return

        # Set by whichever action started this, rather than read off the state:
        # packaging is no longer something the dialog holds between presses, it
        # is an argument to the one press.
        mode = self._pending_mode

        # Issue #29: never quietly hand over a single file that will not travel.
        # This used to switch the selection to Share ZIP itself, which was worse
        # than the problem: the radio buttons did not follow, so the dialog said
        # Standalone HTML while it wrote a zip. The rule now lives on the Map
        # tab where it can inform the choice, and this is the last reminder
        # before it costs anything. The user's choice stands either way.
        if mode is OutputMode.STANDALONE_HTML:
            # A raster is not a size problem with a "write it anyway": no single
            # file can carry one, so the writer would refuse after the user had
            # said yes. Refused here, with the format that does work.
            raster = standalone_raster_reason(export)
            if raster is not None:
                QMessageBox.warning(self, tr("Choose Folder or Share ZIP"), raster)
                self._export_then = None
                return
            reason = standalone_ineligible_reason(export)
            if reason is not None and not self._confirm_oversized_single_file(reason):
                self._export_then = None
                return

        # After the blocking checks: no point downloading the runtime for a
        # project that was never going to export.
        if not self._runtime_ready():
            self._export_then = None
            return

        destination = self._resolve_destination(mode)
        if destination is None:
            self._export_then = None
            return

        writer = self._writer()

        def work(progress: Progress):
            progress.step(READ_SHARE, tr("Writing the map..."))
            try:
                result, outcome = build_artifact(
                    export, destination, mode=mode, writer=writer
                )
            except ExportBlockedError as exc:
                # Returned rather than raised: a blocked export is the user's to
                # fix, and raising would route it to the crash reporter with a
                # "please report this" that has nothing to report.
                return exc
            progress.step(100, tr("Finishing..."))
            return result, outcome

        def on_written(built) -> None:
            if isinstance(built, ExportBlockedError):
                QMessageBox.warning(
                    self,
                    tr("Cannot export"),
                    tr("This map cannot be exported yet:")
                    + "\n\n"
                    + "\n".join(f"- {reason}" for reason in built.reasons),
                )
                self._export_then = None
                return

            result, outcome = built
            save_state(self.project, self.state)
            self._remember_destination(destination)

            # The file:// check happens here, against the bytes that ship.
            self._last_export_path = Path(outcome.path)
            # What the map file was written from, so a later Share action
            # can tell "already current" from "the project has moved on".
            self._written_signature = self._pending_signature
            self._update_save_status()

            summary = outcome.summary()
            self.status_label.setText(summary)

            # The writer's warnings (licence caps, a runtime that does not match
            # the lock, an oversized single file) are things the recipient will
            # experience. Dropping them here is how a user ends up handing over
            # a map with layers missing and no idea it happened.
            if result.warnings:
                QMessageBox.warning(
                    self,
                    tr("Exported, with warnings"),
                    summary
                    + "\n\n"
                    + tr("The exported map has known limitations:")
                    + "\n\n"
                    + "\n".join(f"- {warning}" for warning in result.warnings)
                    + "\n\n"
                    + tr("The Fidelity tab lists the detail."),
                )

            # A Share action asked for this write as a step on the way to
            # something else. Confirming it with a modal would interrupt the
            # thing they actually asked for, so the follow-on runs instead.
            then, self._export_then = self._export_then, None
            if then is not None:
                then(Path(outcome.path))
            elif not result.warnings:
                QMessageBox.information(self, tr("Export complete"), summary)

        self._start_job(work, on_written, tr("Writing the map..."))

    def _confirm_oversized_single_file(self, reason: str) -> bool:
        """Warn about an unwieldy single file, and let them have it anyway.

        A standalone export inlines every feature, so there is no sibling folder
        to carry the weight - the size is inherent to the format rather than a
        fault. That makes this the user's call, not ours.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("This will be a large single file"))
        box.setText(reason)
        box.setInformativeText(
            tr(
                "The map file inlines all the data into the one HTML file, which is "
                "why it is this big. Exporting as Share ZIP on the Map tab packages "
                "the same map to send more easily."
            )
        )
        write_anyway = box.addButton(
            tr("Write it anyway"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(write_anyway)
        box.exec()

        # No "switch to zip" button any more. Packaging is no longer a mode this
        # dialog holds, so there is nothing to switch: the zip is one Share
        # action away, and offering to change a setting that no longer exists
        # would be worse than saying nothing.
        return box.clickedButton() is write_anyway

    def _resolve_destination(self, mode: OutputMode) -> Path | None:
        """Where to write: the Save to field, whichever type is chosen.

        No picker at Export time. The type and the place were both chosen on
        the Map tab, so pressing Export does exactly what it says.
        """
        destination = self._map_file()
        if destination is None:
            self._warn_no_map_file()
            return None
        if not destination.parent.exists():
            QMessageBox.warning(
                self,
                tr("Folder not found"),
                tr(
                    "There is no folder at {path}.\n\nPick "
                    "somewhere that exists, or create it first."
                ).format(path=destination.parent),
            )
            return None
        return destination

    def _warn_no_map_file(self) -> None:
        """The one thing standing between the user and every Share action."""
        QMessageBox.information(
            self,
            tr("Choose where to save first"),
            tr(
                "This map has nowhere to be saved yet.\n\nFill in Save to on the "
                "Map tab, then try again."
            ),
        )

    def _confirm_despite_problems(self, export, action: str) -> bool:
        """Name what will not work, then let the user go on or go and look.

        Blocked used to disable Export outright, which left someone with one
        broken layer among ten unable to share the other nine.
        """
        choice = ask_before(self, export.blocking_items, action)
        if choice is Proceed.REVIEW:
            self._show_fidelity_tab()
        return choice is Proceed.CONTINUE

    def _warn_not_exportable(self, export) -> None:
        """Explain a refusal in terms of what the user has to fix in QGIS."""
        reasons = [item.detail for item in export.blocking_items] or [
            tr("No layer in the selection could be read.")
        ]
        QMessageBox.warning(
            self,
            tr("Cannot export"),
            tr("This map cannot be exported yet:")
            + "\n\n"
            + "\n".join(f"- {reason}" for reason in reasons)
            + "\n\n"
            + tr(
                "Fix these in QGIS, then export again. The Fidelity tab lists "
                "everything that was checked."
            ),
        )

    def _report_failure(self, title: str, exc: Exception) -> None:
        """Surface a failure without ever taking QGIS down with it."""
        QgsMessageLog.logMessage(
            f"{title}:\n{traceback.format_exc()}",
            LOG_TAG,
            level=Qgis.MessageLevel.Critical,
        )
        show_failure(self, title, str(exc))

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
                level=Qgis.MessageLevel.Warning,
            )
        # Before the watcher, because a rebuild triggered mid-teardown would run
        # against a half-disconnected dialog.
        self._stop_live_preview()
        self._fidelity_timer.stop()
        # The preview files exist only to be served by the dialog's own
        # server; once it stops they are dead weight, and on Windows nothing
        # else ever clears the temp directory. The sweep also collects
        # previews orphaned by crashes or renamed projects.
        with contextlib.suppress(Exception):
            remove_preview(self._project_identity())
            prune_stale_previews()
        self.watcher.disconnect_all()
        self.content_watcher.disconnect_all()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._shutdown()
        super().closeEvent(event)
