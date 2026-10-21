"""Keyboard focus you can see, and a dialog that mirrors right to left.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest

qgis_core = pytest.importorskip("qgis.core")

from test_theme import _contrast  # noqa: E402

PALETTES = ["PLUM_LIGHT", "PLUM_DARK"]
NAME = "dlg"

# Every control the keyboard reaches, by the selector its rules use.
FOCUSABLE = [
    "QLineEdit",
    "QComboBox",
    "QPushButton",
    'QPushButton[role="chip"]',
    'QPushButton[role="link"]',
    'QToolButton[role="chip"]',
    'QToolButton[role="primary"]',
    'QToolButton[role="secondary"]',
    'QToolButton[role="corner"]',
    'QToolButton[role="corner"][iconOnly="true"]',
    'QToolButton[role="corner"][accent="true"]',
    'QToolButton[role="cornerPart"]',
    'QToolButton[role="outputPart"]',
    'QToolButton[role="outputTitle"]',
    'QToolButton[role="sectionHeader"]',
    'QToolButton[autoRaise="true"]',
    'QFrame[role="outputRow"]',
    "QCheckBox",
    "QRadioButton",
    "QTabBar::tab",
    "QTreeView::item",
    'QListWidget[role="templateList"]::item',
]

# Rules that must exist right to left, because Qt does not mirror them.
DIRECTIONAL = [
    'QToolButton[role="primary"]',
    'QToolButton[role="secondary"]',
    'QToolButton[role="primary"][hasMenu="false"]',
    'QToolButton[role="primary"]::menu-button',
    'QToolButton[role="secondary"]::menu-button',
    'QToolButton[role="chip"][hasMenu="true"]',
    'QToolButton[role="corner"][hasMenu="true"]',
    'QToolButton[role="cornerPart"][position="left"]',
    'QToolButton[role="cornerPart"][position="right"]',
    'QToolButton[role="outputPart"][position="left"]',
    'QToolButton[role="outputPart"][position="right"]',
    'QToolButton[role="outputTitle"]',
    'QToolButton[role="sectionHeader"]',
    "QTabBar::tab",
    "QHeaderView::section",
    "QMenu::item",
]


def _rules(css: str) -> dict[str, str]:
    """Each selector in `css` mapped to its declarations, merged."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for selector in selectors.split(","):
            key = " ".join(selector.split())
            rules[key] = rules.get(key, "") + body
    return rules


def _focus_selectors(selector: str) -> list[str]:
    """The ways a focus rule for `selector` may be written."""
    return [f"#{NAME} {selector}:focus", f"#{NAME} {selector}:selected:focus"]


class TestFocusRules:
    """A visible ring on every control, in a colour that reads on both themes."""

    @pytest.mark.parametrize("name", PALETTES)
    @pytest.mark.parametrize("selector", FOCUSABLE)
    def test_every_control_has_a_focus_ring(self, qgis_app, name, selector):
        from nika_onlymap_exporter.ui import theme

        p = getattr(theme, name)
        rules = _rules(theme.stylesheet(p, NAME))
        bodies = [rules[s] for s in _focus_selectors(selector) if s in rules]
        assert bodies, f"no :focus rule for {selector}"
        body = " ".join(bodies)
        ring = (theme.focus_colour(p), theme.focus_colour(p, True))
        assert any(colour in body for colour in ring), body
        # A width wherever the rule sets one: never a 1 px hairline.
        for width in re.findall(r"border:\s*(\d+)px", body):
            assert int(width) >= theme.FOCUS_WIDTH, body

    @pytest.mark.parametrize("name", PALETTES)
    def test_the_old_invisible_focus_is_gone(self, qgis_app, name):
        from nika_onlymap_exporter.ui import theme

        p = getattr(theme, name)
        rules = _rules(theme.stylesheet(p, NAME))
        for selector in (f"#{NAME} QLineEdit:focus", f"#{NAME} QComboBox:focus"):
            assert p.accent not in rules[selector] or p.accent == p.link

    @pytest.mark.parametrize("name", PALETTES)
    def test_the_ring_reaches_three_to_one_on_every_ground(self, qgis_app, name):
        from nika_onlymap_exporter.ui import theme

        p = getattr(theme, name)
        ring = theme.focus_colour(p)
        grounds = {
            "paper": p.paper,
            "canvas": p.canvas,
            "field": p.field,
            "hover": p.hover,
            "selection": p.accent_soft,
        }
        low = {k: round(_contrast(ring, v), 2) for k, v in grounds.items()}
        assert all(v >= 3 for v in low.values()), low
        # Round a filled button the ring sits between the fill and the window;
        # it must stand out from the window it is seen against.
        filled = theme.focus_colour(p, True)
        for ground in (p.paper, p.canvas):
            assert _contrast(filled, ground) >= 3, (filled, ground)
        assert _contrast(filled, p.rule) >= 3

    @pytest.mark.parametrize("name", PALETTES)
    def test_the_ring_differs_from_the_unfocused_outline(self, qgis_app, name):
        from nika_onlymap_exporter.ui import theme

        p = getattr(theme, name)
        assert _contrast(theme.focus_colour(p), p.rule) >= 3


