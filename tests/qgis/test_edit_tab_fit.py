"""The Edit tab fits the panel: for every kind of item, at the narrowest panel
and at a wide one, its content is no wider than the scroll area's viewport (so
no sideways scroll bar), and no button's text is cut.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from test_cartograph_panel_widgets import _settle

pytest.importorskip("qgis.core")

KINDS = ("map", "text", "legend", "scale_bar", "north_arrow")
WIDTHS = (320, 350, 560)


@pytest.fixture
def signal_layout(qgis_app, project):
    from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view

    project.setTitle("Survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    extent = layer.extent()
    extent.grow(200)
    return cartograph_from_view(
        extent, UTM, [layer], project, template_id="dashboard", layout_name="signal"
    ).layout


def _item_of(layout, kind):
    """An item of `kind` on the layout, added if the design has none."""
    from qgis.core import (
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutItemMap,
        QgsLayoutItemPicture,
        QgsLayoutItemScaleBar,
    )

    cls = {
        "map": QgsLayoutItemMap,
        "text": QgsLayoutItemLabel,
        "legend": QgsLayoutItemLegend,
        "scale_bar": QgsLayoutItemScaleBar,
        "north_arrow": QgsLayoutItemPicture,
    }[kind]
    maps = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
    for item in layout.items():
        if isinstance(item, cls) and (kind != "north_arrow" or item.linkedMap()):
            return item
    item = cls(layout)
    layout.addLayoutItem(item)
    if kind != "map" and kind != "text":
        item.setLinkedMap(maps[0])
    return item


def shown_controls(layout, kind, width):
    """The Edit tab in a scroll area of the dialog's own kind, `width` wide,
    with an item of `kind` selected."""
    from nika_onlymap_exporter.ui import theme
    from nika_onlymap_exporter.ui.cartograph.controller import _panel_scroll
    from nika_onlymap_exporter.ui.cartograph.item_controls import ContextControls

    controls = ContextControls()
    scroll = _panel_scroll(controls)
    # The dialog's own look: its font and button padding are what take room.
    theme.apply(scroll)
    scroll.resize(width, 700)
    scroll.show()
    controls.set_layout(layout)
    item = _item_of(layout, kind)
    layout.deselectAll()
    item.setSelected(True)
    controls.refresh()
    if kind == "map":
        # An Image map also offers its resolution: the widest the tab gets.
        controls.dpi_combo.addItem("200 dpi")
        controls.dpi_combo.setVisible(True)
        if hasattr(controls, "dpi_label"):
            controls.dpi_label.setVisible(True)
    return scroll, controls


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("kind", KINDS)
class TestEditTabFits:
    def test_no_sideways_scrolling(self, qgis_app, signal_layout, kind, width) -> None:
        scroll, controls = shown_controls(signal_layout, kind, width)
        try:
            _settle(qgis_app, 0.2)
            viewport = scroll.viewport().width()
            assert not scroll.horizontalScrollBar().isVisible()
            assert controls.width() <= viewport
            assert controls.minimumSizeHint().width() <= viewport
        finally:
            scroll.close()

    def test_every_visible_widget_is_inside_the_panel(
        self, qgis_app, signal_layout, kind, width
    ) -> None:
        from qgis.PyQt.QtWidgets import QWidget

        scroll, controls = shown_controls(signal_layout, kind, width)
        try:
            _settle(qgis_app, 0.2)
            limit = scroll.viewport().width()
            for child in controls.findChildren(QWidget):
                if not child.isVisibleTo(controls):
                    continue
                right = child.mapTo(controls, child.rect().topRight()).x()
                assert right <= limit, (
                    child,
                    child.text() if hasattr(child, "text") else "",
                )
        finally:
            scroll.close()

    def test_no_button_text_is_cut(self, qgis_app, signal_layout, kind, width) -> None:
        from qgis.PyQt.QtWidgets import QAbstractButton

        scroll, controls = shown_controls(signal_layout, kind, width)
        try:
            _settle(qgis_app, 0.2)
            for button in controls.findChildren(QAbstractButton):
                if not button.isVisibleTo(controls) or not button.text():
                    continue
                need = button.fontMetrics().horizontalAdvance(
                    button.text().replace("&", "")
                )
                assert button.width() >= need, (button.text(), button.width(), need)
        finally:
            scroll.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_a_map_has_no_gap_between_its_button_row_and_rotation(
    qgis_app, signal_layout, width
) -> None:
    """Spare room in the panel goes below the rows, not into a gap inside."""
    scroll, controls = shown_controls(signal_layout, "map", width)
    try:
        _settle(qgis_app, 0.2)
        buttons = controls.canvas_button
        rotation = controls.rotation_spin
        bottom = buttons.mapTo(controls, buttons.rect().bottomLeft()).y()
        top = rotation.mapTo(controls, rotation.rect().topLeft()).y()
        assert 0 <= top - bottom <= 16, (bottom, top)
    finally:
        scroll.close()
