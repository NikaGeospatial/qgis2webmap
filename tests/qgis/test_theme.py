"""The dialog's NIKA look: which palette it picks, and what it leaves alone.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import zlib

import pytest

qgis_core = pytest.importorskip("qgis.core")


def _palette_with_window(hex_colour: str):
    from qgis.PyQt.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(hex_colour))
    return palette


def _relative_luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


class TestPalettePick:
    """Light or dark follows QGIS, so the dialog never glows inside Night Mapping."""

    def test_a_dark_qgis_gets_plum_dark(self, qgis_app):
        from nika_onlymap_exporter.ui import theme

        assert theme.pick_palette(_palette_with_window("#323232")) is theme.PLUM_DARK

    def test_a_light_qgis_gets_plum_light(self, qgis_app):
        from nika_onlymap_exporter.ui import theme

        assert theme.pick_palette(_palette_with_window("#efefef")) is theme.PLUM_LIGHT

    def test_the_accents_are_brass_by_day_and_lime_by_night(self, qgis_app):
        from nika_onlymap_exporter.ui import theme

        assert theme.PLUM_LIGHT.accent == "#eba941"
        assert theme.PLUM_DARK.accent == "#abf051"


class TestContrast:
    """Every pairing of text and ground the stylesheet uses reads at WCAG AA."""

    @pytest.mark.parametrize("name", ["PLUM_LIGHT", "PLUM_DARK"])
    def test_text_pairs_reach_four_and_a_half_to_one(self, qgis_app, name):
        from nika_onlymap_exporter.ui import theme

        p = getattr(theme, name)
        pairs = {
            "ink on paper": (p.ink, p.paper),
            "ink on canvas": (p.ink, p.canvas),
            "muted on canvas": (p.muted, p.canvas),
            "link on canvas": (p.link, p.canvas),
            "warn on canvas": (p.warn, p.canvas),
            "ink on field": (p.ink, p.field),
            "muted on paper": (p.muted, p.paper),
            "muted on field": (p.muted, p.field),
            "link on paper": (p.link, p.paper),
            "warn on paper": (p.warn, p.paper),
            "on-accent on accent": (p.on_accent, p.accent),
        }
        weak = {
            k: round(_contrast(*v), 2) for k, v in pairs.items() if _contrast(*v) < 4.5
        }
        assert not weak, f"{name} pairs under 4.5:1: {weak}"


class TestScope:
    """The look is the dialog's own; QGIS around it is never restyled."""

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_the_application_stylesheet_is_untouched(
        self, qgis_app, project, make_memory_layer
    ):
        before = qgis_app.styleSheet()
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert qgis_app.styleSheet() == before
            assert "#qgis2webmapMainDialog" in dialog.styleSheet()
        finally:
            dialog.close()

    def test_the_application_font_is_untouched(
        self, qgis_app, project, make_memory_layer
    ):
        before = qgis_app.font().family()
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert qgis_app.font().family() == before
            assert dialog.font().family() == "Inter"
        finally:
            dialog.close()


class TestFonts:
    def test_inter_registers_both_weights(self, qgis_app):
        from qgis.PyQt.QtGui import QFontDatabase

        from nika_onlymap_exporter.ui import theme

        assert theme.load_fonts() == "Inter"
        # Static from Qt6; Qt5 wants an instance, and Qt6 has no constructor.
        try:
            styles = set(QFontDatabase.styles("Inter"))
        except TypeError:
            styles = set(QFontDatabase().styles("Inter"))
        assert {"Regular", "SemiBold"} <= styles

    def test_the_fonts_add_under_120_kb_to_the_zip(self):
        from pathlib import Path

        import nika_onlymap_exporter

        fonts = Path(nika_onlymap_exporter.__file__).parent / "fonts"
        compressed = sum(
            len(zlib.compress(f.read_bytes(), 9)) for f in fonts.glob("*.ttf")
        )
        assert compressed < 120_000, f"fonts add {compressed} bytes compressed"


class TestHelpPane:
    """Markdown bakes the application's link blue into the document at import,
    so the Help tab kept blue links on plum until they were recoloured."""

    def test_links_take_the_theme_link_colour(
        self, qgis_app, project, make_memory_layer
    ):
        dialog = TestScope()._dialog(project, make_memory_layer)
        try:
            document = dialog.help_browser.document()
            colours = set()
            block = document.begin()
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    fragment = it.fragment()
                    if fragment.isValid() and fragment.charFormat().isAnchor():
                        colours.add(fragment.charFormat().foreground().color().name())
                    it += 1
                block = block.next()
            assert colours == {dialog.theme.link}
        finally:
            dialog.close()


