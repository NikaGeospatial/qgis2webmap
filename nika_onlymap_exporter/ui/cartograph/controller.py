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
import functools
import html
import re
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from qgis.core import QgsLayoutItemMap
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QSize, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication, QPalette
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
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import cartograph_destination, cartograph_export
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
from ...core.layout_identity import existing_key, layout_key
from ...core.page_settings import (
    adopt_theme,
    apply_theme,
    apply_theme_quietly,
    attribution_state,
    current_theme,
    has_theme_record,
    set_attribution,
    set_title,
    set_title_quietly,
    title_state,
)
from ...core.portable_names import avoid_windows_reserved
from ...core.print_settings import PrintSettings, apply_to_spec
from ...core.save_folders import cartographs_folder, unused_path
from ...core.settings import (
    DEFAULT_STATIC_DPI,
    DialogOutput,
    FrameChoice,
    save_state,
)
from ...core.template_registry import get_template
from ...exporters.frame_raster import encode_png
from ...packaging.runtime_manager import (
    RuntimeBundle,
    RuntimeNotAcceptedError,
    RuntimeUnavailableError,
)
from ...qt_compat import QUndoCommand
from ..background_job import BackgroundJob, retire
from ..fitted_label import FittedLabel
from ..i18n import N_, plural, tr
from ..live_server import BIND_HOST, NO_FILES, PAGE_PATH, PreviewServer
from ..reveal_file import RevealOutcome, reveal_file
from ..runtime_setup import ensure_runtime
from . import layout_thumbnails
from .frames import FrameItem, layout_map_items
from .frames_list import FrameRow, FramesList
from .layer_membership import LayerMembership
from .layout_header import (
    LayoutChooserDialog,
    LayoutEntry,
    LayoutHeader,
    layout_metadata,
)
from .layout_page import (
    ElidedLabel,
    PagePane,
    RenameLayoutDialog,
    SaveLocation,
)
from .panel_section import PanelSection
from .stacked_scroll import StackedScrolling
from .status import page_label, status_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    # QtWidgets on Qt 5, QtGui on Qt 6.
    from qgis.core import QgsPrintLayout, QgsProject
    from qgis.PyQt.QtGui import QUndoStack

    from ...core.canvas_cartograph import LayerRevisions
    from ...core.cvd import Condition, LegendEntry
    from ...core.export_ir import ExportProject
    from ...core.fidelity_report import FidelityReportBuilder
    from ...core.layout_builder import BuiltLayout
    from ...core.layout_reading import FrameMode
    from ..main_dialog import MainDialog
    from .accessibility_dialog import AccessibilityDialog

# Debounce per the spec: a burst of edits - dragging a DPI slider would be one
# if this tab had one - collapses into one rebuild, the same ~600 ms the plan
# settles on for the document model.
AUTOSAVE_DEBOUNCE_MS = 600
# How long an autosave may keep waiting for a drag to end or the preview map to
# finish drawing before it runs anyway.
AUTOSAVE_DEFER_LIMIT_S = 4.0
#: How long a restyle burst must rest before the preview is redrawn.
REFRESH_DEBOUNCE_MS = 120

# A layout settled on show is photographed for the chooser after this long,
# and again this many times (as often) while a map on it is still drawing.
THUMBNAIL_DELAY_MS = 1500
THUMBNAIL_TRIES = 6

STATUS_TICK_MS = 1000
# How stale "Saved 3 min ago" may get. Every change to what the line says
# (a save, an edit, an error) refreshes it at once; this only moves the age on.
STATUS_AGE_REFRESH_MS = 30_000
# How long closing waits for a running build, as `main_dialog`'s own job.
SHUTDOWN_WAIT_MS = 5000

# How often a close waiting for the last save looks at how that save is going.
CLOSE_SAVE_POLL_MS = 50
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

#: The rule above the panel tabs: its 1 px line and 2 px of the 8 px gap below
#: it (the column's 6 px spacing is the rest).
PANEL_RULE_HEIGHT = 3

_THEME_LABELS = (
    ("default", N_("Default")),
    ("dark", N_("Dark")),
    ("sepia", N_("Sepia")),
    ("blueprint", N_("Blueprint")),
    ("minimal", N_("Minimal")),
)


#: Links in the status line after a PDF is saved.
_PDF_OPEN_LINK = "open-pdf"
_PDF_SHOW_LINK = "show-pdf"
_COPY_PATH_LINK = "copy-path"


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


def _guarded(function: Callable[..., None], *args: object) -> None:
    """Run an undo or redo body; an exception inside a Qt virtual would abort
    QGIS, so it is reported instead."""
    try:
        function(*args)
    except Exception:
        traceback.print_exc()


class _FrameModeCommand(QUndoCommand):
    """Switching maps to Interactive as one step on the layout's undo stack:
    redo sets each to Interactive, undo gives each back its earlier choice."""

    def __init__(
        self, controller: CartographController, previous: dict[str, str], text: str
    ) -> None:
        super().__init__(text)
        self._controller = controller
        self._previous = previous

    def redo(self) -> None:
        _guarded(self._apply, {item_id: "live" for item_id in self._previous})

    def undo(self) -> None:
        _guarded(self._apply, self._previous)

    def _apply(self, modes: dict[str, str]) -> None:
        for item_id, mode in modes.items():
            self._controller._set_frame_mode(item_id, mode)


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


def _forget_build_commands(layout: QgsPrintLayout) -> None:
    """A layout just built from a template or an import starts with an empty
    undo stack: the commands that created its items are not the user's edits,
    and Undo must not take the design apart."""
    layout.undoStack().stack().clear()


@dataclasses.dataclass
class _DocumentRevisions:
    """One layout's cartograph file against its edits: `made` counts the saves
    asked for, `saved` how many of them the file on disk carries."""

    made: int = 0
    saved: int = 0


class _OtherLayoutWatch:
    """Reports the edits made to one layout the plugin is not showing - in its
    designer, through the layout and its undo stack - to the controller. A
    bound method of this object is what the layout's signals are connected to
    (never a closure over the controller: the layout outlives the dialog)."""

    def __init__(self, controller: CartographController, layout: QgsPrintLayout):
        self.controller = controller
        self.layout = layout
        self.stack = layout.undoStack().stack()
        layout.changed.connect(self.edited)
        self.stack.indexChanged.connect(self.edited)

    def edited(self, *_args: object) -> None:
        if not sip.isdeleted(self.layout):
            self.controller._on_other_layout_edited(self.layout)

    def stop(self) -> None:
        with contextlib.suppress(TypeError, RuntimeError):
            if not sip.isdeleted(self.layout):
                self.layout.changed.disconnect(self.edited)
        with contextlib.suppress(TypeError, RuntimeError):
            if not sip.isdeleted(self.stack):
                self.stack.indexChanged.disconnect(self.edited)


class _StaticRenders:
    """The build phase in which QGIS draws the static frames' pictures off the
    GUI thread. It stands in `CartographController._job` so that everything
    asking whether a build is in flight - Preview and Share waiting for the
    latest edits, the status line, the web-map job - sees this phase too."""

    def __init__(self, pending: list[cartograph_export.PendingStaticFrame]) -> None:
        self.pending = pending

    @property
    def all_done(self) -> bool:
        return all(frame.render.done for frame in self.pending)

    def cancel(self) -> None:
        for frame in self.pending:
            frame.render.cancel()


