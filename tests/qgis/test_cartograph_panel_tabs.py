"""The rule above the Cartograph panel's tabs: one continuous line, 8 px clear
of the tabs, that no selected, hovered or focused tab interrupts.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtTest import QTest
from test_layout_header import _open


def _rule_row(cartograph):
    """The panel's grab, and the y of the line within it."""
    rule = cartograph.panel_rule
    panel = rule.parentWidget()
    image = panel.grab().toImage()
    top = rule.mapTo(panel, QPoint(0, 0)).y()
    return image, rule, top


def _assert_continuous(cartograph) -> None:
    image, rule, top = _rule_row(cartograph)
    background = image.pixelColor(2, max(top - 3, 0))
    row = [image.pixelColor(x, top) for x in range(rule.width())]
    assert all(c == row[0] for c in row), "the line has a break"
    assert row[0] != background, "the line is not drawn"


@pytest.mark.parametrize("state", ["plain", "hover", "focus"])
def test_the_rule_is_one_unbroken_line_across_the_panel(
    qgis_app, project, state
) -> None:
    dialog, cartograph = _open(project, "One")
    try:
        tabs = cartograph.panel_tabs
        bar = tabs.tabBar()
        tabs.setCurrentIndex(0)
        if state == "hover":
            QTest.mouseMove(bar, bar.tabRect(1).center())
        elif state == "focus":
            bar.setFocus(Qt.FocusReason.TabFocusReason)
        qgis_app.processEvents()
        _assert_continuous(cartograph)
        assert cartograph.panel_rule.width() >= cartograph.panel_tabs.width() - 2
    finally:
        dialog.close()


def test_the_tabs_sit_eight_pixels_below_the_rule_and_nothing_overlaps(
    qgis_app, project
) -> None:
    dialog, cartograph = _open(project, "One")
    try:
        tabs, rule = cartograph.panel_tabs, cartograph.panel_rule
        panel = rule.parentWidget()
        line_bottom = rule.mapTo(panel, QPoint(0, 0)).y() + 1
        tabs_top = tabs.mapTo(panel, QPoint(0, 0)).y()
        assert tabs_top - line_bottom == 8
        bar = tabs.tabBar()
        assert bar.mapTo(panel, QPoint(0, 0)).y() >= tabs_top
    finally:
        dialog.close()


def test_the_selected_tab_keeps_its_underline(qgis_app, project) -> None:
    dialog, cartograph = _open(project, "One")
    try:
        bar = cartograph.panel_tabs.tabBar()
        cartograph.panel_tabs.setCurrentIndex(1)
        qgis_app.processEvents()
        image = bar.grab().toImage()
        rect = bar.tabRect(1)
        x = rect.center().x()
        # The bottom rows of the selected tab carry the accent bar, which the
        # other tabs' bottoms do not.
        bars = {image.pixelColor(x, rect.bottom() - n).name() for n in range(3)}
        # All three rows of the bar are one colour, not the other tabs'.
        assert len(bars) == 1, bars
        other = image.pixelColor(bar.tabRect(0).center().x(), rect.bottom() - 1)
        assert bars != {other.name()}
    finally:
        dialog.close()


@pytest.mark.parametrize("current", [0, 2])
def test_nothing_but_the_rule_is_drawn_in_the_gap_above_the_tabs(
    qgis_app, project, current
) -> None:
    dialog, cartograph = _open(project, "One")
    try:
        cartograph.panel_tabs.setCurrentIndex(current)
        bar = cartograph.panel_tabs.tabBar()
        QTest.mouseMove(bar, bar.tabRect(1).center())
        qgis_app.processEvents()
        image, rule, top = _rule_row(cartograph)
        tabs_top = cartograph.panel_tabs.mapTo(rule.parentWidget(), QPoint(0, 0)).y()
        for y in range(top + 1, tabs_top + 4):
            colours = {image.pixelColor(x, y).name() for x in range(0, rule.width())}
            assert len(colours) == 1, f"row {y} has stray ink"
    finally:
        dialog.close()
