"""Shared set-up for the closing-lifecycle tests and their subprocess probe.

A cartograph export whose HTML carries the text of the layout's labels, so a
test can read the saved file and tell which edit it holds, and whose worker half
can be held back or slowed down to build the situations closing has to cope
with. Used by `test_close_lifecycle.py` and `close_lifecycle_probe.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import threading
import time
import types
from pathlib import Path
from typing import Callable


class FakeIface:
    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class LabelExport:
    """Stands in for the cartograph export's two halves.

    The GUI half reads the labels off the layout (as the real one does, on the
    GUI thread), the worker half writes them into the HTML. `hold` keeps the
    worker half waiting until `release()`; `delay_s` makes it slow instead.
    """

    def __init__(self) -> None:
        self.hold = False
        self.delay_s = 0.0
        self._gate = threading.Event()
        self._gate.set()
        self.worker_started = threading.Event()
        self.worker_done = threading.Event()
        self.builds_started = 0

    def hold_workers(self) -> None:
        self.hold = True
        self._gate.clear()

    def release(self) -> None:
        self.hold = False
        self._gate.set()

    def install(self, monkeypatch) -> None:
        from qgis.core import QgsLayoutItemLabel

        import nika_onlymap_exporter.core.cartograph_export as export_module
        import nika_onlymap_exporter.ui.cartograph.controller as controller_module
        from nika_onlymap_exporter.core.cartograph_ir import CartographSpec
        from nika_onlymap_exporter.writers.cartograph_writer import (
            CartographRuntimeBundle,
        )

        outer = self

        def prepare_cartograph(
            layout,
            project,
            runtime,
            report,
            *,
            frame_modes,
            frame_dpis,
            static_dpi=200,
            export_dpi=None,
            title_override=None,
            pending_static=None,
            refresh_counts=True,
        ):
            labels = "|".join(
                item.text()
                for item in layout.items()
                if isinstance(item, QgsLayoutItemLabel)
            )
            outer.builds_started += 1
            return export_module.PreparedCartograph(
                spec=CartographSpec(
                    cartograph_id=f"labels:{labels}",
                    width=420.0,
                    height=297.0,
                    orientation="landscape",
                ),
                live_frames=(),
                bundle=CartographRuntimeBundle(cartograph_js=b"", cartograph_css=""),
            )

        def finish_cartograph_full(
            prepared, project, report, *, writer=None, layer_settings=None
        ):
            outer.worker_started.set()
            try:
                # Waits in slices so a held worker still ends with the test.
                deadline = time.monotonic() + 30
                while not outer._gate.is_set() and time.monotonic() < deadline:
                    outer._gate.wait(0.05)
                if outer.delay_s:
                    time.sleep(outer.delay_s)
                return export_module.FinishedCartograph(
                    html=f"<html>{prepared.spec.cartograph_id}</html>", projects=()
                )
            finally:
                outer.worker_done.set()

        class _Provider:
            version = "fake"

            def load(self):
                return types.SimpleNamespace(version="fake")

        monkeypatch.setattr(export_module, "prepare_cartograph", prepare_cartograph)
        monkeypatch.setattr(
            export_module, "finish_cartograph_full", finish_cartograph_full
        )
        monkeypatch.setattr(
            controller_module,
            "ensure_runtime",
            lambda parent=None, ask=None: _Provider(),
        )


def add_survey_layout(project, name: str = "Survey", text: str = "Old"):
    """A layout with one map frame and one label reading `text`."""
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsLayoutItemLabel,
        QgsLayoutItemMap,
        QgsPrintLayout,
    )
    from qgis.PyQt.QtCore import QRectF

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptSetSceneRect(QRectF(0, 0, 100, 100))
    map_item.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    map_item.setId("Overview")
    layout.addLayoutItem(map_item)
    label = QgsLayoutItemLabel(layout)
    label.setText(text)
    layout.addLayoutItem(label)
    project.layoutManager().addLayout(layout)
    return layout, label


def edit_label(layout, label, text: str) -> None:
    """A designer-style edit: an undo command, which is what the controller
    follows."""
    layout.undoStack().beginCommand(label, "designer edit")
    label.setText(text)
    layout.undoStack().endCommand()


def open_saved_cartograph(qgis_app, project, target: Path, wait_for, text="Old"):
    """A shown dialog on Cartograph whose first save has landed at `target`.
    Returns (dialog, controller, layout, label)."""
    from nika_onlymap_exporter.core.settings import DialogOutput
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    layout, label = add_survey_layout(project, text=text)
    dialog = MainDialog(FakeIface(), None)
    dialog.show()
    dialog.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
    cartograph = dialog._cartograph
    cartograph.show_layout_named(layout.name())
    cartograph.file_edit.setText(str(target))
    cartograph._on_file_edited(str(target))
    assert wait_for(
        qgis_app,
        lambda: (
            target.exists()
            and cartograph._job is None
            and not cartograph._autosave_timer.isActive()
        ),
    )
    return dialog, cartograph, layout, label


def make_folder_unwritable(monkeypatch, folder: Path) -> Callable[[], None]:
    """Make every cartograph save into `folder` fail with PermissionError, until
    the returned `repair()` is called; after that saves really write again.

    This replaces `folder.chmod(0o500)`, which a root user (the QGIS CI
    container) walks straight through, so the failure never happened. The
    failure is injected at the controller's single file-write step, after the
    destination ownership check, so everything the user sees - the failure
    box, the status text, the retry - is the real code running.
    """
    from nika_onlymap_exporter.ui.cartograph.controller import CartographController

    real_write = CartographController._write_atomic
    state = {"broken": True}

    def write(path: Path, content: str) -> None:
        if state["broken"] and Path(path).parent == folder:
            raise PermissionError(13, "Permission denied (injected)", str(path))
        real_write(path, content)

    monkeypatch.setattr(CartographController, "_write_atomic", staticmethod(write))

    def repair() -> None:
        state["broken"] = False

    return repair