@dataclasses.dataclass
class _BuildLanding:
    """What a build's worker was started with, kept for when it reports back."""

    job: BackgroundJob
    path: Path
    report: FidelityReportBuilder
    built_for: str | None
    build_number: int
    edits: int
    pending: list[cartograph_export.PendingStaticFrame]
    pictures: dict[str, bytes]


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

        # The build in flight: first the static frames being drawn
        # (`_StaticRenders`), then the worker reading the project.
        self._job: BackgroundJob | _StaticRenders | None = None
        # When the autosave was first put off for the preview still drawing
        # or the user still dragging (None when it is not being put off).
        self._deferred_since: float | None = None
        self._last_saved_at: float | None = None
        self._last_saved_bytes: int | None = None
        self._last_error = ""
        # Files the user picked in the Save dialog, which asked before
        # replacing: the one kind of existing file an autosave may overwrite
        # without it being this cartograph's own output.
        self._approved_destinations: set[str] = set()
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
        # Counted per layout (see `_revisions`), so switching away from a layout
        # with a save pending neither forgets it nor lets another layout's
        # save stand in for it.
        self._revisions: dict[str, _DocumentRevisions] = {}
        # Why a layout other than the one on screen could not be saved.
        self._document_errors: dict[str, str] = {}
        # The layout the running build is for.
        self._job_document: str | None = None
        # The user asked for a check and no build has answered yet; and the
        # report state the Checks tab and strip were last drawn for.
        self._check_requested = False
        self._shown_report_state: ReportState | None = None
        self._loading_page = False
        self._colour_vision: AccessibilityDialog | None = None
        # True while a PDF is being written (the export is synchronous, but
        # events run around it); a second request waits for the first.
        self._pdf_running = False
        self._last_pdf_path: Path | None = None
        self._last_reveal_path: Path | None = None
        self._status_links_connected = False
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
        self._static_carry_on: Callable[[], None] | None = None
        self._build_landing: _BuildLanding | None = None
        # Set while the window waits to close until the file is current: the
        # two ways that wait can end (saved, failed). See `start_close_save`.
        self._close_waiter: tuple[Callable[[], None], Callable[[str], None]] | None = (
            None
        )
        self._preview_server: PreviewServer | None = None
        self._preview_server_error = ""

        # Rendered static frames are kept between saves; a closed project's
        # are no use to the next one and only hold memory.
        self.project.cleared.connect(cartograph_export.clear_static_frame_cache)

        self._autosave_timer = QTimer(dialog)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._run_autosave)
        # Saves the layouts that were switched away from with edits pending.
        self._other_saves_timer = QTimer(dialog)
        self._other_saves_timer.setSingleShot(True)
        self._other_saves_timer.setInterval(0)
        self._other_saves_timer.timeout.connect(self._run_other_saves)
        # Layouts the plugin is not showing but that already have a file are
        # watched for edits made in the designer; a burst of them waits out
        # the same debounce as the shown layout's before its save.
        self._watches: list[_OtherLayoutWatch] = []
        self._watched_edits_timer = QTimer(dialog)
        self._watched_edits_timer.setSingleShot(True)
        self._watched_edits_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._watched_edits_timer.timeout.connect(self._kick_other_saves)
        self._close_save_timer = QTimer(dialog)
        self._close_save_timer.setInterval(CLOSE_SAVE_POLL_MS)
        self._close_save_timer.timeout.connect(self._advance_close_save)
        # A restyle in QGIS arrives as a stream of events; each refresh drops
        # the preview's caches and starts the maps drawing again, so only the
        # last of a burst is worth it.
        self._refresh_timer = QTimer(dialog)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self._refresh_current_layout)

        # The layout view's pixels, kept for the chooser once a layout has
        # settled on show; retried while a map on it is still drawing.
        self._thumbnail_timer = QTimer(dialog)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.setInterval(THUMBNAIL_DELAY_MS)
        self._thumbnail_timer.timeout.connect(self._thumbnail_tick)
        self._thumbnail_tries = 0

        self._status_timer = QTimer(dialog)
        self._status_timer.setInterval(STATUS_TICK_MS)
        self._status_timer.timeout.connect(self._on_status_tick)
        self._status_age_timer = QTimer(dialog)
        self._status_age_timer.setInterval(STATUS_AGE_REFRESH_MS)
        self._status_age_timer.timeout.connect(self._update_status_label)

        self._corner: QWidget | None = None

    # ---- Lifecycle --------------------------------------------------------

    def showing(self) -> None:
        """The dialog just switched into Cartograph. Catch up on the project."""
        self.save_bar.setVisible(True)
        self._status_timer.start()
        self._status_age_timer.start()
        self._update_status_label()
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
        self.save_bar.setVisible(False)
        self._status_timer.stop()
        self._status_age_timer.stop()

    @property
    def busy(self) -> bool:
        """Whether a build is reading the project right now."""
        return self._job is not None

    @property
    def _document(self) -> _DocumentRevisions:
        """The revisions of the layout on screen."""
        return self._document_named(self.state.cartograph_layout)

    def _document_named(self, name: str) -> _DocumentRevisions:
        return self._revisions.setdefault(name, _DocumentRevisions())

    @property
    def _edits_made(self) -> int:
        return self._document.made

    @_edits_made.setter
    def _edits_made(self, value: int) -> None:
        self._document.made = value

    @property
    def _edits_saved(self) -> int:
        return self._document.saved

    @_edits_saved.setter
    def _edits_saved(self, value: int) -> None:
        self._document.saved = value

    def _destination_of(self, name: str) -> Path | None:
        """Where the layout called `name` is saved: the file box for the one
        on screen, the remembered file for any other."""
        if name == self.state.cartograph_layout:
            return self._current_path()
        text = self.state.cartograph_file_for(name).strip()
        return Path(text) if text else None

    def _dirty_other_documents(self) -> list[str]:
        """The layouts not on screen whose file is behind their edits, and
        that still exist and have somewhere to be saved. (A removed layout has
        nothing to save: `_forget_document` drops its record.)"""
        manager = self.project.layoutManager()
        dirty: list[str] = []
        for name, revisions in self._revisions.items():
            if (
                name == self.state.cartograph_layout
                or revisions.saved >= revisions.made
            ):
                continue
            layout = manager.layoutByName(name)
            if layout is None or sip.isdeleted(layout):
                continue
            if self._destination_of(name) is not None:
                dirty.append(name)
        return dirty

    def _sync_watches(self) -> None:
        """Watch exactly the layouts that are not on screen and already have a
        file (never one without: nothing is created for it). Called wherever
        that set can change: the selection, the manager's signals."""
        if self._shut_down:
            return
        shown = self.state.cartograph_layout
        wanted = [
            layout
            for layout in self.project.layoutManager().printLayouts()
            if layout.name() != shown
            and self._destination_of(layout.name()) is not None
        ]
        kept: list[_OtherLayoutWatch] = []
        for watch in self._watches:
            if any(watch.layout is layout for layout in wanted):
                kept.append(watch)
            else:
                watch.stop()
        for layout in wanted:
            if not any(watch.layout is layout for watch in kept):
                kept.append(_OtherLayoutWatch(self, layout))
        self._watches = kept

    def _stop_watching(self) -> None:
        self._watched_edits_timer.stop()
        for watch in self._watches:
            watch.stop()
        self._watches = []

    def _on_other_layout_edited(self, layout: QgsPrintLayout) -> None:
        """An edit to a layout that is not on screen: counted for it, saved to
        its own file by the background machinery."""
        if self._exporting or self._shut_down or sip.isdeleted(layout):
            return
        name = layout.name()
        if name == self.state.cartograph_layout or self._destination_of(name) is None:
            return
        self._document_named(name).made += 1
        self._watched_edits_timer.start()

    def _forget_document(self, name: str) -> None:
        """A layout is gone (or the project cleared): what was pending for it
        is dropped, and a build under way for it delivers nothing."""
        self._revisions.pop(name, None)
        self._document_errors.pop(name, None)
        if self._job_document == name:
            self._abandon_build()

    def _run_other_saves(self) -> None:
        """Save the next layout that was switched away from with edits pending
        - one build at a time, never while one is running. A layout whose save
        failed is left for the close, which reports it, rather than retried in
        a loop."""
        if self._shut_down or self._job is not None or self.dialog._job is not None:
            return
        for name in self._dirty_other_documents():
            if name in self._document_errors:
                continue
            try:
                self._start_build(name)
            except Exception as exc:
                self._document_errors[name] = str(exc)
            return

    def shutdown(self) -> None:
        """Release everything that outlives a single widget - called once,
        from `MainDialog._shutdown`, alongside the web map's own teardown."""
        self._close_waiter = None
        self._close_save_timer.stop()
        self._autosave_timer.stop()
        self._other_saves_timer.stop()
        self._stop_watching()
        self._refresh_timer.stop()
        self._thumbnail_timer.stop()
        self._status_timer.stop()
        self._status_age_timer.stop()
        # Same rule as the web map's job (`MainDialog._shutdown`): a build
        # thread still running when its dialog is destroyed takes QGIS down.
        self._shut_down = True
        self._abandon_build()
        if self._preview_server is not None:
            self._preview_server.stop()
            self._preview_server = None
        self._disconnect_layout_manager()
        self._disconnect_current_layout()
        self.page_pane.live_counts.close()
        with contextlib.suppress(TypeError, RuntimeError):
            self.project.cleared.disconnect(cartograph_export.clear_static_frame_cache)

    def _abandon_build(self) -> None:
        """Drop the build under way: its result is never delivered, so it can
        neither write the file nor report a save. A worker thread still running
        is left owned by `background_job`, never destroyed with the dialog."""
        job, self._job = self._job, None
        self._job_document = None
        self._static_carry_on = None
        self._build_landing = None
        if isinstance(job, _StaticRenders):
            job.cancel()
        elif job is not None:
            retire(job, SHUTDOWN_WAIT_MS)

    def release_project(self) -> None:
        """Let go of the project's layouts before QGIS destroys them: it is
        being cleared (File > New) or replaced (opening another project).

        The layouts are still alive when `aboutToBeCleared` is emitted and are
        destroyed as `clear` carries on; a slot of ours reached from that
        destruction (a layout's `changed`, the manager's `layoutRemoved`) calls
        into half-destroyed objects and takes QGIS down. So everything stops
        watching first, and nothing from here on can write: the cartograph
        file belongs to a layout of a project that is gone, and nothing of this
        dialog's state belongs to whatever project comes next. A close waiting
        on a save simply stops waiting; the window closes it itself.
        """
        self._autosave_timer.stop()
        self._other_saves_timer.stop()
        self._stop_watching()
        self._refresh_timer.stop()
        self._thumbnail_timer.stop()
        self.stop_close_save()
        self._abandon_build()
        self._revisions.clear()
        self._document_errors.clear()
        self._disconnect_layout_manager()
        self._disconnect_current_layout()
        self._current_layout = None
        self.page_pane.show_layout(None)
        self.membership.layout_changed()
        self.atlas_tab.show_layout(None)

    def _abandon_close_save(self) -> None:
        """The layout a held close was saving is gone: there is nothing left to
        save, so the close the user asked for goes ahead - on the next turn of
        the event loop, not from inside the layout's removal."""
        waiter, self._close_waiter = self._close_waiter, None
        self._close_save_timer.stop()
        if waiter is not None:
            QTimer.singleShot(0, functools.partial(self._deliver_abandoned, waiter[0]))

    def _deliver_abandoned(self, on_closed: Callable[[], None]) -> None:
        if not self._shut_down:
            on_closed()

    def _on_layout_about_to_be_removed(self, name: str) -> None:
        """The layout on screen is about to be deleted: stop watching it and
        drop what was meant for it. A build under way would still write its
        file; edits waiting for the debounce have nowhere to go. (The manager
        announces it by name; names are unique in a project.)"""
        current = self._current_layout
        if self._shut_down:
            return
        if current is None or sip.isdeleted(current) or current.name() != name:
            # Not the one on screen, but it may have a save pending.
            self._forget_document(name)
            for watch in [
                w
                for w in self._watches
                if not sip.isdeleted(w.layout) and w.layout.name() == name
            ]:
                watch.stop()
                self._watches.remove(watch)
            return
        self._forget_document(name)
        self._autosave_timer.stop()
        self._abandon_build()
        self._disconnect_current_layout()
        self._abandon_close_save()

    # ---- Closing with changes pending ---------------------------------------

    def unsaved_for_close(self) -> bool:
        """Whether closing now would leave a cartograph file behind its
        edits: one still waiting out the debounce, one a running build does
        not carry, or the last save having failed - for the layout on screen or
        any layout switched away from. False when there is nowhere to save (no
        file chosen): nothing could be done."""
        if self._shut_down:
            return False
        return self._current_unsaved() or bool(self._dirty_other_documents())

    def _current_unsaved(self) -> bool:
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout) or not self.file_edit.text().strip():
            return False
        return self._autosave_timer.isActive() or self._edits_saved < self._edits_made

    def start_close_save(
        self, on_saved: Callable[[], None], on_failed: Callable[[str], None]
    ) -> None:
        """Save every edit made so far, then call `on_saved`; or `on_failed`
        with the reason if that cannot be done.

        A build already running does not count unless it carries the latest
        edit: when it lands, the file is behind again and another build
        follows, as many as it takes. "Saved" means `_on_build_succeeded` has
        committed the file for that revision (`_edits_saved`), nothing earlier.
        Each step is looked at from a short timer rather than hooked into the
        build's many exits, so a build that ends some way nobody thought of
        still ends the wait.
        """
        self._last_error = ""
        self._document_errors.clear()
        self._close_waiter = (on_saved, on_failed)
        self._close_save_timer.start()
        self._update_status_label()
        self._sync_report_display()
        self._advance_close_save()

    def save_within(self, wait_ms: int) -> bool:
        """`start_close_save` for a caller that cannot wait for a signal
        (unloading): run the event loop for at most `wait_ms` and say whether
        the file ended up current."""
        outcome: list[bool] = []
        self.start_close_save(
            lambda: outcome.append(True), lambda _reason: outcome.append(False)
        )
        deadline = time.monotonic() + wait_ms / 1000
        while not outcome and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.stop_close_save()
        return bool(outcome and outcome[0])

    def stop_close_save(self) -> None:
        """Stop waiting (the user cancelled, or the window closes anyway). A
        build in flight carries on and lands as any autosave would."""
        self._close_waiter = None
        self._close_save_timer.stop()
        self._update_status_label()

    def _finish_close_save(self, failure: str | None) -> None:
        waiter, self._close_waiter = self._close_waiter, None
        self._close_save_timer.stop()
        self._update_status_label()
        self._sync_report_display()
        if waiter is None:
            return
        on_saved, on_failed = waiter
        if failure is None:
            on_saved()
        else:
            on_failed(failure)

    def _advance_close_save(self) -> None:
        """One look at how the save a close is waiting for is going."""
        if self._close_waiter is None or self._shut_down:
            return
        try:
            if self._job is not None:
                return  # a build is running; its landing is looked at in turn
            if self._last_error:
                self._finish_close_save(self._last_error)
                return
            failed = [
                self._document_errors[name]
                for name in self._dirty_other_documents()
                if name in self._document_errors
            ]
            if failed:
                self._finish_close_save(failed[0])
                return
            if not self.unsaved_for_close():
                self._finish_close_save(None)
                return
            if not self._current_unsaved():
                # Only layouts switched away from are behind.
                self._run_other_saves()
                if self._job is None and self.dialog._job is None:
                    self._finish_close_save(
                        next(iter(self._document_errors.values()), "")
                        or tr("The cartograph could not be saved. Nothing was written.")
                    )
                return
            self._autosave_timer.stop()
            self._run_autosave(urgent=True)
            if self._job is None and not self._autosave_timer.isActive():
                # Nothing started and nothing will: say why rather than wait.
                self._finish_close_save(
                    self._last_error
                    or tr("The cartograph could not be saved. Nothing was written.")
                )
        except Exception as exc:
            self._finish_close_save(str(exc))

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
        self.page_pane.new_layout_requested.connect(self.create_from_canvas)
        self.page_pane.controls.canvas_extent_requested.connect(self._match_canvas)
        # A Live count's number is part of the page: a new one is a change.
        self.page_pane.live_counts.content_changed.connect(self.schedule_autosave)
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
        # The layout shown, in a header; making another is "New from
        # template..." (it leaves this one as it is) and the rest of the
        # project's layouts are behind "Switch layout...".
        self.layout_header = LayoutHeader(panel)
        self.layout_header.rename_requested.connect(self.on_rename_layout)
        self.layout_header.import_requested.connect(self.on_import_cartograph)
        self.layout_header.new_from_template_requested.connect(self.create_from_canvas)
        self.layout_header.switch_requested.connect(self.on_switch_layout)
        self.new_from_template_button = self.layout_header.new_from_template_button
        self.import_action = self.layout_header.import_action
        self.rename_action = self.layout_header.rename_action
        self._new_from_template_tip = tr(
            "New layout from template: pick a design and build another print "
            "layout from what the map canvas shows. The layout shown here is "
            "kept as it is."
        )
        self.new_from_template_button.setToolTip(self._new_from_template_tip)
        column.addWidget(self.layout_header)
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

        # One continuous line above the tabs. It is a widget of its own, not
        # the tab pane's border, so no tab (selected, hovered or focused) can
        # overlap or erase it. Its height is the 1 px line plus the part of
        # the 8 px gap the column's own 6 px spacing does not give.
        self.panel_rule = QFrame(panel)
        self.panel_rule.setProperty("role", "panelRule")
        self.panel_rule.setFixedHeight(PANEL_RULE_HEIGHT)
        column.addWidget(self.panel_rule)

        self.panel_tabs = QTabWidget(panel)
        self.panel_tabs.setProperty("role", "panelTabs")
        self.panel_tabs.setDocumentMode(True)
        # The style's own line along the bar would sit in the 8 px gap, a
        # second rule beside ours (the pane's border used to hide it).
        self.panel_tabs.tabBar().setDrawBase(False)
        # Never squeezed to nothing by a short window: the save lines below
        # are fixed, so the tabs keep room for a few rows of their own.
        self.panel_tabs.setMinimumHeight(PANEL_TABS_MIN_HEIGHT)

        # Layout: the page's own settings, then its maps, then its layers.
        sections = QWidget()
        sections_column = QVBoxLayout(sections)
        sections_column.setContentsMargins(0, 4, 6, 0)
        sections_column.setSpacing(2)
        self.page_section = PanelSection(
            "page", tr("Page settings"), sections, open_by_default=True
        )
        self.page_section.setToolTip(
            tr(
                "The page's title, attribution and colour theme. They are part "
                "of this layout and show in the preview, the web page and the PDF."
            )
        )
        page_form = QFormLayout()
        page_form.setContentsMargins(0, 0, 0, 0)
        page_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        page_form.setVerticalSpacing(4)
        self.title_edit = QLineEdit(self.page_section.body)
        self.title_edit.setPlaceholderText(tr("The project title"))
        self._title_tip = tr(
            "Shown wherever the layout uses the project title. Blank keeps it."
        )
        self.title_edit.setToolTip(self._title_tip)
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
        # Why a field cannot be edited here (the page text is an expression, or
        # the layout has no such text); empty and hidden otherwise.
        self.page_hint = FittedLabel("", self.page_section.body)
        self.page_hint.setProperty("role", "hint")
        self.page_hint.setVisible(False)
        self.page_section.body_layout.addWidget(self.page_hint)
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
        # Edit: what can be changed on the item selected on the page. The
        # page pane builds the controls; the tab only seats them.
        self.edit_scroll = _panel_scroll(self.page_pane.controls)
        self.panel_tabs.addTab(self.edit_scroll, tr("Edit"))
        self._tab_keys = ("layout", "atlas", "checks", "edit")
        self.page_pane.edit_requested.connect(self._show_edit_tab)
        column.addWidget(self.panel_tabs, 1)

        # Takes the tabs' room while they are hidden, so the layout list
        # stays at the top of an empty panel instead of drifting down it.
        self._panel_filler = QWidget(panel)
        self._panel_filler.setVisible(False)
        column.addWidget(self._panel_filler, 1)

        # Where it saves and whether it has: one row in the dialog's bottom
        # bar (`MainDialog._build_button_row` seats `save_bar`), always in
        # sight however short the window, and giving the panel its height.
        # The bar is shown only while Cartograph is the output (`showing` /
        # `hiding`); `file_edit` inside it follows the layout list as before.
        self.save_bar = QWidget(self.dialog)
        self.save_bar.setVisible(False)
        save_bar_row = QHBoxLayout(self.save_bar)
        save_bar_row.setContentsMargins(0, 0, 0, 0)
        self.file_edit = SaveLocation(self.save_bar)
        save_bar_row.addWidget(self.file_edit)
        self.file_edit.setPlaceholderText(tr("Choose where the cartograph is saved"))
        self.file_edit.textEdited.connect(self._on_file_edited)
        self.file_edit.change_requested.connect(self._on_choose_file)
        self.file_edit.reveal_requested.connect(self.on_reveal)
        panel_side = QWidget(splitter)
        panel_side_column = QVBoxLayout(panel_side)
        panel_side_column.setContentsMargins(12, 0, 0, 0)
        panel_side_column.setSpacing(2)
        self.cartograph_status_label = ElidedLabel("", self.file_edit)
        self.cartograph_status_label.setProperty("role", "hint")
        self.file_edit.set_status_widget(self.cartograph_status_label)
        # Everything below the list belongs to a layout; with none there is
        # nothing to set, so it is hidden rather than left to type into.
        self._layout_only = (
            self.panel_rule,
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
        # Seated on every switch into Cartograph; the bar is made only once,
        # or each switch stacked another copy of its note.
        if self.membership.bar is None:
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
        if (
            self._panel_sized
            or sip.isdeleted(self.workspace)
            or not self.workspace.isVisible()
        ):
            return
        total = self.workspace.width()
        if total <= 0:
            return
        self._panel_sized = True
        width = PANEL_WIDTH_SMALL if total < PANEL_WIDE_WINDOW else PANEL_WIDTH_LARGE
        self.workspace.setSizes([max(1, total - width), width])

    def _show_edit_tab(self) -> None:
        """A double-click on an item, or placing a new one: its controls."""
        self.panel_tabs.setCurrentIndex(self._tab_keys.index("edit"))
        self.page_pane.controls.reveal()

    def open_section(self, key: str) -> None:
        """Bring a part of the panel into view - a jump from elsewhere, such
        as a check's "Show in Layers". "atlas", "checks" and "edit" are tabs; the
        rest are sections of the Layout tab."""
        if key in self._tab_keys:
            self.panel_tabs.setCurrentIndex(self._tab_keys.index(key))
            return
        self.panel_tabs.setCurrentIndex(0)
        section = self.sections[key]
        section.set_open(True)
        scroll = self.layout_scroll

        def reveal() -> None:
            # Runs a tick later: the panel may have been closed meanwhile.
            if not sip.isdeleted(scroll) and not sip.isdeleted(section):
                scroll.ensureWidgetVisible(section, 0, 0)

        QTimer.singleShot(0, reveal)

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
            prepared_input,
            preview_state_key,
            template_layout_name,
            template_preview_async,
            view_input,
        )
        from ...core.layout_templates import plan_template
        from ...core.template_registry import get_template
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

        def plan(template_id: str, page_size: str, orientation: str):
            if base_input is None:
                raise ValueError("the canvas could not be read")
            return plan_template(
                template_id,
                prepared_input(base_input, self.project, page_size, orientation),
            )

        def render(template_id: str, page_size: str, orientation: str, width_px: int):
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
                prepared=base_input,
            )

        # The picker saves the map framing chosen in it to the project as it
        # accepts, so the layout built next reads it from there.
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
        _forget_build_commands(result.layout)
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
        _forget_build_commands(built.layout)
        name = built.layout.name()
        choices = self.state.frame_choices(name)
        for item_id, mode in built.frame_modes.items():
            dpi = built.frame_dpis.get(item_id, DEFAULT_STATIC_DPI)
            choices[item_id] = FrameChoice(mode=mode, dpi=dpi)
        self._carry_in_page_fields(name, built)
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

    def _carry_in_page_fields(self, name: str, built: BuiltLayout) -> None:
        """The imported cartograph's title, attribution and theme become the
        new layout's Page settings, so Preview and the next save keep them. A
        field already filled in for that layout name is left alone. The layout
        is already drawn in the theme, so it is recorded, not restyled."""
        from ...core.cartograph_ir import CARTOGRAPH_THEMES

        page = self.state.page_choice(name)
        if not page.title:
            page.title = built.page_title.strip()
        if not page.attribution:
            page.attribution = built.page_attribution.strip()
        if page.theme == "default" and built.page_theme in CARTOGRAPH_THEMES:
            page.theme = built.page_theme or "default"
            adopt_theme(built.layout, page.theme)

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
        save_state(self.project, self.state)

    def current_layout_name(self) -> str:
        """The name of the layout shown, or "" with none."""
        return self._selected_name or ""

    def show_layout_named(self, name: str) -> None:
        """Open the project's layout called `name`; another name is ignored."""
        if name and self.project.layoutManager().layoutByName(name) is not None:
            self._select_layout(name)

    def on_switch_layout(self) -> None:
        """Switch layout...: the chooser over every layout of the project. It
        shows names, a line of metadata and the thumbnails already taken; it
        never renders a layout."""
        self._remember_thumbnail()
        entries = [
            LayoutEntry(
                name=layout.name(),
                metadata=layout_metadata(layout),
                thumbnail=layout_thumbnails.recall(existing_key(layout)),
            )
            for layout in self.project.layoutManager().printLayouts()
        ]
        dialog = LayoutChooserDialog(entries, self.current_layout_name(), self.dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen_name:
            self.show_layout_named(dialog.chosen_name)

    def _update_layout_header(self) -> None:
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout):
            self.layout_header.show_layout("", "", len(self._listed_layouts))
            return
        self.layout_header.show_layout(
            layout.name(), layout_metadata(layout), len(self._listed_layouts)
        )

    def _remember_thumbnail(self) -> bool:
        """Keep the page as the preview shows it now, for the chooser. False
        when it could not be taken yet (a map on it is still drawing)."""
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout) or self._shut_down:
            return True
        image = layout_thumbnails.grab_page(self.page_preview.view, layout)
        if image is None:
            return False
        layout_thumbnails.remember(layout_key(self.project, layout), image)
        return True

    def _thumbnail_tick(self) -> None:
        if self._remember_thumbnail() or self._thumbnail_tries >= THUMBNAIL_TRIES:
            return
        self._thumbnail_tries += 1
        self._thumbnail_timer.start()

    def _schedule_thumbnail(self) -> None:
        self._thumbnail_tries = 0
        self._thumbnail_timer.start()

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
        """A Page settings field was edited: the layout takes the change as an
        undo step, so the preview, the web page and the PDF all show it."""
        layout = self._current_layout
        if not self.state.cartograph_layout or self._loading_page:
            return
        page = self.state.page_choice(self.state.cartograph_layout)
        title = self.title_edit.text().strip()
        attribution = self.attribution_edit.text().strip()
        theme = self.theme_combo.currentData() or "default"
        if layout is not None and not sip.isdeleted(layout):
            if title != page.title:
                set_title(layout, title, tr("Edit page title"))
            if attribution != page.attribution:
                set_attribution(layout, attribution, tr("Edit page attribution"))
            if theme != page.theme:
                apply_theme(layout, theme, tr("Change page theme"))
        page.title, page.attribution, page.theme = title, attribution, theme
        self._update_section_summaries()
        self._on_cartograph_setting_changed()

    def _load_page_fields(self, *, migrate: bool = True) -> None:
        """Show the selected layout's page fields, read from the layout, without
        counting as edits. A layout last saved before the fields lived in it
        is brought up to what was recorded for it (`migrate`, when the layout is
        selected - not after an undo, which must be able to blank a field)."""
        layout = self._current_layout
        page = self.state.page_choice(self.state.cartograph_layout)
        hint = ""
        title_dynamic = attribution_dynamic = False
        self._loading_page = True
        try:
            if layout is not None and not sip.isdeleted(layout):
                title = title_state(layout)
                if (
                    migrate
                    and title.mode == "variable"
                    and not title.value
                    and page.title
                ):
                    # Saved before the title lived in the layout.
                    set_title_quietly(layout, page.title)
                    title = title_state(layout)
                if migrate and not has_theme_record(layout) and page.theme != "default":
                    apply_theme_quietly(layout, page.theme)
                attribution = attribution_state(layout)
                title_dynamic, attribution_dynamic = title.dynamic, attribution.dynamic
                page.title = title.value
                if attribution.mode == "item":
                    page.attribution = attribution.value
                page.theme = current_theme(layout)
                if title_dynamic:
                    hint = tr(
                        "The title on this page is an expression, which is kept. "
                        "Edit it on the page; this field sets the project title "
                        "the page reads."
                    )
                elif attribution_dynamic:
                    hint = tr(
                        "The attribution on this page is an expression, which is "
                        "kept. Edit it on the page."
                    )
                elif attribution.mode == "none":
                    hint = tr(
                        "This layout has no attribution text, so the attribution "
                        "is for the web page only."
                    )
            if self.title_edit.text() != page.title:
                self.title_edit.setText(page.title)
            if self.attribution_edit.text() != page.attribution:
                self.attribution_edit.setText(page.attribution)
            self.theme_combo.setCurrentIndex(
                max(self.theme_combo.findData(page.theme), 0)
            )
            self.attribution_edit.setEnabled(not attribution_dynamic)
            self.page_hint.setText(hint)
            self.page_hint.setVisible(bool(hint))
            self.title_edit.setToolTip(
                f"{self._title_tip}\n{hint}" if title_dynamic else self._title_tip
            )
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

    def _apply_page_fields(
        self, spec: CartographSpec, name: str, layout: QgsPrintLayout | None
    ) -> CartographSpec:
        """The Cartograph tab's title, attribution and theme on the page root,
        for the layout called `name`."""
        page = self.state.page_choice(name)
        if layout is not None and not sip.isdeleted(layout):
            # The layout is the one source: the web page says what the preview
            # and the PDF say.
            page = dataclasses.replace(
                page, title=title_state(layout).value, theme=current_theme(layout)
            )
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
        # The chooser only confirmed replacing the name it was given. A name
        # changed afterwards needs its own answer when that file exists.
        if str(path) != chosen and path.exists() and not self._confirm_replace(path):
            return
        self._approved_destinations.add(str(path))
        self.file_edit.choose(str(path))

    def _confirm_replace(self, path: Path) -> bool:
        """Ask whether to replace the existing file at `path`. Cancel is the
        default and the Escape/close answer; only Replace returns True."""
        from qgis.PyQt.QtWidgets import QMessageBox

        box = QMessageBox(self.dialog)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("Replace existing file?"))
        box.setText(tr("A file already exists at the final HTML destination:"))
        # Zero-width spaces after separators let a long path wrap, not clip.
        shown = str(path).replace("/", "/\u200b").replace("\\", "\\\u200b")
        box.setInformativeText(
            f"{shown}\n\n{tr('Replacing it will overwrite its contents.')}"
        )
        cancel = box.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        replace = box.addButton(tr("Replace"), QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is replace

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
        self.frames_list.go_to_location_requested.connect(self._go_to_location)
        self.frames_list.issues_requested.connect(self._show_frame_issues)
        self.frames_list.actions_opening.connect(self._refresh_smoothing_options)
        self.frames_list.smoothing_changed.connect(self._set_raster_smoothing)
        self.page_pane.controls.bind_frames(self.frames_list)
        return self.frames_list

    # ---- Atlas tab -------------------------------------------------------

    def build_atlas_tab(self) -> QWidget:
        from .atlas_tab import AtlasHost, AtlasTab

        self.atlas_tab = AtlasTab(
            AtlasHost(
                frame_mode=self._frame_mode,
                switch_to_interactive=self._switch_frames_to_interactive,
                export_pdf=self.on_save_atlas_pdf,
                open_pages=self._open_series_pages,
                print_series=self.on_print_series,
                browse_templates=self.create_from_canvas,
            ),
            self.dialog,
        )
        self.atlas_tab.changed.connect(self.schedule_autosave)
        self.atlas_tab.changed.connect(self._update_share_enabled)
        self.project.layerWillBeRemoved.connect(self.atlas_tab.layer_will_be_removed)
        return self.atlas_tab

    def _open_series_pages(self, kind: str) -> None:
        """Atlas > Export series / Print series: the saved cartograph, opened
        on its pages ("zip", "print") or plainly ("html"). `_open_saved` waits
        for the save that carries the latest edits."""
        path = self._current_path()
        if path is None:
            return
        if not path.exists():
            # Never saved yet: save, and `_open_saved` opens it when that lands.
            self.schedule_autosave()
        self._open_saved(path, "" if kind == "html" else f"atlas={kind}")

    def _open_all_pages(self, kind: str) -> None:
        self._open_series_pages(kind)

    def _frame_mode(self, item_id: str) -> str:
        """The map's Interactive ("live") or Image ("static") choice."""
        choice = self.state.frame_choices(self.state.cartograph_layout).get(item_id)
        if choice is not None:
            return choice.mode
        for frame in layout_map_items(self._current_layout):
            if frame.item_id == item_id:
                return frame.default_mode
        return "live"

    def _set_frame_mode(self, item_id: str, mode: str) -> None:
        """Set a map's Interactive/Image choice as the Map frames list would."""
        row = self._frame_rows.get(item_id)
        if row is None:
            return
        combo = row["mode"]
        index = combo.findData(mode)
        if index >= 0 and combo.currentIndex() != index:
            combo.setCurrentIndex(index)
        self.atlas_tab.refresh_now()

    def _switch_frames_to_interactive(self, item_ids: Sequence[str]) -> None:
        """Switch to Interactive: one explicit step on the layout's undo stack,
        which Undo takes back (the maps return to what they were)."""
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout):
            return
        previous = {
            item_id: self._frame_mode(item_id)
            for item_id in item_ids
            if self._frame_mode(item_id) != "live"
        }
        if not previous:
            return
        layout.undoStack().stack().push(
            _FrameModeCommand(self, previous, tr("Switch to Interactive"))
        )

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
        self.dialog.sync_include_column()
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

    def _go_to_location(self, item_id: str) -> None:
        """Map frames > Go to location...: choose what the frame of this card
        shows. The dialog changes the frame's view as one undo step on the
        layout's stack; a drag of the Edit view mode still in progress is
        dropped first, and the cartograph is saved again afterwards."""
        from ...core.go_to_location import FrameTarget
        from .go_to_location_dialog import GoToLocationDialog

        item = self._map_item(item_id)
        if item is None or self._current_layout is None:
            return
        target = FrameTarget(
            self._current_layout,
            item,
            self.project,
            before_apply=self.page_preview.content_editor.cancel_gesture,
            after_apply=self.schedule_autosave,
        )
        dialog = GoToLocationDialog(target, self.dialog)
        dialog.exec()
        dialog.deleteLater()

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
        self.dialog.sync_include_column()
        self._on_cartograph_setting_changed()

    def _on_frame_dpi_changed(self, item_id: str, dpi_combo: QComboBox) -> None:
        dpi = dpi_combo.currentData()
        self.state.frame_choices(self.state.cartograph_layout)[item_id].dpi = dpi
        self._on_cartograph_setting_changed()

    # ---- Layout selection -------------------------------------------------

    def refresh_layouts(self) -> None:
        """Follow the project's print layouts: the header, and which one is shown."""
        manager = self.project.layoutManager()
        if not self._manager_connected:
            manager.layoutAdded.connect(self._on_layouts_changed)
            manager.layoutAboutToBeRemoved.connect(self._on_layout_about_to_be_removed)
            manager.layoutRemoved.connect(self._on_layouts_changed)
            manager.layoutRenamed.connect(self._on_layout_renamed)
            self._manager_connected = True

        listed = manager.printLayouts()
        names = [pl.name() for pl in listed]
        self._listed_layouts = list(zip(listed, names))
        # A name taken again by another layout is no longer a renamed one.
        for name in names:
            self._renamed.pop(name, None)

        self.layout_empty_note.setVisible(not names)

        target = (
            self.state.cartograph_layout
            if self.state.cartograph_layout in names
            else (names[0] if names else "")
        )
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
            if old in self._revisions:
                self._revisions[new_name] = self._revisions.pop(old)
            if old in self._document_errors:
                self._document_errors[new_name] = self._document_errors.pop(old)
            if self._job_document == old:
                self._job_document = new_name
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
            manager.layoutAboutToBeRemoved.disconnect(
                self._on_layout_about_to_be_removed
            )
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
        if switching:
            self._remember_thumbnail()
            if self._selected_name and self._last_error:
                # The layout left behind keeps its failure: closing reports it.
                self._document_errors[self._selected_name] = self._last_error
            self._document_errors.pop(name, None)
        self._selected_name = name
        self._disconnect_current_layout()
        manager = self.project.layoutManager()
        layout = manager.layoutByName(name) if name else None
        self._current_layout = layout
        self.state.cartograph_layout = name or ""
        self.page_pane.show_layout(layout)
        self._update_layout_header()
        self._schedule_thumbnail()
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
        self._sync_watches()
        if switching:
            if self.state.output is DialogOutput.CARTOGRAPH:
                self.show_fidelity()
            # The file is a document that follows its layout: bring it up to
            # date now, not on the user's first edit.
            self.schedule_autosave()
            # The layout just left may still have an edit to save.
            self._kick_other_saves()

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
        # An undo, a redo or an edit on the page may have changed the title,
        # the attribution or the theme: the fields show the layout's own.
        self._load_page_fields(migrate=False)
        self._update_layout_header()
        self._schedule_thumbnail()
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

    def _run_autosave(self, urgent: bool = False) -> None:
        """The debounce timer's slot. Never lets an exception escape: from a
        timer it would surface as a Python error dialog in QGIS, and the
        status line exists to say why a save did not happen.

        A save that nobody is waiting on (`urgent` is for a Check or a page
        opened from it) lets the user finish first: while a mouse button is
        held on the page (a drag) or the preview map is still drawing, it
        tries again after another debounce - for at most
        `AUTOSAVE_DEFER_LIMIT_S`, so a map that never finishes drawing cannot
        hold the file back."""
        try:
            # A close waiting on the save is not going to wait for a drag.
            urgent = urgent or self._close_waiter is not None
            if not urgent and self._defer_for_preview():
                return
            self._deferred_since = None
            self._start_build()
        except Exception as exc:
            self._cancel_static_renders()
            self._on_build_failed(str(exc), traceback.format_exc())

    def _defer_for_preview(self) -> bool:
        """Whether this autosave should wait: the user is mid-gesture, or the
        preview is still drawing what they just changed."""
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout):
            return False
        busy = QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton or any(
            item.isDrawing()
            for item in layout.items()
            if isinstance(item, QgsLayoutItemMap)
        )
        if not busy:
            return False
        now = time.monotonic()
        if self._deferred_since is None:
            self._deferred_since = now
        if now - self._deferred_since >= AUTOSAVE_DEFER_LIMIT_S:
            return False
        self._autosave_timer.start()
        return True

    def _start_build(self, other: str | None = None) -> None:
        """Build the layout on screen, or - `other` - the layout of that name
        that was switched away from with edits pending. Either way the layout,
        its file and its edit revision are taken here, at the start: what lands
        later is written to and counted for that document and no other."""
        if other is None:
            layout = self._current_layout
            name = self.state.cartograph_layout
        else:
            layout = self.project.layoutManager().layoutByName(other)
            name = other
        if layout is None or sip.isdeleted(layout):
            # Destroyed since the debounce started; `layoutRemoved` picks
            # another layout once it is gone.
            self._cancel_waiting_pages()
            return
        destination = self._destination_of(name)
        if destination is None:
            self._cancel_waiting_pages()
            return
        if self._job is not None or self.dialog._job is not None:
            # A build, or a web-map job, is already reading the project - and
            # two threads walking the same layers is what takes QGIS down.
            # Try again after another debounce; nothing is lost.
            if other is None:
                self._autosave_timer.start()
            else:
                self._other_saves_timer.start()
            return

        shown_error = self._last_error
        runtime = self._load_runtime()
        if runtime is None:
            if other is not None:
                # Belongs to that layout, not to the one on screen.
                self._document_errors[other] = self._last_error
                self._last_error = shown_error
                return
            self._cancel_waiting_pages()
            self._check_requested = False
            self._update_status_label()
            self._sync_report_display()
            return

        path = self._safe_destination(destination, layout, name)
        project = self.project
        choices = dict(self.state.frame_choices(name))
        frame_modes = cast(
            "dict[str, FrameMode]",
            {item_id: choice.mode for item_id, choice in choices.items()},
        )
        frame_dpis = {item_id: choice.dpi for item_id, choice in choices.items()}

        from ...core.fidelity_report import FidelityReportBuilder

        report = FidelityReportBuilder()
        print_settings = self._print_settings(name)
        # The resolution the cartograph is saved at (the runtime's own
        # default when no print settings were saved): a static frame is
        # drawn at least that sharp, whatever its own choice.
        export_dpi = print_settings.dpi if print_settings else DEFAULT_EXPORT_DPI

        # The edits this build carries: those made before it took its
        # snapshot of the layout, whatever is made while it runs.
        edits = self._document_named(name).made
        self._job_edits = edits if other is None else -1
        self._job_document = name
        built_for = name

        # The layout half here, on the GUI thread: it hides and renders the
        # layout's own items, and the layout may be open in its designer.
        # Only the layer-reading half goes to the worker. A static frame's
        # picture is the slow part, so it is drawn by QGIS's own render job:
        # started here, finished by `_continue_build` once the job is done,
        # with the event loop running in between.
        pending: list[cartograph_export.PendingStaticFrame] = []
        # A Live count still being calculated is finished first, so the file
        # carries the numbers of the view it is saved from.
        self.page_pane.live_counts.settle()
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
                pending_static=pending,
                refresh_counts=False,
            )
        except Exception as exc:
            for frame in pending:
                frame.render.cancel()
            self._on_build_failed(str(exc), traceback.format_exc(), built_for)
            return
        finally:
            self._exporting = False
        prepared = dataclasses.replace(
            prepared, spec=self._apply_page_fields(prepared.spec, name, layout)
        )
        if print_settings is not None:
            prepared = dataclasses.replace(
                prepared, spec=apply_to_spec(prepared.spec, print_settings)
            )
        if other is None:
            self._last_page_label = page_label(prepared.spec)
            self._pending_spec = prepared.spec

        def carry_on() -> None:
            self._continue_build(
                path, layout, project, report, prepared, pending, built_for, edits
            )

        if not pending:
            carry_on()
            return
        phase = _StaticRenders(pending)
        self._job = phase
        self._static_carry_on = carry_on
        self._update_status_label()
        # A bound method, not a closure: a render can finish after this
        # controller has shut down, and the method checks for that.
        for frame in pending:
            frame.render.finished.connect(self._on_static_frame_drawn)
        self._static_render_finished(phase, carry_on)

    def _on_static_frame_drawn(self) -> None:
        """A static frame's render finished: go on if it was the last one."""
        phase = self._job
        carry_on = self._static_carry_on
        if self._shut_down or carry_on is None or not isinstance(phase, _StaticRenders):
            return
        self._static_render_finished(phase, carry_on)

    def _static_render_finished(
        self, phase: _StaticRenders, carry_on: Callable[[], None]
    ) -> None:
        """One static frame's picture is drawn; go on once all of them are."""
        if self._job is not phase or self._shut_down or not phase.all_done:
            return
        self._job = None
        self._static_carry_on = None
        try:
            carry_on()
        except Exception as exc:
            self._on_build_failed(str(exc), traceback.format_exc())

    def _cancel_static_renders(self) -> None:
        """Abandon the static frames being drawn, if a build is in that phase
        (a build that failed before they finished)."""
        phase = self._job
        if isinstance(phase, _StaticRenders):
            self._job = None
            phase.cancel()

    def _continue_build(
        self,
        path: Path,
        layout: QgsPrintLayout,
        project: QgsProject,
        report: FidelityReportBuilder,
        prepared: cartograph_export.PreparedCartograph,
        pending: list[cartograph_export.PendingStaticFrame],
        built_for: str | None,
        edits: int,
    ) -> None:
        """The rest of a build, once every static frame's picture is drawn:
        the live frames' layer reads, the legends, then the worker."""
        if sip.isdeleted(layout):
            # Destroyed while its frames were drawn; `layoutRemoved` picks
            # another layout once it is gone.
            self._cancel_waiting_pages()
            self._update_status_label()
            return

        # The Layers tab's popup and label choices, copied: the worker must
        # not read state the GUI thread may be editing.
        layers = copy.deepcopy(dict(self.state.layers))
        on_screen = self._current_name_of(built_for) == self.state.cartograph_layout
        if on_screen:
            self._built_layers_signature = self._layers_signature()
        # The live project's part of reading each frame's layers, still on
        # this thread; the worker finishes it from the snapshot.
        try:
            prepared = cartograph_export.prepare_live_reads(
                prepared, project, report, layer_settings=layers
            )
        except Exception as exc:
            self._on_build_failed(str(exc), traceback.format_exc(), built_for)
            return

        # The web page's legend is taller than QGIS's: the legend boxes are
        # fitted to it here, in the layout itself, so the preview and the
        # page agree. Still the GUI thread, and not an edit of the user's:
        # the fit is not written back as a change to save again.
        self._exporting = True
        try:
            prepared = cartograph_export.fit_legends_to_layout(
                layout, project, prepared, report
            )
        except Exception as exc:
            self._on_build_failed(str(exc), traceback.format_exc(), built_for)
            return
        finally:
            self._exporting = False
        if on_screen:
            self._pending_spec = prepared.spec

        # The dialog's own writer, so a licence key the user set reaches the
        # cartograph's live frames exactly as it reaches a web map.
        writer = self.dialog._writer()
        pictures: dict[str, bytes] = {}

        def work(_progress: object) -> cartograph_export.FinishedCartograph:
            # The static frames' pictures are encoded here, off the GUI thread
            # (a 300 dpi map is several megabytes of PNG).
            for frame in pending:
                pictures[frame.frame_id] = encode_png(frame.render.take_image())
            return cartograph_export.finish_cartograph_full(
                cartograph_export.with_static_images(prepared, pictures),
                None,
                report,
                writer=writer,
                layer_settings=layers,
            )

        job = BackgroundJob(work)
        self._job = job
        self._builds_started += 1
        self._build_landing = _BuildLanding(
            job=job,
            path=path,
            report=report,
            built_for=built_for,
            build_number=self._builds_started,
            edits=edits,
            pending=pending,
            pictures=pictures,
        )
        # Bound methods, not closures: the job can outlive this controller, and
        # both check `_shut_down` and that they answer the build still current.
        job.succeeded.connect(self._on_build_job_succeeded)
        job.failed.connect(self._on_build_job_failed)
        job.start()
        self._update_status_label()

    def _current_landing(self) -> _BuildLanding | None:
        """What the running build was started with, or `None` when a result
        arriving now is not for the build in flight (or nothing is in flight)."""
        landing, self._build_landing = self._build_landing, None
        if landing is None or self._shut_down or landing.job is not self._job:
            return None
        return landing

    def _on_build_job_succeeded(
        self, finished: cartograph_export.FinishedCartograph
    ) -> None:
        landing = self._current_landing()
        if landing is None:
            return
        # The cache listens to layers, which only the GUI thread may do.
        cartograph_export.remember_static_frames(landing.pending, landing.pictures)
        self._on_build_succeeded(
            landing.path,
            finished,
            landing.report,
            landing.built_for,
            landing.build_number,
            landing.edits,
        )

    def _on_build_job_failed(self, message: str, details: str) -> None:
        landing = self._current_landing()
        if landing is None:
            return
        self._on_build_failed(message, details, landing.built_for)

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
        self._job_document = None
        if self._shut_down:
            return
        html = finished.html
        current = self._is_current_build(path, built_for)
        document = self._current_name_of(built_for) if built_for else None
        try:
            self._write_cartograph(path, html, built_for)
        except OSError as exc:
            if document is not None and not current:
                self._document_errors[document] = tr(
                    "Could not save the cartograph: {error}"
                ).format(error=str(exc))
            if not current:
                self._kick_other_saves()
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
        if document is not None and self._destination_of(document) == path:
            # The file on disk now carries this document's edits up to the
            # revision the build was started at - whichever layout is on screen.
            revisions = self._document_named(document)
            revisions.saved = max(
                revisions.saved, revisions.made if edits is None else edits
            )
            self._document_errors.pop(document, None)
        if not current:
            # Saved for a layout (or file) no longer on screen: its own file is
            # up to date, but the status, Checks and a pending print belong to
            # what is selected now, whose own build follows.
            self._update_status_label()
            self._kick_other_saves()
            return
        self._last_error = ""
        self._check_requested = False
        if self._preview_server is not None:
            # An open browser preview follows the layout on screen, and every
            # save of it.
            self._preview_server.show(path)
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
        self._kick_other_saves()

    def _kick_other_saves(self) -> None:
        """Look for a switched-away layout still to be saved, on the next turn
        of the event loop (never from inside a build's landing)."""
        if not self._shut_down and self._dirty_other_documents():
            self._other_saves_timer.start()

    def _on_build_failed(
        self, message: str, details: str, built_for: str | None = None
    ) -> None:
        self._job = None
        self._job_document = None
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
            self._document_errors[self._current_name_of(built_for) or ""] = tr(
                "Could not build the cartograph: {message}"
            ).format(message=message)
            self._update_status_label()
            self._kick_other_saves()
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

    def _safe_destination(self, path: Path, layout: QgsPrintLayout, name: str) -> Path:
        """Where the cartograph of the layout called `name` may be saved:
        `path`, unless that is a file that is not its own
        (`core/cartograph_destination.py`). Then the new place replaces it in
        the project's settings (and the file field, for the layout on screen),
        and the footer says so."""
        resolution = cartograph_destination.destination_for_layout(
            path,
            layout,
            self.project,
            default_cartograph_path(self.project.fileName(), name or "cartograph"),
            self._approved_destinations,
        )
        if resolution.reason is None:
            return resolution.path
        if name == self.state.cartograph_layout:
            self.file_edit.setText(str(resolution.path))
        if name:
            self.state.cartograph_files[name] = str(resolution.path)
        save_state(self.project, self.state)
        self.update_window_title()
        self.dialog.status_label.setText(
            tr(
                "{old} was left as it is: it is not this cartograph's own "
                "file, or cannot be saved to. Saved to {new} instead."
            ).format(old=path, new=resolution.path)
        )
        return resolution.path

    def _write_cartograph(self, path: Path, html: str, built_for: str | None) -> None:
        """Write the finished page, stamped as this layout's own, to `path` -
        which is checked again now, since the file may have changed since the
        build began."""
        layout = (
            self.project.layoutManager().layoutByName(
                self._current_name_of(built_for) or ""
            )
            if built_for is not None
            else self._current_layout
        )
        if layout is None or sip.isdeleted(layout):
            raise OSError(tr("The layout is gone; nothing was written."))
        checked = cartograph_destination.destination_for_layout(
            path, layout, self.project, path, self._approved_destinations
        )
        if checked.reason is not None:
            raise OSError(
                tr("{path} is not this cartograph's own file; not replaced.").format(
                    path=path
                )
            )
        html = cartograph_destination.stamp_for_layout(html, layout, self.project)
        self._write_atomic(path, html)

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        write_text_atomic(path, content)

    def _on_status_tick(self) -> None:
        """Once a second while Cartograph is on screen: save again when the
        Layers tab's popup or label choices changed - the same settings-polling
        the web map's live preview uses (`DialogState.snapshot`), for the one
        part of it a cartograph reads. The "Saved 2 min ago" line is not
        redrawn here: it refreshes when something changes, and every
        `STATUS_AGE_REFRESH_MS` for its age."""
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
        if self._job is not None or self._close_waiter is not None:
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
        self._run_autosave(urgent=True)
        self._sync_report_display()

    @property
    def report_needs_check(self) -> bool:
        """Whether the Checks tab offers a check rather than a report."""
        return self._report_state() is not ReportState.CURRENT

    # ---- Corner: Accessibility, Preview, Share ----------------------------

    def build_corner(self) -> QWidget:
        """The cartograph's actions, styled as the web map's are.

        Share in the same outlined button style with a line icon. There is no
        filled button: the cartograph saves itself.
        "Preview in browser" lives in Share's menu, still on Ctrl+O.
        Accessibility is a tool rather than an act, so it is an icon on its
        own ahead of them.
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

        self.cartograph_share_button = button(tr("Share"), "share")
        self.cartograph_share_button.setProperty("hasMenu", True)
        self.cartograph_share_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.cartograph_share_button.setMenu(self.build_share_menu())

        self._corner = corner
        self._update_share_enabled()
        self._colour_vision_dialog()
        return corner

    # ---- Accessibility: colour vision --------------------------------------

    def _colour_vision_dialog(self) -> AccessibilityDialog:
        """The one Colour vision dialog, built on first use. Built as soon as
        the corner is, too, so a simulation applied in an earlier session is
        on in the preview (and the button says so) before it is ever opened."""
        from .accessibility_dialog import AccessibilityDialog, simulating_tooltip

        if self._colour_vision is not None and not sip.isdeleted(self._colour_vision):
            return self._colour_vision
        dialog = AccessibilityDialog(
            lambda: self._legend_entries,
            canvas=self._map_canvas(),
            preview_view=self.page_pane.preview.view,
            open_preview=self._open_cvd_preview,
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
        # Only the Cartograph dialog going away destroys it; closing just hides.
        dialog.destroyed.connect(lambda *_args: self._forget_colour_vision(dialog))
        self._colour_vision = dialog
        on_mode(dialog.applied_condition() or "")
        return dialog

    def _forget_colour_vision(self, dialog: AccessibilityDialog) -> None:
        # Only the window that went: a replacement may already be open.
        if self._colour_vision is dialog:
            self._colour_vision = None

    def on_accessibility(self) -> None:
        """The corner's Accessibility button: show the one non-modal Colour
        vision dialog, in front, whether it was closed or left behind."""
        dialog = self._colour_vision_dialog()
        # A checkable button toggles itself before this runs; a simulation
        # applied anywhere is what it should show.
        self.accessibility_button.setChecked(bool(dialog.applied_condition()))
        dialog.refresh()
        dialog.present()

    def _open_cvd_preview(self, condition: str | None) -> None:
        """Share's Preview in browser, with the applied simulation."""
        self._open_preview_in_browser(f"cvd={condition}" if condition else "")

    def release_canvas_effects(self) -> None:
        """Undo a colour-vision preview this plugin put on the QGIS canvas.
        Called when the plugin unloads; closing a dialog leaves it, per #4."""
        self._colour_vision_dialog().reset_canvas()

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
        self._open_preview_in_browser()

    def _open_preview_in_browser(self, query: str = "") -> None:
        path = self._current_path()
        if path is None or not path.exists():
            self.dialog.status_label.setText(
                tr(
                    "Nothing to preview yet - the cartograph has not been "
                    "saved. Choose a print layout in the panel."
                )
            )
            return
        self._open_saved(path, query)

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
                + " "
                + tr("This browser view will not follow later changes.")
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
            if self._preview_server is None:
                # One server for the session, serving whichever layout's file
                # is on screen at one address, so a tab left open follows the
                # layout and every save. Nothing else in the folder is served.
                server = PreviewServer(path.parent.resolve(), only=NO_FILES)
                server.start()
                # Kept only once running: a server that failed to bind would
                # otherwise be reused, unstarted, by the next action.
                self._preview_server = server
            published = self._preview_server.published()
            if published is None or published[0] != path.resolve():
                self._preview_server.show(path)
            url = QUrl()
            url.setScheme("http")
            url.setHost(BIND_HOST)
            url.setPort(self._preview_server.port)
            url.setPath(PAGE_PATH)
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

        # Outputs first, then the HTML file's own actions, then NIKA. The page
        # beside the panel is QGIS's drawing, so saving a PDF is a native QGIS
        # export; the browser is a secondary HTML check below.
        menu.addSection(tr("Export"))
        add(
            "save_pdf",
            tr("Save PDF…"),
            tr("Save every page of this layout as a PDF (Ctrl+S)"),
            self.on_save_pdf,
            "Ctrl+S",
        )
        add(
            "save_atlas_pdf",
            tr("Save atlas as PDF…"),
            tr("One run of pages per atlas feature, in a single PDF"),
            self.on_save_atlas_pdf,
        )
        add(
            "export_png",
            tr("Export PNG…"),
            tr("Image of the first page at the saved resolution"),
            self.on_export_png,
        )
        add(
            "export_qpt",
            tr("Export QGIS layout template…"),
            tr("Round trip back into QGIS"),
            self.on_export_qpt,
        )
        add(
            "print_settings",
            tr("PDF settings…"),
            tr("Page size, bleed, crop marks and colour profile for Save PDF"),
            self.on_print_settings,
        )

        menu.addSection(tr("HTML cartograph"))
        add(
            "open_browser",
            tr("Preview in browser"),
            tr(
                "Open the HTML cartograph to check interactive maps and "
                "browser behaviour."
            ),
            self.on_open_in_browser,
            "Ctrl+O",
        )

        add(
            "copy_path",
            tr("Copy HTML file path"),
            tr("Copies the cartograph's path"),
            self.on_copy_path,
        )
        add(
            "reveal",
            tr("Show HTML in folder"),
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
        exists = self._file_exists()
        for key in ("copy_path", "reveal", "open_browser", "export_png"):
            self._set_share_row(
                key,
                exists,
                tr("Available once the cartograph has saved - choose a print layout"),
            )
        for key in ("export_qpt", "print_settings"):
            self._set_share_row(
                key, self._current_layout is not None, tr("Choose a print layout first")
            )
        self._set_share_row(
            "save_pdf",
            self._current_layout is not None and not self._pdf_running,
            tr("Saving PDF…")
            if self._pdf_running
            else tr("Choose or create a layout first"),
        )
        atlas_row = self.cartograph_share_actions.get("save_atlas_pdf")
        if atlas_row is not None:
            atlas_row.setVisible(self._atlas_configured())
            self._set_share_row(
                "save_atlas_pdf",
                not self._pdf_running,
                tr("Saving PDF…"),
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
        self._reveal(path)

    def _reveal(self, path: Path) -> None:
        """Show `path` in the file manager and say what happened once the
        platform has answered (the folder may not exist until the first save
        makes it: the nearest one that does is shown, and named)."""
        self._last_reveal_path = path
        self._connect_status_links()
        reveal_file(path, lambda outcome: self._on_revealed(path, outcome))

    def _on_revealed(self, path: Path, outcome: RevealOutcome) -> None:
        label = self.dialog.status_label
        if sip.isdeleted(label):
            return
        if outcome.ok and outcome.selected:
            label.setText(
                tr("Showing {name} in the file manager").format(name=path.name)
            )
        elif outcome.ok:
            label.setText(tr("Opened {path}").format(path=outcome.folder))
        else:
            colour = self.dialog.palette().color(QPalette.ColorRole.Link).name()
            failure = html.escape(
                tr("Could not show the file in a file manager: {reason}.").format(
                    reason=outcome.reason
                )
            )
            copy = (
                f'<a href="{_COPY_PATH_LINK}"><span style="color:{colour}">'
                f"{html.escape(tr('Copy path'))}</span></a>"
            )
            label.setText(f"{failure} {copy}")
        label.setToolTip(str(path))

    def _connect_status_links(self) -> None:
        if not self._status_links_connected:
            self.dialog.status_label.linkActivated.connect(self._on_status_link)
            self._status_links_connected = True

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

    # ---- Print settings ----------------------------------------------------

    def _print_settings(self, name: str | None = None) -> PrintSettings | None:
        text = self.state.cartograph_print.get(
            self.state.cartograph_layout if name is None else name
        )
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
            QgsUnitTypes.LayoutUnit.LayoutMillimeters,
        )
        return (size.width(), size.height())

    def on_print_settings(self) -> None:
        """Share > PDF settings: saved per layout, applied on every save."""
        from .print_settings_dialog import ACTION_EXPORT, ask_print_settings

        if self._current_layout is None:
            return
        answer = ask_print_settings(
            self._print_settings() or PrintSettings(),
            layout_page_mm=self._layout_page_mm(),
            parent=self.dialog,
        )
        if answer is None:
            return
        action, settings = answer
        self.state.cartograph_print[self.state.cartograph_layout] = settings.to_json()
        save_state(self.project, self.state)
        self._update_share_enabled()
        # The HTML carries the bleed and marks too, so it saves again.
        self.schedule_autosave()
        if action == ACTION_EXPORT:
            self.on_save_pdf()

    # ---- Save PDF ----------------------------------------------------------

    def _atlas_configured(self) -> bool:
        layout = self._current_layout
        if layout is None or sip.isdeleted(layout):
            return False
        atlas = layout.atlas()
        return bool(atlas.enabled() and atlas.coverageLayer() is not None)

    def on_save_pdf(self) -> None:
        """Save PDF…: every page of the selected layout, by QGIS itself."""
        self._save_pdf(atlas=False)

    def on_save_atlas_pdf(self) -> None:
        """Share > Save atlas as PDF…: one run of pages per atlas feature."""
        self._save_pdf(atlas=True)

    def on_print_series(self) -> None:
        """Atlas > Print series > Print with QGIS: every page of the series,
        in order, through the system print dialog (which also prints to a PDF
        file). Where Qt knows of no printer, the series is saved as a PDF to
        print from instead, and the status says that nothing was printed."""
        from qgis.core import QgsFeedback
        from qgis.PyQt.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
        from qgis.PyQt.QtWidgets import QMessageBox, QProgressDialog

        from ...exporters.print_native import PrintSeriesError, print_series

        layout = self._current_layout
        if layout is None or self._pdf_running or not self._atlas_configured():
            return
        title = tr("Print series")
        if not QPrinterInfo.availablePrinterNames():
            self._save_pdf(atlas=True, handoff=True)
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        chooser = QPrintDialog(printer, self.dialog)
        chooser.setWindowTitle(title)
        if not chooser.exec():
            self.dialog.status_label.setText(
                tr("Printing cancelled. Nothing was sent to a printer.")
            )
            return
        print_settings = self._print_settings() or PrintSettings()
        feedback = QgsFeedback()
        progress = QProgressDialog(
            tr("Preparing the print…"), tr("Cancel"), 0, 0, self.dialog
        )
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.canceled.connect(feedback.cancel)

        def on_page(page: int, pages: int) -> None:
            progress.setMaximum(pages)
            progress.setValue(page - 1)
            progress.setLabelText(
                tr("Preparing page {page} of {pages}…").format(page=page, pages=pages)
            )
            QGuiApplication.processEvents()

        self._pdf_running = True
        self.dialog.status_label.setText(tr("Printing…"))
        self._update_share_enabled()
        progress.show()
        QGuiApplication.processEvents()
        try:
            result = print_series(
                layout,
                printer,
                dpi=print_settings.dpi,
                embed_fonts=print_settings.embed_fonts,
                feedback=feedback,
                progress=on_page,
            )
        except PrintSeriesError as exc:
            self.dialog.status_label.setText(
                tr("Could not print the series: {error}").format(error=exc)
            )
            QMessageBox.warning(self.dialog, title, str(exc))
            return
        finally:
            progress.close()
            progress.deleteLater()
            self._pdf_running = False
            self._update_share_enabled()
        if result.cancelled:
            self.dialog.status_label.setText(
                tr("Printing cancelled. The rest of the series was not sent.")
            )
            return
        if printer.printerName():
            text = plural(
                result.sheets,
                "Sent {n} sheet to {printer}.",
                "Sent {n} sheets to {printer}.",
                printer=printer.printerName(),
            )
        else:  # "Print to file (PDF)" in the print dialog
            text = plural(
                result.sheets,
                "Printed {n} sheet to the file {printer}.",
                "Printed {n} sheets to the file {printer}.",
                printer=printer.outputFileName(),
            )
        if result.warnings:
            text += " - " + " ".join(result.warnings)
        self.dialog.status_label.setText(text)

    def _suggested_pdf_path(self, atlas: bool) -> Path:
        name = _file_name_part(self.state.cartograph_layout or "") or "layout"
        if atlas:
            name += "-atlas"
        html = self._current_path()
        folder = (
            html.parent
            if html is not None
            else Path(self.project.fileName() or str(Path.home())).parent
        )
        return folder / f"{name}.pdf"

    def _save_pdf(self, *, atlas: bool, handoff: bool = False) -> None:
        """`handoff`: the series is saved to be printed from elsewhere because
        no printer is available here; nothing is printed."""
        from qgis.PyQt.QtWidgets import QMessageBox

        from ...exporters.print_pdf import PrintPdfError, export_print_pdf

        layout = self._current_layout
        if layout is None or self._pdf_running:
            return
        if atlas and not self._atlas_configured():
            return
        title = tr("Save atlas as PDF") if atlas else tr("Save PDF")
        if handoff:
            title = tr("Save the series as a PDF to print")
        chosen, _ = QFileDialog.getSaveFileName(
            self.dialog,
            title,
            str(self._suggested_pdf_path(atlas)),
            tr("PDF (*.pdf)"),
        )
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() != ".pdf":
            target = target.with_name(target.name + ".pdf")
        # Settings edited since the last look count as edits made, as for the
        # HTML; the layout itself is read as it is right now, from a copy.
        self.sync_layer_settings()
        self.page_pane.live_counts.settle()
        self._pdf_running = True
        self.dialog.status_label.setText(tr("Saving PDF…"))
        self._update_share_enabled()
        QGuiApplication.processEvents()
        try:
            result = export_print_pdf(
                layout,
                self._print_settings() or PrintSettings(),
                target,
                self.state.page_choice(self.state.cartograph_layout),
                atlas=atlas,
                refresh_counts=False,
            )
        except PrintPdfError as exc:
            self.dialog.status_label.setText(
                tr("Could not save the PDF: {error}").format(error=exc)
            )
            QMessageBox.warning(self.dialog, title, str(exc))
            return
        finally:
            self._pdf_running = False
            self._update_share_enabled()
        self._show_pdf_saved(result.path, result.warnings, handoff=handoff)

    def _show_pdf_saved(
        self, path: Path, warnings: tuple[str, ...], *, handoff: bool = False
    ) -> None:
        """ "Saved PDF: name.pdf · Open PDF · Show in folder" in the status
        area, with whatever the exporter noted after it. A `handoff` says
        plainly that no printer was found and nothing was printed."""
        self._last_pdf_path = path
        self._connect_status_links()
        # The theme's link colour, stated outright: the label's own style
        # sheet otherwise leaves a dark theme with the default dark blue.
        colour = self.dialog.palette().color(QPalette.ColorRole.Link).name()

        def link(target: str, text: str) -> str:
            return (
                f'<a href="{target}"><span style="color:{colour}">'
                f"{html.escape(text)}</span></a>"
            )

        saved = (
            tr("No printer found: nothing was printed. PDF to print: {name}")
            if handoff
            else tr("Saved PDF: {name}")
        )
        parts = [
            html.escape(saved.format(name=path.name)),
            link(_PDF_OPEN_LINK, tr("Open PDF")),
            link(_PDF_SHOW_LINK, tr("Show in folder")),
        ]
        text = " · ".join(parts)
        if warnings:
            text += " - " + html.escape(" ".join(warnings))
        self.dialog.status_label.setText(text)
        self.dialog.status_label.setToolTip(str(path))

    def _on_status_link(self, link: str) -> None:
        if link == _COPY_PATH_LINK:
            if self._last_reveal_path is not None:
                QGuiApplication.clipboard().setText(str(self._last_reveal_path))
                self.dialog.status_label.setText(
                    tr("Copied {path}").format(path=self._last_reveal_path)
                )
            return
        path = self._last_pdf_path
        if path is None:
            return
        if link == _PDF_OPEN_LINK:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif link == _PDF_SHOW_LINK:
            self._reveal(path)

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
