"""A small Grand-Canyon-like world for the Go to location tests.

Not a conftest: imported explicitly, like `cartograph_fixtures`. The frame is
drawn in UTM 12N (EPSG:32612) while every layer is stored in WGS 84, and the
project itself is in Web Mercator, so latitude/longitude typed in the dialog
has to cross two coordinate systems before it lands in the frame - the same
shape as the real Canyon demo project.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemMap,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsRendererCategory,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QRectF, Qt, QTimer
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication

FRAME_CRS = "EPSG:32612"
PROJECT_CRS = "EPSG:3857"
#: Where the frame looks to begin with (WGS 84 longitude, latitude).
HOME = (-112.0, 36.1)
GO_TO_LOCATION = "Go to location…"


def wgs_to_frame(lon: float, lat: float) -> QgsPointXY:
    """The independent oracle: a point in UTM 12N, built with an explicit
    (x = longitude, y = latitude) point so a swapped pair would not match."""
    transform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsCoordinateReferenceSystem(FRAME_CRS),
        QgsProject.instance().transformContext(),
    )
    return transform.transform(QgsPointXY(lon, lat))


def _memory_layer(
    project: QgsProject, geometry: str, name: str, fields: str
) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{geometry}?crs=EPSG:4326&{fields}", name, "memory")
    assert layer.isValid()
    project.addMapLayer(layer)
    return layer


def _add(layer: QgsVectorLayer, rows: list[tuple[list[object], QgsGeometry | None]]):
    features = []
    for attributes, geometry in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes(attributes)
        if geometry is not None:
            feature.setGeometry(geometry)
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()


def _pt(lon: float, lat: float) -> QgsGeometry:
    return QgsGeometry.fromPointXY(QgsPointXY(lon, lat))


@dataclass
class World:
    project: QgsProject
    layout: QgsPrintLayout
    main: QgsLayoutItemMap
    inset: QgsLayoutItemMap
    viewpoints: QgsVectorLayer
    trails: QgsVectorLayer
    areas: QgsVectorLayer
    elsewhere: QgsVectorLayer
    ids: dict[str, int] = field(default_factory=dict)


def build_world(project: QgsProject, rotation: float = 0.0) -> World:
    """Layers, a layout with two maps, and the main frame looking at `HOME`."""
    project.setCrs(QgsCoordinateReferenceSystem(PROJECT_CRS))

    viewpoints = _memory_layer(
        project,
        "Point",
        "Viewpoints",
        "field=name:string&field=station_id:string&field=sid:integer",
    )
    # Feature ids follow insertion order from 1: the attribute `sid` is chosen
    # to differ from the QGIS ID (sid 3 belongs to QGIS ID 7, and vice versa).
    _add(
        viewpoints,
        [
            (["Hance Trailhead", "STA-001", 1001], _pt(-112.00, 36.05)),  # 1
            (["Desert View", "STA-013", 1013], _pt(-111.98, 36.12)),  # 2
            (["Desert View", "STA-014", 1014], _pt(-112.05, 36.15)),  # 3
            (["Mather Point", "STA-020", 1020], _pt(-112.10, 36.10)),  # 4
            (["Lookout 50%_off", "STA-021", 1021], _pt(-112.02, 36.08)),  # 5
            (["", "STA-022", 1022], _pt(-112.01, 36.09)),  # 6 (no name)
            (["Lipan Point", "STA-023", 3], _pt(-111.95, 36.03)),  # 7: sid = 3
            (["No Location", "STA-024", 1024], None),  # 8
            (["Far Away Point", "STA-025", 1025], _pt(-110.20, 35.20)),  # 9
            (["O'Neil Overlook", "STA-026", 1026], _pt(-112.03, 36.11)),  # 10
        ],
    )
    trails = _memory_layer(
        project, "LineString", "Trails", "field=name:string&field=code:string"
    )
    _add(
        trails,
        [
            (
                ["Bright Angel", "T-1"],
                QgsGeometry.fromPolylineXY(
                    [
                        QgsPointXY(-112.14, 36.05),
                        QgsPointXY(-112.10, 36.09),
                        QgsPointXY(-112.06, 36.06),
                    ]
                ),
            ),
        ],
    )
    areas = _memory_layer(project, "Polygon", "Areas", "field=name:string")
    # A "U" whose centroid lies in the notch, outside the polygon.
    u_shape = QgsGeometry.fromPolygonXY(
        [
            [
                QgsPointXY(-112.12, 36.00),
                QgsPointXY(-112.04, 36.00),
                QgsPointXY(-112.04, 36.10),
                QgsPointXY(-112.06, 36.10),
                QgsPointXY(-112.06, 36.02),
                QgsPointXY(-112.10, 36.02),
                QgsPointXY(-112.10, 36.10),
                QgsPointXY(-112.12, 36.10),
            ]
        ]
    )
    small = QgsGeometry.fromPolygonXY(
        [
            [
                QgsPointXY(-111.90, 36.00),
                QgsPointXY(-111.89, 36.00),
                QgsPointXY(-111.89, 36.01),
                QgsPointXY(-111.90, 36.01),
            ]
        ]
    )
    multi = QgsGeometry.collectGeometry([small, u_shape])
    _add(
        areas,
        [
            (["U Basin"], u_shape),  # 1
            (["Twin Parcels"], multi),  # 2 (multipart, the U is the larger part)
        ],
    )
    elsewhere = _memory_layer(project, "Point", "Hidden by style", "field=kind:string")
    _add(elsewhere, [(["shown"], _pt(-112.0, 36.1)), (["hidden"], _pt(-112.01, 36.11))])
    renderer = QgsCategorizedSymbolRenderer("kind", [])
    renderer.addCategory(
        QgsRendererCategory("shown", QgsMarkerSymbol.createSimple({}), "shown", True)
    )
    renderer.addCategory(
        QgsRendererCategory("hidden", QgsMarkerSymbol.createSimple({}), "hidden", False)
    )
    elsewhere.setRenderer(renderer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Canyon")
    frame_crs = QgsCoordinateReferenceSystem(FRAME_CRS)
    main = QgsLayoutItemMap(layout)
    main.attemptSetSceneRect(QRectF(10, 10, 200, 100))
    main.setCrs(frame_crs)
    main.setId("Main map")
    layout.addLayoutItem(main)
    main.setLayers([viewpoints, trails, areas, elsewhere])
    main.setKeepLayerSet(True)
    centre = wgs_to_frame(*HOME)
    main.setExtent(QgsRectangle.fromCenterAndSize(centre, 20000, 10000))
    main.setMapRotation(rotation)

    inset = QgsLayoutItemMap(layout)
    inset.attemptSetSceneRect(QRectF(10, 130, 100, 60))
    inset.setCrs(frame_crs)
    inset.setId("Locator")
    layout.addLayoutItem(inset)
    inset.setLayers([viewpoints])
    inset.setKeepLayerSet(True)
    inset.setExtent(QgsRectangle.fromCenterAndSize(centre, 60000, 36000))
    project.layoutManager().addLayout(layout)
    return World(project, layout, main, inset, viewpoints, trails, areas, elsewhere)


def fid_of(layer: QgsVectorLayer, **attributes: object) -> int:
    """The QGIS ID of the one feature whose attributes match."""
    for feature in layer.getFeatures():
        if all(feature[k] == v for k, v in attributes.items()):
            return feature.id()
    raise LookupError(attributes)


def frame_centre(item: QgsLayoutItemMap) -> QgsPointXY:
    return item.extent().center()


def go_action(card):
    """The card's ⋯ menu row, found the way a user finds it: in the menu."""
    for action in card.actions_button.menu().actions():
        if action.text() == GO_TO_LOCATION:
            return action
    raise LookupError("the Go to location… row is not in the menu")


