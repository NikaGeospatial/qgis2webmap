"""Closing the window: pending cartograph saves are finished, and a worker still
running is never destroyed with it.

These drive the real dialog, controller and plugin and look at the result: the
HTML on disk after the window has gone, the text the user is shown, and whether
the process survived. A thread destroyed while running aborts the interpreter, so
those cases run in a subprocess (`close_lifecycle_probe.py`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from close_lifecycle_fixtures import (
    FakeIface,
    LabelExport,
    add_survey_layout,
    edit_label,
    make_folder_unwritable,
    open_saved_cartograph,
)

qgis_core = pytest.importorskip("qgis.core")
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtTest import QTest  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication, QMessageBox  # noqa: E402

PROBE = Path(__file__).with_name("close_lifecycle_probe.py")


def _wait_for(qgis_app, predicate, timeout=15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        qgis_app.processEvents()
        time.sleep(0.02)
    return predicate()


def _click_close(dialog) -> None:
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)


def _failure_box() -> QMessageBox | None:
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QMessageBox) and widget.isVisible():
            return widget
    return None


def _answer(qgis_app, wanted: str) -> bool:
    """Click the button called `wanted` on the question the window asks when
    saving fails. Bounded: False if no such question appears."""
    if not _wait_for(qgis_app, lambda: _failure_box() is not None, timeout=15):
        return False
    box = _failure_box()
    for button in box.buttons():
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
def saved(qgis_app, project, export, tmp_path):
    """A shown dialog on Cartograph; the first save of label "Old" has landed."""
    target = tmp_path / "survey.html"
    dialog, cartograph, layout, label = open_saved_cartograph(
        qgis_app, project, target, _wait_for
    )
    assert "Old" in target.read_text(encoding="utf-8")
    yield dialog, cartograph, layout, label, target
    dialog.close()


class TestCloseFinishesThePendingSave:
    def test_an_edit_followed_straight_by_close_reaches_the_file(
        self, qgis_app, saved
    ) -> None:
        dialog, _cartograph, layout, label, target = saved
        edit_label(layout, label, "New")
        _click_close(dialog)
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())
        assert "New" in target.read_text(encoding="utf-8")

    def test_the_window_manager_close_and_escape_finish_it_too(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        for index, close in enumerate(
            (lambda d: d.close(), lambda d: QTest.keyClick(d, Qt.Key.Key_Escape))
        ):
            target = tmp_path / f"survey-{index}.html"
            project.clear()
            dialog, _c, layout, label = open_saved_cartograph(
                qgis_app, project, target, _wait_for
            )
            edit_label(layout, label, f"Edit{index}")
            close(dialog)
            assert _wait_for(qgis_app, lambda d=dialog: not d.isVisible())
            assert f"Edit{index}" in target.read_text(encoding="utf-8")

    def test_an_edit_made_while_an_older_build_runs_is_saved_too(
        self, qgis_app, saved, export
    ) -> None:
        dialog, _cartograph, layout, label, target = saved
        export.worker_started.clear()
        export.hold_workers()
        edit_label(layout, label, "Older")
        assert _wait_for(qgis_app, export.worker_started.is_set), "build never ran"
        edit_label(layout, label, "Newest")
        _click_close(dialog)
        # The older build ends; the window may only go once the newer is saved.
        export.release()
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())
        assert "Newest" in target.read_text(encoding="utf-8")

    def test_closing_with_the_older_build_still_running_waits_for_it(
        self, qgis_app, saved, export
    ) -> None:
        dialog, _cartograph, layout, label, target = saved
        export.worker_started.clear()
        export.hold_workers()
        edit_label(layout, label, "Older")
        assert _wait_for(qgis_app, export.worker_started.is_set)
        edit_label(layout, label, "Newest")
        _click_close(dialog)
        _wait_for(qgis_app, lambda: False, timeout=0.5)
        assert dialog.isVisible(), "closed while the save was still running"
        assert "Newest" not in target.read_text(encoding="utf-8")
        export.release()
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())
        assert "Newest" in target.read_text(encoding="utf-8")

    def test_opening_it_again_while_the_close_waits_keeps_it_open(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        """Ctrl+Alt+W (or the menu) while the window waits to close on a save:
        it stays open, and the save still lands."""
        import nika_onlymap_exporter

        plugin = nika_onlymap_exporter.classFactory(FakeIface())
        plugin.initGui()
        try:
            layout, label = add_survey_layout(project)
            plugin.run_cartograph(layout.name())
            dialog = plugin.dialog
            cartograph = dialog._cartograph
            target = tmp_path / "kept.html"
            cartograph.show_layout_named(layout.name())
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            assert _wait_for(
                qgis_app,
                lambda: (
                    target.exists()
                    and cartograph._job is None
                    and not cartograph._autosave_timer.isActive()
                ),
            )
            export.worker_started.clear()
            export.hold_workers()
            edit_label(layout, label, "Kept")
            assert _wait_for(qgis_app, export.worker_started.is_set)
            _click_close(dialog)
            assert dialog.isVisible()

            plugin.run()
            export.release()
            assert _wait_for(qgis_app, lambda: "Kept" in _text(target))
            _wait_for(qgis_app, lambda: False, timeout=0.5)

            assert plugin.dialog is dialog and dialog.isVisible()
            assert dialog.close_button.isEnabled()
        finally:
            export.release()
            plugin.unload()

    def test_the_window_says_it_is_saving_while_the_close_waits(
        self, qgis_app, saved, export
    ) -> None:
        dialog, cartograph, layout, label, _target = saved
        export.worker_started.clear()
        export.hold_workers()
        edit_label(layout, label, "New")
        _click_close(dialog)
        assert _wait_for(qgis_app, export.worker_started.is_set)
        qgis_app.processEvents()
        assert dialog.isVisible()
        shown = (
            dialog.status_label.text() + " " + cartograph.cartograph_status_label.text()
        )
        assert "Saving" in shown
        assert "Saved" not in cartograph.cartograph_status_label.text()
        export.release()
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())

    def test_a_window_with_nothing_pending_closes_at_once(
        self, qgis_app, saved
    ) -> None:
        dialog, *_rest = saved
        _click_close(dialog)
        assert not dialog.isVisible()

    @pytest.mark.skipif(
        not os.environ.get("ONLYMAP_RUNTIME_DIR"), reason="needs ONLYMAP_RUNTIME_DIR"
    )
    def test_the_real_export_has_the_new_label_after_close(
        self, qgis_app, project, tmp_path
    ) -> None:
        from cartograph_fixtures import build_survey_layout

        from nika_onlymap_exporter.core.settings import DialogOutput
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        survey = build_survey_layout(project, tmp_path)
        project.layoutManager().addLayout(survey.layout)
        dialog = MainDialog(FakeIface(), None)
        dialog.show()
        try:
            dialog.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
            cartograph = dialog._cartograph
            cartograph.show_layout_named(survey.layout.name())
            target = tmp_path / "real.html"
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            assert _wait_for(
                qgis_app,
                lambda: (
                    target.exists()
                    and cartograph._job is None
                    and not cartograph._autosave_timer.isActive()
                ),
                timeout=90,
            )
            edit_label(survey.layout, survey.title_label, "Zebra crossing survey")
            _click_close(dialog)
            assert _wait_for(qgis_app, lambda: not dialog.isVisible(), timeout=90)
            assert "Zebra crossing survey" in target.read_text(encoding="utf-8")
        finally:
            dialog.close()


class TestCloseWhenTheSaveFails:
    def _break_destination(self, monkeypatch, target: Path) -> None:
        """A folder nothing can be written to. (A folder where the file
        belongs is no longer a failure: the cartograph saves beside it. A
        read-only folder is no failure for root, as in the QGIS CI container,
        so the write is made to fail instead.)"""
        self._repair = make_folder_unwritable(monkeypatch, target.parent)

    def _repair_destination(self, target: Path) -> None:
        self._repair()

    def test_it_explains_and_a_retry_after_the_fix_closes_with_the_edit(
        self, qgis_app, saved, monkeypatch
    ) -> None:
        dialog, cartograph, layout, label, target = saved
        self._break_destination(monkeypatch, target)
        edit_label(layout, label, "New")
        _click_close(dialog)
        assert _wait_for(qgis_app, lambda: _failure_box() is not None)
        box = _failure_box()
        assert dialog.isVisible()
        assert target.name in (box.text() + box.informativeText()) or "save" in (
            box.text().lower()
        )
        assert "Saved" not in cartograph.cartograph_status_label.text()
        self._repair_destination(target)
        assert _answer(qgis_app, "Try again")
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())
        assert "New" in target.read_text(encoding="utf-8")

    def test_the_status_never_says_saved_while_the_file_is_behind(
        self, qgis_app, saved, monkeypatch
    ) -> None:
        dialog, cartograph, layout, label, target = saved
        self._break_destination(monkeypatch, target)
        seen: list[str] = []
        edit_label(layout, label, "New")
        _click_close(dialog)
        end = time.monotonic() + 3
        while time.monotonic() < end:
            qgis_app.processEvents()
            seen.append(cartograph.cartograph_status_label.text())
            time.sleep(0.02)
        assert seen and not any(text.startswith("Saved") for text in seen), seen
        assert _answer(qgis_app, "Cancel closing")

    def test_cancelling_the_close_leaves_the_window_usable(
        self, qgis_app, saved, monkeypatch
    ) -> None:
        dialog, cartograph, layout, label, target = saved
        self._break_destination(monkeypatch, target)
        edit_label(layout, label, "New")
        _click_close(dialog)
        assert _answer(qgis_app, "Cancel closing")
        qgis_app.processEvents()
        assert dialog.isVisible()
        assert dialog.close_button.isEnabled()
        assert not cartograph.cartograph_status_label.text().startswith("Saved")
        # Usable: fix the destination, edit again, and the autosave works.
        self._repair_destination(target)
        edit_label(layout, label, "Later")
        assert _wait_for(qgis_app, lambda: "Later" in _text(target), timeout=20)
        # And closing works again, with nothing pending.
        _click_close(dialog)
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())

    def test_close_without_saving_closes_and_leaves_the_file_as_it_was(
        self, qgis_app, saved, monkeypatch
    ) -> None:
        dialog, _cartograph, layout, label, target = saved
        edit_label(layout, label, "New")
        self._break_destination(monkeypatch, target)
        _click_close(dialog)
        assert _answer(qgis_app, "Close without saving")
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())
        assert "Old" in target.read_text(encoding="utf-8")
        assert "New" not in target.read_text(encoding="utf-8")

    def test_a_save_that_never_finishes_is_offered_the_same_choices(
        self, qgis_app, saved, export, monkeypatch
    ) -> None:
        import nika_onlymap_exporter.ui.main_dialog as main_dialog_module

        monkeypatch.setattr(main_dialog_module, "CLOSE_SAVE_LIMIT_MS", 500)
        dialog, _cartograph, layout, label, target = saved
        export.worker_started.clear()
        export.hold_workers()
        edit_label(layout, label, "New")
        _click_close(dialog)
        assert _wait_for(qgis_app, lambda: _failure_box() is not None)
        export.release()
        assert _answer(qgis_app, "Try again")
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())
        assert "New" in _text(target)


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class TestAnEditThenALanguageRebuild:
    def test_the_pending_edit_is_saved_before_the_window_is_rebuilt(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        import nika_onlymap_exporter

        plugin = nika_onlymap_exporter.classFactory(FakeIface())
        plugin.initGui()
        layout, label = add_survey_layout(project)
        plugin.run_cartograph(layout.name())
        old = plugin.dialog
        cartograph = old._cartograph
        target = tmp_path / "lang.html"
        cartograph.show_layout_named(layout.name())
        cartograph.file_edit.setText(str(target))
        cartograph._on_file_edited(str(target))
        assert _wait_for(
            qgis_app,
            lambda: (
                target.exists()
                and cartograph._job is None
                and not cartograph._autosave_timer.isActive()
            ),
        )
        edit_label(layout, label, "Bonjour")
        plugin._reopen_dialog()
        assert _wait_for(qgis_app, lambda: plugin.dialog is not None)
        assert _wait_for(qgis_app, lambda: plugin.dialog is not old)
        assert "Bonjour" in target.read_text(encoding="utf-8")
        plugin.unload()


class TestTheRuntimeIsAskedForOnlyWhenTheUserActs:
    def test_edits_and_closing_never_ask(
        self, qgis_app, project, export, tmp_path, monkeypatch
    ) -> None:
        """Picking a layout may ask for the runtime once, Check now always;
        the autosave after an edit and the save a close waits for never put
        the licence or a download on screen."""
        import nika_onlymap_exporter.ui.cartograph.controller as controller_module
        from nika_onlymap_exporter.ui.runtime_setup import Ask

        asks: list[Ask] = []
        installed = controller_module.ensure_runtime

        def recording(parent=None, ask=Ask.ALWAYS):
            asks.append(ask)
            return installed(parent, ask)

        monkeypatch.setattr(controller_module, "ensure_runtime", recording)
        target = tmp_path / "survey.html"
        dialog, cartograph, layout, label = open_saved_cartograph(
            qgis_app, project, target, _wait_for
        )
        assert asks[0] is Ask.FIRST_TIME

        asks.clear()
        edit_label(layout, label, "New")
        assert _wait_for(qgis_app, lambda: "New" in _text(target))
        assert asks and set(asks) == {Ask.NEVER}

        asks.clear()
        cartograph.check_now()
        assert _wait_for(qgis_app, lambda: cartograph._job is None)
        assert Ask.ALWAYS in asks

        asks.clear()
        edit_label(layout, label, "Newer")
        _click_close(dialog)
        assert _wait_for(qgis_app, lambda: not dialog.isVisible())
        assert "Newer" in _text(target)
        assert asks and set(asks) == {Ask.NEVER}


class TestALayoutNotOnScreen:
    def test_its_save_counts_its_live_counts_afresh(
        self, qgis_app, project, export, tmp_path, monkeypatch
    ) -> None:
        """Nothing follows a layout's Live counts while another is on screen,
        so its own save counts them, rather than writing whatever its numbers
        last read (a "…" among them)."""
        import nika_onlymap_exporter.core.cartograph_export as export_module

        counted: dict[str, list[bool]] = {}
        installed = export_module.prepare_cartograph

        def recording(layout, *args, refresh_counts=True, **kwargs):
            counted.setdefault(layout.name(), []).append(refresh_counts)
            return installed(layout, *args, refresh_counts=refresh_counts, **kwargs)

        monkeypatch.setattr(export_module, "prepare_cartograph", recording)
        survey_file = tmp_path / "survey.html"
        dialog, cartograph, survey, label = open_saved_cartograph(
            qgis_app, project, survey_file, _wait_for
        )
        try:
            add_survey_layout(project, name="Other")
            other_file = tmp_path / "other.html"
            cartograph.show_layout_named("Other")
            cartograph.file_edit.setText(str(other_file))
            cartograph._on_file_edited(str(other_file))
            assert _wait_for(
                qgis_app, lambda: other_file.exists() and cartograph._job is None
            )
            counted.clear()

            edit_label(survey, label, "Away")

            assert _wait_for(qgis_app, lambda: "Away" in _text(survey_file))
            assert counted.get("Survey") and all(counted["Survey"])
            assert not any(counted.get("Other", []))
        finally:
            dialog.close()

    def test_its_save_waits_out_a_web_map_job(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        """An edit to a layout not on screen while the web map exports is
        saved once the export is done, not dropped."""
        survey_file = tmp_path / "survey.html"
        dialog, cartograph, survey, label = open_saved_cartograph(
            qgis_app, project, survey_file, _wait_for
        )
        try:
            add_survey_layout(project, name="Other")
            other_file = tmp_path / "other.html"
            cartograph.show_layout_named("Other")
            cartograph.file_edit.setText(str(other_file))
            cartograph._on_file_edited(str(other_file))
            assert _wait_for(
                qgis_app, lambda: other_file.exists() and cartograph._job is None
            )
            dialog._job = object()  # a web-map export reading the project

            edit_label(survey, label, "Away")
            _wait_for(qgis_app, lambda: False, timeout=1.5)
            assert "Away" not in _text(survey_file)
            dialog._job = None

            assert _wait_for(qgis_app, lambda: "Away" in _text(survey_file))
        finally:
            dialog._job = None
            dialog.close()
