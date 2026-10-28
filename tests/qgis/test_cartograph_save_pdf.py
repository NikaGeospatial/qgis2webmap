"""Save PDF… as a native Cartograph action, driven through the real dialog.

The PDF is written by QGIS from a copy of the selected layout; no HTML file,
browser or print dialog is involved. Page counts are read from the written PDF.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("qgis.core")

import re

from atlas_fixtures import build_atlas_layout
from cartograph_fixtures import build_survey_layout
from print_fixtures import media_boxes_mm
from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox, QToolButton
from test_cartograph_dialog import (  # noqa: F401 - the fixture is used by name
    _choose_cartograph,
    _dialog,
    _file_manager_answers,
    _wait_for,
    fake_cartograph_export,
)

from nika_onlymap_exporter.core.print_settings import PrintSettings
from nika_onlymap_exporter.ui.cartograph import controller as controller_module
from nika_onlymap_exporter.ui.cartograph import print_settings_dialog


@pytest.fixture
def survey_dialog(qgis_app, project, tmp_path, fake_cartograph_export):  # noqa: F811
    survey = build_survey_layout(project, tmp_path)
    project.layoutManager().addLayout(survey.layout)
    dialog = _dialog(project)
    _choose_cartograph(dialog)
    dialog._cartograph.show_layout_named(survey.layout.name())
    yield dialog, survey.layout
    dialog.close()


@pytest.fixture
def chosen_pdf(monkeypatch, tmp_path):
    """The native save dialog answers with a path and records what it was
    given. `opened` collects everything handed to the desktop (a browser, a
    PDF viewer, a file manager)."""
    asked: list[tuple[object, ...]] = []
    target = tmp_path / "out" / "chosen.pdf"

    def fake(*args, **kwargs):
        asked.append(args)
        return str(target), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake)
    opened: list[object] = []
    monkeypatch.setattr(
        controller_module.QDesktopServices, "openUrl", lambda url: opened.append(url)
    )
    revealed = _file_manager_answers(monkeypatch)
    return types.SimpleNamespace(
        asked=asked, target=target, opened=opened, revealed=revealed
    )


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    """A real modal dialog would hang the run: every dialog the code under
    test could open is answered at once, and an unexpected one fails the test."""
    unexpected: list[str] = []

    def refuse(name):
        def stub(*args, **kwargs):
            unexpected.append(name)
            return ("", "") if name.startswith("QFileDialog") else 0

        return stub

    for owner, names in (
        (QFileDialog, ("getSaveFileName", "getOpenFileName", "exec")),
        (QMessageBox, ("warning", "information", "question", "critical", "exec")),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, refuse(f"{owner.__name__}.{name}"))
    yield
    assert unexpected == [], f"unexpected modal dialogs: {unexpected}"


def _button(dialog, text):
    found = [
        b
        for b in dialog.findChildren(QToolButton)
        if b.text() == text and b.isVisibleTo(dialog)
    ]
    assert len(found) == 1, text
    return found[0]


def _click(button) -> None:
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def _share_texts(dialog) -> list[str]:
    menu = _button(dialog, "Share").menu()
    return [a.text() for a in menu.actions() if a.isSeparator() or a.isVisible()]


def _share_action(dialog, text):
    menu = _button(dialog, "Share").menu()
    return next(a for a in menu.actions() if a.text() == text)


def _pdf_pages(path) -> int:
    return len(media_boxes_mm(path.read_bytes()))


def _state_of(dialog, layout):
    """What a PDF export must leave alone on the source layout."""
    stack = layout.undoStack().stack()
    return {
        "modes": {
            key: choice.mode
            for key, choice in dialog.state.frame_choices(layout.name()).items()
        },
        "selected": sorted(i.uuid() for i in layout.selectedLayoutItems()),
        "undo": (stack.index(), stack.count()),
        "items": sorted(
            (i.uuid(), round(i.pos().x(), 3), round(i.pos().y(), 3))
            for i in layout.items()
            if hasattr(i, "uuid")
        ),
        "pages": layout.pageCollection().pageCount(),
    }


class TestSavePdf:
    def test_an_rgb_pdf_has_every_page_with_no_html_and_no_browser(
        self, survey_dialog, chosen_pdf
    ) -> None:
        dialog, layout = survey_dialog
        pages = layout.pageCollection().pageCount()
        assert pages > 1
        assert not list(chosen_pdf.target.parent.glob("*.html"))

        _share_action(dialog, "Save PDF…").trigger()

        assert _pdf_pages(chosen_pdf.target) == pages
        assert chosen_pdf.opened == []  # no browser, viewer or print dialog
        assert not list(chosen_pdf.target.parent.glob("*.html"))

    def test_the_dialog_suggests_the_layout_name(
        self, survey_dialog, chosen_pdf
    ) -> None:
        dialog, layout = survey_dialog
        _share_action(dialog, "Save PDF…").trigger()
        assert chosen_pdf.asked[0][2].endswith(f"{layout.name()}.pdf")

    def test_the_source_layout_is_left_as_it_was(
        self, survey_dialog, chosen_pdf
    ) -> None:
        dialog, layout = survey_dialog
        # Interactive frames stay interactive for the HTML.
        for choice in dialog.state.frame_choices(layout.name()).values():
            choice.mode = "live"
        next(i for i in layout.items() if hasattr(i, "uuid")).setSelected(True)
        dialog.state.cartograph_print[layout.name()] = PrintSettings(
            bleed="3mm", crop_marks=True
        ).to_json()
        before = _state_of(dialog, layout)

        _share_action(dialog, "Save PDF…").trigger()

        assert chosen_pdf.target.exists()
        assert _state_of(dialog, layout) == before
        assert set(before["modes"].values()) == {"live"}

    def test_success_shows_the_name_and_working_links(
        self, survey_dialog, chosen_pdf
    ) -> None:
        dialog, _layout = survey_dialog
        autosave_line = dialog._cartograph.cartograph_status_label.text()
        _share_action(dialog, "Save PDF…").trigger()
        label = dialog.status_label
        assert "Saved PDF: chosen.pdf" in label.text()
        assert "Open PDF" in label.text() and "Show in folder" in label.text()
        # The HTML autosave line is a different label and is untouched.
        assert dialog._cartograph.cartograph_status_label.text() == autosave_line

        # Following each link, as a click on it does.
        for href in re.findall(r'href="([^"]+)"', label.text()):
            label.linkActivated.emit(href)
        # Open PDF goes to the viewer; Show in folder goes to the file
        # manager, selecting the saved PDF (never the default folder opener).
        assert [u.toLocalFile() for u in chosen_pdf.opened] == [str(chosen_pdf.target)]
        assert chosen_pdf.revealed == [
            ("ShowItems", QUrl.fromLocalFile(str(chosen_pdf.target)).toString())
        ]

    def test_disabled_with_no_layout(self, qgis_app, project) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            action = _share_action(dialog, "Save PDF…")
            assert not action.isEnabled()
            assert action.toolTip() == "Choose or create a layout first"
            assert not any(
                b.text() == "Save PDF…" for b in dialog.findChildren(QToolButton)
            )
        finally:
            dialog.close()

    def test_a_second_request_while_saving_is_ignored(
        self, survey_dialog, chosen_pdf, qgis_app
    ) -> None:
        dialog, _layout = survey_dialog
        action = _share_action(dialog, "Save PDF…")
        seen: dict[str, object] = {}

        def duplicate() -> None:
            # An exception in a Qt slot aborts the process: keep it for the test.
            try:
                seen["status"] = dialog.status_label.text()
                seen["enabled"] = action.isEnabled()
                action.trigger()
                _share_action(dialog, "Save atlas as PDF…").trigger()
            except Exception as exc:
                seen["error"] = repr(exc)

        # Fires while the first save is under way (its event processing).
        QTimer.singleShot(0, duplicate)
        action.trigger()

        assert "error" not in seen, seen
        assert seen["status"] == "Saving PDF…"
        assert seen["enabled"] is False
        assert len(chosen_pdf.asked) == 1  # one save dialog, one PDF
        assert "Saved PDF: chosen.pdf" in dialog.status_label.text()
        assert action.isEnabled()

    def test_a_cancelled_dialog_changes_nothing(
        self, survey_dialog, monkeypatch
    ) -> None:
        dialog, _layout = survey_dialog
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        dialog.status_label.setText("earlier message")
        _share_action(dialog, "Save PDF…").trigger()
        assert dialog.status_label.text() == "earlier message"
        assert _share_action(dialog, "Save PDF…").isEnabled()

    def test_a_failure_is_explained_and_the_layout_kept(
        self, survey_dialog, chosen_pdf, monkeypatch, tmp_path
    ) -> None:
        dialog, layout = survey_dialog
        # A folder where the PDF should go: the write cannot succeed.
        blocked = tmp_path / "blocked.pdf"
        blocked.mkdir()
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **k: (str(blocked), "")
        )
        shown: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda parent, title, text: shown.append(text)
        )
        before = _state_of(dialog, layout)

        _share_action(dialog, "Save PDF…").trigger()

        assert len(shown) == 1 and "blocked.pdf" in shown[0]
        assert dialog.status_label.text().startswith("Could not save the PDF")
        assert "Saved PDF" not in dialog.status_label.text()
        assert _state_of(dialog, layout) == before
        assert _share_action(dialog, "Save PDF…").isEnabled()

    def test_ctrl_p_saves_the_pdf(self, survey_dialog, chosen_pdf, qgis_app) -> None:
        dialog, layout = survey_dialog
        dialog.show()
        dialog.activateWindow()
        qgis_app.processEvents()
        QTest.keyClick(dialog, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
        assert _wait_for(qgis_app, chosen_pdf.target.exists)
        assert _pdf_pages(chosen_pdf.target) == layout.pageCollection().pageCount()
        assert chosen_pdf.opened == []  # no browser opened

    def test_the_pdf_settings_button_saves_a_pdf_with_those_settings(
        self, survey_dialog, chosen_pdf, monkeypatch
    ) -> None:
        dialog, layout = survey_dialog
        monkeypatch.setattr(
            print_settings_dialog,
            "ask_print_settings",
            lambda *a, **k: (
                print_settings_dialog.ACTION_EXPORT,
                PrintSettings(page="A3", orientation="portrait", bleed="3mm"),
            ),
        )
        _share_action(dialog, "PDF settings…").trigger()
        boxes = media_boxes_mm(chosen_pdf.target.read_bytes())
        assert len(boxes) == layout.pageCollection().pageCount()
        assert boxes[0] == pytest.approx((303.0, 426.0), abs=0.5)


class TestShareMenuAndToolbar:
    def test_header_order(self, survey_dialog) -> None:
        dialog, _layout = survey_dialog
        header = _button(dialog, "Share").parentWidget().parentWidget()
        shown = [
            b
            for b in header.findChildren(QToolButton)
            if b.property("role") == "corner" and b.isVisibleTo(header)
        ]
        names = [b.text() or b.accessibleName() for b in shown]
        assert names == ["Accessibility", "Share", "Settings", "Help"]
        assert not any(b.text() == "Preview in browser" for b in shown)

    def test_share_sections_without_an_atlas(self, survey_dialog) -> None:
        dialog, _layout = survey_dialog
        assert _share_texts(dialog)[:9] == [
            "Export",
            "Save PDF…",
            "Export PNG…",
            "Export QGIS layout template…",
            "PDF settings…",
            "HTML cartograph",
            "Preview in browser",
            "Copy HTML file path",
            "Show HTML in folder",
        ]


class TestAtlasPdf:
    @pytest.fixture
    def atlas_dialog(self, qgis_app, project, tmp_path, fake_cartograph_export):  # noqa: F811
        built = build_atlas_layout(project)
        project.layoutManager().addLayout(built.layout)
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        dialog._cartograph.show_layout_named(built.layout.name())
        yield dialog, built
        dialog.close()

    def test_the_row_leads_the_export_section(self, atlas_dialog) -> None:
        dialog, _built = atlas_dialog
        assert _share_texts(dialog)[:6] == [
            "Export",
            "Save PDF…",
            "Save atlas as PDF…",
            "Export PNG…",
            "Export QGIS layout template…",
            "PDF settings…",
        ]

    def test_saves_one_run_of_pages_per_feature_not_the_current_one(
        self, atlas_dialog, chosen_pdf
    ) -> None:
        dialog, built = atlas_dialog
        pages = built.layout.pageCollection().pageCount()
        atlas = built.layout.atlas()
        before = (atlas.currentFeatureNumber(), atlas.count())

        _share_action(dialog, "Save atlas as PDF…").trigger()

        assert _pdf_pages(chosen_pdf.target) == pages * 3
        assert (atlas.currentFeatureNumber(), atlas.count()) == before
        assert chosen_pdf.asked[0][2].endswith("-atlas.pdf")