class TestRightToLeftRules:
    @pytest.mark.parametrize("selector", DIRECTIONAL)
    def test_each_lopsided_rule_has_a_mirror(self, qgis_app, selector):
        from nika_onlymap_exporter.ui import theme

        rules = _rules(theme.stylesheet(theme.PLUM_LIGHT, NAME))
        assert f'#{NAME}[rtl="true"] {selector}' in rules, selector

    def test_each_lopsided_focus_padding_has_a_mirror(self, qgis_app):
        from nika_onlymap_exporter.ui import theme

        rules = _rules(theme.stylesheet(theme.PLUM_LIGHT, NAME))
        for selector, (_t, right, _b, left), _w, _f in theme._FOCUS_PADDED:
            if right == left or selector in theme._FOCUS_NOT_MIRRORED:
                continue
            assert f'#{NAME}[rtl="true"] {selector}:focus' in rules, selector


def _padding(body: str, start: list[int]) -> list[int]:
    """`start` (top, right, bottom, left) after the paddings in `body`."""
    values = list(start)
    for name, value in re.findall(r"(padding(?:-\w+)?):\s*([^;]+);", body):
        px = [int(v) for v in re.findall(r"(-?\d+)px", value)]
        if name == "padding":
            if len(px) == 1:
                px *= 4
            elif len(px) == 2:
                px *= 2
            values = px
        else:
            side = ["top", "right", "bottom", "left"].index(name.split("-")[1])
            values[side] = px[0]
    return values


class TestNothingMoves:
    """Focus trades padding for ring width, so the box stays the same size."""

    @pytest.mark.parametrize("name", PALETTES)
    def test_focus_padding_plus_ring_equals_the_unfocused_box(self, qgis_app, name):
        from nika_onlymap_exporter.ui import theme

        rules = _rules(theme.stylesheet(getattr(theme, name), NAME))
        for selector, _pad, _width, _fill in theme._FOCUS_PADDED:
            # The unfocused box: every base rule this selector inherits from,
            # plain to specific, as the stylesheet cascades them.
            base = [0, 0, 0, 0]
            width = 0
            for parent, *_rest in theme._FOCUS_PADDED:
                if not selector.startswith(parent):
                    continue
                body = rules.get(f"#{NAME} {parent}", "")
                base = _padding(body, base)
                widths = re.findall(r"border:\s*(\d+)px", body)
                width = int(widths[-1]) if widths else width
            focused = _padding(rules[f"#{NAME} {selector}:focus"], base)
            assert [v + theme.FOCUS_WIDTH for v in focused] == [
                v + width for v in base
            ], selector

    def test_recoloured_controls_keep_room_for_the_ring(self, qgis_app):
        from nika_onlymap_exporter.ui import theme

        rules = _rules(theme.stylesheet(theme.PLUM_LIGHT, NAME))
        for selector in theme._FOCUS_RECOLOURED:
            body = rules[f"#{NAME} {selector}"]
            assert re.search(
                rf"border:\s*{theme.FOCUS_WIDTH}px solid transparent", body
            ), selector


def _dialog(direction=None):
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout

    dialog = QDialog()
    dialog.setObjectName("focusTestDialog")
    if direction is not None:
        dialog.setLayoutDirection(direction)
    QHBoxLayout(dialog)
    return dialog, Qt