class TestAttachedMenus:
    """A dropdown opens as part of its button, not as a box from nowhere."""

    def _dialog(self, project, make_memory_layer):
        return TestScope()._dialog(project, make_memory_layer)

    def test_a_list_still_attaches_after_python_lets_go_of_its_window(self, qgis_app):
        """The list window is Qt's own, so Python drops its wrapper as soon
        as nothing refers to it. The attacher once went with it while Qt kept
        calling its filter - an interpreter crash on the list's next event."""
        import gc

        from qgis.PyQt.QtCore import QObject
        from qgis.PyQt.QtWidgets import QComboBox

        from nika_onlymap_exporter.ui import theme

        combo = QComboBox()
        combo.addItems(["one", "two"])
        combo.resize(240, 30)
        theme.attach_list(combo)
        gc.collect()
        # Still the attacher, Python half and all - not a bare QObject that
        # Qt has kept and Python has forgotten.
        attachers = [
            child
            for child in combo.view().window().findChildren(QObject)
            if isinstance(child, theme._MenuAttacher)
        ]
        assert len(attachers) == 1
        try:
            combo.show()
            combo.showPopup()
            for _ in range(3):
                qgis_app.processEvents()
            assert combo.view().window().width() == combo.width()
        finally:
            combo.hidePopup()
            combo.close()

    def test_a_menu_opens_flush_under_its_button(
        self, qgis_app, project, make_memory_layer
    ):
        from qgis.PyQt.QtCore import QPoint

        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.resize(720, 500)
            dialog.move(0, 0)
            dialog.show()
            dialog.tabs.setCurrentWidget(dialog._fidelity_page)
            button = dialog.fidelity_panel.filter_button
            menu = button.menu()
            menu.popup(QPoint(0, 0))
            for _ in range(3):
                qgis_app.processEvents()
            anchor = button.mapToGlobal(QPoint(0, button.height()))
            assert menu.geometry().top() == anchor.y() - 1
            assert menu.width() >= button.width()
            assert button.property("menuOpen") is True
            menu.hide()
            qgis_app.processEvents()
            assert button.property("menuOpen") is False
        finally:
            dialog.close()

    def test_opening_a_menu_keeps_the_buttons_font_and_width(
        self, qgis_app, project, make_memory_layer
    ):
        from qgis.PyQt.QtCore import QPoint

        from nika_onlymap_exporter.ui import theme

        if theme.load_fonts() is None:
            pytest.skip("bundled Inter not loadable here")
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.show()
            dialog.tabs.setCurrentWidget(dialog._fidelity_page)
            for _ in range(3):
                qgis_app.processEvents()
            button = dialog.fidelity_panel.filter_button
            width = button.width()
            menu = button.menu()
            menu.popup(QPoint(0, 0))
            for _ in range(3):
                qgis_app.processEvents()
            assert button.font().family() == theme.FONT_FAMILY
            assert button.width() == width
            menu.hide()
        finally:
            dialog.close()

    def test_lists_drop_below_their_box(self, qgis_app, project, make_memory_layer):
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert "combobox-popup: 0" in dialog.styleSheet()
        finally:
            dialog.close()


