"""Cartograph (Beta): the Cartograph and Frames tabs, the corner, and Share.

One object, built once per `MainDialog` and owning everything the plan's
"Minimal-diff approach" keeps out of `main_dialog.py`: the Source/Frames
widgets, the autosave job, the status line, and the cartograph Share menu.
`main_dialog.py` only swaps these widgets in and dispatches the corner here;
see its `_apply_output`.

**The document model (issue #3 S1).** A cartograph is a file that is always
saved, never something Export produces on demand. Any change on the
Cartograph or Frames tab - and any change to the selected layout itself, via
its `changed` signal - schedules a debounced rebuild here, which writes the
result as a temp file next to the target and `os.replace`s it in: a kill
mid-write leaves the previous file intact, never a half-written one.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import re
import sys
import time
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSize, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QAction,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import cartograph_export
from ...core.atomic_write import write_text_atomic
from ...core.canvas_cartograph import (
    template_record,
)
from ...core.cartograph_ir import CartographSpec
from ...core.export_resolution import DEFAULT_EXPORT_DPI
from ...core.fidelity_summary import (
    ReportState,
    ReportSummary,
    strip_message,
    summarise,
)
from ...core.layout_templates import TextSizes, get_template
from ...core.portable_names import avoid_windows_reserved
from ...core.print_settings import PrintSettings, apply_to_spec
from ...core.save_folders import cartographs_folder, unused_path
from ...core.settings import (
    DEFAULT_STATIC_DPI,
    DialogOutput,
    FrameChoice,
    save_state,
)
from ...packaging.runtime_manager import (
    RuntimeBundle,
    RuntimeNotAcceptedError,
    RuntimeUnavailableError,
)
from ..background_job import BackgroundJob
from ..fitted_label import FittedLabel
from ..i18n import N_, plural, tr
from ..live_server import BIND_HOST, PreviewServer
from ..runtime_setup import ensure_runtime
from .frames import FrameItem, layout_map_items
from .frames_list import FrameRow, FramesList
from .layer_membership import LayerMembership
from .layout_page import (
    LayoutPicker,
    PagePane,
    RenameLayoutDialog,
    SaveLocation,
    new_layout_button,
)
from .panel_section import PanelSection
from .stacked_scroll import StackedScrolling
from .status import page_label, status_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    # QtWidgets on Qt 5, QtGui on Qt 6.
    from qgis.core import QgsPrintLayout
    from qgis.PyQt.QtGui import QUndoStack

    from ...core.canvas_cartograph import LayerRevisions
    from ...core.cvd import Condition, LegendEntry
    from ...core.export_ir import ExportProject
    from ...core.fidelity_report import FidelityReportBuilder
    from ..main_dialog import MainDialog
    from .accessibility_dialog import AccessibilityDialog

# Debounce per the spec: a burst of edits - dragging a DPI slider would be one
# if this tab had one - collapses into one rebuild, the same ~600 ms the plan
# settles on for the document model.
AUTOSAVE_DEBOUNCE_MS = 600
#: How long a restyle burst must rest before the preview is redrawn.
REFRESH_DEBOUNCE_MS = 120
STATUS_TICK_MS = 1000
# How long closing waits for a running build, as `main_dialog`'s own job.
SHUTDOWN_WAIT_MS = 5000
DPI_CHOICES = (150, 200, 300)

# The runtime's built-in themes (`core/cartograph_ir.CARTOGRAPH_THEMES`), in
# the order the Theme box offers them.
# The panel beside the page preview: wide enough for the layer list's four
# columns and a path's tail, narrow enough that the page keeps most of the
# screen. The user can drag it wider; never narrower than the minimum.
PANEL_WIDTH = 380
PANEL_MIN_WIDTH = 320
# Its first width follows the window (`fit_panel_to_window`): a small window
# keeps the page roomy, a large one gives the longer controls space.
PANEL_WIDTH_SMALL = 350
PANEL_WIDTH_LARGE = 430
PANEL_WIDE_WINDOW = 1200
PANEL_TABS_MIN_HEIGHT = 180

_THEME_LABELS = (
    ("default", N_("Default")),
    ("dark", N_("Dark")),
    ("sepia", N_("Sepia")),
    ("blueprint", N_("Blueprint")),
    ("minimal", N_("Minimal")),
)


def _reveal_label() -> str:
    """What "show me this file" is called here. Duplicated from
    `main_dialog._reveal_label` rather than imported: that module imports this
    package to build the switch, so importing back would be circular."""
    if sys.platform == "darwin":
        return tr("Reveal in Finder")
    if sys.platform.startswith("win"):
        return tr("Show in Explorer")
    return tr("Show in file manager")


def default_cartograph_path(
    project_file_name: str, layout_name: str, folder: Path | None = None
) -> Path:
    """Where a layout's cartograph is written by default.

    In the Cartographs folder (`core/save_folders.py`), so every cartograph
    sits together whether or not the project has been saved, named after the
    project and the layout: `parcels-Survey.html`. A project never saved has
    no name yet, so the layout's alone names it.
    """
    base_dir = folder if folder is not None else cartographs_folder()
    safe = _file_name_part(layout_name) or "cartograph"
    project = _file_name_part(Path(project_file_name).stem)
    return base_dir / (f"{project}-{safe}.html" if project else f"{safe}.html")


def _file_name_part(name: str) -> str:
    """A name as one part of a file name, safe on Windows, macOS and Linux.

    Separators, reserved and control characters become a dash, and dots and
    dashes are trimmed from both ends: "carto: Parcels" -> "carto-Parcels",
    "Survey /2026" -> "Survey-2026", and "..." -> "" (the caller's fallback).
    A Windows device name gets "-map" added: "CON" -> "CON-map".
    """
    return avoid_windows_reserved(re.sub(r"[^\w.-]+", "-", name).strip("-."))


def _panel_scroll(content: QWidget) -> QScrollArea:
    """A panel tab's content, scrolling up and down only."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    # As needed rather than never: every tab is laid out to fit the panel,
    # but a larger system font or a long translation can still widen it,
    # and a scroll bar is better than controls cut off at the edge.
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    # Not a Tab stop of its own: the controls inside are, and a focused
    # scroll area shows no sign of having the focus.
    scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    scroll.setWidget(content)
    return scroll


class _PanelColumn(QWidget):
    """The panel's column, as tall as it must be rather than would like.

    Qt 5's `QScrollArea` sizes a widget whose layout wraps text by its
    preferred height for the width, so on QGIS 3 the column outgrew a normal
    window and scrolled as a whole beside its own scrolling tabs. Its minimum
    is what decides whether the column needs to scroll; Qt 6 already uses it.
    """

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        layout = self.layout()
        if layout is None:
            return super().heightForWidth(width)
        return max(layout.minimumHeightForWidth(width), layout.minimumSize().height())


class CartographController:
    """Owns the Cartograph output's widgets, state and autosave job."""

    def __init__(self, dialog: MainDialog) -> None:
        self.dialog = dialog
        self.project = dialog.project
        self.state = dialog.state

        self._current_layout = None
        self._layout_changed_connected = False
        self._selection_connected = False
        self._watched_stack: QUndoStack | None = None
        # True while this controller's own export reads (and briefly changes)
        # the layout: what that emits is not an edit.
        self._exporting = False
        self._manager_connected = False
        self._frame_rows: dict[str, dict[str, QComboBox]] = {}
        # What each listed map item's row shows: (name, CRS authid), so a
        # rename or CRS change of an item already listed is noticed.
        self._frame_identities: dict[str, tuple[str, str]] = {}
        # Which layers the layout's map frame draws, edited from the Layers list.
        self.membership = LayerMembership(self)

        self._job: BackgroundJob | None = None
        self._last_saved_at: float | None = None
        self._last_saved_bytes: int | None = None
        self._last_error = ""
        self._last_summary: ReportSummary | None = None
        self._runtime: RuntimeBundle | None = None
        # "A3 landscape" / "420 x 297 mm landscape", from the last save, for
        # the hand-off prompt's print-safety line.
        self._last_page_label: str | None = None
        # Builds started so far, and the pages to open once a save has landed
        # that carries at least so many edits: (query, edit revision). See
        # `_open_saved`.
        self._builds_started = 0
        self._open_after_save: list[tuple[str, int]] = []
        # The edit revision the running build was started at.
        self._job_edits = 0
        # Saves asked for, and how many of them the file on disk carries: the
        # file is current only when the two meet. A failed save (worker or
        # disk) leaves it behind, so an action never opens an older file.
        self._edits_made = 0
        self._edits_saved = 0
        # The user asked for a check and no build has answered yet; and the
        # report state the Checks tab and strip were last drawn for.
        self._check_requested = False
        self._shown_report_state: ReportState | None = None
        self._loading_page = False
        self._colour_vision: AccessibilityDialog | None = None
        self._legend_entries: tuple[LegendEntry, ...] = ()
        self._pending_spec: CartographSpec | None = None
        # Map item uuid -> how many of its fidelity rows are not "Kept".
        self._frame_issue_counts: dict[str, int] = {}
        # Each vector layer's edits, counted from the first "From canvas...":
        # part of the template picker's preview key.
        self._layer_revisions: LayerRevisions | None = None
        self._built_layers_signature = self._layers_signature()
        self._selected_name: str | None = None
        # The project's print layouts as last listed, to tell which one a
        # rename was about; and old name -> new name for builds begun under a
        # name that has since changed.
        self._listed_layouts: list[tuple[QgsPrintLayout, str]] = []
        self._renamed: dict[str, str] = {}
        self._shut_down = False
        self._preview_server: PreviewServer | None = None
        self._preview_server_error = ""

        # Rendered static frames are kept between saves; a closed project's
        # are no use to the next one and only hold memory.
        self.project.cleared.connect(cartograph_export.clear_static_frame_cache)

        self._autosave_timer = QTimer(dialog)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._run_autosave)
        # A restyle in QGIS arrives as a stream of events; each refresh drops
        # the preview's caches and starts the maps drawing again, so only the
        # last of a burst is worth it.
        self._refresh_timer = QTimer(dialog)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self._refresh_current_layout)

        self._status_timer = QTimer(dialog)
        self._status_timer.setInterval(STATUS_TICK_MS)
        self._status_timer.timeout.connect(self._on_status_tick)

        self._corner: QWidget | None = None

    # ---- Lifecycle --------------------------------------------------------

    def showing(self) -> None:
        """The dialog just switched into Cartograph. Catch up on the project."""
        self._status_timer.start()
        has_canvas = self._map_canvas() is not None
        self.page_preview.template_button.setEnabled(has_canvas)
        self.new_from_template_button.setEnabled(has_canvas)
        self.new_from_template_button.setToolTip(
            self._new_from_template_tip
            if has_canvas
            else tr("There is no map canvas to build from.")
        )
        self.refresh_layouts()

    def hiding(self) -> None:
        """The dialog just switched back to the web map."""
        self._status_timer.stop()

    @property
    def busy(self) -> bool:
        """Whether a build is reading the project right now."""
        return self._job is not None

    def shutdown(self) -> None:
        """Release everything that outlives a single widget - called once,
        from `MainDialog._shutdown`, alongside the web map's own teardown."""
        self._autosave_timer.stop()
        self._refresh_timer.stop()
        self._status_timer.stop()
        # Same rule as the web map's job (`MainDialog._shutdown`): a build
        # thread still running when its dialog is destroyed takes QGIS down.
        self._shut_down = True
        job, self._job = self._job, None
        if job is not None:
            # Its result is for widgets that are about to go: never deliver it.
            for signal in (job.succeeded, job.failed):
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect()
            job.cancel()
            with contextlib.suppress(RuntimeError):
                job.wait(SHUTDOWN_WAIT_MS)
        if self._preview_server is not None:
            self._preview_server.stop()
            self._preview_server = None
        self._disconnect_layout_manager()
        self._disconnect_current_layout()
        with contextlib.suppress(TypeError, RuntimeError):
            self.project.cleared.disconnect(cartograph_export.clear_static_frame_cache)

    # ---- Cartograph tab -----------------------------------------------

    def build_cartograph_tab(self) -> QWidget:
        """The Cartograph output's whole screen: the page, live, and one
        panel beside it - the layout list, then three tabs.

        It replaces seven tabs across the dialog. The panel's own tabs -
        Layout, Atlas, Checks - are the split QGIS's layout designer and
        ArcGIS Pro make beside their page: the longer tasks get the panel's
        full height instead of fighting for one scrolling column. Inside
        Layout, Page, Map frames and Layers are sections that open and close
        (`panel_section`). Layers and Checks are the dialog's own widgets,
        shared with the web map; the dialog seats them here while Cartograph
        is on screen (`seat_shared`).
        """
        splitter = QSplitter(Qt.Orientation.Horizontal, self.dialog)
        splitter.setChildrenCollapsible(False)

        self.page_pane = PagePane(splitter)
        self.page_preview = self.page_pane.preview
        self.page_preview.new_from_template_requested.connect(self.create_from_canvas)
        self.page_preview.import_requested.connect(self.on_import_cartograph)
        self.page_pane.changes_requested.connect(lambda: self.open_section("checks"))
        self.page_pane.open_editor_requested.connect(self.on_open_layout_editor)
        self.page_pane.shown.connect(
            lambda: QTimer.singleShot(0, self.fit_panel_to_window)
        )
        splitter.addWidget(self.page_pane)

        # The whole column scrolls, not only its tabs: the layout list above
        # them and the save location below do not shrink, and on a window at
        # its minimum height (320 px, see main_dialog) they would otherwise
        # be painted over the tabs. Taller than that it never scrolls.
        panel_scroll = QScrollArea(splitter)
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        panel_scroll.setMinimumWidth(PANEL_MIN_WIDTH - 12)
        panel = _PanelColumn()
        panel.setMinimumWidth(PANEL_MIN_WIDTH - 12)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        layout_heading = QLabel(tr("Print layout"), panel)
        layout_heading.setProperty("role", "panelHeading")
        column.addWidget(layout_heading)
        picker_row = QHBoxLayout()
        picker_row.setSpacing(6)
        self.layout_combo = LayoutPicker(panel)
        self.layout_combo.currentTextChanged.connect(self._on_layout_combo_changed)
        picker_row.addWidget(self.layout_combo, 1)
        # Only existing layouts are listed above; this renames the one shown.
        self.rename_layout_button = QToolButton(panel)
        self.rename_layout_button.setText(tr("Rename..."))
        self.rename_layout_button.setProperty("role", "secondary")
        self.rename_layout_button.setProperty("hasMenu", False)
        self.rename_layout_button.setAccessibleName(tr("Rename layout"))
        self.rename_layout_button.setToolTip(tr("Rename the print layout shown"))
        self.rename_layout_button.clicked.connect(self.on_rename_layout)
        picker_row.addWidget(self.rename_layout_button)
        column.addLayout(picker_row)
        # The one way to make a layout, always in sight: it makes ANOTHER
        # layout from the canvas in the design picked; the layout shown above
        # is not changed. Its menu holds the import.
        self.new_from_template_button, self.import_action = new_layout_button(
            panel, "secondary"
        )
        self._new_from_template_tip = tr(
            "New layout from template: pick a design and build another print "
            "layout from what the map canvas shows. The layout shown here is "
            "kept as it is."
        )
        self.new_from_template_button.setToolTip(self._new_from_template_tip)
        self.new_from_template_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.new_from_template_button.clicked.connect(self.create_from_canvas)
        self.import_action.triggered.connect(self.on_import_cartograph)
        column.addWidget(self.new_from_template_button)
        # Which design the selected layout came from, for a template-made one.
        self.based_on_label = FittedLabel("", panel)
        self.based_on_label.setProperty("role", "hint")
        self.based_on_label.setVisible(False)
        column.addWidget(self.based_on_label)

        self.layout_empty_note = FittedLabel(
            tr(
                "This project has no print layouts yet. Start from a template, "
                "or import a cartograph."
            ),
            panel,
        )
        self.layout_empty_note.setWordWrap(True)
        self.layout_empty_note.setProperty("role", "hint")
        self.layout_empty_note.setVisible(False)
        column.addWidget(self.layout_empty_note)
        column.addSpacing(4)

        self.panel_tabs = QTabWidget(panel)
        self.panel_tabs.setProperty("role", "panelTabs")
        self.panel_tabs.setDocumentMode(True)
        # Never squeezed to nothing by a short window: the save lines below
        # are fixed, so the tabs keep room for a few rows of their own.
        self.panel_tabs.setMinimumHeight(PANEL_TABS_MIN_HEIGHT)

        # Layout: the page's own settings, then its maps, then its layers.
        sections = QWidget()
        sections_column = QVBoxLayout(sections)
        sections_column.setContentsMargins(0, 4, 6, 0)
        sections_column.setSpacing(2)
        self.page_section = PanelSection(
            "page", tr("Browser appearance"), sections, open_by_default=True
        )
        self.page_section.setToolTip(
            tr(
                "Settings for the web page only. The layout itself is edited in "
                "the preview and the layout editor."
            )
        )
        # Next to the heading, before the fields: these are web-page-only
        # settings, which the QGIS layout preview beside the panel never shows.
        page_note = FittedLabel(
            tr(
                "Only the web page changes, not the layout preview here. "
                "See these with Preview in browser."
            ),
            self.page_section.body,
        )
        page_note.setWordWrap(True)
        page_note.setProperty("role", "hint")
        self.page_page_note = page_note
        self.page_section.body_layout.addWidget(page_note)
        page_form = QFormLayout()
        page_form.setContentsMargins(0, 0, 0, 0)
        page_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        page_form.setVerticalSpacing(4)
        self.title_edit = QLineEdit(self.page_section.body)
        self.title_edit.setPlaceholderText(tr("The project title"))
        self.title_edit.setToolTip(
            tr("Shown wherever the layout uses the project title. Blank keeps it.")
        )
        self.title_edit.textEdited.connect(self._on_page_edited)
        page_form.addRow(tr("Title"), self.title_edit)
        self.attribution_edit = QLineEdit(self.page_section.body)
        self.attribution_edit.setPlaceholderText(tr("Optional, e.g. data sources"))
        self.attribution_edit.textEdited.connect(self._on_page_edited)
        page_form.addRow(tr("Attribution"), self.attribution_edit)
        self.theme_combo = QComboBox(self.page_section.body)
        for theme_id, label in _THEME_LABELS:
            self.theme_combo.addItem(tr(label), theme_id)
        self.theme_combo.currentIndexChanged.connect(self._on_page_edited)
        page_form.addRow(tr("Theme"), self.theme_combo)
        self.page_section.body_layout.addLayout(page_form)
        self.frames_section = PanelSection("frames", tr("Map frames"), sections)
        self.frames_section.body_layout.addWidget(self.build_frames_tab())
        self.layers_section = PanelSection("layers", tr("Layers"), sections)
        self.sections = {
            "page": self.page_section,
            "frames": self.frames_section,
            "layers": self.layers_section,
        }
        for section in self.sections.values():
            sections_column.addWidget(section)
        sections_column.addStretch(1)
        self.layout_scroll = _panel_scroll(sections)
        self.panel_tabs.addTab(self.layout_scroll, tr("Layout"))

        self.build_atlas_tab()
        self.atlas_tab.changed.connect(self._update_section_summaries)
        self.panel_tabs.addTab(_panel_scroll(self.atlas_tab), tr("Atlas"))

        # Checks: the dialog's report panel, which scrolls by itself.
        self.checks_page = QWidget()
        self._checks_layout = QVBoxLayout(self.checks_page)
        self._checks_layout.setContentsMargins(0, 4, 0, 0)
        self.panel_tabs.addTab(self.checks_page, tr("Checks"))
        self._tab_keys = ("layout", "atlas", "checks")
        column.addWidget(self.panel_tabs, 1)

        # Takes the tabs' room while they are hidden, so the layout list
        # stays at the top of an empty panel instead of drifting down it.
        self._panel_filler = QWidget(panel)
        self._panel_filler.setVisible(False)
        column.addWidget(self._panel_filler, 1)

        self.file_edit = SaveLocation(panel)
        self.file_edit.setPlaceholderText(tr("Choose where the cartograph is saved"))
        self.file_edit.textEdited.connect(self._on_file_edited)
        self.file_edit.change_requested.connect(self._on_choose_file)
        self.file_edit.reveal_requested.connect(self.on_reveal)
        column.addWidget(self.file_edit)
        # Beside the scrolling column, not in it: whether the work is saved
        # is always in sight, however short the window.
        panel_side = QWidget(splitter)
        panel_side_column = QVBoxLayout(panel_side)
        panel_side_column.setContentsMargins(12, 0, 0, 0)
        panel_side_column.setSpacing(2)
        self.cartograph_status_label = FittedLabel("", panel_side)
        self.cartograph_status_label.setWordWrap(True)
        self.cartograph_status_label.setProperty("role", "hint")
        # Everything below the list belongs to a layout; with none there is
        # nothing to set, so it is hidden rather than left to type into.
        self._layout_only = (
            self.panel_tabs,
            self.file_edit,
            self.cartograph_status_label,
        )
        self._update_status_label()

        panel_scroll.setWidget(panel)
        self.panel_scroll = panel_scroll
        # One scrolling surface at a time: the tabs while the column fits, the
        # column (with the tabs grown to their content) when it does not.
        self._stacked_scrolling = StackedScrolling(
            panel_scroll,
            self.panel_tabs,
            PANEL_TABS_MIN_HEIGHT,
            lambda width: panel.heightForWidth(width),
        )
        panel_side_column.insertWidget(0, panel_scroll, 1)
        panel_side_column.addWidget(self.cartograph_status_label)
        panel_side.setMinimumWidth(PANEL_MIN_WIDTH)
        splitter.addWidget(panel_side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([10_000, PANEL_WIDTH])
        self._panel_sized = False
        self.workspace = splitter
        return splitter

    def seat_shared(self, layers_page: QWidget, report_page: QWidget) -> None:
        """Put the dialog's Layers list and report in this panel - the same
        widgets the web map shows as tabs."""
        self.layers_section.body_layout.addWidget(
            self.membership.attach(self.layers_section)
        )
        self.layers_section.body_layout.addWidget(layers_page)
        self._checks_layout.addWidget(report_page)
        layers_page.show()
        report_page.show()

    def fit_panel_to_window(self) -> None:
        """The panel's first width, from the window's: narrower on a small
        window so the page keeps room, wider on a large one so the longer
        controls breathe. Once only - after that the width is the user's."""
        if self._panel_sized or not self.workspace.isVisible():
            return
        total = self.workspace.width()
        if total <= 0:
            return
        self._panel_sized = True
        width = PANEL_WIDTH_SMALL if total < PANEL_WIDE_WINDOW else PANEL_WIDTH_LARGE
        self.workspace.setSizes([max(1, total - width), width])

    def open_section(self, key: str) -> None:
        """Bring a part of the panel into view - a jump from elsewhere, such
        as a check's "Show in Layers". "atlas" and "checks" are tabs; the
        rest are sections of the Layout tab."""
        if key in self._tab_keys:
            self.panel_tabs.setCurrentIndex(self._tab_keys.index(key))
            return
        self.panel_tabs.setCurrentIndex(0)
        section = self.sections[key]
        section.set_open(True)
        scroll = self.layout_scroll
        QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(section, 0, 0))

    def _update_checks_summary(self) -> None:
        """The Checks tab's title carries the count, as a badge would."""
        if not hasattr(self, "panel_tabs"):
            return
        summary = self._last_summary
        index = self._tab_keys.index("checks")
        if summary is None:
            tip = tr("Not checked yet.")
            title = tr("Checks")
        elif not summary.change_count:
            tip = tr("No differences from the QGIS layout.")
            title = tr("Checks")
        else:
            tip = plural(
                summary.change_count,
                "{n} difference from the QGIS layout to review",
                "{n} differences from the QGIS layout to review",
            )
            title = tr("Checks ({n})").format(n=summary.change_count)
        self.panel_tabs.setTabText(index, title)
        self.panel_tabs.setTabToolTip(index, tip)
        self.page_pane.set_change_count(summary.change_count if summary else 0)

    def _update_section_summaries(self, *_args: object) -> None:
        """The few words each closed section, and the Atlas tab, show."""
        if not hasattr(self, "sections"):
            return
        self.page_section.set_summary(
            tr("{theme} theme").format(theme=self.theme_combo.currentText())
        )
        self.frames_section.set_summary(self.frames_list.summary())
        atlas = getattr(self, "atlas_tab", None)
        on = atlas is not None and atlas.enabled_check.isChecked()
        index = self._tab_keys.index("atlas")
        self.panel_tabs.setTabText(index, tr("Atlas (on)") if on else tr("Atlas"))
        self.panel_tabs.setTabToolTip(
            index,
            tr("One page per feature: on") if on else tr("One page per feature: off"),
        )

    def create_from_canvas(self) -> None:
        """One-click mode (issue #1 S4): the canvas becomes a "carto:" print
        layout in the project, selected as this cartograph's source -- in the
        layout template the user picks (`template_picker.py`), named
        "<design> · <project title>"."""
        from ...core.canvas_cartograph import (
            LayerRevisions,
            cartograph_from_canvas,
            preview_state_key,
            template_layout_name,
            template_preview_async,
            view_input,
        )
        from ...core.layout_templates import get_template, plan_template
        from .template_picker import ask_template

        canvas = self._map_canvas()
        if canvas is None:
            self.dialog.status_label.setText(
                tr("There is no map canvas to build a layout from.")
            )
            return
        settings = canvas.mapSettings()
        layers = canvas.layers()
        placeholders = _template_placeholders()
        if self._layer_revisions is None:
            self._layer_revisions = LayerRevisions()
        # Everything the previews draw: the same key next time means the
        # pages drawn now are still true, and the picker shows them at once.
        try:
            canvas_key = preview_state_key(
                settings.extent(),
                settings.destinationCrs(),
                layers,
                self.project,
                rotation=settings.rotation(),
                revisions=self._layer_revisions.track(layers),
            )
        except Exception:  # an unreadable layer: no sharing, not no picker
            canvas_key = ""
        # Read once per opening: the planner checks each card's cues against
        # this project's page for every page setting the user tries.
        try:
            base_input = view_input(
                settings.extent(),
                settings.destinationCrs(),
                layers,
                self.project,
                rotation=settings.rotation(),
                placeholders=placeholders,
            )
        except Exception:  # the cards then say only what every page has
            base_input = None

        def plan(
            template_id: str,
            page_size: str,
            orientation: str,
            *,
            figures: Sequence[tuple[str, str]],
            sizes: TextSizes | None = None,
        ):
            if base_input is None:
                raise ValueError("the canvas could not be read")
            return plan_template(
                template_id,
                (sizes or TextSizes()).apply(
                    dataclasses.replace(
                        base_input,
                        page_size=page_size,
                        orientation=orientation,
                        figures=tuple(figures),
                    )
                ),
            )

        def render(
            template_id: str,
            page_size: str,
            orientation: str,
            width_px: int,
            figures: Sequence[tuple[str, str]] | None = None,
            sizes: TextSizes | None = None,
        ):
            return template_preview_async(
                settings.extent(),
                settings.destinationCrs(),
                layers,
                self.project,
                template_id=template_id,
                width_px=width_px,
                rotation=settings.rotation(),
                page_size=page_size,
                orientation=orientation,
                placeholders=placeholders,
                figures=figures,
                text_sizes=sizes,
            )

        # The picker saves the key figures and text sizes chosen in it to the project
        # as it accepts, so the layout built next reads them from there.
        choice = ask_template(
            render,
            parent=self.dialog,
            canvas_key=canvas_key,
            project=self.project,
            planner=plan,
        )
        if choice is None:
            return
        template_id, page_size, orientation = choice
        try:
            result = cartograph_from_canvas(
                canvas,
                self.project,
                page_size=page_size,
                orientation=orientation,
                template_id=template_id,
                placeholders=placeholders,
                layout_name=template_layout_name(
                    tr(get_template(template_id).name),
                    self.project.title(),
                    tr("Untitled map"),
                ),
            )
        except Exception as exc:  # a canvas state nobody anticipated
            self.dialog.status_label.setText(
                tr("Could not build a layout from the canvas: {error}").format(
                    error=str(exc)
                )
            )
            return
        name = result.layout.name()
        choices = self.state.frame_choices(name)
        for item_id, mode in result.frame_modes.items():
            choices[item_id] = FrameChoice(mode=mode)
        self._show_new_layout(name)
        self.dialog.status_label.setText(
            tr(
                'Created the print layout "{name}" from the canvas. Edit it in '
                "the layout designer any time; the cartograph follows."
            ).format(name=name)
        )

    def on_import_cartograph(self) -> None:
        """Import cartograph...: an HTML cartograph back into a QGIS layout
        (the round trip), then used as the source."""
        from qgis.PyQt.QtWidgets import QMessageBox

        from ...core.cartograph_import import CartographImportError
        from ...core.layout_builder import import_cartograph_file

        chosen, _ = QFileDialog.getOpenFileName(
            self.dialog,
            tr("Import cartograph"),
            str(
                Path(self.project.fileName()).parent
                if self.project.fileName()
                else Path.home()
            ),
            tr("Cartograph (*.html *.htm)"),
        )
        if not chosen:
            return
        try:
            built = import_cartograph_file(Path(chosen), self.project)
        except (CartographImportError, OSError) as exc:
            QMessageBox.warning(self.dialog, tr("Import cartograph"), str(exc))
            return
        name = built.layout.name()
        choices = self.state.frame_choices(name)
        for item_id, mode in built.frame_modes.items():
            dpi = built.frame_dpis.get(item_id, DEFAULT_STATIC_DPI)
            choices[item_id] = FrameChoice(mode=mode, dpi=dpi)
        self._show_new_layout(name)
        message = tr('Imported as the print layout "{name}".').format(name=name)
        if built.issues:
            details = "\n".join(
                f"- {issue.subject}: {issue.detail}" for issue in built.issues
            )
            QMessageBox.information(
                self.dialog,
                tr("Import cartograph"),
                message
                + "\n\n"
                + tr("Some parts could not be carried into QGIS:")
                + "\n"
                + details,
            )
        self.dialog.status_label.setText(message)

    def on_rename_layout(self) -> None:
        """Rename layout...: the layout shown, through QGIS's layout manager.
        The manager's `layoutRenamed` signal does the rest (`_on_layout_renamed`
        moves the saved settings and refreshes the list, caption and title)."""
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout):
            return
        old = layout.name()
        taken = {pl.name() for pl in self.project.layoutManager().printLayouts()}
        dialog = RenameLayoutDialog(old, taken, self.dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = dialog.new_name()
        if new == old:
            return
        layout.setName(new)
        self.dialog.status_label.setText(
            tr('Renamed the print layout "{old}" to "{new}".').format(old=old, new=new)
        )

    def _show_new_layout(self, name: str) -> None:
        """Select a layout that was just added to the project."""
        self.state.cartograph_layout = name
        self.refresh_layouts()
        self.layout_combo.setCurrentText(name)
        save_state(self.project, self.state)

    def _on_layout_combo_changed(self, _text: str) -> None:
        # An action row passes through the list on its way to running; it
        # is never a layout to select.
        name = self.layout_combo.current_layout()
        if name:
            self._select_layout(name)

    def _on_file_edited(self, text: str) -> None:
        # Whatever the footer said about the old file ("Opened ...") is not
        # about this one.
        self._clear_footer()
        if self.state.cartograph_layout:
            self.state.cartograph_files[self.state.cartograph_layout] = text.strip()
        self._on_cartograph_setting_changed()
        self._update_share_enabled()
        self.update_window_title()

    def _on_page_edited(self, *_args: object) -> None:
        if not self.state.cartograph_layout or self._loading_page:
            return
        page = self.state.page_choice(self.state.cartograph_layout)
        page.title = self.title_edit.text().strip()
        page.attribution = self.attribution_edit.text().strip()
        page.theme = self.theme_combo.currentData() or "default"
        self._update_section_summaries()
        self._on_cartograph_setting_changed()

    def _load_page_fields(self) -> None:
        """Show the selected layout's page fields without counting as edits."""
        page = self.state.page_choice(self.state.cartograph_layout)
        self._loading_page = True
        try:
            self.title_edit.setText(page.title)
            self.attribution_edit.setText(page.attribution)
            index = self.theme_combo.findData(page.theme)
            self.theme_combo.setCurrentIndex(max(index, 0))
        finally:
            self._loading_page = False

    def _sync_based_on(self) -> None:
        """Show the selected layout's template and the editor link."""
        layout = self._current_layout
        record = template_record(layout) if layout is not None else None
        self._show_based_on(record)
        self._update_editor_link()

    def _show_based_on(self, record) -> None:  # TemplateRecord | None
        """ "Based on <design>" under the template button, for a layout made
        from a template; nothing for any other layout."""
        name = ""
        if record is not None:
            try:
                name = tr(get_template(record.template_id).name)
            except ValueError:  # a design this version no longer has
                name = ""
        self.based_on_label.setText(tr("Based on {name}").format(name=name))
        self.based_on_label.setVisible(bool(name))

    def _editor_iface(self):
        return getattr(self.dialog, "iface", None)

    def _update_editor_link(self) -> None:
        link = self.page_pane.editor_link
        ready = self._current_layout is not None and hasattr(
            self._editor_iface(), "openLayoutDesigner"
        )
        link.setEnabled(ready)
        link.setToolTip(
            tr("Edit this layout in QGIS's layout designer.")
            if ready
            else tr("Choose a print layout first.")
        )

    def on_open_layout_editor(self) -> None:
        layout = self._current_layout
        iface = self._editor_iface()
        if layout is None or not hasattr(iface, "openLayoutDesigner"):
            return
        iface.openLayoutDesigner(layout)

    def _apply_page_fields(self, spec: CartographSpec) -> CartographSpec:
        """The Cartograph tab's title, attribution and theme on the page root."""
        page = self.state.page_choice(self.state.cartograph_layout)
        return dataclasses.replace(
            spec,
            theme=page.theme or None,
            title=page.title or spec.title,
            attribution=page.attribution or spec.attribution,
        )

    def update_window_title(self) -> None:
        """The dialog's title names the cartograph file, per issue #3 S1."""
        if self.state.output is not DialogOutput.CARTOGRAPH:
            return
        path = self._current_path()
        base = tr("QGIS2WebMap by NIKA")
        self.dialog.setWindowTitle(f"{path.name} - {base}" if path else base)

    def _on_choose_file(self) -> None:
        current = self.file_edit.text().strip()
        start = current or str(
            default_cartograph_path(
                self.project.fileName(), self.state.cartograph_layout or "cartograph"
            )
        )
        chosen, _ = QFileDialog.getSaveFileName(
            self.dialog, tr("Save cartograph as"), start, tr("Web page (*.html)")
        )
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != ".html":
            path = path.with_suffix(".html")
        self.file_edit.choose(str(path))

    # ---- Frames tab -----------------------------------------------------

    def build_frames_tab(self) -> QWidget:
        """Map frames: a card per map item - see `frames_list`."""
        self.frames_list = FramesList(DPI_CHOICES, self.dialog)
        self.frames_list.mode_changed.connect(
            lambda item_id, _mode: self._on_frame_mode_changed(
                item_id,
                self._frame_rows[item_id]["mode"],
                self._frame_rows[item_id]["dpi"],
            )
        )
        self.frames_list.dpi_changed.connect(
            lambda item_id, _dpi: self._on_frame_dpi_changed(
                item_id, self._frame_rows[item_id]["dpi"]
            )
        )
        self.frames_list.match_canvas_requested.connect(self._match_canvas)
        self.frames_list.frame_selected.connect(self._select_frame_on_page)
        self.frames_list.locate_requested.connect(self._locate_frame)
        self.frames_list.issues_requested.connect(self._show_frame_issues)
        self.frames_list.actions_opening.connect(self._refresh_smoothing_options)
        self.frames_list.smoothing_changed.connect(self._set_raster_smoothing)
        return self.frames_list

    # ---- Atlas tab -------------------------------------------------------

    def build_atlas_tab(self) -> QWidget:
        from .atlas_tab import AtlasTab

        self.atlas_tab = AtlasTab(self._open_all_pages, self.dialog)
        self.atlas_tab.changed.connect(self.schedule_autosave)
        return self.atlas_tab

    def _open_all_pages(self, kind: str) -> None:
        """Atlas > Export all pages / Print all pages: the saved cartograph,
        opened with this plugin's `?atlas=` trigger (see the writer)."""
        path = self._current_path()
        if path is None or not path.exists():
            return
        self._open_saved(path, f"atlas={kind}")

    def _show_atlas_outcome(
        self, report: FidelityReportBuilder, atlas_pages: tuple[int, ...] | None
    ) -> None:
        from ...core.export_ir import FidelityStatus
        from ...core.layout_reader import ATLAS_SUBJECT

        problems = tuple(
            row.detail
            for row in report.items
            if row.subject == ATLAS_SUBJECT
            and row.status in (FidelityStatus.BLOCKED, FidelityStatus.UNSUPPORTED)
        )
        # The pages the saved document will actually page, not whether an
        # `<om-atlas` happens to be in it.
        self.atlas_tab.show_outcome(
            problems, pages=None if atlas_pages is None else len(atlas_pages)
        )

    def _rebuild_frames_rows(self) -> None:
        self._frame_rows.clear()
        self._frame_identities.clear()
        if self._current_layout is None:
            self.frames_list.set_frames(None)
            return

        choices = self.state.frame_choices(self.state.cartograph_layout)
        rows = []
        for frame_item in layout_map_items(self._current_layout):
            choice = choices.get(frame_item.item_id)
            if choice is None:
                choice = FrameChoice(mode=frame_item.default_mode)
                choices[frame_item.item_id] = choice
            rows.append(
                FrameRow(
                    item_id=frame_item.item_id,
                    name=frame_item.name,
                    crs=frame_item.crs_authid or tr("Unknown CRS"),
                    mode=choice.mode,
                    dpi=choice.dpi,
                    issues=self._frame_issue_counts.get(frame_item.item_id),
                )
            )
            self._frame_identities[frame_item.item_id] = (
                frame_item.name,
                frame_item.crs_authid,
            )
        self.frames_list.set_frames(rows)
        self.frames_list.set_canvas_available(self._map_canvas() is not None)
        for row in rows:
            card = self.frames_list.card(row.item_id)
            self._frame_rows[row.item_id] = {
                "mode": card.mode_combo,
                "dpi": card.dpi_combo,
            }

    def _refresh_frame_identities(self, items: list[FrameItem]) -> None:
        """A map item was renamed or reprojected: the rows and the Atlas map
        choice follow, in place."""
        self._frame_identities = {
            item.item_id: (item.name, item.crs_authid) for item in items
        }
        self.frames_list.update_identity(
            {
                item.item_id: (item.name, item.crs_authid or tr("Unknown CRS"))
                for item in items
            }
        )
        self.atlas_tab.rename_maps()

    def _map_item(self, item_id: str):  # QgsLayoutItemMap | None
        """The map item of the current layout with this uuid, if it is still there."""
        from qgis.core import QgsLayoutItemMap

        layout = self._current_layout
        if layout is None or sip.isdeleted(layout):
            return None
        return next(
            (
                item
                for item in layout.items()
                if isinstance(item, QgsLayoutItemMap) and item.uuid() == item_id
            ),
            None,
        )

    def _select_frame_on_page(self, item_id: str) -> None:
        """Map frames: choosing an entry selects its map item on the page."""
        item = self._map_item(item_id)
        if item is not None and self._current_layout is not None:
            self._current_layout.setSelectedItem(item)

    def _locate_frame(self, item_id: str) -> None:
        """Map frames > Locate on page: select the map item and bring it to
        the middle of the page view."""
        item = self._map_item(item_id)
        if item is None:
            return
        self._select_frame_on_page(item_id)
        self.page_pane.preview.view.centerOn(item)

    def _on_page_selection_changed(self, item: object) -> None:
        """The page's selection moved: mark the entry of a selected map."""
        from qgis.core import QgsLayoutItemMap

        selected = None
        if isinstance(item, QgsLayoutItemMap) and not sip.isdeleted(item):
            selected = item.uuid()
        self.frames_list.set_selected(selected)
        self.membership.page_selection_changed(selected)

    def _show_frame_issues(self, item_id: str) -> None:
        """Map frames > "N issues": Checks, narrowed to what concerns the map."""
        item = self._map_item(item_id)
        if item is not None and hasattr(self.dialog, "fidelity_panel"):
            self.dialog.fidelity_panel.search.setText(f"'{item.displayName()}'")
        self.open_section("checks")

    def _map_canvas(self):
        iface = getattr(self.dialog, "iface", None)
        get_canvas = getattr(iface, "mapCanvas", None)
        return get_canvas() if callable(get_canvas) else None

    def _match_canvas(self, item_id: str) -> None:
        """Map frames > Match QGIS canvas: the map item takes the canvas's extent.

        An edit to the user's own layout, so it goes on the layout's undo
        stack where the layout designer's Undo can take it back.
        """
        from qgis.core import QgsCoordinateTransform, QgsLayoutItemMap

        canvas = self._map_canvas()
        layout = self._current_layout
        if canvas is None or layout is None:
            return
        item = next(
            (
                i
                for i in layout.items()
                if isinstance(i, QgsLayoutItemMap) and i.uuid() == item_id
            ),
            None,
        )
        if item is None:
            return
        extent = canvas.extent()
        source = canvas.mapSettings().destinationCrs()
        if source.isValid() and item.crs().isValid() and source != item.crs():
            transform = QgsCoordinateTransform(source, item.crs(), self.project)
            extent = transform.transformBoundingBox(extent)
        layout.undoStack().beginCommand(item, tr("Match QGIS canvas"))
        item.zoomToExtent(extent)
        layout.undoStack().endCommand()
        self.schedule_autosave()

    def _refresh_smoothing_options(self, item_id: str) -> None:
        """The map's actions menu is opening: list the continuous rasters it
        draws now, each with whether this map smooths it."""
        from ...core.raster_smoothing import smoothable_layers, smoothing_on

        item = self._map_item(item_id)
        options: list[tuple[str, str, bool]] = []
        try:
            if item is not None:
                options = [
                    (layer.id(), layer.name(), smoothing_on(item, layer))
                    for layer in smoothable_layers(item)
                ]
        except Exception:  # a slot must not raise: the menu just has no entry
            options = []
        self.frames_list.card(item_id).set_smoothing_options(options)

    def _set_raster_smoothing(
        self, item_id: str, layer_id: str, smoothed: bool
    ) -> None:
        """Map frames > Smooth when enlarged: one raster of one map item,
        an edit to the user's layout on its undo stack, as any other."""
        from ...core.raster_smoothing import set_smoothing

        layout = self._current_layout
        item = self._map_item(item_id)
        layer = self.project.mapLayer(layer_id)
        if layout is None or item is None or layer is None:
            return
        layout.undoStack().beginCommand(item, tr("Smooth raster when enlarged"))
        try:
            changed = set_smoothing(item, layer, smoothed)
        except Exception:  # a slot must not raise; the edit simply is not made
            changed = False
        finally:
            layout.undoStack().endCommand()
        if changed:
            self.schedule_autosave()

    def _on_frame_mode_changed(
        self, item_id: str, mode_combo: QComboBox, dpi_combo: QComboBox
    ) -> None:
        mode = mode_combo.currentData()
        self.state.frame_choices(self.state.cartograph_layout)[item_id].mode = mode
        self._update_section_summaries()
        self._on_cartograph_setting_changed()

    def _on_frame_dpi_changed(self, item_id: str, dpi_combo: QComboBox) -> None:
        dpi = dpi_combo.currentData()
        self.state.frame_choices(self.state.cartograph_layout)[item_id].dpi = dpi
        self._on_cartograph_setting_changed()

    # ---- Layout selection -------------------------------------------------

    def refresh_layouts(self) -> None:
        """Repopulate the Source combo from the project's print layouts."""
        manager = self.project.layoutManager()
        if not self._manager_connected:
            manager.layoutAdded.connect(self._on_layouts_changed)
            manager.layoutRemoved.connect(self._on_layouts_changed)
            manager.layoutRenamed.connect(self._on_layout_renamed)
            self._manager_connected = True

        listed = manager.printLayouts()
        names = [pl.name() for pl in listed]
        self._listed_layouts = list(zip(listed, names))
        # A name taken again by another layout is no longer a renamed one.
        for name in names:
            self._renamed.pop(name, None)

        self.layout_combo.blockSignals(True)
        self.layout_combo.set_layouts(names)
        self.layout_combo.blockSignals(False)
        self.layout_empty_note.setVisible(not names)

        target = (
            self.state.cartograph_layout
            if self.state.cartograph_layout in names
            else (names[0] if names else "")
        )
        self.layout_combo.select_layout(target)
        self._select_layout(target)

    def _on_layouts_changed(self, *_args: object) -> None:
        self.refresh_layouts()

    def _on_layout_renamed(self, layout: object, new_name: str) -> None:
        """Follow a renamed layout by identity: what the state, the selection
        and the builds under way kept under its old name moves with it."""
        old = next(
            (name for known, name in self._listed_layouts if known == layout), None
        )
        if old is not None and old != new_name:
            self.state.rename_cartograph_layout(old, new_name)
            if self._selected_name == old:
                self._selected_name = new_name
            self._renamed = {
                was: (new_name if now == old else now)
                for was, now in self._renamed.items()
            }
            self._renamed[old] = new_name
            save_state(self.project, self.state)
        self.refresh_layouts()

    def _current_name_of(self, built_for: str | None) -> str | None:
        """The name a layout a build began for has now."""
        return self._renamed.get(built_for, built_for) if built_for else built_for

    def _disconnect_layout_manager(self) -> None:
        if not self._manager_connected:
            return
        manager = self.project.layoutManager()
        with contextlib.suppress(Exception):
            manager.layoutAdded.disconnect(self._on_layouts_changed)
            manager.layoutRemoved.disconnect(self._on_layouts_changed)
            manager.layoutRenamed.disconnect(self._on_layout_renamed)
        self._manager_connected = False

    def _disconnect_current_layout(self) -> None:
        if self._current_layout is not None and self._layout_changed_connected:
            with contextlib.suppress(Exception):
                self._current_layout.changed.disconnect(self._on_layout_content_changed)
        self._layout_changed_connected = False
        if self._current_layout is not None and self._selection_connected:
            with contextlib.suppress(Exception):
                self._current_layout.selectedItemChanged.disconnect(
                    self._on_page_selection_changed
                )
        self._selection_connected = False
        if self._watched_stack is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                self._watched_stack.indexChanged.disconnect(
                    self._on_layout_content_changed
                )
        self._watched_stack = None

    def _select_layout(self, name: str) -> None:
        # The same layout again (the tab re-shown, the list refreshed) keeps
        # its last save's status and report; only another layout starts over.
        switching = name != self._selected_name
        self._selected_name = name
        self._disconnect_current_layout()
        manager = self.project.layoutManager()
        layout = manager.layoutByName(name) if name else None
        self._current_layout = layout
        self.state.cartograph_layout = name or ""
        self.page_pane.show_layout(layout)
        self.rename_layout_button.setEnabled(layout is not None)
        for widget in self._layout_only:
            widget.setVisible(layout is not None)
        self._panel_filler.setVisible(layout is None)

        if layout is not None:
            try:
                layout.changed.connect(self._on_layout_content_changed)
                self._layout_changed_connected = True
            except Exception:
                # Not every QGIS build's `QgsLayout.changed` is connectable
                # from Python the same way; falling back to no live hook is
                # safer than failing to open the dialog over it. Autosave
                # still fires on every control this tab itself offers.
                self._layout_changed_connected = False

            # The designer's edits (a label's text, a moved map) never emit
            # `changed`; they are undo commands, so the stack's index is what
            # moves: for an edit, an added or removed item (also a command), an
            # undo and a redo.
            self._watched_stack = layout.undoStack().stack()
            self._watched_stack.indexChanged.connect(self._on_layout_content_changed)
            with contextlib.suppress(Exception):
                layout.selectedItemChanged.connect(self._on_page_selection_changed)
                self._selection_connected = True

        self._rebuild_frames_rows()
        self.membership.layout_changed()
        self.atlas_tab.show_layout(layout)
        self._update_file_default()
        self._load_page_fields()
        self._sync_based_on()
        self.update_window_title()
        if switching:
            self._forget_last_save()
        self._update_status_label()
        self._update_share_enabled()
        self._update_section_summaries()
        save_state(self.project, self.state)
        if switching:
            if self.state.output is DialogOutput.CARTOGRAPH:
                self.show_fidelity()
            # The file is a document that follows its layout: bring it up to
            # date now, not on the user's first edit.
            self.schedule_autosave()

    def _forget_last_save(self) -> None:
        """What the status line and Fidelity tab say about a save belongs to
        the layout it was for; another layout starts from its own file."""
        path = self._current_path()
        if path is not None and path.exists():
            stat = path.stat()
            self._last_saved_at, self._last_saved_bytes = stat.st_mtime, stat.st_size
        else:
            self._last_saved_at = self._last_saved_bytes = None
        self._last_error = ""
        self._last_summary = None
        self._frame_issue_counts = {}
        # A page asked for the layout left behind; this one did not ask, and
        # the footer's last message was about that layout's file.
        self._open_after_save = []
        self._clear_footer()

    def on_project_content_changed(self) -> None:
        """A layer was restyled or its edits saved in QGIS: the cartograph
        draws those layers, so it is saved again."""
        if self.state.output is DialogOutput.CARTOGRAPH and self._current_layout:
            if sip.isdeleted(self._current_layout):
                # Being deleted; `layoutRemoved` picks another layout.
                return
            # The preview draws from caches the layout keeps per item; a
            # restyled layer is only redrawn once they are refreshed.
            self._refresh_timer.start()
            self.schedule_autosave()

    def _refresh_current_layout(self) -> None:
        """One refresh for a burst of restyles (`_refresh_timer`)."""
        if self.state.output is DialogOutput.CARTOGRAPH and self._current_layout:
            if sip.isdeleted(self._current_layout):
                # Destroyed since the timer started; `layoutRemoved` picks
                # another layout once it is gone.
                return
            self._current_layout.refresh()

    def _on_layout_content_changed(self, *_args: object) -> None:
        if self._exporting or self.membership.editing:
            return
        if self._current_layout is not None and sip.isdeleted(self._current_layout):
            # Being deleted: its destructor clears the undo stack, which moves
            # the index. `layoutRemoved` picks another layout once it is gone.
            return
        if self._current_layout is not None:
            items = layout_map_items(self._current_layout)
            if {item.item_id for item in items} != set(self._frame_rows.keys()):
                self._rebuild_frames_rows()
                self.atlas_tab.show_layout(self._current_layout)
            elif {
                item.item_id: (item.name, item.crs_authid) for item in items
            } != self._frame_identities:
                self._refresh_frame_identities(items)
            self.membership.layout_changed()
        self.schedule_autosave()

    def _update_file_default(self) -> None:
        name = self.state.cartograph_layout
        if not name:
            self.file_edit.setText("")
            return
        stored = self.state.cartograph_file_for(name)
        suggested = default_cartograph_path(self.project.fileName(), name)
        if not stored:
            # The Cartographs folder, saved or not, so the cartograph starts
            # at once and the user never has to browse; a name another map
            # already has gets a number instead.
            stored = str(unused_path(suggested))
            self.state.cartograph_files[name] = stored
        self.file_edit.setPlaceholderText(str(suggested))
        self.file_edit.setText(stored)

    def _on_cartograph_setting_changed(self) -> None:
        save_state(self.project, self.state)
        self.schedule_autosave()

    # ---- Autosave ----------------------------------------------------------

    def schedule_autosave(self) -> None:
        """Debounce a rebuild ~600 ms after the last change. Restarting an
        already-running `QTimer` is what gives a burst of edits one write."""
        self._edits_made += 1
        self._autosave_timer.start()
        self._update_status_label()
        self._sync_report_display()

    def _run_autosave(self) -> None:
        """The debounce timer's slot. Never lets an exception escape: from a
        timer it would surface as a Python error dialog in QGIS, and the
        status line exists to say why a save did not happen."""
        try:
            self._start_build()
        except Exception as exc:
            self._job = None
            self._on_build_failed(str(exc), traceback.format_exc())

    def _start_build(self) -> None:
        if self._current_layout is None or sip.isdeleted(self._current_layout):
            # Destroyed since the debounce started; `layoutRemoved` picks
            # another layout once it is gone.
            self._cancel_waiting_pages()
            return
        path_text = self.file_edit.text().strip()
        if not path_text:
            self._cancel_waiting_pages()
            return
        if self._job is not None or self.dialog._job is not None:
            # A build, or a web-map job, is already reading the project - and
            # two threads walking the same layers is what takes QGIS down.
            # Try again after another debounce; nothing is lost.
            self._autosave_timer.start()
            return

        runtime = self._load_runtime()
        if runtime is None:
            self._cancel_waiting_pages()
            self._check_requested = False
            self._update_status_label()
            self._sync_report_display()
            return

        path = Path(path_text)
        layout = self._current_layout
        project = self.project
        choices = dict(self.state.frame_choices(self.state.cartograph_layout))
        frame_modes = {item_id: choice.mode for item_id, choice in choices.items()}
        frame_dpis = {item_id: choice.dpi for item_id, choice in choices.items()}

        from ...core.fidelity_report import FidelityReportBuilder

        report = FidelityReportBuilder()
        print_settings = self._print_settings()
        # The resolution the cartograph is saved at (the runtime's own
        # default when no print settings were saved): a static frame is
        # drawn at least that sharp, whatever its own choice.
        export_dpi = print_settings.dpi if print_settings else DEFAULT_EXPORT_DPI

        # The layout half here, on the GUI thread: it hides and renders the
        # layout's own items, and the layout may be open in its designer.
        # Only the layer-reading half goes to the worker.
        self._exporting = True
        try:
            prepared = cartograph_export.prepare_cartograph(
                layout,
                project,
                runtime,
                report,
                frame_modes=frame_modes,
                frame_dpis=frame_dpis,
                static_dpi=DEFAULT_STATIC_DPI,
                export_dpi=export_dpi,
            )
        except Exception as exc:
            self._on_build_failed(str(exc), traceback.format_exc())
            return
        finally:
            self._exporting = False
        prepared = dataclasses.replace(
            prepared, spec=self._apply_page_fields(prepared.spec)
        )
        if print_settings is not None:
            prepared = dataclasses.replace(
                prepared, spec=apply_to_spec(prepared.spec, print_settings)
            )
        self._last_page_label = page_label(prepared.spec)
        self._pending_spec = prepared.spec

        # The Layers tab's popup and label choices, copied: the worker must
        # not read state the GUI thread may be editing.
        layers = copy.deepcopy(dict(self.state.layers))
        self._built_layers_signature = self._layers_signature()
        # The live project's part of reading each frame's layers, still on
        # this thread; the worker finishes it from the snapshot.
        try:
            prepared = cartograph_export.prepare_live_reads(
                prepared, project, report, layer_settings=layers
            )
        except Exception as exc:
            self._on_build_failed(str(exc), traceback.format_exc())
            return

        # The dialog's own writer, so a licence key the user set reaches the
        # cartograph's live frames exactly as it reaches a web map.
        writer = self.dialog._writer()

        def work(_progress: object) -> cartograph_export.FinishedCartograph:
            return cartograph_export.finish_cartograph_full(
                prepared, None, report, writer=writer, layer_settings=layers
            )

        job = BackgroundJob(work, self.dialog)
        self._job = job
        built_for = self.state.cartograph_layout
        self._builds_started += 1
        build_number = self._builds_started
        edits = self._edits_made
        self._job_edits = edits
        job.succeeded.connect(
            lambda finished: self._on_build_succeeded(
                path, finished, report, built_for, build_number, edits
            )
        )
        job.failed.connect(
            lambda message, details: self._on_build_failed(message, details, built_for)
        )
        job.finished.connect(job.deleteLater)
        job.start()
        self._update_status_label()

    def _load_runtime(self) -> RuntimeBundle | None:
        """The runtime's bytes, read once per runtime version.

        `ensure_runtime` answers with a *provider* (asking and downloading
        first if it must); the export needs what it loads. Reading 9 MB from
        disk on every autosave would make each save visibly slower, so the
        bundle is kept until the provider would load a different version.
        """
        provider = ensure_runtime(self.dialog)
        if provider is None:
            self._last_error = tr(
                "The OnlyMap runtime is needed to save a cartograph. Nothing "
                "was written."
            )
            return None
        version = getattr(provider, "version", None)
        if self._runtime is not None and (
            version is None or version == self._runtime.version
        ):
            return self._runtime
        try:
            self._runtime = provider.load()
        except (RuntimeUnavailableError, RuntimeNotAcceptedError, OSError) as exc:
            self._last_error = tr(
                "The OnlyMap runtime could not be loaded, so nothing was "
                "written: {error}"
            ).format(error=str(exc))
            return None
        return self._runtime

    def _on_build_succeeded(
        self,
        path: Path,
        finished: cartograph_export.FinishedCartograph,
        report: FidelityReportBuilder,
        built_for: str | None = None,
        build_number: int = 0,
        edits: int | None = None,
    ) -> None:
        self._job = None
        if self._shut_down:
            return
        html = finished.html
        current = self._is_current_build(path, built_for)
        try:
            self._write_atomic(path, html)
        except OSError as exc:
            if not current:
                return
            # The pages waiting on this save would open the file it failed
            # to replace.
            self._cancel_waiting_pages()
            self._last_error = tr("Could not save the cartograph: {error}").format(
                error=str(exc)
            )
            self._check_requested = False
            self._update_status_label()
            self._sync_report_display()
            return
        if not current:
            # Saved for a layout (or file) no longer on screen: its own file is
            # up to date, but the status, Checks and a pending print belong to
            # what is selected now, whose own build follows.
            self._update_status_label()
            return
        self._last_error = ""
        self._check_requested = False
        self._edits_saved = max(
            self._edits_saved, self._edits_made if edits is None else edits
        )
        self._last_saved_at = time.time()
        self._last_saved_bytes = path.stat().st_size
        self._last_summary = summarise(report.items)
        self._count_frame_issues(report)
        self._show_atlas_outcome(report, finished.atlas_pages)
        if self._pending_spec is not None:
            self._collect_legend_entries(self._pending_spec, finished.projects)
            if self._colour_vision is not None:
                self._colour_vision.refresh()
        ready = [
            query
            for query, needed in self._open_after_save
            if self._edits_saved >= needed
        ]
        self._open_after_save = [
            entry for entry in self._open_after_save if self._edits_saved < entry[1]
        ]
        if ready:
            self._clear_footer()  # no longer "saving first"
        for query in ready:
            self._open_url(self._page_url(path, query))
        self._update_status_label()
        self._update_share_enabled()
        if self.state.output is DialogOutput.CARTOGRAPH:
            self.show_fidelity()

    def _on_build_failed(
        self, message: str, details: str, built_for: str | None = None
    ) -> None:
        self._job = None
        if self._shut_down:
            return
        from qgis.core import Qgis, QgsMessageLog

        QgsMessageLog.logMessage(
            details, "QGIS2WebMap", level=Qgis.MessageLevel.Warning
        )
        if (
            built_for is not None
            and self._current_name_of(built_for) != self.state.cartograph_layout
        ):
            self._update_status_label()
            return
        # The page waiting on this save would open without the latest edits.
        self._cancel_waiting_pages()
        self._last_error = tr("Could not build the cartograph: {message}").format(
            message=message
        )
        self._check_requested = False
        self._update_status_label()
        self._sync_report_display()

    def _is_current_build(self, path: Path, built_for: str | None) -> bool:
        """Whether a finished build is for the layout and file now selected."""
        if (
            built_for is not None
            and self._current_name_of(built_for) != self.state.cartograph_layout
        ):
            return False
        return self._current_path() == path

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        write_text_atomic(path, content)

    def _on_status_tick(self) -> None:
        """Once a second while Cartograph is on screen: refresh the "Saved 2 s
        ago" line, and save again when the Layers tab's popup or label choices
        changed - the same settings-polling the web map's live preview uses
        (`DialogState.snapshot`), for the one part of it a cartograph reads."""
        self._update_status_label()
        self.sync_layer_settings()

    def sync_layer_settings(self) -> bool:
        """Mark the cartograph dirty if the Layers tab's popup or label
        choices differ from the last ones seen. Called straight from the
        settings' change handlers and before any action decides the file is
        current, so it never waits for the status tick. Returns whether a
        change was found."""
        if self.state.output is not DialogOutput.CARTOGRAPH:
            # The Web map's own settings; the first tick after switching over
            # compares them against the last build.
            return False
        layers = self._layers_signature()
        if layers == self._built_layers_signature:
            return False
        self._built_layers_signature = layers
        self.schedule_autosave()
        return True

    def _layers_signature(self) -> str:
        import json

        return json.dumps(
            {k: v.to_dict() for k, v in self.state.layers.items()}, sort_keys=True
        )

    def _save_phase(self) -> str:
        """Where the cartograph file stands against the edits made: "saving"
        while a build runs, "pending" while the debounce waits, else "". The
        layout preview's own rendering status is a separate line."""
        if self._job is not None:
            return "saving"
        if self._autosave_timer.isActive():
            return "pending"
        return ""

    def _update_status_label(self) -> None:
        if not hasattr(self, "cartograph_status_label"):
            return
        self.cartograph_status_label.setText(
            status_text(
                saved_at=self._last_saved_at,
                saved_bytes=self._last_saved_bytes,
                error=self._last_error,
                now=time.time(),
                translate=tr,
                has_path=bool(self.file_edit.text().strip())
                if hasattr(self, "file_edit")
                else True,
                phase=self._save_phase(),
            )
        )

    def _count_frame_issues(self, report: FidelityReportBuilder) -> None:
        """Per map item, the fidelity rows that are not "Kept"."""
        from ...core.export_ir import FidelityStatus

        counts: dict[str, int] = {}
        if self._current_layout is not None:
            for item in layout_map_items(self._current_layout):
                counts[item.item_id] = sum(
                    1
                    for row in report.items
                    if row.status is not FidelityStatus.PRESERVED
                    and row.item_id == item.item_id
                )
        self._frame_issue_counts = counts
        for item_id, count in counts.items():
            if item_id in self._frame_rows:
                self.frames_list.set_issue_count(item_id, count)

    @property
    def has_report(self) -> bool:
        return self._last_summary is not None

    def show_fidelity(self) -> None:
        """Put the last saved cartograph's report on the Fidelity tab and
        strip. Its own report, never the web map's: the dialog calls this
        whenever Cartograph is the output on screen."""
        if not hasattr(self.dialog, "fidelity_strip"):
            # A project saved on Cartograph opens on it before the dialog has
            # built its strip; the dialog draws the report once it has.
            return
        panel = self.dialog.fidelity_panel
        strip = self.dialog.fidelity_strip
        self._update_checks_summary()
        state = self._report_state()
        self._shown_report_state = state
        summary = self._last_summary
        if state is ReportState.FAILED:
            panel.show_error(
                self._last_error,
                why=tr(
                    "{message}\n\nThe full error is in the QGIS message log. "
                    "Nothing about the cartograph is known until a check "
                    "finishes, so none of it is shown as a verdict."
                ).format(message=self._last_error),
            )
        elif state is ReportState.CHECKING:
            panel.show_checking()
        elif summary is None:
            panel.show_not_checked()
        else:
            panel.show_summary(summary)
            panel.set_out_of_date(state is ReportState.OUT_OF_DATE)
        strip.show_message(
            strip_message(
                state,
                summary,
                self._last_error,
                tr=tr,
                plural=plural,
            )
        )

    def _report_state(self) -> ReportState:
        """Where the cartograph's report stands: failed, being checked, not
        there yet, older than the edits made, or current."""
        if self._last_error:
            return ReportState.FAILED
        building = self._job is not None or self._autosave_timer.isActive()
        if self._check_requested and building:
            return ReportState.CHECKING
        if self._last_summary is None:
            return ReportState.NOT_CHECKED
        if self._edits_saved < self._edits_made:
            return ReportState.OUT_OF_DATE
        return ReportState.CURRENT

    def _sync_report_display(self) -> None:
        """Redraw the Checks tab and strip when the report's state changed,
        not on every edit: drawing a report rebuilds its list."""
        if self.state.output is not DialogOutput.CARTOGRAPH:
            return
        if self._report_state() is not self._shown_report_state:
            self.show_fidelity()

    def check_now(self) -> None:
        """The Checks tab's and strip's "Check now" / "Try again" / "Check
        again": rebuild the selected layout now and show that result. A
        cartograph is checked by saving it, so this is a save that does not
        wait for the debounce."""
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout) or not self.file_edit.text().strip():
            self.dialog.status_label.setText(
                tr("Choose a print layout first - there is nothing to check yet.")
            )
            return
        self._last_error = ""
        self._check_requested = True
        self.schedule_autosave()
        self._autosave_timer.stop()
        self._run_autosave()
        self._sync_report_display()

    @property
    def report_needs_check(self) -> bool:
        """Whether the Checks tab offers a check rather than a report."""
        return self._report_state() is not ReportState.CURRENT

    # ---- Corner: Accessibility, Preview, Share ----------------------------

    def build_corner(self) -> QWidget:
        """The cartograph's actions, styled as the web map's are.

        Preview and Share in the same outlined buttons with the same line
        icons. There is no filled button: the cartograph saves itself, so it
        has no Export, and "Open in browser" did what Preview does - it lives
        in Share's menu now, still on Ctrl+O. Accessibility is a tool rather
        than an act, so it is an icon on its own ahead of them.
        """
        if self._corner is not None:
            return self._corner

        corner = QWidget(self.dialog)
        layout = QHBoxLayout(corner)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(6)

        def button(text: str, icon: str) -> QToolButton:
            made = QToolButton(corner)
            made.setText(text)
            made.setProperty("role", "corner")
            made.setProperty("accent", False)
            made.setProperty("hasMenu", False)
            made.setProperty("lineIcon", icon)
            made.setIconSize(QSize(15, 15))
            made.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            layout.addWidget(made)
            return made

        self.accessibility_button = button("", "contrast")
        self.accessibility_button.setProperty("iconOnly", True)
        self.accessibility_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.accessibility_button.setAccessibleName(tr("Accessibility"))
        self.accessibility_button.setCheckable(True)
        self.accessibility_button.setToolTip(
            tr("Colour vision: simulate it and check the legend's colours")
        )
        self.accessibility_button.clicked.connect(self.on_accessibility)

        # The page beside the panel is QGIS's drawing; this opens the
        # cartograph itself, in the browser, as the web map's Preview does.
        self.cartograph_preview_button = button(tr("Preview in browser"), "preview")
        self.cartograph_preview_button.setToolTip(
            tr("Open the cartograph in your browser.")
        )
        self.cartograph_preview_button.clicked.connect(self.preview)

        self.cartograph_share_button = button(tr("Share"), "share")
        self.cartograph_share_button.setProperty("hasMenu", True)
        self.cartograph_share_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.cartograph_share_button.setMenu(self.build_share_menu())

        self._corner = corner
        self._update_share_enabled()
        return corner

    # ---- Accessibility: colour vision --------------------------------------

    def on_accessibility(self) -> None:
        """The corner's Accessibility button: one non-modal Colour vision
        dialog, raised again rather than duplicated."""
        from .accessibility_dialog import AccessibilityDialog, simulating_tooltip

        if self._colour_vision is None:
            dialog = AccessibilityDialog(
                lambda: self._legend_entries,
                canvas=self._map_canvas(),
                open_preview=self._open_cvd_preview,
                # The hand-off is not ready for cartographs: greyed out here
                # too, as on the Share menu.
                ask_nika=None,
                parent=self.dialog,
            )

            def on_mode(condition: str) -> None:
                self.accessibility_button.setChecked(bool(condition))
                self.accessibility_button.setToolTip(
                    simulating_tooltip(cast("Condition", condition))
                    if condition
                    else tr("Colour vision: simulate it and check the legend's colours")
                )

            dialog.mode_changed.connect(on_mode)
            self._colour_vision = dialog
        # A checkable button toggles itself before this runs; the dialog's own
        # mode is what it should show.
        self.accessibility_button.setChecked(
            bool(self._colour_vision.current_condition())
        )
        self._colour_vision.refresh()
        self._colour_vision.show()
        self._colour_vision.raise_()

    def _open_cvd_preview(self, condition: str | None) -> None:
        path = self._current_path()
        if path is None or not path.exists():
            return
        query = f"cvd={condition}" if condition else ""
        self._open_saved(path, query)

    def release_canvas_effects(self) -> None:
        """Undo a colour-vision preview this plugin put on the QGIS canvas.
        Called when the plugin unloads; closing a dialog leaves it, per #4."""
        if self._colour_vision is not None:
            self._colour_vision.reset_canvas()

    def _collect_legend_entries(
        self, spec: CartographSpec, projects: Sequence[ExportProject] = ()
    ) -> None:
        """The legend classes the colour-vision check compares, from a save.

        Static frames' legends are literal rows already in the spec; a live
        frame's come from its layers' renderers (added by the export).
        """
        from ...core.cartograph_ir import LegendSpec
        from ...core.cvd import LegendEntry

        entries: list[LegendEntry] = []
        for item in spec.items:
            if isinstance(item, LegendSpec):
                for row in item.rows:
                    if row.color:
                        entries.append(
                            LegendEntry(
                                label=row.label or row.layer or "",
                                color=row.color,
                                layer=row.layer,
                                layer_name=row.layer,
                            )
                        )
        from ...core.cvd import legend_entries_for_projects

        entries.extend(legend_entries_for_projects(projects))
        self._legend_entries = tuple(entries)

    # ---- Preview -----------------------------------------------------------

    def _current_path(self) -> Path | None:
        text = self.file_edit.text().strip() if hasattr(self, "file_edit") else ""
        return Path(text) if text else None

    def _file_exists(self) -> bool:
        path = self._current_path()
        return path is not None and path.exists()

    def preview(self) -> None:
        self.on_open_in_browser()

    def on_open_in_browser(self) -> None:
        path = self._current_path()
        if path is None or not path.exists():
            self.dialog.status_label.setText(
                tr(
                    "Nothing to preview yet - the cartograph has not been "
                    "saved. Choose a print layout in the panel."
                )
            )
            return
        self._open_saved(path)

    def _clear_footer(self) -> None:
        """Empty the dialog's footer message. Not there yet while the dialog
        is still being built - a project saved on Cartograph selects its
        layout from inside `MainDialog.__init__`."""
        footer = getattr(self.dialog, "status_label", None)
        if footer is not None:
            footer.setText("")

    def _cancel_waiting_pages(self) -> None:
        """Drop the pages waiting on a save that will not come, and the
        footer's promise that they open when it does."""
        if self._open_after_save:
            self._open_after_save = []
            self._clear_footer()

    def _open_saved(self, path: Path, query: str = "") -> None:
        """Open the cartograph in the browser as it is with every edit made
        so far: now if it is saved, otherwise once the save carrying the
        latest edits lands. A failed save or another layout cancels it."""
        # Settings edited since the last look (the status tick may not have
        # run yet) count as edits made.
        self.sync_layer_settings()
        needed = self._edits_made
        if self._edits_saved >= needed and self._job is None:
            self._clear_footer()
            self._open_url(self._page_url(path, query))
            return
        running_carries_it = self._job is not None and self._job_edits >= needed
        if not running_carries_it and not self._autosave_timer.isActive():
            # Nothing on the way carries the latest edits (the last save
            # failed, or the running build predates them): save again and
            # open that. Failing again says why, and opens nothing.
            self.schedule_autosave()
            needed = self._edits_made
        self._open_after_save.append((query, needed))
        self.dialog.status_label.setText(
            tr("Saving the latest changes first - it opens when saved.")
        )

    def _page_url(self, path: Path, query: str = "") -> QUrl:
        """`path` served locally, or the file itself when the server cannot
        start - with `query` either way, since it is what tells the page to
        print, export or page through the atlas."""
        url = self._served_url(path)
        if url is None:
            url = QUrl.fromLocalFile(str(path))
            # Some systems open a file link without its query, which would
            # leave a print or export silently undone: say what went wrong.
            self.dialog.status_label.setText(
                tr(
                    "Could not start the local preview server ({error}), so the "
                    "cartograph opened from its file. If it does not print or "
                    "download, check that local connections to {host} are "
                    "allowed, then try again."
                ).format(error=self._preview_server_error, host=BIND_HOST)
            )
        if query:
            url.setQuery(query)
        return url

    def _served_url(self, path: Path) -> QUrl | None:
        """Serve `path`'s folder locally, same as the web map's preview path.

        `QWebEngineView` cannot create a WebGL context inside QGIS (see
        `ui/preview.py`), so this opens the system browser exactly as the web
        map's Preview does - a stable local URL rather than `file://`, which
        some browsers refuse to run WebGL content from at all.
        """
        try:
            root = path.parent.resolve()
            if (
                self._preview_server is None
                or self._preview_server.root != root
                or self._preview_server.only != path.name
            ):
                if self._preview_server is not None:
                    self._preview_server.stop()
                    self._preview_server = None
                # Only this one file: the cartograph is self-contained, and its
                # folder may be somewhere like Documents.
                server = PreviewServer(root, only=path.name)
                server.start()
                # Kept only once running: a server that failed to bind would
                # otherwise be reused, unstarted, by the next action.
                self._preview_server = server
            url = QUrl()
            url.setScheme("http")
            url.setHost(BIND_HOST)
            url.setPort(self._preview_server.port)
            # Set as a decoded path, so a `#` or `?` in the name is encoded
            # rather than read as a fragment or query.
            url.setPath(f"/{path.name}")
            return url
        except OSError as exc:
            self._preview_server_error = str(exc)
            return None

    @staticmethod
    def _open_url(url: QUrl) -> None:
        QDesktopServices.openUrl(url)

    # ---- Share menu --------------------------------------------------------

    def build_share_menu(self) -> QMenu:
        menu = QMenu(self.dialog)
        menu.setToolTipsVisible(True)
        self.cartograph_share_actions: dict[str, QAction] = {}

        # Each row's own wording, put back once a greyed-out row is
        # available again; while it is not, its tooltip says what it needs.
        self._cartograph_share_tips: dict[str, str] = {}

        def add(key: str, label: str, tip: str, slot, shortcut: str = "") -> QAction:
            action = QAction(label, self.dialog)
            action.setToolTip(tip)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            self.cartograph_share_actions[key] = action
            self._cartograph_share_tips[key] = tip
            return action

        # Outputs first, then the file's own actions, then NIKA.
        menu.addSection(tr("Export"))
        add(
            "export_pdf",
            tr("Export PDF..."),
            tr("Opens the cartograph and prints it at true page size"),
            self.on_export_pdf,
            "Ctrl+P",
        )
        add(
            "export_png",
            tr("Export PNG..."),
            tr("Image of the first page at the saved resolution"),
            self.on_export_png,
        )
        add(
            "export_qpt",
            tr("Export QGIS layout template (.qpt)..."),
            tr("Round trip back into QGIS"),
            self.on_export_qpt,
        )
        add(
            "print_settings",
            tr("Print settings..."),
            tr("Page size, bleed, crop marks and colour profile for Export PDF"),
            self.on_print_settings,
        )

        menu.addSection(tr("File"))
        add(
            "open_browser",
            tr("Open in browser"),
            tr("Opens the cartograph in your browser"),
            self.on_open_in_browser,
            "Ctrl+O",
        )

        add(
            "copy_path",
            tr("Copy file path"),
            tr("Copies the cartograph's path"),
            self.on_copy_path,
        )
        add(
            "reveal",
            _reveal_label(),
            tr("Shows the cartograph in your file manager"),
            self.on_reveal,
        )

        # NIKA after everything that works on the file, each named for what
        # it does today, as in the web map's menu: the hand-offs prepare a
        # prompt for the user to take to NIKA, shown greyed out until they are
        # ready for cartographs. Hosting is for web maps only, so no hosting
        # row here. The hand-off dialog is issue #3 S4.
        menu.addSection(tr("NIKA hand-off"))
        add(
            "enhance",
            tr("Prepare enhancement prompt..."),
            tr(
                "Opens a prompt for improving this cartograph, to copy into NIKA "
                "or another AI assistant. Nothing is sent from here."
            ),
            lambda: self.on_handoff("enhance"),
        )
        add(
            "critique",
            tr("Prepare critique prompt..."),
            tr(
                "Opens a prompt asking for a critique of this cartograph, to copy "
                "into NIKA or another AI assistant. Nothing is sent from here."
            ),
            lambda: self.on_handoff("critique"),
        )

        return menu

    def _update_share_enabled(self) -> None:
        if not hasattr(self, "cartograph_share_actions"):
            return
        # Export PDF goes where the Print settings send it, and says so.
        settings = self._print_settings()
        self._cartograph_share_tips["export_pdf"] = (
            tr(
                "Makes a CMYK or ICC PDF with QGIS's own exporter from the "
                "print layout, and asks where to save it"
            )
            if settings is not None and settings.uses_qgis_exporter
            else tr("Opens the cartograph and prints it at true page size")
        )
        exists = self._file_exists()
        for key in (
            "copy_path",
            "reveal",
            "open_browser",
            "export_pdf",
            "export_png",
        ):
            self._set_share_row(
                key,
                exists,
                tr("Available once the cartograph has saved - choose a print layout"),
            )
        for key in ("export_qpt", "print_settings"):
            self._set_share_row(
                key, self._current_layout is not None, tr("Choose a print layout first")
            )
        for key in ("enhance", "critique"):
            self._set_share_row(key, False, tr("Not available for cartographs yet"))

    def _set_share_row(self, key: str, available: bool, needs: str) -> None:
        """Grey a row out with what it needs, or give it back its own words."""
        action = self.cartograph_share_actions.get(key)
        if action is None:
            return
        action.setEnabled(available)
        action.setToolTip(self._cartograph_share_tips[key] if available else needs)

    def on_copy_path(self) -> None:
        path = self._current_path()
        if path is None:
            return
        QGuiApplication.clipboard().setText(str(path))
        self.dialog.status_label.setText(tr("Copied {path}").format(path=path))

    def on_reveal(self) -> None:
        path = self._current_path()
        if path is None:
            return
        # The folder may not exist until the first save makes it: open the
        # nearest one that does, and say which.
        folder = path.parent
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.dialog.status_label.setText(tr("Opened {path}").format(path=folder))

    def on_handoff(self, task: str, cvd_findings: str | None = None) -> None:
        """Share > Enhance / Critique: the hand-off dialog on the saved file.

        The cartograph is always saved (autosave), so unlike the web map there
        is nothing to build first - only a file that must exist.
        """
        from ...core.handoff_prompt import PageInfo
        from ...writers.onlymap_writer import PLUGIN_VERSION
        from ..handoff_dialog import HandoffContext, HandoffDialog

        path = self._current_path()
        if path is None or not path.exists():
            return
        context = HandoffContext(
            output="cartograph",
            file_path=path,
            source_name=self.state.cartograph_layout or path.stem,
            plugin_version=PLUGIN_VERSION,
            task="enhance" if task == "enhance" else "critique",
            cvd_findings=cvd_findings,
            page=PageInfo(size=self._last_page_label, bleed=None, atlas_pages=None)
            if self._last_page_label
            else None,
        )
        HandoffDialog(context, self.dialog).exec()

    def on_export_pdf(self) -> None:
        """Share > Export PDF, by the route the Print settings choose.

        RGB (the default) prints from the browser, which honours the page's
        true size; the file already carries the print attributes, because
        every save applies the settings. CMYK and ICC go through QGIS's own
        PDF exporter on the source layout - the one thing a browser cannot do.
        """
        path = self._current_path()
        if path is None or not path.exists():
            return
        settings = self._print_settings()
        if settings is not None and settings.uses_qgis_exporter:
            self._export_pdf_through_qgis(settings, path)
            return
        self._open_saved(path, "print=1")

    # ---- Print settings ----------------------------------------------------

    def _print_settings(self) -> PrintSettings | None:
        text = self.state.cartograph_print.get(self.state.cartograph_layout)
        if not text:
            return None
        try:
            return PrintSettings.from_json(text)
        except ValueError:
            return None

    def _layout_page_mm(self) -> tuple[float, float]:
        from qgis.core import QgsLayoutMeasurementConverter, QgsUnitTypes

        layout = self._current_layout
        if layout is None or layout.pageCollection().pageCount() == 0:
            return (297.0, 210.0)
        converter = QgsLayoutMeasurementConverter()
        size = converter.convert(
            layout.pageCollection().page(0).sizeWithUnits(),
            QgsUnitTypes.LayoutMillimeters,
        )
        return (size.width(), size.height())

    def _has_live_frame(self) -> bool:
        if self._current_layout is None:
            return False
        choices = self.state.frame_choices(self.state.cartograph_layout)
        for item in layout_map_items(self._current_layout):
            choice = choices.get(item.item_id)
            mode = choice.mode if choice else item.default_mode
            if mode == "live":
                return True
        return False

    def on_print_settings(self) -> None:
        """Share > Print settings: saved per layout, applied on every save."""
        from .print_settings_dialog import ACTION_EXPORT, ask_print_settings

        if self._current_layout is None:
            return
        answer = ask_print_settings(
            self._print_settings() or PrintSettings(),
            layout_page_mm=self._layout_page_mm(),
            has_live_frame=self._has_live_frame(),
            parent=self.dialog,
        )
        if answer is None:
            return
        action, settings = answer
        self.state.cartograph_print[self.state.cartograph_layout] = settings.to_json()
        save_state(self.project, self.state)
        self._update_share_enabled()
        if action == ACTION_EXPORT:
            if settings.uses_qgis_exporter:
                path = self._current_path()
                if path is not None:
                    self._export_pdf_through_qgis(settings, path)
                self.schedule_autosave()
            else:
                # The browser must print the file with these settings in it,
                # so print once the save that applies them has landed.
                self.schedule_autosave()
                path = self._current_path()
                if path is not None:
                    self._open_saved(path, "print=1")
        else:
            self.schedule_autosave()

    def _export_pdf_through_qgis(self, settings: PrintSettings, path: Path) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        from ...exporters.print_pdf import (
            PrintPdfError,
            attribution_warning,
            export_print_pdf,
            theme_warning,
        )

        if self._current_layout is None:
            return
        if self._has_live_frame():
            QMessageBox.information(
                self.dialog,
                tr("Export PDF"),
                tr(
                    "A CMYK or ICC PDF is made by QGIS from the print layout, "
                    "which only works when every frame is static. Set the live "
                    "frames to Static in Map frames, or choose RGB in Print "
                    "settings."
                ),
            )
            return
        page = self.state.page_choice(self.state.cartograph_layout)
        accepted: list[str] = []
        themed = page.theme not in ("", "default")
        if page.attribution or themed:
            from .print_settings_dialog import (
                NATIVE_ADD_ATTRIBUTION,
                NATIVE_BROWSER,
                NATIVE_CONTINUE,
                ask_native_pdf_differences,
            )

            answer = ask_native_pdf_differences(
                attribution=page.attribution, themed=themed, parent=self.dialog
            )
            if answer == NATIVE_BROWSER:
                self._open_saved(path, "print=1")
                return
            if answer == NATIVE_ADD_ATTRIBUTION:
                self._add_attribution_label(page.attribution)
            elif answer != NATIVE_CONTINUE:
                return
            # Told already (or, for the attribution, now in the layout): the
            # post-export line leaves these two out.
            accepted = [attribution_warning(), theme_warning()]
        chosen, _ = QFileDialog.getSaveFileName(
            self.dialog,
            tr("Export PDF"),
            str(path.with_suffix(".pdf")),
            tr("PDF (*.pdf)"),
        )
        if not chosen:
            return
        try:
            result = export_print_pdf(
                self._current_layout,
                settings,
                Path(chosen),
                page,
            )
        except PrintPdfError as exc:
            QMessageBox.warning(self.dialog, tr("Export PDF"), str(exc))
            return
        message = tr("Saved {path}").format(path=result.path)
        warnings = [w for w in result.warnings if w not in accepted]
        if warnings:
            message += " - " + " ".join(warnings)
        self.dialog.status_label.setText(message)

    def _add_attribution_label(self, text: str) -> None:
        """A small label with `text` along the foot of page 1, as one step on
        the layout's undo stack."""
        from qgis.core import (
            QgsLayoutItemLabel,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsUnitTypes,
        )

        layout = self._current_layout
        if layout is None:
            return
        width, height = self._layout_page_mm()
        mm = QgsUnitTypes.LayoutMillimeters
        label = QgsLayoutItemLabel(layout)
        label.setText(text)
        label.attemptResize(QgsLayoutSize(width - 10, 5, mm))
        label.attemptMove(QgsLayoutPoint(5, height - 8, mm))
        stack = layout.undoStack()
        stack.beginMacro(tr("Add attribution"))
        layout.addLayoutItem(label)
        stack.endMacro()
        self.schedule_autosave()

    def on_export_png(self) -> None:
        path = self._current_path()
        if path is None or not path.exists():
            return
        # No `dpi=`: the saved cartograph carries its resolution, which the
        # runtime's PNG export and the atlas routes all read.
        self._open_saved(path, "export=png")

    def on_export_qpt(self) -> None:
        if self._current_layout is None:
            return
        start = str(
            Path(self.project.fileName() or str(Path.home())).with_name(
                f"{_file_name_part(self.state.cartograph_layout or '') or 'layout'}.qpt"
            )
        )
        chosen, _ = QFileDialog.getSaveFileName(
            self.dialog,
            tr("Save layout template as"),
            start,
            tr("QGIS layout template (*.qpt)"),
        )
        if not chosen:
            return
        from qgis.core import QgsReadWriteContext
        from qgis.PyQt.QtWidgets import QMessageBox

        if not self._current_layout.saveAsTemplate(chosen, QgsReadWriteContext()):
            QMessageBox.warning(
                self.dialog,
                tr("Save layout template as"),
                tr("Could not save the layout template to {path}.").format(path=chosen),
            )
            return
        self.dialog.status_label.setText(tr("Saved {path}").format(path=chosen))


def _template_placeholders() -> dict[str, str]:
    """The words a template's panels show until the user writes their own,
    in the user's language (`core/layout_templates.py` keeps the keys)."""
    return {
        "notes": tr("Describe the map here: what it shows, where the data comes from."),
        "figure": tr("Key figure"),
        "figure_note": tr("What the number means"),
        "author": tr("Author"),
    }