def wait_for(
    condition: Callable[[], bool], limit_ms: int = 8000, what: str = ""
) -> None:
    """Let the event loop run until `condition()`; fail loudly when it never holds."""
    waited = 0
    while not condition():
        if waited >= limit_ms:
            raise AssertionError(f"timed out after {limit_ms} ms waiting for {what}")
        QTest.qWait(20)
        waited += 20


def open_dialog(action, script: Callable[[object], None], limit_ms: int = 25000):
    """Trigger `action` (which opens the modal dialog) and run `script(dialog)`
    from the dialog's own event loop. The dialog is closed afterwards if the
    script left it open. Any exception in `script` is re-raised here."""
    outcome: dict[str, object] = {}

    def drive() -> None:
        dialog = QApplication.activeModalWidget()
        outcome["dialog"] = dialog
        try:
            # Focus changes only reach widgets of the active window.
            dialog.activateWindow()
            QTest.qWaitForWindowActive(dialog, 3000)
            script(dialog)
        except BaseException:  # reported to the test, never lost in a slot
            outcome["error"] = traceback.format_exc()
        finally:
            if dialog is not None and dialog.isVisible():
                dialog.reject()

    def give_up() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            outcome["error"] = f"the dialog stayed open for {limit_ms} ms"
            dialog.reject()

    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(give_up)
    watchdog.start(limit_ms)
    QTimer.singleShot(0, drive)
    action.trigger()
    watchdog.stop()
    if "error" in outcome:
        raise AssertionError(outcome["error"])
    return outcome.get("dialog")


