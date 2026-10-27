"""The layout header, "Layout actions", "Switch layout..." and its chooser.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QPoint, Qt, QTimer
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication
from test_cartograph_dialog import (
    _add_layout,
    _choose_cartograph,
    _click_new_from_template,
    _dialog,
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)

LONG_NAME = (
    "After Hours — Grand Canyon field atlas, access routes, water sources "
    "and terrain hazards for the autumn survey (second revision)"
)


def _open(project, *names):
    for name in names:
        _add_layout(project, name, [("Map", "EPSG:4326")])
    dialog = _dialog(project)
    dialog.resize(1280, 800)
    dialog.show()
    _choose_cartograph(dialog)
    cartograph = dialog._cartograph
    cartograph._autosave_timer.stop()
    return dialog, cartograph


def _with_chooser(cartograph, drive):
    """Open "Switch layout..." with a click; `drive(box)` runs while it is up."""
    out: list[object] = []
    failures: list[BaseException] = []

    def run() -> None:
        # An exception inside a Qt slot aborts the process: carry it out.
        box = QApplication.activeModalWidget()
        try:
            assert box is not None
            out.append(drive(box))
        except BaseException as exc:
            failures.append(exc)
        if box is not None and box.isVisible():
            box.reject()

    QTimer.singleShot(0, run)
    button = cartograph.layout_header.switch_button
    QTest.mouseClick(
        button, Qt.MouseButton.LeftButton, pos=QPoint(4, button.height() // 2)
    )
    if failures:
        raise failures[0]
    return out[0]


class TestHeader:
    def test_it_names_the_layout_and_describes_it(self, qgis_app, project) -> None:
        from qgis.core import QgsLayoutItemPage

        layout = _add_layout(
            project, "Grand Canyon — Field atlas", [("M", "EPSG:4326")]
        )
        layout.pageCollection().page(0).setPageSize(
            "A3", QgsLayoutItemPage.Orientation.Landscape
        )
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            header = dialog._cartograph.layout_header
            assert header.name_label.full_text() == "Grand Canyon — Field atlas"
            assert header.meta_label.text() == "A3 landscape · 1 page"
        finally:
            dialog.close()

    def test_portrait_pages_and_atlas_are_stated(self, qgis_app, project) -> None:
        from qgis.core import QgsLayoutItemPage

        layout = _add_layout(project, "Sheet", [("M", "EPSG:4326")])
        layout.pageCollection().page(0).setPageSize(
            "A4", QgsLayoutItemPage.Orientation.Portrait
        )
        page = QgsLayoutItemPage(layout)
        page.setPageSize("A4", QgsLayoutItemPage.Orientation.Portrait)
        layout.pageCollection().addPage(page)
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            header = cartograph.layout_header
            assert header.meta_label.text() == "A4 portrait · 2 pages"
            layout.atlas().setEnabled(True)
            cartograph._on_layout_content_changed()
            assert header.meta_label.text() == ("A4 portrait · 2 pages · Atlas on")
        finally:
            dialog.close()

    def test_a_long_name_takes_two_lines_and_is_whole_in_the_tooltip(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph = _open(project, LONG_NAME)
        try:
            label = cartograph.layout_header.name_label
            assert label.full_text() == LONG_NAME
            assert label.toolTip() == LONG_NAME
            assert 1 <= label.text().count("\n") + 1 <= 2
            assert label.text().endswith("…")
            assert label.height() <= 2 * label.fontMetrics().lineSpacing() + 4
        finally:
            dialog.close()

    def test_layout_actions_hold_rename_and_import(self, qgis_app, project) -> None:
        from qgis.PyQt.QtWidgets import QToolButton

        dialog, cartograph = _open(project, "Survey")
        try:
            header = cartograph.layout_header
            button = header.actions_button
            assert button.accessibleName() == "Layout actions"
            assert button.toolTip() == "Layout actions"
            assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
            assert [a.text() for a in button.menu().actions()] == [
                "Rename layout...",
                "Import cartograph...",
            ]
            # No separate Rename button is left in the panel.
            assert not hasattr(cartograph, "rename_layout_button")
            assert all(
                b.text() != "Rename..."
                for b in cartograph.workspace.findChildren(QToolButton)
            )
        finally:
            dialog.close()

    def test_switch_layout_shows_only_with_two_or_more_layouts(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph = _open(project, "One")
        try:
            switch = cartograph.layout_header.switch_button
            assert switch.isHidden()
            _add_layout(project, "Two", [("M", "EPSG:4326")])
            assert not switch.isHidden()
            assert switch.text() == "Switch layout..."
        finally:
            dialog.close()

    def test_new_from_template_is_a_plain_prominent_button(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph = _open(project, "One")
        try:
            button = cartograph.layout_header.new_from_template_button
            assert button.text() == "New from template..."
            assert button.menu() is None
            assert button.width() >= cartograph.layout_header.width() - 4
        finally:
            dialog.close()


class TestChooser:
    NAMES = tuple(f"Layout {n}" for n in range(1, 9))

    def test_it_lists_every_layout_and_marks_the_current_one(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph = _open(project, *self.NAMES)
        try:
            cartograph.show_layout_named("Layout 3")

            def drive(box):
                assert box.windowTitle() == "Layouts in this project"
                assert sorted(box.visible_names()) == sorted(self.NAMES)
                rows = {
                    box.list.item(r).data(Qt.ItemDataRole.UserRole): box.list.item(r)
                    for r in range(box.list.count())
                }
                assert rows["Layout 3"].text().splitlines()[1].startswith("Current")
                assert rows["Layout 3"].font().bold()
                assert "Current" not in rows["Layout 4"].text()
                assert "A4 landscape" in rows["Layout 4"].text()
                assert box.list.currentItem() is rows["Layout 3"]

            _with_chooser(cartograph, drive)
        finally:
            dialog.close()

    def test_search_filters_a_long_list_and_is_absent_from_a_short_one(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph = _open(project, *self.NAMES)
        try:

            def drive(box):
                assert not box.search_edit.isHidden()
                QTest.keyClicks(box.search_edit, "layout 7")
                assert box.visible_names() == ["Layout 7"]
                box.search_edit.setText("zzz")
                assert box.visible_names() == []
                assert not box.empty_note.isHidden()
                assert not box.open_button.isEnabled()

            _with_chooser(cartograph, drive)
        finally:
            dialog.close()

    def test_a_short_list_has_no_search(self, qgis_app, project) -> None:
        dialog, cartograph = _open(project, "A", "B")
        try:
            assert _with_chooser(cartograph, lambda box: box.search_edit.isHidden())
        finally:
            dialog.close()

    def test_choosing_a_row_opens_that_layout(self, qgis_app, project) -> None:
        dialog, cartograph = _open(project, *self.NAMES)
        try:
            cartograph.show_layout_named("Layout 1")

            def drive(box):
                item = next(
                    box.list.item(r)
                    for r in range(box.list.count())
                    if box.list.item(r).data(Qt.ItemDataRole.UserRole) == "Layout 6"
                )
                box.list.setCurrentItem(item)
                QTest.mouseClick(box.open_button, Qt.MouseButton.LeftButton)

            _with_chooser(cartograph, drive)
            assert cartograph.current_layout_name() == "Layout 6"
            assert cartograph._current_layout is project.layoutManager().layoutByName(
                "Layout 6"
            )
            assert dialog.state.cartograph_layout == "Layout 6"
            assert cartograph.layout_header.name_label.full_text() == "Layout 6"
        finally:
            dialog.close()

    def test_cancel_changes_nothing(self, qgis_app, project) -> None:
        dialog, cartograph = _open(project, "A", "B")
        try:
            cartograph.show_layout_named("A")
            _with_chooser(cartograph, lambda box: box.reject())
            assert cartograph.current_layout_name() == "A"
        finally:
            dialog.close()

    def test_opening_it_renders_no_layout(self, qgis_app, project, monkeypatch) -> None:
        from qgis.core import QgsLayoutExporter

        import nika_onlymap_exporter.core.cartograph_export as export_module

        dialog, cartograph = _open(project, *self.NAMES)
        try:
            calls: list[str] = []
            for name in ("renderPageToImage", "renderRegionToImage", "exportToImage"):
                monkeypatch.setattr(
                    QgsLayoutExporter, name, lambda *a, _n=name, **k: calls.append(_n)
                )
            monkeypatch.setattr(
                export_module,
                "render_static_frame",
                lambda *a, **k: calls.append("static"),
            )
            _with_chooser(cartograph, lambda box: box.visible_names())
            assert calls == []
        finally:
            dialog.close()

    def test_a_shown_layout_has_its_thumbnail_the_others_a_placeholder(
        self, qgis_app, project, monkeypatch
    ) -> None:
        from qgis.PyQt.QtGui import QImage

        from nika_onlymap_exporter.core.layout_identity import existing_key
        from nika_onlymap_exporter.ui.cartograph import layout_thumbnails

        layout_thumbnails.clear()
        dialog, cartograph = _open(project, "Shown", "Never shown")
        try:
            cartograph.show_layout_named("Shown")
            monkeypatch.setattr(layout_thumbnails, "grab_page", lambda *a: None)
            shown = project.layoutManager().layoutByName("Shown")
            image = QImage(80, 56, QImage.Format.Format_ARGB32)
            image.fill(0xFF336699)
            from nika_onlymap_exporter.core.layout_identity import layout_key

            layout_thumbnails.remember(layout_key(project, shown), image)
            other = project.layoutManager().layoutByName("Never shown")
            assert existing_key(other) is None

            def drive(box):
                icons = {
                    box.list.item(r).data(Qt.ItemDataRole.UserRole): box.list.item(r)
                    .icon()
                    .pixmap(box.list.iconSize())
                    .toImage()
                    for r in range(box.list.count())
                }
                assert icons["Shown"].pixelColor(40, 28).name() == "#336699"
                assert icons["Never shown"].pixelColor(40, 28).name() != "#336699"

            _with_chooser(cartograph, drive)
            assert existing_key(other) is None  # opening wrote nothing
        finally:
            dialog.close()
            layout_thumbnails.clear()


class TestRenameAndNaming:
    def test_rename_refreshes_header_and_chooser_and_keeps_settings(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, cartograph = _open(project, "First", "Second")
        try:
            cartograph.show_layout_named("Second")
            target = tmp_path / "second.html"
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            second = project.layoutManager().layoutByName("Second")
            cartograph._autosave_timer.stop()

            second.setName("Gorge sheet")

            header = cartograph.layout_header
            assert header.name_label.full_text() == "Gorge sheet"
            assert dialog.state.cartograph_files["Gorge sheet"] == str(target)
            assert cartograph.file_edit.text() == str(target)
            names = _with_chooser(cartograph, lambda box: sorted(box.visible_names()))
            assert names == ["First", "Gorge sheet"]
        finally:
            dialog.close()

    def test_editing_the_map_title_does_not_rename_the_layout(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph = _open(project, "Survey")
        try:
            cartograph.title_edit.setText("A new map title")
            cartograph.title_edit.textEdited.emit("A new map title")
            cartograph._autosave_timer.stop()
            assert cartograph.current_layout_name() == "Survey"
            assert project.layoutManager().layoutByName("Survey") is not None
            assert cartograph.layout_header.name_label.full_text() == "Survey"
        finally:
            dialog.close()

    def test_a_new_template_layout_is_named_from_title_and_template(
        self,
        qgis_app,
        project,
        picked_template,  # noqa: F811
    ) -> None:
        from test_cartograph_dialog import TestCanvasAndImport

        dialog, _canvas = TestCanvasAndImport()._canvas_dialog(project)
        project.setTitle("Grand Canyon")
        try:
            _choose_cartograph(dialog)
            picked_template.choice = ("legend-below", "A4", "landscape")
            _click_new_from_template(dialog)
            cartograph = dialog._cartograph
            assert cartograph.current_layout_name() == "Grand Canyon — Clearline"
            project.setTitle("Something else")
            assert cartograph.current_layout_name() == "Grand Canyon — Clearline"
        finally:
            dialog.close()