def _settle(dialog):
    from qgis.PyQt.QtWidgets import QApplication

    dialog.show()
    dialog.activateWindow()
    for _ in range(5):
        QApplication.processEvents()


def _edge(widget) -> str:
    """The colour two pixels in from the widget's left edge, halfway down."""
    image = widget.grab().toImage()
    return image.pixelColor(1, image.height() // 2).name()


class TestRenderedFocus:
    """What a keyboard user actually sees, drawn by Qt."""

    @pytest.mark.parametrize("kind", ["push", "corner", "export", "combo"])
    def test_focus_draws_the_ring_and_moves_nothing(self, qgis_app, kind):
        from qgis.PyQt.QtWidgets import QComboBox, QLineEdit, QPushButton, QToolButton

        from nika_onlymap_exporter.ui import theme

        dialog, qt = _dialog()
        if kind == "push":
            widget = QPushButton("Choose...", dialog)
        elif kind == "combo":
            widget = QComboBox(dialog)
            widget.addItems(["One", "Two"])
        else:
            widget = QToolButton(dialog)
            widget.setText("Export")
            widget.setProperty("role", "corner")
            widget.setProperty("hasMenu", False)
            widget.setProperty("accent", kind == "export")
            widget.setFocusPolicy(qt.FocusPolicy.StrongFocus)
        dialog.layout().addWidget(widget)
        other = QLineEdit(dialog)
        dialog.layout().addWidget(other)
        palette = theme.apply(dialog)
        _settle(dialog)
        other.setFocus(qt.FocusReason.TabFocusReason)
        _settle(dialog)
        before_size = widget.size()
        before_hint = widget.sizeHint()
        before = _edge(widget)

        widget.setFocus(qt.FocusReason.TabFocusReason)
        _settle(dialog)
        assert widget.hasFocus()
        ring = theme.focus_colour(palette, kind == "export")
        assert _edge(widget) == ring
        assert before != ring
        assert widget.size() == before_size
        assert widget.sizeHint() == before_hint
        dialog.close()

    def test_a_section_header_draws_arrow_and_title_apart_right_to_left(self, qgis_app):
        from qgis.PyQt.QtWidgets import QToolButton

        from nika_onlymap_exporter.ui import theme

        dialog, qt = _dialog(direction=None)
        dialog.setLayoutDirection(qt.LayoutDirection.RightToLeft)
        header = QToolButton(dialog)
        header.setProperty("role", "sectionHeader")
        header.setText("Page")
        header.setArrowType(qt.ArrowType.LeftArrow)
        header.setToolButtonStyle(qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        dialog.layout().addWidget(header)
        theme.apply(dialog)
        _settle(dialog)
        # Qt draws the arrow over the title right to left otherwise.
        assert header.layoutDirection() == qt.LayoutDirection.LeftToRight
        dialog.close()

    def test_a_chevron_keeps_its_room_right_to_left(self, qgis_app):
        from qgis.PyQt.QtGui import QColor
        from qgis.PyQt.QtWidgets import QMenu, QToolButton

        from nika_onlymap_exporter.ui import theme

        dialog, qt = _dialog()
        dialog.setLayoutDirection(qt.LayoutDirection.RightToLeft)
        button = QToolButton(dialog)
        button.setText("Share")
        button.setProperty("role", "corner")
        button.setProperty("hasMenu", True)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(QMenu(button))
        dialog.layout().addWidget(button)
        theme.apply(dialog)
        _settle(dialog)
        image = button.grab().toImage()
        ground = QColor(image.pixelColor(image.width() // 2, 3)).lightness()
        rows = range(4, image.height() - 4)

        def inked(x: int) -> bool:
            return any(
                abs(QColor(image.pixelColor(x, y)).lightness() - ground) > 80
                for y in rows
            )

        # Skip the outline; then the chevron, a clear gap, and the label.
        columns = [inked(x) for x in range(3, image.width() - 3)]
        assert any(columns[:18]), "no chevron in the room kept on the left"
        first = columns.index(True)
        rest = columns[first:]
        gap = rest.index(False) if False in rest else len(rest)
        assert any(rest[gap:]), "no label after the chevron"
        assert first >= 2, "the chevron is cut off at the edge"
        dialog.close()