class TestThemeChoice:
    """Settings > Appearance can override QGIS: light or dark on purpose."""

    def test_a_choice_overrides_qgis(self, qgis_app):
        from nika_onlymap_exporter.ui import theme

        dark_qgis = _palette_with_window("#323232")
        light_qgis = _palette_with_window("#efefef")
        assert theme.pick_palette(dark_qgis, "light") is theme.PLUM_LIGHT
        assert theme.pick_palette(light_qgis, "dark") is theme.PLUM_DARK
        assert theme.pick_palette(dark_qgis, "auto") is theme.PLUM_DARK

    def test_switching_restyles_the_open_dialog(
        self, qgis_app, project, make_memory_layer
    ):
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.ui import theme

        dialog = TestScope()._dialog(project, make_memory_layer)
        try:
            picker = dialog.theme_picker
            picker.setCurrentIndex(picker.findData("dark"))
            assert dialog.theme is theme.PLUM_DARK
            assert theme.PLUM_DARK.canvas in dialog.styleSheet()
            picker.setCurrentIndex(picker.findData("light"))
            assert dialog.theme is theme.PLUM_LIGHT
        finally:
            QSettings().remove(theme.THEME_KEY)
            dialog.close()

    def test_an_open_colour_vision_window_follows_a_switch(
        self, qgis_app, project, make_memory_layer
    ):
        """It is kept between openings, so it is already open when the
        theme changes; its table cells used to keep QGIS's light base under
        the new theme's light text."""
        from qgis.PyQt.QtCore import QSettings
        from qgis.PyQt.QtGui import QPalette
        from qgis.PyQt.QtWidgets import QTableWidgetItem

        from nika_onlymap_exporter.ui import theme
        from nika_onlymap_exporter.ui.cartograph.accessibility_dialog import (
            AccessibilityDialog,
        )

        dialog = TestScope()._dialog(project, make_memory_layer)
        try:
            picker = dialog.theme_picker
            picker.setCurrentIndex(picker.findData("light"))
            window = AccessibilityDialog(lambda: (), parent=dialog)
            window.table.setRowCount(1)
            window.table.setItem(0, 0, QTableWidgetItem("Contours"))
            window.show()
            for choice in ("dark", "light", "dark"):
                picker.setCurrentIndex(picker.findData(choice))
                qgis_app.processEvents()
                base = window.table.palette().color(QPalette.ColorRole.Base).name()
                assert base == dialog.theme.paper, (choice, base)
                cell = window.table.visualItemRect(window.table.item(0, 0))
                image = window.table.viewport().grab().toImage()
                behind = image.pixelColor(cell.right() - 2, cell.center().y()).name()
                assert _contrast(dialog.theme.ink, behind) >= 4.5, (choice, behind)
            window.close()
        finally:
            QSettings().remove(theme.THEME_KEY)
            dialog.close()


class TestMessageBoxes:
    """A box the dialog opens is its child, so it inherits the text colour."""

    @pytest.mark.parametrize("choice", ["light", "dark"])
    def test_a_message_box_text_is_readable_on_its_background(
        self, qgis_app, project, make_memory_layer, choice
    ):
        from qgis.PyQt.QtCore import QSettings
        from qgis.PyQt.QtWidgets import QMessageBox

        from nika_onlymap_exporter.ui import theme

        dialog = TestScope()._dialog(project, make_memory_layer)
        try:
            picker = dialog.theme_picker
            picker.setCurrentIndex(picker.findData(choice))
            # A person never raises a box in the same instant they switch the
            # theme; Qt5 restyles children on the next turn of the loop.
            qgis_app.processEvents()
            box = QMessageBox(dialog)
            box.setText("Publishing is not in this version yet.")
            box.show()
            qgis_app.processEvents()
            image = box.grab().toImage()
            # Bottom-left corner: clear of the icon, the text and the button.
            background = image.pixelColor(2, image.height() - 3).name()
            assert _contrast(dialog.theme.ink, background) >= 4.5, background
            box.close()
        finally:
            QSettings().remove(theme.THEME_KEY)
            dialog.close()


class TestCornerMenus:
    """Share's menu hangs from Share, growing left so it stays on the dialog."""

    def _open(self, qgis_app, button):
        from qgis.PyQt.QtCore import QPoint

        menu = button.menu()
        menu.popup(QPoint(0, 0))
        for _ in range(3):
            qgis_app.processEvents()
        return menu

    def test_share_ends_at_its_right_edge(self, qgis_app, project, make_memory_layer):
        from qgis.PyQt.QtCore import QPoint

        dialog = TestScope()._dialog(project, make_memory_layer)
        try:
            dialog.resize(720, 500)
            dialog.move(0, 0)
            dialog.show()
            last = dialog.share_button
            menu = self._open(qgis_app, last)
            right = last.mapToGlobal(QPoint(last.width(), 0)).x()
            assert menu.geometry().left() + menu.width() == right
            menu.hide()
        finally:
            dialog.close()


def _painted_contrast(widget) -> float:
    """The contrast of what a widget really paints: its most common pixel is the
    ground, the pixel furthest from it in luminance is the text."""
    import collections

    from qgis.PyQt.QtGui import QColor

    top = widget.window()
    region = widget.visibleRegion().boundingRect()
    image = top.grab(
        region.translated(widget.mapTo(top, region.topLeft()) - region.topLeft())
    ).toImage()
    counts = collections.Counter(
        image.pixel(x, y) for y in range(image.height()) for x in range(image.width())
    )
    ground = QColor.fromRgba(counts.most_common(1)[0][0]).name()
    return max(_contrast(QColor.fromRgba(px).name(), ground) for px in counts)


