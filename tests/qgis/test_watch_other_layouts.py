"""Every layout that has a cartograph file stays in sync with it while the
window is open, not only the layout shown in the plugin.

The edits are made the way the QGIS layout designer makes them - through the
layout and its undo stack - to a layout the plugin is not showing, and the HTML
on disk is read.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from close_lifecycle_fixtures import (
    FakeIface,
    LabelExport,
    add_survey_layout,
    edit_label,
    make_folder_unwritable,
)

qgis_core = pytest.importorskip("qgis.core")
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtTest import QTest  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication, QMessageBox  # noqa: E402


def _wait_for(qgis_app, predicate, timeout=20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        qgis_app.processEvents()
        time.sleep(0.02)
    return predicate()


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _settled(cartograph) -> bool:
    return cartograph._job is None and not cartograph._autosave_timer.isActive()


def _failure_box() -> QMessageBox | None:
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QMessageBox) and widget.isVisible():
            return widget
    return None


def _answer(qgis_app, wanted: str) -> bool:
    if not _wait_for(qgis_app, lambda: _failure_box() is not None):
        return False
    for button in _failure_box().buttons():
        if button.text().replace("&", "") == wanted:
            button.click()
            return True
    return False


@pytest.fixture
def export(monkeypatch) -> LabelExport:
    fake = LabelExport()
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def two(qgis_app, project, export, tmp_path):
    """A shown dialog with layouts A and B, each saved to its own file; B is
    on screen. Returns a namespace-like tuple."""
    from nika_onlymap_exporter.core.settings import DialogOutput
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    layout_a, label_a = add_survey_layout(project, "A", "OldA")
    layout_b, label_b = add_survey_layout(project, "B", "OldB")
    file_a, file_b = tmp_path / "a.html", tmp_path / "b.html"
    dialog = MainDialog(FakeIface(), None)
    dialog.show()
    dialog.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
    cartograph = dialog._cartograph
    for layout, target in ((layout_a, file_a), (layout_b, file_b)):
        cartograph.show_layout_named(layout.name())
        cartograph.file_edit.setText(str(target))
        cartograph._on_file_edited(str(target))
        assert _wait_for(qgis_app, lambda t=target: t.exists() and _settled(cartograph))
    yield dialog, cartograph, (layout_a, label_a, file_a), (layout_b, label_b, file_b)
    dialog.close()


def _show(qgis_app, cartograph, layout) -> None:
    cartograph.show_layout_named(layout.name())
    qgis_app.processEvents()


def _click_close(dialog) -> None:
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)


def test_an_edit_to_a_layout_that_is_not_shown_reaches_its_file(qgis_app, two) -> None:
    _, cartograph, (layout_a, label_a, file_a), (_, _, file_b) = two
    assert cartograph.state.cartograph_layout == "B"
    edit_label(layout_a, label_a, "DesignerA")
    assert _wait_for(qgis_app, lambda: "DesignerA" in _text(file_a))
    assert cartograph.state.cartograph_layout == "B"  # never switched
    assert "OldB" in _text(file_b)


def test_a_layout_without_a_file_is_never_written(
    qgis_app, project, two, tmp_path
) -> None:
    _, cartograph, *_ = two
    layout_c, label_c = add_survey_layout(project, "C", "OldC")
    qgis_app.processEvents()
    before = set(tmp_path.rglob("*.html"))
    edit_label(layout_c, label_c, "DesignerC")
    _wait_for(qgis_app, lambda: False, timeout=2.0)
    assert set(tmp_path.rglob("*.html")) == before
    assert cartograph.state.cartograph_file_for("C") == ""
    assert cartograph.state.cartograph_layout == "B"


def test_a_layout_removed_while_its_save_is_pending_writes_nothing(
    qgis_app, project, two
) -> None:
    _, _, (layout_a, label_a, file_a), _b = two
    before = _text(file_a)
    edit_label(layout_a, label_a, "DesignerA")
    project.layoutManager().removeLayout(layout_a)
    _wait_for(qgis_app, lambda: False, timeout=2.0)
    assert _text(file_a) == before


def test_a_renamed_layout_keeps_syncing_to_its_file(qgis_app, two) -> None:
    _, _, (layout_a, label_a, file_a), _b = two
    layout_a.setName("Renamed")
    qgis_app.processEvents()
    edit_label(layout_a, label_a, "DesignerRenamed")
    assert _wait_for(qgis_app, lambda: "DesignerRenamed" in _text(file_a))


def test_edits_to_two_layouts_then_close_saves_both(qgis_app, two) -> None:
    dialog, _, (layout_a, label_a, file_a), (layout_b, label_b, file_b) = two
    edit_label(layout_a, label_a, "DesignerA")
    edit_label(layout_b, label_b, "DesignerB")
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "DesignerA" in _text(file_a)
    assert "DesignerB" in _text(file_b)


def test_a_failed_background_save_of_a_is_reported_at_close(
    qgis_app, two, monkeypatch
) -> None:
    dialog, _, (layout_a, label_a, file_a), _b = two
    repair = make_folder_unwritable(monkeypatch, file_a.parent)
    edit_label(layout_a, label_a, "DesignerA")
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: _failure_box() is not None)
    assert dialog.isVisible()
    repair()
    assert _answer(qgis_app, "Try again")
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "DesignerA" in _text(file_a)


def test_the_background_save_does_not_loop_on_its_own_changes(
    qgis_app, two, export
) -> None:
    _, cartograph, (layout_a, label_a, file_a), _b = two
    edit_label(layout_a, label_a, "DesignerA")
    assert _wait_for(qgis_app, lambda: "DesignerA" in _text(file_a))
    assert _wait_for(qgis_app, lambda: cartograph._job is None)
    started = export.worker_started
    started.clear()
    _wait_for(qgis_app, lambda: False, timeout=2.0)
    assert not started.is_set(), "the save re-triggered itself"
