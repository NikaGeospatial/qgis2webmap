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
import dataclasses
import re
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import QSize, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QAction,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import cartograph_export
from ...core.atomic_write import write_text_atomic
from ...core.fidelity_summary import (
    ReportState,
    ReportSummary,
    strip_message,
    summarise,
)
from ...core.print_settings import PrintSettings, apply_to_spec
from ...core.settings import (
    DEFAULT_STATIC_DPI,
    DialogOutput,
    FrameChoice,
    default_frame_mode,
    save_state,
)
from ...packaging.runtime_manager import (
    RuntimeBundle,
    RuntimeNotAcceptedError,
    RuntimeUnavailableError,
)
from ..background_job import BackgroundJob
from ..fitted_label import FittedLabel
from ..i18n import plural, tr
from ..live_server import BIND_HOST, PreviewServer
from ..runtime_setup import ensure_runtime
from .frames import layout_map_items
from .status import page_label, status_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...core.fidelity_report import FidelityReportBuilder
    from ..main_dialog import MainDialog

# Debounce per the spec: a burst of edits - dragging a DPI slider would be one
# if this tab had one - collapses into one rebuild, the same ~600 ms the plan
# settles on for the document model.
AUTOSAVE_DEBOUNCE_MS = 600
STATUS_TICK_MS = 1000
DPI_CHOICES = (150, 200, 300)


def _reveal_label() -> str:
    """What "show me this file" is called here. Duplicated from
    `main_dialog._reveal_label` rather than imported: that module imports this
    package to build the switch, so importing back would be circular."""
    if sys.platform == "darwin":
        return tr("Reveal in Finder")
    if sys.platform.startswith("win"):
        return tr("Show in Explorer")
    return tr("Show in file manager")


def default_cartograph_path(project_file_name: str, layout_name: str) -> Path:
    """Where a layout's cartograph is written by default.

    Beside the project file, named after the layout - the plan's words for it.
    A project that has never been saved has no folder of its own to sit beside,
    so the home directory is the fallback rather than the current working one,
    which for a GUI application is rarely where the user would look.
    """
    base_dir = Path(project_file_name).parent if project_file_name else Path.home()
    safe = re.sub(r"[^\w .-]", "_", layout_name).strip(" .") or "cartograph"
    return base_dir / f"{safe}.html"