@pytest.fixture
def qgis_host(qgis_app):
    """Simulates QGIS's own theme: a window palette and the application
    stylesheet that paints every bare widget, as Night Mapping does."""
    from qgis.PyQt.QtGui import QColor, QPalette

    original = QPalette(qgis_app.palette())

    def set_host(kind: str) -> None:
        ground, text = (
            ("#ffffff", "#111111") if kind == "light" else ("#202020", "#eeeeee")
        )
        palette = QPalette()
        for role, colour in (
            (QPalette.ColorRole.Window, ground),
            (QPalette.ColorRole.Base, ground),
            (QPalette.ColorRole.Button, ground),
            (QPalette.ColorRole.WindowText, text),
            (QPalette.ColorRole.Text, text),
            (QPalette.ColorRole.ButtonText, text),
        ):
            palette.setColor(role, QColor(colour))
        qgis_app.setPalette(palette)
        qgis_app.setStyleSheet(
            f"QWidget {{ background-color:{ground}; color:{text}; }}"
        )

    yield set_host
    qgis_app.setStyleSheet("")
    qgis_app.setPalette(original)


class TestHostSurfaces:
    """Text the plugin colours sits on a surface the plugin colours too: a
    host theme's background must never show under it (light ink on white,
    dark ink on near-black), whichever theme each side is wearing."""

    @staticmethod
    def _controls(tmp_path):
        from qgis.PyQt.QtCore import QSettings
        from qgis.PyQt.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

        from nika_onlymap_exporter.core.nika_desktop import NOT_FOUND
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.ui.cartograph.print_settings_dialog import (
            PrintSettingsDialog,
        )
        from nika_onlymap_exporter.ui.handoff_dialog import (
            HandoffContext,
            HandoffDialog,
        )

        root = QDialog()
        root.setObjectName("hostSurfaceRoot")
        output = tmp_path / "map.html"
        output.write_text("<html></html>", encoding="utf-8")
        qsettings = QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat)
        printing = PrintSettingsDialog(
            PrintSettings(page="custom"),
            layout_page_mm=(210, 297),
            has_live_frame=False,
            parent=root,
            qsettings=qsettings,
        )
        handoff = HandoffDialog(
            HandoffContext(
                output="cartograph",
                file_path=output,
                source_name="Parcel survey",
                plugin_version="0.3.0",
                task="critique",
            ),
            parent=root,
            detector=lambda: NOT_FOUND,
        )
        handoff.instructions_edit.setPlainText("Keep the legend short.")
        handoff.files_list.setCurrentRow(0)
        # A plain container holding text, as a template card's text column is.
        container = QWidget(printing)
        QVBoxLayout(container).addWidget(QLabel("Plain container text", container))
        container.setProperty("probe", True)
        printing.layout().addWidget(container)
        controls = {
            "custom width": printing.custom_width,
            "custom height": printing.custom_height,
            "instructions": handoff.instructions_edit,
            "prompt": handoff.prompt_view,
            "file list": handoff.files_list,
            "plain container": container,
        }
        return root, (printing, handoff), controls

    @pytest.mark.parametrize("host", ["light", "dark"])
    @pytest.mark.parametrize("plugin", ["light", "dark"])
    def test_text_reads_on_the_surface_it_is_painted_on(
        self, qgis_app, qgis_host, tmp_path, host, plugin
    ):
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.ui import theme

        qgis_host(host)
        QSettings().setValue(theme.THEME_KEY, plugin)
        root, windows, controls = self._controls(tmp_path)
        try:
            theme.apply(root)
            other = "dark" if plugin == "light" else "light"
            # The second pass is the theme switched while the windows stay open.
            for choice in (plugin, other):
                QSettings().setValue(theme.THEME_KEY, choice)
                theme.apply(root)
                for window in windows:
                    window.show()
                qgis_app.processEvents()
                weak = {
                    name: round(_painted_contrast(widget), 2)
                    for name, widget in controls.items()
                    if _painted_contrast(widget) < 4.5
                }
                assert not weak, (host, choice, weak)
        finally:
            QSettings().remove(theme.THEME_KEY)
            for window in windows:
                window.close()
            root.close()
