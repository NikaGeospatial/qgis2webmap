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
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from html import escape as escape_html
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import (
    Qgis,
    QgsMapLayer,
    QgsMessageLog,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsColorButton
from qgis.PyQt import sip
from qgis.PyQt.QtCore import (
    QObject,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QKeyEvent,
    QPalette,
    QResizeEvent,
    QShowEvent,
    QTextDocument,
    QTextFrame,
    QTextLength,
    QTextTable,
)
from qgis.PyQt.QtWidgets import (
    QAction,
    QBoxLayout,
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
    QStackedWidget,
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
from ..core.project_reader import (
    PendingProject,
    extent_from_canvas,
    finish_project,
    prepare_project,
    resolve_title,
    with_output_notes,
)
from ..core.report_facts import ReportFacts, report_facts
from ..core.save_folders import (
    default_save_root,
    save_root,
    set_save_root,
    unused_path,
    web_maps_folder,
)
from ..core.settings import (
    MAX_PRECISION,
    MIN_PRECISION,
    PRECISION_FULL,
    DialogOutput,
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
)
from ..packaging.runtime_manager import read_lock
from ..writers.onlymap_writer import (
    PLUGIN_VERSION,
    ExportBlockedError,
    OnlyMapWriter,
)
from . import languages, theme
from .account_button import AccountButton, PendingHostingBackend
from .background_job import BackgroundJob, Progress
from .cartograph import CartographController
from .fidelity_panel import FidelityPanel, FidelityStrip, Proceed, ask_before
from .fitted_label import FittedLabel
from .i18n import N_, active_language, plural, tr
from .language_picker import LanguagePicker, make_offer
from .layer_watcher import LayerContentWatcher, LayerTreeWatcher
from .links import (
    COMMUNITY_URL,
    DISCORD_URL,
    DOCS_URL,
    FEATURE_REQUEST_URL,
    REPO_URL,
)
from .live_server import PreviewServer
from .output_switcher import OutputSwitcher
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

# `manifest_builder.OSM_FILE_NOTE`, for the catalogue: core has no Qt, so the
# warning is translated here, where the Map tab shows it. A test keeps the two
# in step.
_OSM_FILE_NOTE_FOR_CATALOGUE = N_(
    "OpenStreetMap's tile servers refuse maps opened straight from a file, so "
    "recipients who double-click this map may see 'Access blocked' tiles. "
    "Choose Liberty - the same OpenStreetMap data - or another basemap, or "
    "export as a Folder for a website."
)

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
LAYER_TREE_MIN_HEIGHT = 200

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

# The folder the last export was written to. QSettings for the same reason as
# the live-preview flag: "where I keep my maps" is a property of the person, not
# of the project, so it should follow them into the next one.
LAST_EXPORT_DIR_KEY = "qgis2webmap/lastExportDir"
# The narrowest a drop-down gets in the cartograph's panel, which may make
# it shorten its text.
COMPACT_COMBO_WIDTH = 120

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

# What the dialog opens at when the screen has room for it. A fifth larger
# than the 960x640 it was, so the cartograph's page and its panel both have
# room side by side; `_resize_to_fit_screen` still shrinks it to the screen.
DEFAULT_DIALOG_WIDTH = 1152
DEFAULT_DIALOG_HEIGHT = 768
# The most of the screen's work area (taskbars excluded) it opens across.
OPENING_SHARE_W = 0.88
OPENING_SHARE_H = 0.85

# How much of the progress bar the reading stage owns. Reading every feature of
# every layer dominates the wall clock; writing and packaging share the rest, so
# the bar keeps moving rather than sitting at 100% through the slow part.
READ_SHARE = 70
# Where the bar stands when the finished artifact starts being put in place - the
# last point at which Cancel still stops an export.
COMMIT_AT = 95

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


def _name_fields_first(fields: list[str]) -> list[str]:
    """Field names, the ones that look like a name first, otherwise in order."""
    hints = ("name", "title", "label", "nom", "nombre", "nome")
    named = [f for f in fields if any(h in f.lower() for h in hints)]
    return named + [f for f in fields if f not in named]


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


class _ReflowRow(QWidget):
    """A tree row that stacks its controls once the tree is too narrow for them
    side by side.

    The Cartograph panel is about 240 px wide and a settings row of three
    combos needs twice that, so the right-hand controls ran off the edge and
    the row looked blank. Stacked, each control gets the row's full width.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._item: QTreeWidgetItem | None = None
        self._stacked = False
        self._side_by_side_width = 0

    def _box(self) -> QBoxLayout | None:
        layout = self.layout()
        return layout if isinstance(layout, QBoxLayout) else None

    def fit(self, item: QTreeWidgetItem) -> None:
        """Size the row from its own layout, side by side, then reflow."""
        self._item = item
        box = self._box()
        self.ensurePolished()
        for child in self.findChildren(QWidget):
            child.ensurePolished()
        if box is not None:
            self._set_stacked(False)
            self._side_by_side_width = box.sizeHint().width()
        self._update_item_hint()

    def _set_stacked(self, stacked: bool) -> None:
        box = self._box()
        if box is None:
            return
        self._stacked = stacked
        box.setDirection(
            QBoxLayout.Direction.TopToBottom
            if stacked
            else QBoxLayout.Direction.LeftToRight
        )
        box.setSpacing(4 if stacked else 8)
        box.invalidate()

    def _update_item_hint(self) -> None:
        """Make the tree row as tall as the controls placed in it.

        A tree sizes a row for its text, and an item widget is squeezed into
        that rect: the themed combo boxes are taller than a line of text, so
        their bottom edge was cut off. Polished first, so the height is the
        themed one.

        Taken from the row's own layout, never from every descendant: a combo
        box's hidden popup list reports a height near 190 px, which made a row
        of one 30 px combo 200 px tall and mostly blank.

        The width is real because Qt drops a size hint with a negative width.
        The extra height is the theme's `QTreeView::item` padding (3 px above
        and below), which the tree takes out of the widget's rect.
        """
        # A refresh deletes the item and this row while a deferred update can
        # still be queued for them.
        if self._item is None or sip.isdeleted(self._item) or sip.isdeleted(self):
            return
        box = self._box()
        hint = self.sizeHint()
        height = hint.height()
        if box is not None:
            # Qt5 keeps the layout's hint from before the polish, which left
            # the row 6 px shorter than the themed combos in it; the tallest
            # control is the floor whatever the layout remembers. Direct
            # members only, so a hidden popup list does not count.
            controls = [box.itemAt(i).widget() for i in range(box.count())]
            visible_heights = [c.sizeHint().height() for c in controls if c]
            if self._stacked:
                height = max(height, sum(visible_heights))
            else:
                height = max(height, max(visible_heights, default=0))
        self._item.setSizeHint(0, QSize(max(hint.width(), 1), height + 8))
        tree = self._item.treeWidget()
        if tree is not None:
            tree.doItemsLayout()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._item is None or self._box() is None or self._side_by_side_width <= 0:
            return
        stacked = self.width() < self._side_by_side_width
        if stacked != self._stacked:
            self._set_stacked(stacked)
            # Deferred: a size hint changed while the tree is laying this very
            # widget out is taken for the old height, and the controls overlap.
            QTimer.singleShot(0, self._update_item_hint)


def _fit_row_to_widget(item: QTreeWidgetItem, widget: QWidget) -> None:
    """Size a tree row from the controls placed in it; see `_ReflowRow`."""
    if isinstance(widget, _ReflowRow):
        widget.fit(item)


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
    # Not a Tab stop of its own: the controls inside are, and a focused scroll
    # area shows no sign of having the focus.
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
    (N_("Your first cartograph"), "cartograph-first-export.md"),
    (N_("The Cartograph dialog, tab by tab"), "cartograph-dialog.md"),
    (N_("Live or static frames"), "live-or-static-frames.md"),
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


def _search_key(text: str) -> str:
    """Case- and accent-insensitive form of `text` for the layer search."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class _SearchBox(QLineEdit):
    """Esc empties the box when it holds text; only on an empty box does it
    pass on, so it still closes the dialog."""

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt override
        if event is not None and event.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)


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
        self._cached_canvas_extent = None
        # Captured on this thread before each job starts; see `_ensure_export`.
        self._pending_canvas_extent = None
        self._pending_license_key: str | None = None

        # Which tier the write in flight is producing, and what to do once it
        # lands. Both belong to one press rather than to the dialog: the type
        # can be changed on the Map tab while a write is still running.
        self._pending_mode: OutputMode = OutputMode.STANDALONE_HTML
        self._export_then = None
        # The state the map file on disk was last written from. `None` means
        # "not by us, not this session" - so the first Share action rebuilds
        # rather than trusting a file it has never seen written.
        self._written_signature: str | None = None
        self._pending_signature: str | None = None
        self._pending_destination: Path | None = None
        self._project_generation = 0
        self._pending_generation = 0

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

        # Owns everything the Cartograph (Beta) output needs that is not the
        # switch itself or which tabs/corner are on screen - see
        # `ui/cartograph/controller.py`. Built before the tabs below because
        # `_build_cartograph_tab`/`_build_frames_tab` ask it for their widgets.
        self._cartograph = CartographController(self)

        self.tabs = QTabWidget(self)
        self._map_tab_widget = _scrollable(self._build_map_tab())
        self.tabs.addTab(self._map_tab_widget, tr("Map"))
        # The page is shared with the Cartograph panel, which scrolls on its
        # own; on the web map it sits in a scroll area of its own so a short
        # window scrolls the tab instead of squeezing the list to nothing.
        self._layers_page = self._build_layers_tab()
        self._layers_scroll = _scrollable(self._layers_page)
        self._layers_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tabs.addTab(self._layers_scroll, tr("Layers"))
        self._appearance_tab_widget = _scrollable(self._build_appearance_tab())
        self.tabs.addTab(self._appearance_tab_widget, tr("Appearance"))
        # Held, not found by title: the title is translated.
        self._fidelity_page = self._build_fidelity_tab()
        self.tabs.addTab(self._fidelity_page, tr("Fidelity"))
        # Settings and Help are not tabs: they are the person's, not the
        # map's, so they open from icons in the header (`_show_aside`).
        self._help_page = self._build_help_tab()
        # Tabs by their English names, for jumps that must survive translation.
        self._pages = {
            "Map": self._map_tab_widget,
            "Layers": self._layers_scroll,
            "Appearance": self._appearance_tab_widget,
            "Fidelity": self._fidelity_page,
            "Help": self._help_page,
        }
        # The web map's actions. They sit in the header beside the output's
        # name, with the Cartograph output's own; `_apply_output` shows the
        # one that matches.
        self._web_corner = self._build_tab_corner()
        self._settings_page = _scrollable(self._build_settings_tab())
        self._pages["Settings"] = self._settings_page
        self._fidelity_is_stale = True
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # The Cartograph output's whole screen - the page and its panel -
        # built now and swapped in by `_apply_output`, never rebuilt, so a
        # user's in-progress edit is never lost by switching outputs and back.
        self._cartograph_workspace = self._cartograph.build_cartograph_tab()

        # What fills the dialog: the web map's tabs, the cartograph's
        # workspace, or Settings / Help with a way back.
        self.views = QStackedWidget(self)
        self.views.addWidget(self.tabs)
        self.views.addWidget(self._cartograph_workspace)
        self.views.addWidget(self._build_aside())

        self._header = self._build_header()
        layout.addWidget(self._header)
        layout.addWidget(self.views)
        # Puts Map/Appearance or Cartograph/Frames on screen to match what was
        # last saved, without marking a project that only opened the dialog as
        # changed - see `_apply_output`'s `persist` argument.
        self._apply_output(self.state.output, persist=False)
        layout.addWidget(self._build_fidelity_strip())
        # The cartograph's Checks section says the same in its header.
        self.fidelity_strip.setVisible(self.state.output is DialogOutput.WEB_MAP)
        self._show_active_fidelity()
        layout.addWidget(self._build_progress_row())
        layout.addLayout(self._build_button_row())

        # Last, so the stylesheet meets every widget the tabs built. See
        # `theme` for why it is scoped to this dialog and never to QGIS.
        self.theme = theme.apply(self)
        theme.recolour_document(self.help_browser.document(), self.theme)
        self.account_button.refresh()
        self._update_basemap_warning()
        self._update_terrain_warning()
        self._arrange_keyboard()

        # The list tracks QGIS live; there is no refresh button because there is
        # nothing to refresh.
        self.watcher = LayerTreeWatcher(self.project, self)
        self.watcher.changed.connect(self.refresh_layers)
        # What the layers LOOK like, which the tree does not carry: a restyle
        # (or saved edits) changes the map without moving anything above.
        self.content_watcher = LayerContentWatcher(self.project, self)
        self.content_watcher.changed.connect(self._project_content_changed)
        self.content_watcher.changed.connect(
            self._cartograph.on_project_content_changed
        )

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

        # A served one-off preview has no live session to notice its tab
        # closing, so this does that one job and nothing else.
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(LIVE_POLL_MS)
        self._idle_timer.timeout.connect(self._stop_when_one_off_tab_closes)

        self.refresh_layers()

    def _warn_style(self) -> str:
        """Warning text in the theme's warn colour.

        Warnings can be set while the tabs are still being built, before the
        theme exists; those fall back to the old fixed red and are redrawn once
        `theme.apply` has run.
        """
        palette = getattr(self, "theme", None)
        return f"color: {palette.warn if palette else '#c0392b'};"

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            # The keyboard starts on the screen itself, not its first control:
            # Qt's own choice put the focus ring round "Map" or "Fit page" on
            # opening, which read as something selected. Tab reaches them.
            QTimer.singleShot(0, self._park_focus)

    def _arrange_keyboard(self) -> None:
        """Tab follows the screen: header left to right, then the main
        content, then the foot, with Close last.

        Qt chains focus in creation order, which is not the layout's: the
        header's actions are built with the tabs, the Settings and Help pages
        before the Back arrow above them, and the foot after both. Run again
        whenever a page is moved (`_apply_output` re-seats Layers and Checks).
        Also keeps scroll areas out of the chain: they take focus and show
        nothing. (A tree tabbed into needs no help: Qt makes its first row
        current, so the focus shows.)
        """
        for area in self.findChildren(QScrollArea):
            area.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        foot = (self.fidelity_strip, self.progress_row, self.close_button)
        header_actions = (self.settings_button, self.help_button)

        def region(widget: QWidget) -> tuple[int, int]:
            if widget is self.output_switcher:
                return (1, 0)
            if self._header.isAncestorOf(widget):
                return (1, 2 if widget in header_actions else 1)
            if self.aside_back is widget:
                return (2, 0)
            if self.views.isAncestorOf(widget):
                return (2, 1)
            for rank, group in enumerate(foot):
                if group is widget or group.isAncestorOf(widget):
                    return (3, rank)
            return (0, 0)

        # Only widgets that take focus: `setTabOrder` ignores any pair with a
        # `NoFocus` member, which would leave a gap in the chain.
        chain: list[QWidget] = []
        widget = self.nextInFocusChain()
        while widget is not None and widget is not self:
            if widget.focusPolicy() != Qt.FocusPolicy.NoFocus:
                chain.append(widget)
            widget = widget.nextInFocusChain()
        chain.sort(key=region)
        for previous, widget in zip(chain, chain[1:]):
            QWidget.setTabOrder(previous, widget)

    def _park_focus(self) -> None:
        if hasattr(self, "views") and not sip.isdeleted(self.views):
            self.views.setFocus(Qt.FocusReason.OtherFocusReason)

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
            # A share of the work area rather than all of it, so QGIS stays
            # visible around the dialog on a laptop; on a large screen the
            # default above is the smaller and wins. The opening size only -
            # a window the user resizes is theirs.
            width = min(
                width,
                max(MINIMUM_DIALOG_WIDTH, int(available.width() * OPENING_SHARE_W)),
            )
            height = min(
                height,
                max(MINIMUM_DIALOG_HEIGHT, int(available.height() * OPENING_SHARE_H)),
            )

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
        # Where the map is written and in which form: Standalone HTML, Share
        # ZIP or Folder, chosen here and produced by Export.
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
        # The field is filled from the project, or with a new file in the Web
        # maps folder (`core/save_folders.py`). Nothing is written there until
        # Export.
        self.path_edit.setText(self.state.map_file or str(self._default_map_file()))
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
            tr(OSM_FILE_NOTE)
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

        The file picker proposes it, and switching the type swaps it on the
        name already typed.
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

    def _default_map_file(self) -> Path:
        """A new map's file: the Web maps folder, named after the map, with
        a number when that name is taken."""
        suffix = self._mode_suffix(self.state.output_mode)
        return unused_path(web_maps_folder() / f"{self._default_basename()}{suffix}")

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
        # decides whether it warns: redrawn from the same read, not re-read.
        if (
            self._fidelity_state is ReportState.CURRENT
            and self._cached_report is not None
            and self._fidelity_signature == self._cached_signature
        ):
            self._show_fidelity(self._cached_report, self._cached_signature)
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

        Proposes a name with the chosen type's extension, or asks for a folder
        when the type is Folder.
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
        chosen, the Share rows that act on the file are disabled, and this is
        where that is explained. A disabled row with no reason beside it is the
        failure this label exists to prevent.
        """
        if not hasattr(self, "save_status"):
            return

        destination = self._map_file()
        if destination is None:
            self.save_status.setText(tr("Choose where to save, then press Export."))
            return

        # What an export leaves there: the file, or a Folder export's page. A
        # folder that merely exists - the Folder picker only offers existing
        # ones - has not been written to by anyone.
        folder = self.state.output_mode is OutputMode.FOLDER
        written = destination / "index.html" if folder else destination
        if not written.is_file():
            self.save_status.setText(tr("Not written yet."))
            return

        try:
            stat = written.stat()
        except OSError:
            # The path is real enough to have been chosen but cannot be read -
            # a disconnected network share, most likely. Saying so beats a
            # traceback in a slot.
            self.save_status.setText(tr("The map file cannot be read from here."))
            return

        size = stat.st_size
        if folder:
            # A Folder export: its files, not the directory entry's 4 KB. Only
            # one this plugin wrote - this runs on every keystroke in Save to,
            # and walking "/" or a drive root on the way to a path would freeze QGIS.
            with contextlib.suppress(OSError):
                size = sum(
                    f.stat().st_size for f in destination.rglob("*") if f.is_file()
                )
        megabytes = size / 1024 / 1024
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

        self.layer_search = _SearchBox(page)
        self.layer_search.setClearButtonEnabled(True)
        self.layer_search.setPlaceholderText(tr("Search layers and fields"))
        self.layer_search.setAccessibleName(tr("Search layers and fields"))
        self.layer_search.textChanged.connect(self._apply_layer_filter)
        layout.addWidget(self.layer_search)
        # Expanded layers as they were when a search began, put back when it
        # ends; None while no search is active.
        self._expanded_before_search: set[str] | None = None

        self.layer_tree = QTreeWidget(page)
        self.layer_tree.setColumnCount(4)
        self.layer_tree.setHeaderLabels(
            [tr("Layer"), tr("Include"), tr("Popups"), tr("Labels")]
        )
        self.layer_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # The layer column takes the spare width. Left on, the header's own
        # last-section stretch fought it and the columns came out 13 px wider
        # than the narrow Cartograph panel, with a sideways scrollbar.
        self.layer_tree.header().setStretchLastSection(False)
        # The checkbox columns are as wide as their headings; at the
        # dialog's minimum size on Qt 5 fonts that left the layer column at
        # Qt's 50 px floor and the whole tree 5 px too wide. The names elide
        # instead, so every checkbox and heading stays whole.
        self.layer_tree.header().setMinimumSectionSize(32)
        self.layer_tree.itemChanged.connect(self._on_layer_item_changed)
        # Always reserved: a scrollbar appearing as a layer opens took its
        # width from the tree and slid the checkbox columns sideways.
        self.layer_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        # About a layer row and one stacked settings row; below this the tab
        # scrolls rather than the list shrinking until nothing shows.
        self.layer_tree.setMinimumHeight(LAYER_TREE_MIN_HEIGHT)
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
        self._bulk_row = bulk
        return page

    def _set_layers_compact(self, compact: bool) -> None:
        """Fit the cartograph's narrow panel: the bulk action stacks, and the
        three tick columns take only the room their headings need."""
        self._bulk_row.setDirection(
            QBoxLayout.Direction.TopToBottom
            if compact
            else QBoxLayout.Direction.LeftToRight
        )
        # Its longest choice is wider than the panel; there it may shorten
        # its text rather than push the panel off the dialog's edge. An
        # explicit minimum, because a styled combo box reports its longest
        # item as its minimum whatever its size policy says.
        self.bulk_mode_combo.setMinimumWidth(COMPACT_COMBO_WIDTH if compact else 0)
        # Stacked under the button, the bare "i" explained nothing and looked
        # like a stray icon; with room for it, it says what it is for.
        self.layers_info_button.setText(tr("What these columns do") if compact else "")
        self.layers_info_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if compact
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        header = self.layer_tree.header()
        for column in (1, 2, 3):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents
                if compact
                else QHeaderView.ResizeMode.Interactive,
            )

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

        # Rebuilt on every project change, which would otherwise fold what the
        # user had just opened.
        expanded_ids = (
            self._expanded_before_search
            if self._expanded_before_search is not None
            else self._expanded_layer_ids()
        )

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
                (1, self._cartograph.membership.included(layer.id(), settings.include)),
                (2, settings.popup),
                (3, settings.label),
            ):
                item.setCheckState(
                    column,
                    Qt.CheckState.Checked if value else Qt.CheckState.Unchecked,
                )

            self._add_field_rows(item, layer, settings)
            item.setExpanded(layer.id() in expanded_ids)

        self.layer_tree.blockSignals(False)
        self._apply_layer_filter(self.layer_search.text())
        # These drop-downs are rebuilt on every layer change, long after
        # `theme.apply` attached the rest, so they are attached here.
        theme.attach_menus(self.layer_tree)
        self._update_export_readiness()

    def _expanded_layer_ids(self) -> set[str]:
        return {
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in (
                self.layer_tree.topLevelItem(index)
                for index in range(self.layer_tree.topLevelItemCount())
            )
            if item is not None and item.isExpanded()
        }

    def _apply_layer_filter(self, text: str) -> None:
        """Hide layers and fields that do not match the search box.

        A matching field expands its layer; a matching layer name keeps its
        own rows. Only visibility and expansion change - never a checkbox or
        a field's mode - and clearing the box restores the expansion the
        list had when the search began.
        """
        needle = _search_key(text.strip())
        tree = self.layer_tree
        if needle and self._expanded_before_search is None:
            self._expanded_before_search = self._expanded_layer_ids()
        saved = self._expanded_before_search or set()

        for index in range(tree.topLevelItemCount()):
            layer_item = tree.topLevelItem(index)
            if layer_item is None:
                continue
            layer_id = layer_item.data(0, Qt.ItemDataRole.UserRole)
            if not needle:
                layer_item.setHidden(False)
                for child_index in range(layer_item.childCount()):
                    layer_item.child(child_index).setHidden(False)
                if self._expanded_before_search is not None:
                    layer_item.setExpanded(layer_id in saved)
                continue

            name_match = needle in _search_key(layer_item.text(0))
            field_matches = [
                child.data(0, Qt.ItemDataRole.UserRole) != LAYER_OPTIONS_ROLE
                and needle in _search_key(str(child.data(0, Qt.ItemDataRole.UserRole)))
                for child in (
                    layer_item.child(i) for i in range(layer_item.childCount())
                )
            ]
            # A name-only match keeps all its rows; a field match narrows the
            # layer to just those fields.
            for child_index, match in enumerate(field_matches):
                layer_item.child(child_index).setHidden(
                    not (match or (name_match and not any(field_matches)))
                )
            layer_item.setHidden(not (name_match or any(field_matches)))
            layer_item.setExpanded(any(field_matches) or layer_id in saved)

        if not needle:
            self._expanded_before_search = None

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
        if (
            isinstance(layer, QgsVectorLayer)
            and layer.geometryType() == QgsWkbTypes.GeometryType.PointGeometry
        ):
            self._add_point_options_row(parent, layer, settings)

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

            row = _ReflowRow(self.layer_tree)
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

        row = _ReflowRow(self.layer_tree)
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

    def _add_point_options_row(
        self, parent: QTreeWidgetItem, layer: QgsVectorLayer, settings: LayerSettings
    ) -> None:
        """Point layers only: names beside every point, and the way out of
        clustering, which is otherwise automatic for a dense layer."""
        item = QTreeWidgetItem(parent)
        item.setFirstColumnSpanned(True)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setData(0, Qt.ItemDataRole.UserRole, LAYER_OPTIONS_ROLE)

        row = _ReflowRow(self.layer_tree)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layer_id = layer.id()
        label = QLabel(tr("Points"), row)
        label.setMinimumWidth(FIELD_NAME_COLUMN_WIDTH)
        layout.addWidget(label)

        names = QComboBox(row)
        names.setToolTip(
            tr(
                "Show each point's value of this field beside it, with a halo, "
                "without clicking. Replaces the layer's QGIS labels."
            )
        )
        names.addItem(tr("Names: off"), None)
        for field_name in _name_fields_first(layer.fields().names()):
            names.addItem(tr("Names: {field}").format(field=field_name), field_name)
        names.setCurrentIndex(max(0, names.findData(settings.name_field)))
        names.currentIndexChanged.connect(
            lambda _i, lid=layer_id, box=names: self._on_layer_names_changed(lid, box)
        )
        layout.addWidget(names)

        cluster = QCheckBox(tr("Cluster dense points"), row)
        cluster.setToolTip(
            tr(
                "Points that would pile up when zoomed out merge into count "
                "bubbles that split as you zoom in. Untick to show every point."
            )
        )
        cluster.setChecked(settings.cluster)
        cluster.toggled.connect(
            lambda on, lid=layer_id: self._on_layer_cluster_toggled(lid, on)
        )
        layout.addWidget(cluster)

        layout.addStretch(1)
        self.layer_tree.setItemWidget(item, 0, row)
        _fit_row_to_widget(item, row)

    def _on_layer_names_changed(self, layer_id: str, combo: QComboBox) -> None:
        self.state.for_layer(layer_id).name_field = combo.currentData()
        self._fidelity_is_stale = True

    def _on_layer_cluster_toggled(self, layer_id: str, on: bool) -> None:
        self.state.for_layer(layer_id).cluster = on
        self._fidelity_is_stale = True

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
            self._sync_cartograph_layer_settings()
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

        self._sync_cartograph_layer_settings()
        self.refresh_layers()

    def _on_layer_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        layer_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not layer_id:
            return
        settings = self.state.for_layer(layer_id)
        checked = item.checkState(column) == Qt.CheckState.Checked
        if column == 1 and self._cartograph.membership.is_active():
            # In Cartograph the tick is the map frame's own layer list; the
            # web map's Include selection stays as it was.
            self._cartograph.membership.set_included(layer_id, checked)
        elif column == 1:
            settings.include = checked
        elif column == 2:
            settings.popup = checked
        elif column == 3:
            settings.label = checked
        self._sync_cartograph_layer_settings()
        self._update_export_readiness()

    def sync_include_column(self) -> None:
        """Show each layer's Include tick for the output on screen: the web
        map's selection, or in Cartograph the selected map frame's layers."""
        if not hasattr(self, "layer_tree"):
            return
        self.layer_tree.blockSignals(True)
        try:
            for index in range(self.layer_tree.topLevelItemCount()):
                item = self.layer_tree.topLevelItem(index)
                layer_id = item.data(0, Qt.ItemDataRole.UserRole) if item else None
                if not layer_id:
                    continue
                on = self._cartograph.membership.included(
                    layer_id, self.state.for_layer(layer_id).include
                )
                item.setCheckState(
                    1, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                )
        finally:
            self.layer_tree.blockSignals(False)
        if hasattr(self, "status_label"):
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
        self.fidelity_panel.check_requested.connect(self._on_check_requested)
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
        if stale:
            self._sync_cartograph_layer_settings()
        self._refresh_fidelity_freshness()

    def _sync_cartograph_layer_settings(self) -> None:
        """A Layers tab setting just changed: tell the Cartograph now, so a
        Preview or Share right after it never opens the file without it."""
        cartograph = getattr(self, "_cartograph", None)
        if cartograph is not None:
            cartograph.sync_layer_settings()

    def _show_fidelity(
        self, report: FidelityReportBuilder, signature: str | None = None
    ) -> None:
        """Show a report. `signature` is the data snapshot it was read against;
        `None` means the settings as they are now."""
        report = with_output_notes(report, self.state.to_export_settings())
        self._fidelity_signature = (
            signature if signature is not None else self.state.data_snapshot()
        )
        self._fidelity_state = ReportState.CURRENT
        self._fidelity_dirty = False
        self._update_fidelity_strip(report)
        if self._fidelity_summary is not None and self._web_fidelity_on_screen():
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
        if self._web_fidelity_on_screen():
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
        if not self._web_fidelity_on_screen():
            return  # the cartograph's report is built by its autosave
        if not self._fidelity_is_stale or self._fidelity_state is ReportState.CHECKING:
            return
        self._check_fidelity()

    def _on_check_requested(self) -> None:
        """The panel's Check now / Try again / Check again, for the output on
        screen: the Web map reads the project, a Cartograph rebuilds the
        selected layout."""
        if self._web_fidelity_on_screen():
            self._check_fidelity()
        else:
            self._cartograph.check_now()

    def _check_fidelity(self) -> None:
        """Read the project for a fresh report, or show a cached one."""

        def then(_export, report: FidelityReportBuilder) -> None:
            # A cached read skips `_ensure_export`'s own display. Only that one:
            # a fresh read has been shown already, and one QGIS overtook while
            # it ran must stay out of date rather than be shown as current.
            if self._fidelity_is_stale and report is self._cached_report:
                self._show_fidelity(report, self._cached_signature)

        self._ensure_export(then, tr("Checking the project..."))

    def _begin_fidelity_check(self) -> None:
        # A placeholder rather than an empty table: "empty" reads as "nothing
        # to report".
        self._fidelity_state = ReportState.CHECKING
        if self._web_fidelity_on_screen():
            self.fidelity_panel.show_checking()
        self._show_fidelity_strip()

    def _end_fidelity_check_without_report(self) -> None:
        """A check that was stopped leaves the last report, marked for what it is."""
        if self._fidelity_state is not ReportState.CHECKING:
            return
        if self._fidelity_summary is None:
            self._fidelity_state = ReportState.NOT_CHECKED
            if self._web_fidelity_on_screen():
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
        the pages are looked up rather than the text.

        Settings and Help open beside the map; on the cartograph, Layers is a
        section of its panel and Fidelity is its Checks tab, so those open
        instead.
        """
        page = self._pages.get(title)
        if page is None:
            return
        if page in (self._settings_page, self._help_page):
            self._show_aside(page)
            return
        self._close_aside()
        carto_sections = {"Layers": "layers", "Fidelity": "checks"}
        if self.state.output is DialogOutput.CARTOGRAPH and title in carto_sections:
            self._cartograph.open_section(carto_sections[title])
            return
        self.tabs.setCurrentWidget(page)

    # ---- Help tab -------------------------------------------------------

    # ---- Header: Web map ▾ | the output's actions -----------------------

    def _build_header(self) -> QWidget:
        """The output's name as the dialog's title, its actions on the right.

        One row where there were two: the old `Make:` switch had a row of its
        own above a tab bar that also carried the actions. Here the title
        opens the menu of outputs (see `ui/output_switcher.py`), and the tab
        bar below is left with nothing but its tabs.
        """
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 6)
        row_layout.setSpacing(8)

        # No "Create:" before it: its chevron, hover and tooltip say it is a
        # choice, and a quiet title leaves the map the loudest thing here.
        self.output_switcher = OutputSwitcher(self.state.output, row)
        self.output_switcher.chosen.connect(self._apply_output)
        row_layout.addWidget(self.output_switcher)
        row_layout.addWidget(
            self.output_switcher.beta_chip, 0, Qt.AlignmentFlag.AlignVCenter
        )
        row_layout.addStretch(1)
        # Both always in the row, one hidden: a hidden widget takes no room.
        row_layout.addWidget(self._web_corner)
        row_layout.addWidget(self._cartograph.build_corner())
        self.settings_button = self._aside_button("settings", tr("Settings"), row)
        self.help_button = self._aside_button("help", tr("Help"), row)
        row_layout.addWidget(self.settings_button)
        row_layout.addWidget(self.help_button)
        return row

    def _aside_button(self, icon: str, name: str, parent: QWidget) -> QToolButton:
        """An icon in the header that opens Settings or Help, and closes it
        again when pressed a second time."""
        button = QToolButton(parent)
        button.setProperty("role", "corner")
        button.setProperty("iconOnly", True)
        button.setProperty("lineIcon", icon)
        button.setCheckable(True)
        button.setToolTip(name)
        button.setAccessibleName(name)
        page = self._settings_page if icon == "settings" else self._help_page
        button.clicked.connect(
            lambda checked, page=page: (
                self._show_aside(page) if checked else self._close_aside()
            )
        )
        return button

    def _build_aside(self) -> QWidget:
        """Settings or Help in place of the map's own screen, with Back."""
        aside = QWidget(self)
        column = QVBoxLayout(aside)
        column.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        # One header: a plain back arrow, then the page's name as its title.
        # A boxed "Back" beside a bare word read as two unrelated things.
        self.aside_back = QToolButton(aside)
        self.aside_back.setProperty("role", "asideBack")
        self.aside_back.setProperty("lineIcon", "back")
        self.aside_back.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.aside_back.setText(tr("Back"))
        self.aside_back.setToolTip(tr("Back"))
        self.aside_back.setAccessibleName(tr("Back"))
        self.aside_back.clicked.connect(self._close_aside)
        top.addWidget(self.aside_back)
        self.aside_heading = QLabel(aside)
        self.aside_heading.setProperty("role", "asideHeading")
        top.addWidget(self.aside_heading)
        top.addStretch(1)
        column.addLayout(top)
        self._aside_stack = QStackedWidget(aside)
        self._aside_stack.addWidget(self._settings_page)
        self._aside_stack.addWidget(self._help_page)
        column.addWidget(self._aside_stack, 1)
        self._aside = aside
        return aside

    def _show_aside(self, page: QWidget) -> None:
        self._aside_stack.setCurrentWidget(page)
        settings = page is self._settings_page
        self.aside_heading.setText(tr("Settings") if settings else tr("Help"))
        self.settings_button.setChecked(settings)
        self.help_button.setChecked(not settings)
        # The output's own actions mean nothing on these pages.
        self._web_corner.setVisible(False)
        self._cartograph.build_corner().setVisible(False)
        self.views.setCurrentWidget(self._aside)

    def _close_aside(self) -> None:
        """Back to the output's own screen."""
        self.settings_button.setChecked(False)
        self.help_button.setChecked(False)
        web = self.state.output is DialogOutput.WEB_MAP
        self._web_corner.setVisible(web)
        self._cartograph.build_corner().setVisible(not web)
        self.views.setCurrentWidget(self.tabs if web else self._cartograph_workspace)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Esc on Settings or Help goes Back, as the arrow does; only on the
        output's own screen does it close the dialog. (Close stays Close.)"""
        if (
            event.key() == Qt.Key.Key_Escape
            and self.views.currentWidget() is self._aside
        ):
            self._close_aside()
            event.accept()
            return
        super().keyPressEvent(event)

    def active_actions(self) -> QWidget:
        """The header's actions for the output on screen."""
        if self.state.output is DialogOutput.WEB_MAP:
            return self._web_corner
        return self._cartograph.build_corner()

    def _apply_output(self, output: DialogOutput, persist: bool = True) -> None:
        """Put `output`'s screen and actions on show.

        The web map is tabs; the cartograph is its page and one panel (see
        `CartographController.build_cartograph_tab`). Layers and the checks
        are shared, so they move between a tab and a panel section. Every
        widget is built once (`__init__`), so switching never rebuilds one or
        loses whatever the user had typed into it.
        """
        self.state.output = output
        web = output is DialogOutput.WEB_MAP
        self.sync_include_column()
        # Layers and the checks are one set of widgets for both outputs: tabs
        # on the web map, sections of the panel on the cartograph.
        if web:
            if self.tabs.indexOf(self._layers_scroll) < 0:
                self._layers_scroll.setWidget(self._layers_page)
                self.tabs.insertTab(1, self._layers_scroll, tr("Layers"))
            if self.tabs.indexOf(self._fidelity_page) < 0:
                self.tabs.insertTab(3, self._fidelity_page, tr("Fidelity"))
            # Back on whichever tab was open: taking Layers or Fidelity out
            # moved the selection on to a neighbour.
            open_tab = getattr(self, "_web_tab_before_cartograph", None)
            if open_tab is not None and self.tabs.indexOf(open_tab) >= 0:
                self.tabs.setCurrentWidget(open_tab)
        else:
            if self.tabs.indexOf(self._layers_scroll) >= 0:
                self._web_tab_before_cartograph = self.tabs.currentWidget()
            # Out of the scroll area first: the panel seats the page itself.
            if self._layers_scroll.widget() is self._layers_page:
                self._layers_scroll.takeWidget()
            for page in (self._layers_scroll, self._fidelity_page):
                index = self.tabs.indexOf(page)
                if index >= 0:
                    self.tabs.removeTab(index)
            self._cartograph.seat_shared(self._layers_page, self._fidelity_page)
        self._set_layers_compact(not web)
        self.fidelity_panel.set_compact(not web)
        if hasattr(self, "fidelity_strip"):
            self.fidelity_strip.setVisible(web)
        if hasattr(self, "views"):
            self._close_aside()

        if hasattr(self, "status_label"):
            self.status_label.setText(
                getattr(self, "_ready_message", "") if web else ""
            )
        self._web_corner.setVisible(web)
        self._cartograph.build_corner().setVisible(not web)
        if hasattr(self, "output_switcher"):
            self.output_switcher.set_output(output)
            if hasattr(self, "theme"):
                theme.paint_line_icons(self.output_switcher.parentWidget(), self.theme)

        if web:
            self._cartograph.hiding()
            self.setWindowTitle(tr("QGIS2WebMap by NIKA"))
        else:
            self._cartograph.showing()
            self._cartograph.update_window_title()
        self._show_active_fidelity()
        if web:
            # The web map's report may have gone stale while it was off
            # screen; check it now if its tab is the one open.
            self._on_tab_changed(self.tabs.currentIndex())

        if hasattr(self, "close_button"):
            self._arrange_keyboard()

        if persist:
            save_state(self.project, self.state)

    def _web_fidelity_on_screen(self) -> bool:
        """Whether the Fidelity tab and strip show the web map's report.

        The web map's checks keep their own state whatever is on screen; only
        their drawing waits, so switching back shows them as they stand.
        """
        return self.state.output is DialogOutput.WEB_MAP

    def _show_active_fidelity(self) -> None:
        """Draw the report of the output on screen, tab and strip both."""
        if not hasattr(self, "fidelity_strip"):
            return  # still building the dialog; drawn once the strip exists
        if not self._web_fidelity_on_screen():
            self._cartograph.show_fidelity()
            return
        if self._fidelity_state is ReportState.CHECKING:
            self.fidelity_panel.show_checking()
        elif self._fidelity_state is ReportState.FAILED:
            self.fidelity_panel.show_error(self._fidelity_error)
        elif self._fidelity_summary is None:
            self.fidelity_panel.show_not_checked()
        else:
            self.fidelity_panel.show_summary(
                self._fidelity_summary, self._fidelity_facts(self._fidelity_signature)
            )
        self._refresh_fidelity_freshness()

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

        They now sit in the header, beside the output's name, rather than in
        the tab bar's corner - see `_build_header`.
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
        # Just the dot; "Live: on/off" stays as its text for screen readers.
        self.live_switch.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.live_switch.setChecked(
            QSettings().value(LIVE_PREVIEW_KEY, False, type=bool)
        )
        self.live_switch.toggled.connect(self._on_live_toggled)
        self.live_switch.setText(
            tr("Live: on") if self.live_switch.isChecked() else tr("Live: off")
        )
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
        self.live_switch.setText(tr("Live: on") if live else tr("Live: off"))
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
        button.setText(tr("Stop live") if running else tr("Preview"))
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
                action.setIcon(theme.qgis_icon(name))
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

        add(
            "cartograph",
            tr("Make a cartograph..."),
            tr(
                "Builds a print layout from the current map view and switches "
                "to the Cartograph (Beta) output"
            ),
            self._switch_to_cartograph,
        )

        # What NIKA offers, after everything that works on the file itself,
        # each named for what it does today: the hand-offs prepare a prompt
        # for the user to take to NIKA - nothing is sent anywhere - and the
        # hosting row joins a waitlist, it does not publish.
        menu.addSection(tr("NIKA hand-off"))
        add(
            "enhance",
            tr("Prepare enhancement prompt..."),
            tr(
                "Opens a prompt for improving this map, to copy into NIKA or "
                "another AI assistant. Nothing is sent from here."
            ),
            lambda: self.on_handoff("enhance"),
        )
        add(
            "critique",
            tr("Prepare critique prompt..."),
            tr(
                "Opens a prompt asking for a critique of this map, to copy into "
                "NIKA or another AI assistant. Nothing is sent from here."
            ),
            lambda: self.on_handoff("critique"),
        )
        menu.addSection(tr("NIKA hosting"))
        add(
            "publish",
            tr("Join NIKA hosting waitlist ↗"),
            tr(
                "Hosting is not live yet. This opens a short form in your browser "
                "so we can see how many people want it - it does not publish "
                "the map."
            ),
            self.on_publish,
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
        return menu

    def open_on_cartograph(self, layout_name: str | None = None) -> None:
        """Switch to Cartograph (Beta), optionally selecting one layout.

        The plugin's two cartograph entry points - the Web menu action and the
        layout designer's "Export as cartograph (HTML)..." action - both call
        this rather than touching `self.state` directly, for the reason
        `_switch_to_cartograph` is: the switch's checked state has to move
        with the output it names.
        """
        self.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
        if layout_name:
            self._cartograph.layout_combo.setCurrentText(layout_name)

    def open_on_web_map(self) -> None:
        """Switch to Web map - the plugin's "Create web map" entry point."""
        self.output_switcher.actions_by_output[DialogOutput.WEB_MAP].trigger()

    def release_canvas_effects(self) -> None:
        """For the plugin's unload: undo canvas previews this dialog set."""
        self._cartograph.release_canvas_effects()

    def _switch_to_cartograph(self) -> None:
        """The web-map Share menu's "Make a cartograph..." row.

        Flips the switch, which is the one thing that both applies the output
        and keeps the button's checked state in step with it - setting
        `self.state.output` directly would leave the switch showing the wrong
        side pressed.
        """
        self.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
        self._cartograph.create_from_canvas()

    def _build_settings_tab(self) -> QWidget:
        """The person's own preferences, apart from the map's.

        Everything here is true of every project - who is signed in, the
        language, the theme - so it lives in one familiar place rather than in
        the footer. Live preview stays as the switch beside the Preview button,
        next to the thing it changes. The map's own options stay on the Map, Layers and
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

        saving_box = QGroupBox(tr("Saving"), page)
        saving_form = QFormLayout(saving_box)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        self.save_folder_label = QLabel(saving_box)
        self.save_folder_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        folder_row.addWidget(self.save_folder_label, 1)
        self.save_folder_button = QPushButton(tr("Change..."), saving_box)
        self.save_folder_button.setProperty("role", "link")
        self.save_folder_button.clicked.connect(self._on_change_save_folder)
        folder_row.addWidget(self.save_folder_button)
        self.save_folder_reset = QPushButton(tr("Use Documents"), saving_box)
        self.save_folder_reset.setProperty("role", "link")
        self.save_folder_reset.clicked.connect(lambda: self._set_save_folder(None))
        folder_row.addWidget(self.save_folder_reset)
        saving_form.addRow(tr("Save new maps in"), folder_row)
        saving_form.addRow(
            "",
            _help_label(
                tr(
                    "Web maps and cartographs each get their own folder inside "
                    "it, Web maps and Cartographs. Maps already saved stay "
                    "where they are."
                ),
                saving_box,
            ),
        )
        self._show_save_folder()
        layout.addWidget(saving_box)

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

    def _show_save_folder(self) -> None:
        root = save_root()
        self.save_folder_label.setText(str(root))
        self.save_folder_label.setToolTip(str(root))
        self.save_folder_reset.setVisible(root != default_save_root())

    def _on_change_save_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Save new maps in"), str(save_root())
        )
        if chosen:
            self._set_save_folder(Path(chosen))

    def _set_save_folder(self, root: Path | None) -> None:
        """Change where new maps save. A web map not exported yet moves with
        it; anything already written stays where it is."""
        before = web_maps_folder()
        set_save_root(root)
        self._show_save_folder()
        current = self._map_file()
        if current is not None and current.parent == before and not current.exists():
            self.path_edit.setText(str(self._default_map_file()))

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
        browser = _HelpBrowser(page)
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

        close = self.close_button = QPushButton(tr("Close"), self)
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
        hard rule: each job works from a snapshot of the project taken when it
        was requested, and two jobs would race to write the same preview and
        cache.

        `quiet` reports a failure on the status line instead of in a modal. The
        live preview rebuilds on every edit, so a modal there would mean a
        dialog per keystroke for a user who is mid-edit and whose next change
        may well fix it.
        """
        if self._refuse_while_busy():
            return False

        job = BackgroundJob(work, self)
        self._job = job
        job.progressed.connect(self._on_job_progress)
        job.committing.connect(self._on_job_committing)
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

    def _refuse_while_busy(self) -> bool:
        """Say so and return True if a job or the cartograph is already working."""
        if self._job is not None or self._cartograph.busy:
            self.status_label.setText(tr("Still working on the last request."))
            return True
        return False

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
            # Only the rows that write the map: the others start no job, and
            # nothing but `_update_share_enabled` would turn them back on.
            for key, action in getattr(self, "share_actions", {}).items():
                if key in SHARE_ACTIONS_ON_THE_FILE:
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

    def _on_job_committing(self) -> None:
        """The artifact is being put in place: Cancel can no longer stop it."""
        self.cancel_button.setEnabled(False)

    def _on_cancel_job(self) -> None:
        if self._job is None or self._job.is_committed:
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
        if not self._web_fidelity_on_screen():
            # A cartograph is checked by saving it.
            if self._cartograph.has_report and not self._cartograph.report_needs_check:
                self._show_fidelity_tab()
            else:
                self._cartograph.check_now()
            return
        if self._fidelity_state in (ReportState.NOT_CHECKED, ReportState.OUT_OF_DATE):
            self._check_fidelity()
        else:
            self._show_fidelity_tab()

    def _show_fidelity_tab(self) -> None:
        self._show_tab("Fidelity")

    def _update_fidelity_strip(self, report) -> None:
        """Summarise the report in one line, problems first."""
        self._fidelity_summary = self._fidelity_summary_of(report)
        if not self._web_fidelity_on_screen():
            return
        self.fidelity_strip.show_message(
            strip_message(
                ReportState.CURRENT, self._fidelity_summary, tr=tr, plural=plural
            )
        )

    def _show_fidelity_strip(self) -> None:
        if not self._web_fidelity_on_screen():
            return
        self.fidelity_strip.show_message(
            strip_message(
                self._fidelity_state,
                self._fidelity_summary,
                self._fidelity_error,
                tr=tr,
                plural=plural,
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
        if not self._web_fidelity_on_screen():
            return
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
            identity = self._project_identity()

            def work(progress: Progress):
                progress.step(READ_SHARE, tr("Updating the preview..."))
                write_preview(
                    export,
                    identity,
                    writer=writer,
                    live=True,
                    on_progress=self._writing_progress(progress),
                    should_cancel=progress.should_cancel,
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

    def _stop_when_one_off_tab_closes(self) -> None:
        if self._shut_down or self._server is None or self._live_running():
            self._idle_timer.stop()
            return
        if self._preview_tab_was_closed():
            self._stop_live_preview()

    def _stop_live_preview(self) -> None:
        if hasattr(self, "_idle_timer"):
            self._idle_timer.stop()
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
        # In Cartograph the count is the selected map frame's real layers.
        carto_summary = self._cartograph.membership.summary(available)
        self._cartograph.layers_section.set_summary(
            carto_summary
            if carto_summary is not None
            else plural(
                len(available),
                "{included} of {n} layer included",
                "{included} of {n} layers included",
                included=len(selected),
            )
            if available
            else ""
        )

    def _set_ready(self, ready: bool, message: str) -> None:
        self.preview_button.setEnabled(ready)
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(ready)
        # Share's rows need a destination as well as something to put in it,
        # so their own rule is applied on top rather than instead.
        self._export_ready = ready
        self._update_share_enabled()
        # The footer line is the web map's; on Cartograph it would count
        # layers for an export that is not the one on screen.
        self._ready_message = message
        if self.state.output is DialogOutput.WEB_MAP:
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

    @staticmethod
    def _writing_progress(progress: Progress) -> Callable[[int, int | None], None]:
        """The writer's raster progress `(done, total)` on the bar's writing share.

        Converting a large raster is the slow part of writing, and GDAL reports
        it; spreading it over the share between reading and "Finishing" keeps
        the bar moving rather than parked while it runs.
        """

        def on_progress(done: int, total: int | None) -> None:
            if not total:
                return
            span = COMMIT_AT - READ_SHARE
            progress.step(
                READ_SHARE + int(span * done / total),
                tr("Converting rasters ({percent}%)...").format(
                    percent=int(100 * done / total)
                ),
            )

        return on_progress

    def _prepare_current_project(self) -> tuple[PendingProject, FidelityReportBuilder]:
        """Read everything the project must be asked for. **GUI thread only.**

        The live `QgsProject` and its layers are touched here and nowhere on the
        worker: this returns a `PendingProject` of plain values and feature
        sources, which `_read_current_project` finishes off the GUI thread. See
        `background_job` for the boundary.
        """
        report = FidelityReportBuilder()
        pending = prepare_project(
            self.project,
            report,
            settings=self.state.to_export_settings(),
            title_override=self.state.map_name or None,
            selected_layer_ids=self.state.selected_layer_ids(
                self._available_layer_ids()
            ),
            layer_settings=self.state.layers,
            canvas_extent=self._pending_canvas_extent,
        )
        return pending, report

    def _read_current_project(
        self,
        progress: Progress | None = None,
        prepared: tuple[PendingProject, FidelityReportBuilder] | None = None,
    ):
        """Finish reading the project into the export model. **Runs on a worker
        thread** when given `prepared`, which the GUI thread made with
        `_prepare_current_project`; without it the preparation happens here, on
        the caller's thread, for callers that are not a job.

        Everything it touches is either a plain value captured before the job
        started or part of `prepared`, which holds no project or layer. Nothing
        here may touch a widget.
        """
        pending, report = prepared or self._prepare_current_project()

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

        export = finish_project(pending, report, on_layer)
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
        # Read on the GUI thread, where the canvas lives, and hand the plain
        # value to the worker. Calling `iface.mapCanvas()` from the thread would
        # be touching a widget from the wrong side. It is also part of what the
        # cache is keyed on: None unless an extent setting uses the view, so a
        # pan only costs a re-read when the export actually depends on it.
        canvas_extent = self._canvas_extent()
        if (
            self._cached_export is not None
            and self._cached_signature == signature
            and self._cached_canvas_extent == canvas_extent
        ):
            # The read is reused, but the settings it carries are the ones it
            # was taken with: a colour or control changed since would never
            # reach the page. Today's settings replace them.
            current = replace(
                self._cached_export, settings=self.state.to_export_settings()
            )
            then(current, self._cached_report)
            return True

        if self._refuse_while_busy():
            return False

        self._pending_canvas_extent = canvas_extent
        self._pending_license_key = self._license_key()

        # On this thread: it reads the live project. The job gets the snapshot.
        try:
            prepared = self._prepare_current_project()
        except Exception as exc:
            details = traceback.format_exc()
            if quiet:
                self._on_job_failed_quietly(str(exc), details)
            else:
                self._on_job_failed(str(exc), details)
            return False

        def work(progress: Progress):
            progress.step(0, tr("Reading the project..."))
            return self._read_current_project(progress, prepared)

        generation = self._project_generation

        def on_success(result) -> None:
            export, report = result
            if generation != self._project_generation:
                # QGIS changed the project during the read: use it for this
                # press, but neither cache it nor call the report current.
                self._show_fidelity(report, signature)
                self._fidelity_is_stale = True
                then(export, report)
                return
            self._cached_export = export
            self._cached_report = report
            self._cached_signature = signature
            self._cached_canvas_extent = canvas_extent
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
        self._cached_canvas_extent = None
        # Tells a read or write already running that it started on the old
        # project, so it must not record itself as current when it lands.
        self._project_generation += 1
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
            # Served one-offs carry a listener too, deaf to reloads: it is how
            # the server learns the tab has closed.
            return write_preview(
                export,
                identity,
                writer=writer,
                live=live,
                served=served,
                on_progress=self._writing_progress(progress),
                should_cancel=progress.should_cancel,
            )

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
            if live and self._server is not None and not self._live_running():
                # A fresh server for a live session: an old one-off tab still
                # connected to this one would count as a viewer, and keep the
                # session rebuilding after its own tab had closed.
                self._stop_live_preview()
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
                self._idle_timer.start()
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

    def on_handoff(self, task: str) -> None:
        """Share > Enhance / Critique: the hand-off dialog, on a current file.

        Through `_with_current_map_file` like every other Share row, so the
        prompt never names a map file that is missing or out of date.
        """
        from .handoff_dialog import HandoffContext, HandoffDialog

        def show(destination: Path) -> None:
            context = HandoffContext(
                output="web_map",
                file_path=destination,
                source_name=self.state.map_name
                or self.project.title()
                or Path(self._project_identity()).stem,
                plugin_version=PLUGIN_VERSION,
                task="enhance" if task == "enhance" else "critique",
            )
            HandoffDialog(context, self).exec()

        self._with_current_map_file(show)

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
        # With it, the place: switching the type renames the field, and a write
        # landing after that would put one type's output at the other's path.
        self._pending_destination = self._map_file()
        # Taken before the read, not after the write: a setting changed while
        # this runs is not in the file, so the file must not claim it.
        self._pending_signature = self.state.snapshot()
        self._pending_generation = self._project_generation
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
        # the Map tab's type can change while the read is still running.
        mode = self._pending_mode

        # Issue #29: never quietly hand over a single file that will not travel.
        # This used to switch the selection to Share ZIP itself, which was worse
        # than the problem: the radio buttons did not follow, so the dialog said
        # Standalone HTML while it wrote a zip. The rule now lives on the Map
        # tab where it can inform the choice, and this is the last reminder
        # before it costs anything. The user's choice stands either way.
        if mode is OutputMode.STANDALONE_HTML:
            # A raster's size here is the source file's, an estimate: the
            # converted raster is measured against the page limit during the
            # export, so this is the user's call like any large single file.
            reason = standalone_ineligible_reason(export)
            if reason is not None and not self._confirm_oversized_single_file(reason):
                self._export_then = None
                return

        # After the blocking checks: no point downloading the runtime for a
        # project that was never going to export.
        if not self._runtime_ready():
            self._export_then = None
            return

        destination = self._resolve_destination(self._pending_destination)
        if destination is None:
            self._export_then = None
            return

        writer = self._writer()

        def work(progress: Progress):
            progress.step(READ_SHARE, tr("Writing the map..."))

            def on_commit() -> None:
                # The last point Cancel can stop: from here the map is being
                # put in place, and a stop now would report "cancelled" over a
                # file that was written.
                progress.step(COMMIT_AT, tr("Saving the map..."))
                progress.commit()

            try:
                result, outcome = build_artifact(
                    export,
                    destination,
                    mode=mode,
                    writer=writer,
                    on_progress=self._writing_progress(progress),
                    should_cancel=progress.should_cancel,
                    on_commit=on_commit,
                )
            except ExportBlockedError as exc:
                # Returned rather than raised: a blocked export is the user's to
                # fix, and raising would route it to the crash reporter with a
                # "please report this" that has nothing to report.
                return exc
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

            # What the map file was written from, so a later Share action
            # can tell "already current" from "the project has moved on".
            if self._pending_generation == self._project_generation:
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

        # No "switch to zip" button: switching the type behind the user's back
        # left the radios saying one thing while the dialog wrote another
        # (issue #29). The Map tab's Export as is where the type is chosen.
        return box.clickedButton() is write_anyway

    def _resolve_destination(self, chosen: Path | None = None) -> Path | None:
        """Where to write: `chosen`, or the Save to field as it is now.

        No picker at Export time. The type and the place were both chosen on
        the Map tab, so pressing Export does exactly what it says.
        """
        destination = chosen or self._map_file()
        if destination is None:
            self._warn_no_map_file()
            return None
        if not destination.parent.exists() and destination.parent.is_relative_to(
            save_root()
        ):
            # The save folders are ours to make: the default path names them
            # before anything has been saved there. A failure is refused just
            # below, with the folder named.
            with contextlib.suppress(OSError):
                destination.parent.mkdir(parents=True, exist_ok=True)
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
        self._cartograph.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._shutdown()
        super().closeEvent(event)