class CartographController:
    """Owns the Cartograph output's widgets, state and autosave job."""

    def __init__(self, dialog: MainDialog) -> None:
        self.dialog = dialog
        self.project = dialog.project
        self.state = dialog.state

        self._current_layout = None
        self._layout_changed_connected = False
        self._manager_connected = False
        self._frame_rows: dict[str, dict[str, QComboBox]] = {}

        self._job: BackgroundJob | None = None
        self._last_saved_at: float | None = None
        self._last_saved_bytes: int | None = None
        self._last_error = ""
        self._last_summary: ReportSummary | None = None
        self._runtime: RuntimeBundle | None = None
        # "A3 landscape" / "420 x 297 mm landscape", from the last save, for
        # the hand-off prompt's print-safety line.
        self._last_page_label: str | None = None
        self._print_after_save = False
        self._preview_server: PreviewServer | None = None

        self._autosave_timer = QTimer(dialog)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._run_autosave)

        self._status_timer = QTimer(dialog)
        self._status_timer.setInterval(STATUS_TICK_MS)
        self._status_timer.timeout.connect(self._update_status_label)

        self._corner: QWidget | None = None

    # ---- Lifecycle --------------------------------------------------------

    def showing(self) -> None:
        """The dialog just switched into Cartograph. Catch up on the project."""
        self._status_timer.start()
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
        self._status_timer.stop()
        if self._preview_server is not None:
            self._preview_server.stop()
            self._preview_server = None
        self._disconnect_layout_manager()
        self._disconnect_current_layout()

    # ---- Cartograph tab -----------------------------------------------

    def build_cartograph_tab(self) -> QWidget:
        page = QWidget(self.dialog)
        layout = QVBoxLayout(page)

        source_box = QGroupBox(tr("Source"), page)
        source_layout = QVBoxLayout(source_box)

        self._source_group = QButtonGroup(source_box)
        self.layout_radio = QRadioButton(tr("A print layout"), source_box)
        self.layout_radio.setChecked(True)
        self.canvas_radio = QRadioButton(tr("Current canvas (coming soon)"), source_box)
        self.canvas_radio.setEnabled(False)
        self.canvas_radio.setToolTip(
            tr(
                "One-click canvas mode - building a layout for you from what is "
                "on screen - is coming in a later version."
            )
        )
        self._source_group.addButton(self.layout_radio)
        self._source_group.addButton(self.canvas_radio)
        source_layout.addWidget(self.layout_radio)
        source_layout.addWidget(self.canvas_radio)

        self.layout_combo = QComboBox(source_box)
        self.layout_combo.currentTextChanged.connect(self._on_layout_combo_changed)
        source_layout.addWidget(self.layout_combo)

        self.layout_empty_note = FittedLabel(
            tr(
                "This project has no print layouts yet. Create one from the "
                "Layout Manager (Project → Layouts → New Layout), add a "
                "map to it, then come back here."
            ),
            source_box,
        )
        self.layout_empty_note.setWordWrap(True)
        self.layout_empty_note.setVisible(False)
        source_layout.addWidget(self.layout_empty_note)
        layout.addWidget(source_box)

        file_box = QGroupBox(tr("Cartograph file"), page)
        file_form = QFormLayout(file_box)
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit(file_box)
        self.file_edit.setPlaceholderText(tr("Choose where the cartograph is saved"))
        self.file_edit.textEdited.connect(self._on_file_edited)
        file_row.addWidget(self.file_edit, 1)
        choose = QPushButton(tr("Choose…"), file_box)
        choose.clicked.connect(self._on_choose_file)
        file_row.addWidget(choose)
        file_form.addRow(tr("Save to"), file_row)
        layout.addWidget(file_box)

        self.cartograph_status_label = FittedLabel("", page)
        self.cartograph_status_label.setWordWrap(True)
        layout.addWidget(self.cartograph_status_label)
        self._update_status_label()

        layout.addStretch(1)
        return page

    def _on_layout_combo_changed(self, name: str) -> None:
        self._select_layout(name)

    def _on_file_edited(self, text: str) -> None:
        if self.state.cartograph_layout:
            self.state.cartograph_files[self.state.cartograph_layout] = text.strip()
        self._on_cartograph_setting_changed()
        self._update_share_enabled()

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
        self.file_edit.setText(str(path))

    # ---- Frames tab -----------------------------------------------------

    def build_frames_tab(self) -> QWidget:
        page = QWidget(self.dialog)
        layout = QVBoxLayout(page)

        note = FittedLabel(
            tr(
                "One row per map on the selected layout. Live frames render in "
                "the browser and need a CRS the runtime can map; Static frames "
                "export a raster image at the DPI you choose."
            ),
            page,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.frames_tree = QTreeWidget(page)
        self.frames_tree.setColumnCount(4)
        self.frames_tree.setHeaderLabels(
            [tr("Map item"), tr("CRS"), tr("Frame"), tr("DPI")]
        )
        self.frames_tree.setRootIsDecorated(False)
        self.frames_tree.setUniformRowHeights(True)
        layout.addWidget(self.frames_tree)

        self.frames_empty_note = FittedLabel(
            tr("This layout has no maps on it yet."), page
        )
        self.frames_empty_note.setWordWrap(True)
        self.frames_empty_note.setVisible(False)
        layout.addWidget(self.frames_empty_note)

        layout.addStretch(1)
        return page

    def _rebuild_frames_rows(self) -> None:
        self.frames_tree.clear()
        self._frame_rows.clear()
        if self._current_layout is None:
            self.frames_empty_note.setVisible(False)
            return

        items = layout_map_items(self._current_layout)
        self.frames_empty_note.setVisible(not items)
        choices = self.state.frame_choices(self.state.cartograph_layout)

        for frame_item in items:
            choice = choices.get(frame_item.item_id)
            if choice is None:
                choice = FrameChoice(mode=default_frame_mode(frame_item.crs_authid))
                choices[frame_item.item_id] = choice

            row = QTreeWidgetItem(
                [frame_item.name, frame_item.crs_authid or tr("Unknown CRS"), "", ""]
            )
            self.frames_tree.addTopLevelItem(row)

            mode_combo = QComboBox(self.frames_tree)
            mode_combo.addItem(tr("Live"), "live")
            mode_combo.addItem(tr("Static"), "static")
            mode_combo.setCurrentIndex(0 if choice.mode == "live" else 1)
            self.frames_tree.setItemWidget(row, 2, mode_combo)

            dpi_combo = QComboBox(self.frames_tree)
            for dpi in DPI_CHOICES:
                dpi_combo.addItem(str(dpi), dpi)
            found = dpi_combo.findData(choice.dpi)
            dpi_combo.setCurrentIndex(found if found >= 0 else 0)
            dpi_combo.setEnabled(choice.mode == "static")
            self.frames_tree.setItemWidget(row, 3, dpi_combo)

            item_id = frame_item.item_id
            mode_combo.currentIndexChanged.connect(
                lambda _index, item_id=item_id, mode=mode_combo, dpi=dpi_combo: (
                    self._on_frame_mode_changed(item_id, mode, dpi)
                )
            )
            dpi_combo.currentIndexChanged.connect(
                lambda _index, item_id=item_id, dpi_combo=dpi_combo: (
                    self._on_frame_dpi_changed(item_id, dpi_combo)
                )
            )
            self._frame_rows[item_id] = {"mode": mode_combo, "dpi": dpi_combo}

        for column in range(4):
            self.frames_tree.resizeColumnToContents(column)

    def _on_frame_mode_changed(
        self, item_id: str, mode_combo: QComboBox, dpi_combo: QComboBox
    ) -> None:
        mode = mode_combo.currentData()
        self.state.frame_choices(self.state.cartograph_layout)[item_id].mode = mode
        dpi_combo.setEnabled(mode == "static")
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

        names = [pl.name() for pl in manager.printLayouts()]

        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        self.layout_combo.addItems(names)
        self.layout_combo.blockSignals(False)
        self.layout_combo.setEnabled(bool(names))
        self.layout_empty_note.setVisible(not names)

        target = (
            self.state.cartograph_layout
            if self.state.cartograph_layout in names
            else (names[0] if names else "")
        )
        if target:
            self.layout_combo.blockSignals(True)
            self.layout_combo.setCurrentText(target)
            self.layout_combo.blockSignals(False)
        self._select_layout(target)

    def _on_layouts_changed(self, *_args: object) -> None:
        self.refresh_layouts()

    def _on_layout_renamed(self, *_args: object) -> None:
        self.refresh_layouts()

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

    def _select_layout(self, name: str) -> None:
        self._disconnect_current_layout()
        manager = self.project.layoutManager()
        layout = manager.layoutByName(name) if name else None
        self._current_layout = layout
        self.state.cartograph_layout = name or ""

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

        self._rebuild_frames_rows()
        self._update_file_default()
        self._update_status_label()
        self._update_share_enabled()
        save_state(self.project, self.state)

    def _on_layout_content_changed(self) -> None:
        if self._current_layout is not None:
            current_ids = {
                item.item_id for item in layout_map_items(self._current_layout)
            }
            if current_ids != set(self._frame_rows.keys()):
                self._rebuild_frames_rows()
        self.schedule_autosave()

    def _update_file_default(self) -> None:
        name = self.state.cartograph_layout
        if not name:
            self.file_edit.setText("")
            return
        stored = self.state.cartograph_file_for(name)
        if not stored:
            stored = str(default_cartograph_path(self.project.fileName(), name))
            self.state.cartograph_files[name] = stored
        self.file_edit.setText(stored)

    def _on_cartograph_setting_changed(self) -> None:
        save_state(self.project, self.state)
        self.schedule_autosave()

    # ---- Autosave ----------------------------------------------------------

    def schedule_autosave(self) -> None:
        """Debounce a rebuild ~600 ms after the last change. Restarting an
        already-running `QTimer` is what gives a burst of edits one write."""
        self._autosave_timer.start()

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
        if self._current_layout is None:
            return
        path_text = self.file_edit.text().strip()
        if not path_text:
            return
        if self._job is not None or self.dialog._job is not None:
            # A build, or a web-map job, is already reading the project - and
            # two threads walking the same layers is what takes QGIS down.
            # Try again after another debounce; nothing is lost.
            self._autosave_timer.start()
            return

        runtime = self._load_runtime()
        if runtime is None:
            self._update_status_label()
            return

        path = Path(path_text)
        layout = self._current_layout
        project = self.project
        choices = dict(self.state.frame_choices(self.state.cartograph_layout))
        frame_modes = {item_id: choice.mode for item_id, choice in choices.items()}
        frame_dpis = {item_id: choice.dpi for item_id, choice in choices.items()}

        from ...core.fidelity_report import FidelityReportBuilder

        report = FidelityReportBuilder()

        # The layout half here, on the GUI thread: it hides and renders the
        # layout's own items, and the layout may be open in its designer.
        # Only the layer-reading half goes to the worker.
        try:
            prepared = cartograph_export.prepare_cartograph(
                layout,
                project,
                runtime,
                report,
                frame_modes=frame_modes,
                frame_dpis=frame_dpis,
                static_dpi=DEFAULT_STATIC_DPI,
            )
        except Exception as exc:
            self._on_build_failed(str(exc), traceback.format_exc())
            return
        print_settings = self._print_settings()
        if print_settings is not None:
            prepared = dataclasses.replace(
                prepared, spec=apply_to_spec(prepared.spec, print_settings)
            )
        self._last_page_label = page_label(prepared.spec)

        def work(_progress: object) -> str:
            return cartograph_export.finish_cartograph(prepared, project, report)

        job = BackgroundJob(work, self.dialog)
        self._job = job
        job.succeeded.connect(lambda html: self._on_build_succeeded(path, html, report))
        job.failed.connect(self._on_build_failed)
        job.finished.connect(job.deleteLater)
        job.start()

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
        self, path: Path, html: str, report: FidelityReportBuilder
    ) -> None:
        self._job = None
        try:
            self._write_atomic(path, html)
        except OSError as exc:
            self._last_error = tr("Could not save the cartograph: {error}").format(
                error=str(exc)
            )
            self._update_status_label()
            return
        self._last_error = ""
        self._last_saved_at = time.time()
        self._last_saved_bytes = path.stat().st_size
        self._last_summary = summarise(report.items)
        if self._print_after_save:
            self._print_after_save = False
            self._open_url(
                self._served_url(path, "print=1") or QUrl.fromLocalFile(str(path))
            )
        self._update_status_label()
        self._update_share_enabled()
        if self.state.output is DialogOutput.CARTOGRAPH:
            self.show_fidelity()

    def _on_build_failed(self, message: str, details: str) -> None:
        self._job = None
        from qgis.core import Qgis, QgsMessageLog

        QgsMessageLog.logMessage(
            details, "QGIS2WebMap", level=Qgis.MessageLevel.Warning
        )
        self._last_error = tr("Could not build the cartograph: {message}").format(
            message=message
        )
        self._update_status_label()

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        write_text_atomic(path, content)

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
            )
        )

    @property
    def has_report(self) -> bool:
        return self._last_summary is not None

    def show_fidelity(self) -> None:
        """Put the last saved cartograph's report on the Fidelity tab and
        strip. Its own report, never the web map's: the dialog calls this
        whenever Cartograph is the output on screen."""
        panel = self.dialog.fidelity_panel
        strip = self.dialog.fidelity_strip
        if self._last_summary is None:
            panel.show_not_checked()
            strip.show_message(
                strip_message(ReportState.NOT_CHECKED, tr=tr, plural=plural)
            )
            return
        panel.show_summary(self._last_summary)
        panel.set_out_of_date(False)
        strip.show_message(
            strip_message(ReportState.CURRENT, self._last_summary, tr=tr, plural=plural)
        )

    # ---- Corner: Accessibility, Preview, Share ----------------------------

    def build_corner(self) -> QWidget:
        if self._corner is not None:
            return self._corner

        corner = QWidget(self.dialog)
        layout = QHBoxLayout(corner)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(6)

        self.accessibility_button = QToolButton(corner)
        self.accessibility_button.setText(tr("Accessibility"))
        self.accessibility_button.setProperty("role", "corner")
        self.accessibility_button.setCheckable(True)
        self.accessibility_button.setEnabled(False)
        self.accessibility_button.setToolTip(tr("Coming in a later version"))
        layout.addWidget(self.accessibility_button)

        self.cartograph_preview_button = QToolButton(corner)
        self.cartograph_preview_button.setText(tr("Preview"))
        self.cartograph_preview_button.setProperty("role", "corner")
        self.cartograph_preview_button.setToolTip(
            tr("Open the cartograph in your browser.")
        )
        self.cartograph_preview_button.setIconSize(QSize(15, 15))
        self.cartograph_preview_button.clicked.connect(self.preview)
        layout.addWidget(self.cartograph_preview_button)

        self.cartograph_share_button = QToolButton(corner)
        self.cartograph_share_button.setText(tr("Open in browser"))
        self.cartograph_share_button.setProperty("role", "corner")
        self.cartograph_share_button.setProperty("hasMenu", True)
        self.cartograph_share_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        self.cartograph_share_button.setMenu(self.build_share_menu())
        self.cartograph_share_button.clicked.connect(self.on_open_in_browser)
        layout.addWidget(self.cartograph_share_button)

        self._corner = corner
        self._update_share_enabled()
        return corner

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
                    "saved. Choose a layout and a file on the Cartograph tab."
                )
            )
            return
        self._open_url(self._served_url(path) or QUrl.fromLocalFile(str(path)))

    def _served_url(self, path: Path, query: str = "") -> QUrl | None:
        """Serve `path`'s folder locally, same as the web map's preview path.

        `QWebEngineView` cannot create a WebGL context inside QGIS (see
        `ui/preview.py`), so this opens the system browser exactly as the web
        map's Preview does - a stable local URL rather than `file://`, which
        some browsers refuse to run WebGL content from at all.
        """
        try:
            root = path.parent.resolve()
            if self._preview_server is None or self._preview_server.root != root:
                if self._preview_server is not None:
                    self._preview_server.stop()
                self._preview_server = PreviewServer(root)
                self._preview_server.start()
            url = QUrl(f"http://{BIND_HOST}:{self._preview_server.port}/{path.name}")
            if query:
                url.setQuery(query)
            return url
        except OSError:
            return None

    @staticmethod
    def _open_url(url: QUrl) -> None:
        QDesktopServices.openUrl(url)

    # ---- Share menu --------------------------------------------------------

    def build_share_menu(self) -> QMenu:
        menu = QMenu(self.dialog)
        menu.setToolTipsVisible(True)
        self.cartograph_share_actions: dict[str, QAction] = {}

        def add(key: str, label: str, tip: str, slot, shortcut: str = "") -> QAction:
            action = QAction(label, self.dialog)
            action.setToolTip(tip)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            self.cartograph_share_actions[key] = action
            return action

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
        add(
            "open_browser",
            tr("Open in browser"),
            tr("Opens the cartograph in your browser"),
            self.on_open_in_browser,
            "Ctrl+O",
        )

        menu.addSection(tr("Publish"))
        add(
            "publish",
            tr("Publish with NIKA hosting... (waitlist)"),
            tr(
                "Hosting is not live yet. This opens a short form so we can see "
                "how many people want it."
            ),
            self.dialog.on_publish,
        )

        # Out of scope for this task - the hand-off dialog (issue #3 S4) - so
        # these stay disabled, as they already were on the web-map menu.
        menu.addSection(tr("NIKA"))
        add(
            "enhance",
            tr("Enhance with NIKA AI..."),
            tr("Opens a hand-off dialog with a prompt to paste into NIKA"),
            lambda: self.on_handoff("enhance"),
        )
        add(
            "critique",
            tr("Ask NIKA for a critique..."),
            tr("Opens a hand-off dialog with a prompt to paste into NIKA"),
            lambda: self.on_handoff("critique"),
        )

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
            tr("300 dpi image of the first page"),
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

        return menu

    def _update_share_enabled(self) -> None:
        if not hasattr(self, "cartograph_share_actions"):
            return
        exists = self._file_exists()
        for key in (
            "copy_path",
            "reveal",
            "open_browser",
            "export_pdf",
            "export_png",
            "enhance",
            "critique",
        ):
            action = self.cartograph_share_actions.get(key)
            if action is not None:
                action.setEnabled(exists)
        for key in ("export_qpt", "print_settings"):
            action = self.cartograph_share_actions.get(key)
            if action is not None:
                action.setEnabled(self._current_layout is not None)

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
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        self.dialog.status_label.setText(tr("Opened {path}").format(path=path.parent))

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
        self._open_url(
            self._served_url(path, "print=1") or QUrl.fromLocalFile(str(path))
        )

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
            mode = choice.mode if choice else default_frame_mode(item.crs_authid)
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
        if action == ACTION_EXPORT:
            if settings.uses_qgis_exporter:
                path = self._current_path()
                if path is not None:
                    self._export_pdf_through_qgis(settings, path)
                self.schedule_autosave()
            else:
                # The browser must print the file with these settings in it,
                # so print once the save that applies them has landed.
                self._print_after_save = True
                self.schedule_autosave()
        else:
            self.schedule_autosave()

    def _export_pdf_through_qgis(self, settings: PrintSettings, path: Path) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        from ...exporters.print_pdf import PrintPdfError, export_print_pdf

        if self._current_layout is None:
            return
        if self._has_live_frame():
            QMessageBox.information(
                self.dialog,
                tr("Export PDF"),
                tr(
                    "A CMYK or ICC PDF is made by QGIS from the print layout, "
                    "which only works when every frame is static. Set the live "
                    "frames to Static on the Frames tab, or choose RGB in Print "
                    "settings."
                ),
            )
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self.dialog,
            tr("Export PDF"),
            str(path.with_suffix(".pdf")),
            tr("PDF (*.pdf)"),
        )
        if not chosen:
            return
        try:
            result = export_print_pdf(self._current_layout, settings, Path(chosen))
        except PrintPdfError as exc:
            QMessageBox.warning(self.dialog, tr("Export PDF"), str(exc))
            return
        message = tr("Saved {path}").format(path=result.path)
        if result.warnings:
            message += " - " + " ".join(result.warnings)
        self.dialog.status_label.setText(message)

    def on_export_png(self) -> None:
        path = self._current_path()
        if path is None or not path.exists():
            return
        self._open_url(
            self._served_url(path, "export=png&dpi=300")
            or QUrl.fromLocalFile(str(path))
        )

    def on_export_qpt(self) -> None:
        if self._current_layout is None:
            return
        start = str(
            Path(self.project.fileName() or str(Path.home())).with_name(
                f"{self.state.cartograph_layout or 'layout'}.qpt"
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

        self._current_layout.saveAsTemplate(chosen, QgsReadWriteContext())
        self.dialog.status_label.setText(tr("Saved {path}").format(path=chosen))
