"""Switching between layouts never leaves one layout's edit unsaved.

Each layout has its own file; an edit made to layout A that is still waiting for
its autosave (or behind a running older build) when the user switches to B must
still reach A's file - by the time the window has closed at the latest. These
drive the real dialog and controller and read the HTML on disk.

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


def test_edit_a_switch_to_b_before_the_debounce_then_close_saves_a(
    qgis_app, two
) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, _lb, file_b) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    edit_label(layout_a, label_a, "EditedA")
    _show(qgis_app, cartograph, layout_b)  # before A's debounce fires
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "EditedA" in _text(file_a)
    assert "OldB" in _text(file_b)


def test_an_older_build_of_a_running_when_switching_still_gets_the_newest_edit(
    qgis_app, two, export
) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, _lb, _fb) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    export.worker_started.clear()
    export.hold_workers()
    edit_label(layout_a, label_a, "OlderA")
    assert _wait_for(qgis_app, export.worker_started.is_set), "build never ran"
    edit_label(layout_a, label_a, "NewestA")
    _show(qgis_app, cartograph, layout_b)
    export.release()
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "NewestA" in _text(file_a)


def test_both_layouts_edited_then_close_saves_both(qgis_app, two) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, label_b, file_b) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    edit_label(layout_a, label_a, "EditedA")
    _show(qgis_app, cartograph, layout_b)
    edit_label(layout_b, label_b, "EditedB")
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "EditedA" in _text(file_a)
    assert "EditedB" in _text(file_b)


def test_the_closing_window_says_it_is_saving_for_the_other_layout(
    qgis_app, two, export
) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, _lb, _fb) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    export.worker_started.clear()
    export.hold_workers()
    edit_label(layout_a, label_a, "EditedA")
    _show(qgis_app, cartograph, layout_b)
    _click_close(dialog)
    assert _wait_for(qgis_app, export.worker_started.is_set)
    qgis_app.processEvents()
    assert dialog.isVisible()
    shown = dialog.status_label.text() + " " + cartograph.cartograph_status_label.text()
    assert "Saving" in shown
    assert "EditedA" not in _text(file_a)
    export.release()
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "EditedA" in _text(file_a)


def test_a_failed_write_for_a_is_reported_and_a_retry_saves_it(
    qgis_app, two, monkeypatch
) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, _lb, file_b) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    # Nothing can be written into A's folder. (A folder at A's path would not
    # do: a destination that is not A's own file is not replaced, the
    # cartograph is saved beside it instead. A read-only folder would not do
    # either: root, as in the QGIS CI container, writes into it anyway.)
    repair = make_folder_unwritable(monkeypatch, file_a.parent)
    edit_label(layout_a, label_a, "EditedA")
    _show(qgis_app, cartograph, layout_b)
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: _failure_box() is not None)
    assert dialog.isVisible()
    repair()
    assert _answer(qgis_app, "Try again")
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "EditedA" in _text(file_a)
    assert "OldB" in _text(file_b)


def test_cancelling_after_a_failure_for_a_keeps_the_window_usable(
    qgis_app, two, monkeypatch
) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, _lb, _fb) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    repair = make_folder_unwritable(monkeypatch, file_a.parent)
    edit_label(layout_a, label_a, "EditedA")
    _show(qgis_app, cartograph, layout_b)
    _click_close(dialog)
    assert _answer(qgis_app, "Cancel closing")
    qgis_app.processEvents()
    assert dialog.isVisible() and dialog.close_button.isEnabled()
    repair()
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert "EditedA" in _text(file_a)


def test_a_layout_removed_with_a_pending_save_writes_nothing_and_b_still_saves(
    qgis_app, project, two
) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, label_b, file_b) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    before = _text(file_a)
    edit_label(layout_a, label_a, "EditedA")
    _show(qgis_app, cartograph, layout_b)
    project.layoutManager().removeLayout(layout_a)
    qgis_app.processEvents()
    edit_label(layout_b, label_b, "EditedB")
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert _text(file_a) == before
    assert "EditedA" not in _text(file_a)
    assert "EditedB" in _text(file_b)


def test_a_background_save_never_replaces_a_file_that_is_not_its_own(
    qgis_app, two, export
) -> None:
    dialog, cartograph, (layout_a, label_a, file_a), (layout_b, _lb, file_b) = two
    _show(qgis_app, cartograph, layout_a)
    assert _wait_for(qgis_app, lambda: _settled(cartograph))
    export.worker_started.clear()
    export.hold_workers()
    edit_label(layout_a, label_a, "EditedA")
    _show(qgis_app, cartograph, layout_b)  # A is now saved in the background
    assert _wait_for(qgis_app, export.worker_started.is_set)
    # While that save runs, something else takes A's file: the write is
    # refused and explained, and a retry saves beside it instead.
    file_a.write_text("someone else's notes", encoding="utf-8")
    export.release()
    _click_close(dialog)
    assert _wait_for(qgis_app, lambda: _failure_box() is not None)
    assert "not this cartograph's own file" in _failure_box().text()
    assert file_a.read_text(encoding="utf-8") == "someone else's notes"
    assert _answer(qgis_app, "Try again")
    assert _wait_for(qgis_app, lambda: not dialog.isVisible())
    assert file_a.read_text(encoding="utf-8") == "someone else's notes"
    beside = file_a.with_name("a (2).html")
    assert "EditedA" in _text(beside)
    # Only A's own setting moved; B is untouched.
    assert cartograph.state.cartograph_file_for("A") == str(beside)
    assert cartograph.state.cartograph_file_for("B") == str(file_b)
    assert "OldB" in _text(file_b)
