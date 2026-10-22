"""The Hand off to NIKA dialog against real Qt widgets.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.
Detection is injected (`detector=`), so a test decides whether NIKA desktop is
"installed" without touching this machine's disk; the browser, the file dialogs
and the launcher are patched where they would leave the process.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")

from nika_onlymap_exporter.core.handoff_prompt import (  # noqa: E402
    HandoffRequest,
    PageInfo,
    build_prompt,
)
from nika_onlymap_exporter.core.nika_desktop import (  # noqa: E402
    DOWNLOAD_URL,
    NOT_FOUND,
    DesktopApp,
)

INSTALLED = DesktopApp(True, Path("/Applications/NIKA.app"), "0.3.6")


@pytest.fixture
def make_dialog(qgis_app, tmp_path):
    from nika_onlymap_exporter.ui.handoff_dialog import HandoffContext, HandoffDialog

    made = []

    def make(output="cartograph", task="critique", app=NOT_FOUND, **extra):
        output_file = tmp_path / "parcel-survey.html"
        output_file.write_text("<html></html>", encoding="utf-8")
        context = HandoffContext(
            output=output,
            file_path=output_file,
            source_name="Parcel survey",
            plugin_version="0.3.0",
            task=task,
            **extra,
        )
        dialog = HandoffDialog(context, detector=lambda: app)
        made.append(dialog)
        return dialog

    yield make
    for dialog in made:
        dialog.deleteLater()


def _expected(dialog, **changes) -> str:
    fields = {
        "target": "nika_desktop",
        "task": "critique",
        "output": dialog.context.output,
        "file_path": str(dialog.context.file_path),
        "source_name": "Parcel survey",
        "plugin_version": "0.3.0",
        "files": ("parcel-survey.html",),
        "cvd_findings": dialog.context.cvd_findings,
        "page": dialog.context.page,
    }
    fields.update(changes)
    return build_prompt(HandoffRequest(**fields))


class TestTask:
    @pytest.mark.parametrize("task", ["critique", "enhance"])
    def test_preselected_by_the_caller(self, make_dialog, task) -> None:
        dialog = make_dialog(task=task)
        assert dialog.task_combo.currentData() == task
        assert dialog.prompt_view.toPlainText() == _expected(dialog, task=task)

    def test_web_map_wording(self, make_dialog) -> None:
        dialog = make_dialog(output="web_map")
        assert dialog.task_combo.itemText(0) == "Critique this web map"
        assert "Load the map-authoring skill first." in dialog.prompt_view.toPlainText()


class TestPromptRegenerates:
    def test_on_task_target_and_instructions(self, make_dialog) -> None:
        dialog = make_dialog(
            cvd_findings="2 pairs fail", page=PageInfo(size="A3", atlas_pages=3)
        )
        dialog.task_combo.setCurrentIndex(dialog.task_combo.findData("enhance"))
        assert dialog.prompt_view.toPlainText() == _expected(dialog, task="enhance")

        dialog.other_target_button.click()
        assert dialog.prompt_view.toPlainText() == _expected(
            dialog, task="enhance", target="other_agent"
        )

        dialog.instructions_edit.setPlainText("Keep the logo.")
        assert dialog.prompt_view.toPlainText() == _expected(
            dialog, task="enhance", target="other_agent", extra="Keep the logo."
        )

    def test_change_files_keeps_only_the_outputs_folder(
        self, make_dialog, monkeypatch, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui import handoff_dialog

        dialog = make_dialog()
        (tmp_path / "data").mkdir()
        inside = tmp_path / "data" / "parcels.geojson"
        inside.write_text("{}", encoding="utf-8")
        elsewhere = tmp_path.parent / "elsewhere.txt"
        elsewhere.write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            handoff_dialog.QFileDialog,
            "getOpenFileNames",
            lambda *a, **k: (
                [str(dialog.context.file_path), str(inside), str(elsewhere)],
                "",
            ),
        )
        dialog.change_files_button.click()
        files = ("parcel-survey.html", "data/parcels.geojson")
        assert dialog.files == files
        assert [
            dialog.files_list.item(i).text() for i in range(dialog.files_list.count())
        ] == list(files)
        assert dialog.prompt_view.toPlainText() == _expected(dialog, files=files)
        assert "Left out 1 file" in dialog.files_note.text()


class TestCopy:
    def test_puts_the_exact_prompt_on_the_clipboard_and_flips(
        self, make_dialog
    ) -> None:
        from qgis.PyQt.QtGui import QGuiApplication

        dialog = make_dialog()
        dialog.instructions_edit.setPlainText("Line one\nLine two")
        dialog.copy_button.click()
        assert QGuiApplication.clipboard().text() == dialog.prompt()
        assert QGuiApplication.clipboard().text() == dialog.prompt_view.toPlainText()
        assert dialog.copy_button.text() == "Copied"
        assert dialog._copied_timer.isActive()
        assert dialog._copied_timer.interval() == 2000
        dialog._copied_timer.timeout.emit()
        assert dialog.copy_button.text() == "Copy prompt"


class TestDesktopApp:
    def test_absent_disables_open_and_offers_download(
        self, make_dialog, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui import handoff_dialog

        opened = []
        monkeypatch.setattr(
            handoff_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url)
        )
        dialog = make_dialog(app=NOT_FOUND)
        assert not dialog.open_button.isEnabled()
        assert not dialog.open_action.isEnabled()
        assert "Re-check" in dialog.open_button.toolTip()
        assert dialog.status_label.text().startswith("not found on this")
        assert not dialog.download_button.isHidden()

        dialog.download_button.click()
        assert [url.toString() for url in opened] == [DOWNLOAD_URL]

    def test_present_shows_version_and_opens(self, make_dialog, monkeypatch) -> None:
        from nika_onlymap_exporter.ui import handoff_dialog

        launched = []
        monkeypatch.setattr(
            handoff_dialog, "launch", lambda app: launched.append(app) or True
        )
        dialog = make_dialog(app=INSTALLED)
        assert dialog.status_label.text() == "0.3.6 found"
        assert dialog.open_button.isEnabled() and dialog.open_action.isEnabled()
        assert dialog.download_button.isHidden()
        dialog.open_button.click()
        assert launched == [INSTALLED]

    def test_recheck_finds_a_fresh_install(self, make_dialog) -> None:
        dialog = make_dialog(app=NOT_FOUND)
        dialog._detector = lambda: INSTALLED
        dialog.recheck_button.click()
        assert dialog.open_button.isEnabled()
        assert dialog.status_label.text() == "0.3.6 found"

    def test_launch_opens_a_bundle_through_the_desktop(
        self, qgis_app, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui import handoff_dialog

        opened = []
        monkeypatch.setattr(
            handoff_dialog.QDesktopServices,
            "openUrl",
            lambda url: opened.append(url.toLocalFile()) or True,
        )
        assert handoff_dialog.launch(INSTALLED)
        assert opened == ["/Applications/NIKA.app"]
        assert not handoff_dialog.launch(NOT_FOUND)

    @pytest.mark.parametrize(
        ("platform", "word"),
        [("darwin", "Mac"), ("win32", "PC"), ("linux", "computer")],
    )
    def test_not_found_names_the_platform(self, qgis_app, platform, word) -> None:
        from nika_onlymap_exporter.ui.handoff_dialog import not_found_text

        assert not_found_text(platform) == f"not found on this {word}"


class TestSave:
    def test_writes_the_prompt(self, make_dialog, monkeypatch, tmp_path) -> None:
        from nika_onlymap_exporter.ui import handoff_dialog

        target = tmp_path / "out" / "my-prompt"
        target.parent.mkdir()
        suggested = []

        def fake_save(parent, title, start, filters):
            suggested.append(start)
            return str(target), filters

        monkeypatch.setattr(handoff_dialog.QFileDialog, "getSaveFileName", fake_save)
        dialog = make_dialog(task="enhance")
        dialog.save_action.trigger()
        assert suggested == [str(tmp_path / "parcel-survey-enhance-prompt.md")]
        written = target.with_name("my-prompt.md").read_text(encoding="utf-8")
        assert written == dialog.prompt()

    def test_unwritable_target_warns_instead_of_raising(
        self, make_dialog, monkeypatch, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui import handoff_dialog

        warned = []
        (tmp_path / "is-a-folder.md").mkdir()
        monkeypatch.setattr(
            handoff_dialog.QFileDialog,
            "getSaveFileName",
            lambda *a: (str(tmp_path / "is-a-folder.md"), ""),
        )
        monkeypatch.setattr(
            handoff_dialog.QMessageBox, "warning", lambda *a: warned.append(a[2])
        )
        make_dialog().save_action.trigger()
        assert warned and "Could not save the prompt" in warned[0]


def test_the_status_is_never_clipped_at_the_minimum_width(qgis_app, tmp_path) -> None:
    """At the narrowest the dialog allows, the "not found" status reads in
    full rather than "not found on this cor"."""
    from nika_onlymap_exporter.core.nika_desktop import NOT_FOUND
    from nika_onlymap_exporter.ui.handoff_dialog import HandoffContext, HandoffDialog

    target = tmp_path / "map.html"
    target.write_text("<html></html>")
    dialog = HandoffDialog(
        HandoffContext(
            output="cartograph",
            file_path=target,
            source_name="Survey",
            plugin_version="0.3.0",
        ),
        detector=lambda *_a, **_k: NOT_FOUND,
    )
    try:
        dialog.resize(dialog.minimumWidth(), dialog.sizeHint().height())
        dialog.show()
        qgis_app.processEvents()
        label = dialog.status_label
        needed = label.fontMetrics().horizontalAdvance(label.text())
        assert label.width() >= needed or label.wordWrap()
        assert label.height() >= label.heightForWidth(label.width()) - 1
    finally:
        dialog.close()
