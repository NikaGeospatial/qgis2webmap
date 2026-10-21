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
from pathlib import Path
from typing import TYPE_CHECKING, cast

from qgis.PyQt.QtCore import QSize, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QAction,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import cartograph_export
from ...core.atomic_write import write_text_atomic
from ...core.cartograph_ir import CartographSpec
from ...core.fidelity_summary import (
    ReportState,
    ReportSummary,
    strip_message,
    summarise,
)
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
from .frames import layout_map_items
from .layout_page import LayoutPicker, LayoutPreview, SaveLocation
from .status import page_label, status_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from ...core.cvd import Condition, LegendEntry
    from ...core.export_ir import ExportProject
    from ...core.fidelity_report import FidelityReportBuilder
    from ..main_dialog import MainDialog
    from .accessibility_dialog import AccessibilityDialog

# Debounce per the spec: a burst of edits - dragging a DPI slider would be one
# if this tab had one - collapses into one rebuild, the same ~600 ms the plan
# settles on for the document model.
AUTOSAVE_DEBOUNCE_MS = 600
STATUS_TICK_MS = 1000
# How long closing waits for a running build, as `main_dialog`'s own job.
SHUTDOWN_WAIT_MS = 5000
DPI_CHOICES = (150, 200, 300)

# The runtime's built-in themes (`core/cartograph_ir.CARTOGRAPH_THEMES`), in
# the order the Theme box offers them.
# The settings beside the page preview: wide enough for a path's tail and a
# theme name, narrow enough that the page keeps most of the tab.
PANEL_WIDTH = 300

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
    # "carto: Parcels" -> "carto-Parcels": a name a file manager shows as is.
    safe = re.sub(r"[^\w.-]+", "-", layout_name).strip("-.") or "cartograph"
    project = re.sub(r"[^\w.-]+", "-", Path(project_file_name).stem).strip("-.")
    return base_dir / (f"{project}-{safe}.html" if project else f"{safe}.html")


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
        self._frame_tree_rows: dict[str, QTreeWidgetItem] = {}

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
        self._loading_page = False
        self._colour_vision: AccessibilityDialog | None = None
        self._legend_entries: tuple[LegendEntry, ...] = ()
        self._pending_spec: CartographSpec | None = None
        # Map item uuid -> how many of its fidelity rows are not "Kept".
        self._frame_issue_counts: dict[str, int] = {}
        self._built_layers_signature = self._layers_signature()
        self._selected_name: str | None = None
        self._shut_down = False
        self._preview_server: PreviewServer | None = None

        # Rendered static frames are kept between saves; a closed project's
        # are no use to the next one and only hold memory.
        self.project.cleared.connect(cartograph_export.clear_static_frame_cache)

        self._autosave_timer = QTimer(dialog)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._run_autosave)

        self._status_timer = QTimer(dialog)
        self._status_timer.setInterval(STATUS_TICK_MS)
        self._status_timer.timeout.connect(self._on_status_tick)

        self._corner: QWidget | None = None

    # ---- Lifecycle --------------------------------------------------------

    def showing(self) -> None:
        """The dialog just switched into Cartograph. Catch up on the project."""
        self._status_timer.start()
        has_canvas = self._map_canvas() is not None
        self.layout_combo.set_canvas_available(
            has_canvas, tr("There is no map canvas to build from.")
        )
        self.page_preview.canvas_button.setEnabled(has_canvas)
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
        """The page on the left, live; the layout and its settings beside it.

        See `layout_page` for why the tab is split this way.
        """
        page = QWidget(self.dialog)
        row = QHBoxLayout(page)
        row.setSpacing(16)

        self.page_preview = LayoutPreview(page)
        self.page_preview.from_canvas_requested.connect(self.create_from_canvas)
        self.page_preview.import_requested.connect(self.on_import_cartograph)
        row.addWidget(self.page_preview, 1)

        panel = QWidget(page)
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        layout_heading = QLabel(tr("Layout"), panel)
        layout_heading.setProperty("role", "panelHeading")
        column.addWidget(layout_heading)
        self.layout_combo = LayoutPicker(panel)
        self.layout_combo.currentTextChanged.connect(self._on_layout_combo_changed)
        self.layout_combo.from_canvas_requested.connect(self.create_from_canvas)
        self.layout_combo.import_requested.connect(self.on_import_cartograph)
        column.addWidget(self.layout_combo)

        self.layout_empty_note = FittedLabel(
            tr(
                "This project has no print layouts yet. Build one from the "
                "canvas, or import a cartograph."
            ),
            panel,
        )
        self.layout_empty_note.setWordWrap(True)
        self.layout_empty_note.setProperty("role", "hint")
        self.layout_empty_note.setVisible(False)
        column.addWidget(self.layout_empty_note)
        column.addSpacing(10)

        # Everything below the list belongs to a layout; with none there is
        # nothing to set, so it is hidden rather than left to type into.
        self._layout_settings = QWidget(panel)
        settings_column = QVBoxLayout(self._layout_settings)
        settings_column.setContentsMargins(0, 0, 0, 0)
        settings_column.setSpacing(6)
        page_fields = QWidget(self._layout_settings)
        page_form = QFormLayout(page_fields)
        page_form.setContentsMargins(0, 0, 0, 0)
        page_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        page_form.setVerticalSpacing(4)
        self.title_edit = QLineEdit(page_fields)
        self.title_edit.setPlaceholderText(tr("The project title"))
        self.title_edit.setToolTip(
            tr("Shown wherever the layout uses the project title. Blank keeps it.")
        )
        self.title_edit.textEdited.connect(self._on_page_edited)
        page_form.addRow(tr("Title"), self.title_edit)
        self.attribution_edit = QLineEdit(page_fields)
        self.attribution_edit.setPlaceholderText(tr("Optional, e.g. data sources"))
        self.attribution_edit.textEdited.connect(self._on_page_edited)
        page_form.addRow(tr("Attribution"), self.attribution_edit)
        self.theme_combo = QComboBox(page_fields)
        for theme_id, label in _THEME_LABELS:
            self.theme_combo.addItem(tr(label), theme_id)
        self.theme_combo.currentIndexChanged.connect(self._on_page_edited)
        page_form.addRow(tr("Theme"), self.theme_combo)
        settings_column.addWidget(page_fields)
        settings_column.addStretch(1)

        self.file_edit = SaveLocation(self._layout_settings)
        self.file_edit.setPlaceholderText(tr("Choose where the cartograph is saved"))
        self.file_edit.textEdited.connect(self._on_file_edited)
        self.file_edit.change_requested.connect(self._on_choose_file)
        settings_column.addWidget(self.file_edit)
        self.cartograph_status_label = FittedLabel("", self._layout_settings)
        self.cartograph_status_label.setWordWrap(True)
        self.cartograph_status_label.setProperty("role", "hint")
        settings_column.addWidget(self.cartograph_status_label)
        column.addWidget(self._layout_settings, 1)
        # Hidden one by one, not as a block: the block's stretch is what keeps
        # the layout list at the top of the panel.
        self._layout_only = (page_fields, self.file_edit, self.cartograph_status_label)
        self._update_status_label()

        row.addWidget(panel)
        return page

    def create_from_canvas(self) -> None:
        """One-click mode (issue #1 S4): the canvas becomes a "carto:" print
        layout in the project, selected as this cartograph's source -- in the
        layout template the user picks (`template_picker.py`)."""
        from ...core.canvas_cartograph import cartograph_from_canvas, template_preview
        from .template_picker import ask_template

        canvas = self._map_canvas()
        if canvas is None:
            self.dialog.status_label.setText(
                tr("There is no map canvas to build a layout from.")
            )
            return
        settings = canvas.mapSettings()
        placeholders = _template_placeholders()

        def render(template_id: str, page_size: str, orientation: str, width_px: int):
            return template_preview(
                settings.extent(),
                settings.destinationCrs(),
                canvas.layers(),
                self.project,
                template_id=template_id,
                width_px=width_px,
                rotation=settings.rotation(),
                page_size=page_size,
                orientation=orientation,
                placeholders=placeholders,
            )

        choice = ask_template(render, parent=self.dialog)
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
        self.frames_tree.setColumnCount(6)
        self.frames_tree.setHeaderLabels(
            [tr("Map item"), tr("CRS"), tr("Frame"), tr("DPI"), tr("Changes"), ""]
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
        self._open_url(
            self._served_url(path, f"atlas={kind}") or QUrl.fromLocalFile(str(path))
        )

    def _show_atlas_outcome(self, report: FidelityReportBuilder, html: str) -> None:
        from ...core.export_ir import FidelityStatus
        from ...core.layout_reader import ATLAS_SUBJECT

        problems = tuple(
            row.detail
            for row in report.items
            if row.subject == ATLAS_SUBJECT
            and row.status in (FidelityStatus.BLOCKED, FidelityStatus.UNSUPPORTED)
        )
        self.atlas_tab.show_outcome(problems, pages_written="<om-atlas" in html)

    def _rebuild_frames_rows(self) -> None:
        self.frames_tree.clear()
        self._frame_rows.clear()
        self._frame_tree_rows.clear()
        if self._current_layout is None:
            self.frames_empty_note.setVisible(False)
            return

        items = layout_map_items(self._current_layout)
        self.frames_empty_note.setVisible(not items)
        choices = self.state.frame_choices(self.state.cartograph_layout)

        for frame_item in items:
            choice = choices.get(frame_item.item_id)
            if choice is None:
                choice = FrameChoice(mode=frame_item.default_mode)
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
            # Wide enough for "300" whatever the style's arrow takes.
            dpi_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            dpi_combo.setMinimumContentsLength(4)
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
            issues = self._frame_issue_counts.get(item_id)
            row.setText(4, "" if issues is None else str(issues))
            row.setToolTip(
                4,
                tr(
                    "Items on this frame the cartograph changes or leaves out; "
                    "see the Fidelity tab"
                ),
            )
            match = QToolButton(self.frames_tree)
            match.setText(tr("Use canvas view"))
            match.setToolTip(
                tr("Set this map item's extent to what the QGIS map canvas shows")
            )
            match.setEnabled(self._map_canvas() is not None)
            match.clicked.connect(
                lambda _checked=False, item_id=item_id: self._match_canvas(item_id)
            )
            self.frames_tree.setItemWidget(row, 5, match)
            self._frame_rows[item_id] = {"mode": mode_combo, "dpi": dpi_combo}
            self._frame_tree_rows[item_id] = row

        for column in range(6):
            self.frames_tree.resizeColumnToContents(column)

    def _map_canvas(self):
        iface = getattr(self.dialog, "iface", None)
        get_canvas = getattr(iface, "mapCanvas", None)
        return get_canvas() if callable(get_canvas) else None

    def _match_canvas(self, item_id: str) -> None:
        """Frames > Use canvas view: the map item takes the canvas's extent.

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
        layout.undoStack().beginCommand(item, tr("Use canvas view"))
        item.zoomToExtent(extent)
        layout.undoStack().endCommand()
        self.schedule_autosave()

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
        # The same layout again (the tab re-shown, the list refreshed) keeps
        # its last save's status and report; only another layout starts over.
        switching = name != self._selected_name
        self._selected_name = name
        self._disconnect_current_layout()
        manager = self.project.layoutManager()
        layout = manager.layoutByName(name) if name else None
        self._current_layout = layout
        self.state.cartograph_layout = name or ""
        self.page_preview.show_layout(layout)
        for widget in self._layout_only:
            widget.setVisible(layout is not None)

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
        self.atlas_tab.show_layout(layout)
        self._update_file_default()
        self._load_page_fields()
        self.update_window_title()
        if switching:
            self._forget_last_save()
        self._update_status_label()
        self._update_share_enabled()
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

    def on_project_content_changed(self) -> None:
        """A layer was restyled or its edits saved in QGIS: the cartograph
        draws those layers, so it is saved again."""
        if self.state.output is DialogOutput.CARTOGRAPH and self._current_layout:
            # The preview draws from caches the layout keeps per item; a
            # restyled layer is only redrawn once they are refreshed.
            self._current_layout.refresh()
            self.schedule_autosave()

    def _on_layout_content_changed(self) -> None:
        if self._current_layout is not None:
            current_ids = {
                item.item_id for item in layout_map_items(self._current_layout)
            }
            if current_ids != set(self._frame_rows.keys()):
                self._rebuild_frames_rows()
                self.atlas_tab.show_layout(self._current_layout)
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
        prepared = dataclasses.replace(
            prepared, spec=self._apply_page_fields(prepared.spec)
        )
        print_settings = self._print_settings()
        if print_settings is not None:
            prepared = dataclasses.replace(
                prepared, spec=apply_to_spec(prepared.spec, print_settings)
            )
        self._last_page_label = page_label(prepared.spec)
        self._pending_spec = prepared.spec

        # The dialog's own writer, so a licence key the user set reaches the
        # cartograph's live frames exactly as it reaches a web map.
        writer = self.dialog._writer()
        # The Layers tab's popup and label choices, copied: the worker must
        # not read state the GUI thread may be editing.
        layers = copy.deepcopy(dict(self.state.layers))

        def work(_progress: object) -> cartograph_export.FinishedCartograph:
            return cartograph_export.finish_cartograph_full(
                prepared, project, report, writer=writer, layer_settings=layers
            )

        job = BackgroundJob(work, self.dialog)
        self._job = job
        job.succeeded.connect(
            lambda finished: self._on_build_succeeded(path, finished, report)
        )
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
        self,
        path: Path,
        finished: cartograph_export.FinishedCartograph,
        report: FidelityReportBuilder,
    ) -> None:
        self._job = None
        if self._shut_down:
            return
        html = finished.html
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
        self._count_frame_issues(report)
        self._show_atlas_outcome(report, html)
        if self._pending_spec is not None:
            self._collect_legend_entries(self._pending_spec, finished.projects)
            if self._colour_vision is not None:
                self._colour_vision.refresh()
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
        if self._shut_down:
            return
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

    def _on_status_tick(self) -> None:
        """Once a second while Cartograph is on screen: refresh the "Saved 2 s
        ago" line, and save again when the Layers tab's popup or label choices
        changed - the same settings-polling the web map's live preview uses
        (`DialogState.snapshot`), for the one part of it a cartograph reads."""
        self._update_status_label()
        layers = self._layers_signature()
        if layers != self._built_layers_signature:
            self._built_layers_signature = layers
            self.schedule_autosave()

    def _layers_signature(self) -> str:
        import json

        return json.dumps(
            {k: v.to_dict() for k, v in self.state.layers.items()}, sort_keys=True
        )

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
            )
        )

    def _count_frame_issues(self, report: FidelityReportBuilder) -> None:
        """Per map item, the fidelity rows that are not "Kept"."""
        from ...core.export_ir import FidelityStatus

        counts: dict[str, int] = {}
        if self._current_layout is not None:
            for item in layout_map_items(self._current_layout):
                needle = f"'{item.name}'"
                counts[item.item_id] = sum(
                    1
                    for row in report.items
                    if row.status is not FidelityStatus.PRESERVED
                    and needle in row.subject
                )
        self._frame_issue_counts = counts
        for item_id, count in counts.items():
            row = self._frame_tree_rows.get(item_id)
            if row is not None:
                row.setText(4, str(count))

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

        self.cartograph_preview_button = button(tr("Preview"), "preview")
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
                ask_nika=lambda findings: self.on_handoff("critique", findings),
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
        self._open_url(self._served_url(path, query) or QUrl.fromLocalFile(str(path)))

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

        # The hand-off dialog (issue #3 S4), shared with the web map's menu.
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


def _template_placeholders() -> dict[str, str]:
    """The words a template's panels show until the user writes their own,
    in the user's language (`core/layout_templates.py` keeps the keys)."""
    return {
        "notes": tr("Describe the map here: what it shows, where the data comes from."),
        "figure": tr("Key figure"),
        "figure_note": tr("What the number means"),
        "author": tr("Author"),
    }
