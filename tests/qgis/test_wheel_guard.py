"""A mouse wheel over a closed dropdown scrolls the panel, never the dropdown.

The events are real `QWheelEvent`s sent to the widget Qt would deliver them to:
the child under the pointer (for an expression widget that is its inner combo or
line edit, not the outer wrapper).
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsVectorLayer
from qgis.gui import (
    QgsFieldComboBox,
    QgsFieldExpressionWidget,
    QgsMapLayerComboBox,
)
from qgis.PyQt.QtCore import QPoint, QPointF, Qt
from qgis.PyQt.QtGui import QWheelEvent
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import (
    QApplication,
    QComboBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nika_onlymap_exporter.ui.wheel_guard import (
    install_wheel_guard,
    remove_wheel_guard,
)


class _PanelContent(QWidget):
    """Stands in for a plugin window's content (module name decides scope)."""


_PanelContent.__module__ = "nika_onlymap_exporter.ui.fake_panel"

_BOUND_MS = 2000


@pytest.fixture
def panel(qgis_app):
    install_wheel_guard()
    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=a:int&field=b:int", "pts", "memory"
    )
    content = _PanelContent()
    lay = QVBoxLayout(content)
    lay.addSpacing(40)
    plain = QComboBox()
    plain.addItems([f"item {i}" for i in range(60)])
    plain.setCurrentIndex(5)
    layers = QgsMapLayerComboBox()
    fields = QgsFieldComboBox()
    fields.setLayer(layer)
    fields.setCurrentIndex(0)
    expression = QgsFieldExpressionWidget()
    expression.setLayer(layer)
    expression.setExpression('"a"')
    for widget in (plain, layers, fields, expression):
        lay.addWidget(widget)
        lay.addSpacing(30)
    lay.addSpacing(1500)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(content)
    area.resize(360, 300)
    area.show()
    QTest.qWaitForWindowExposed(area, _BOUND_MS)
    QApplication.processEvents()
    assert area.verticalScrollBar().maximum() > 200
    yield area, plain, layers, fields, expression
    area.close()
    remove_wheel_guard()


def _wheel(target: QWidget, delta: int = -120) -> None:
    centre = QPoint(target.width() // 2, target.height() // 2)
    QApplication.sendEvent(
        target,
        QWheelEvent(
            QPointF(centre),
            QPointF(target.mapToGlobal(centre)),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        ),
    )


def _under_pointer(area: QScrollArea, combo: QWidget) -> QWidget:
    """The widget Qt delivers a wheel at the middle of `combo` to."""
    content = area.widget()
    centre = combo.mapTo(content, QPoint(combo.width() // 2, combo.height() // 2))
    hit = content.childAt(centre)
    assert hit is not None
    # The deepest child, e.g. the line edit inside an expression widget.
    return hit


def _state(widget: QWidget) -> object:
    combos = (
        [widget] if isinstance(widget, QComboBox) else widget.findChildren(QComboBox)
    )
    combo = combos[0]
    return (combo.currentIndex(), combo.currentText())


@pytest.mark.parametrize(
    "which", [0, 1, 2, 3], ids=["combo", "layer", "field", "expression"]
)
def test_wheel_over_a_closed_dropdown_scrolls_the_panel_not_the_value(
    panel, which
) -> None:
    area, *widgets = panel
    widget = widgets[which]
    area.verticalScrollBar().setValue(0)
    before = _state(widget)
    scroll_before = area.verticalScrollBar().value()
    target = _under_pointer(area, widget)
    for _ in range(3):
        _wheel(target)
    assert _state(widget) == before
    assert area.verticalScrollBar().value() > scroll_before
    if which == 3:
        assert widget.expression() == '"a"'


@pytest.mark.parametrize("which", [0, 3], ids=["combo", "expression"])
def test_wheel_over_the_inner_children_of_a_dropdown_also_scrolls_the_panel(
    panel, which
) -> None:
    area, *widgets = panel
    widget = widgets[which]
    before = _state(widget)
    # What the pointer can be over inside a dropdown: the combo and, when it is
    # editable, its line edit. (The wrapper and its tool buttons are not
    # dropdowns, and a synthetic event sent to them would not climb by itself.)
    combos = (
        [widget] if isinstance(widget, QComboBox) else widget.findChildren(QComboBox)
    )
    leaves = [c for c in combos] + [c.lineEdit() for c in combos if c.lineEdit()]
    assert len(leaves) >= (2 if which == 3 else 1)
    for child in leaves:
        if not child.isVisible() or child.width() < 4 or child.height() < 4:
            continue
        area.verticalScrollBar().setValue(0)
        _wheel(child)
        assert _state(widget) == before
        assert area.verticalScrollBar().value() > 0, type(child).__name__


def test_an_open_popup_still_scrolls_its_own_list(panel) -> None:
    area, plain, *_ = panel
    area.verticalScrollBar().setValue(0)
    plain.showPopup()
    QApplication.processEvents()
    try:
        view = plain.view()
        assert view.isVisible()
        bar = view.verticalScrollBar()
        before = bar.value()
        _wheel(view.viewport())
        assert bar.value() > before
        assert plain.currentIndex() == 5
        assert area.verticalScrollBar().value() == 0
    finally:
        plain.hidePopup()


def test_keyboard_still_changes_the_value(panel) -> None:
    _, plain, _, fields, _ = panel
    plain.setFocus()
    before = plain.currentIndex()
    QTest.keyClick(plain, Qt.Key.Key_Down)
    assert plain.currentIndex() == before + 1
    fields.setFocus()
    QTest.keyClick(fields, Qt.Key.Key_Down)
    assert fields.currentIndex() == 1


def test_a_dropdown_outside_the_plugin_windows_keeps_the_wheel(qgis_app) -> None:
    install_wheel_guard()
    try:
        combo = QComboBox()
        combo.addItems(["a", "b", "c"])
        combo.show()
        combo.setCurrentIndex(0)
        _wheel(combo, -120)
        assert combo.currentIndex() == 1
        combo.close()
    finally:
        remove_wheel_guard()