def type_text(edit, text: str) -> None:
    edit.setFocus()
    edit.clear()
    QTest.keyClicks(edit, text)


def press(widget, key: Qt.Key) -> None:
    QTest.keyClick(widget, key)


def choose_crs(dialog, authid: str) -> None:
    """Click Change… and answer QGIS's own coordinate-system chooser."""
    from qgis.gui import QgsProjectionSelectionDialog

    def answer() -> None:
        chooser = QApplication.activeModalWidget()
        assert isinstance(chooser, QgsProjectionSelectionDialog), chooser
        chooser.setCrs(QgsCoordinateReferenceSystem(authid))
        chooser.accept()

    QTimer.singleShot(50, answer)
    dialog.crs_button.click()


def fill_coordinates(dialog, first: str, second: str) -> None:
    """Type both fields like a user and leave the second one."""
    type_text(dialog.first_edit, first)
    type_text(dialog.second_edit, second)
    dialog.cancel_button.setFocus()
    QApplication.processEvents()


def rows(dialog) -> list[tuple[str, str]]:
    """The result list as the user reads it: (name, second line) per row."""
    found = []
    for index in range(dialog.results.count()):
        item = dialog.results.item(index)
        found.append(
            (
                str(item.data(Qt.ItemDataRole.UserRole + 1)),
                str(item.data(Qt.ItemDataRole.UserRole + 2)),
            )
        )
    return found


def choose_layer(dialog, layer) -> None:
    dialog.tabs.setCurrentIndex(1)
    index = dialog.layer_combo.findData(layer.id())
    assert index >= 0, f"{layer.name()} is not offered"
    dialog.layer_combo.setCurrentIndex(index)


def choose_source(dialog, label: str) -> None:
    index = dialog.source_combo.findText(label)
    assert index >= 0, [
        dialog.source_combo.itemText(i) for i in range(dialog.source_combo.count())
    ]
    dialog.source_combo.setCurrentIndex(index)


def search(dialog, text: str) -> None:
    """Type the query and click Search, then wait until the dialog has an answer."""
    type_text(dialog.query_edit, text)
    dialog.search_button.click()
    wait_for(
        lambda: dialog.status_label.text() not in ("", "Searching…"),
        what=f"the search for {text!r} to finish",
    )


def click_row(dialog, row: int, double: bool = False) -> None:
    item = dialog.results.item(row)
    point = dialog.results.visualItemRect(item).center()
    viewport = dialog.results.viewport()
    if double:
        # What a real double click delivers: a press and release, then the
        # double-click event and its release.
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=point)
    else:
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point)


def many_points(
    project: QgsProject, count: int, name: str = "Many sites"
) -> QgsVectorLayer:
    """`count` points called "Site 000001"... on a grid near HOME, one layer, one
    provider call (so building 200 000 of them takes a moment, not minutes)."""
    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string&field=code:string", name, "memory"
    )
    features = []
    for index in range(count):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([f"Site {index:06d}", f"C{index:06d}"])
        feature.setGeometry(
            QgsGeometry.fromPointXY(
                QgsPointXY(
                    -112.5 + (index % 500) * 0.001, 35.8 + (index // 500) * 0.0005
                )
            )
        )
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    project.addMapLayer(layer)
    return layer
