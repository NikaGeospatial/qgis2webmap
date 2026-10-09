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
        styles = set(QFontDatabase.styles("Inter"))
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

    def test_the_share_menu_opens_flush_under_its_button(
        self, qgis_app, project, make_memory_layer
    ):
        from qgis.PyQt.QtCore import QPoint

        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.show()
            button = dialog.share_button
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

    def test_lists_drop_below_their_box(self, qgis_app, project, make_memory_layer):
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert "combobox-popup: 0" in dialog.styleSheet()
        finally:
            dialog.close()
