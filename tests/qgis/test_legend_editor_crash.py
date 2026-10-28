"""Ticking legend entries in the editor hosted in the Edit tab must never take
QGIS down.

A tick reaches the tree as `QStyledItemDelegate::editorEvent` -> `setData` ->
`itemChanged`. The edit it makes lands on the layout's undo stack, whose
`indexChanged` refreshes the Edit tab, which re-lists the legend editor. If
that re-list empties the tree inside `itemChanged`, Qt then touches the freed
item and the whole process dies (a segfault in libQt6Widgets). A crash cannot
be caught in-process, so the scenario runs in a child process and the test
asserts it exits cleanly.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

# ruff: noqa: E402

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")

from legend_fixtures import build_world
from qgis.core import (
    QgsColorRampShader,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtCore import QPoint, QPointF, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication

from nika_onlymap_exporter.core import legend_edit as le
from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane
from nika_onlymap_exporter.ui.cartograph.legend_editor import LegendEditor

CHILD_FLAG = "Q2W_LEGEND_CRASH_CHILD"
ROUNDS = 6


def _pump() -> None:
    for _ in range(3):
        QApplication.processEvents()


def _band_layer(path: Path) -> QgsRasterLayer:
    """A raster drawn in a few discrete bands, like "Elevation bands"."""
    from legend_fixtures import write_tif

    layer = QgsRasterLayer(str(write_tif(path)), "Elevation bands")
    assert layer.isValid()
    shader = QgsColorRampShader()
    shader.setColorRampType(QgsColorRampShader.Discrete)
    shader.setColorRampItemList(
        [
            QgsColorRampShader.ColorRampItem(value, QColor(colour), label)
            for value, colour, label in (
                (500, "#2b8cbe", "0 - 500 m"),
                (1000, "#7bccc4", "500 - 1000 m"),
                (1500, "#fdae61", "1000 - 1500 m"),
                (2000, "#d7191c", "1500 - 2000 m"),
            )
        ]
    )
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader)
    layer.setRenderer(
        QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, raster_shader)
    )
    return layer


def _tick(editor: LegendEditor, layer_id: str, index: int | None) -> None:
    """Click the row's check box in the tree's viewport, like a user."""
    item = editor.item_for(layer_id, index)
    editor.tree.scrollToItem(item)
    rect = editor.tree.visualItemRect(item)
    QTest.mouseClick(
        editor.tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(rect.left() + 9, rect.center().y()),
    )
    _pump()


@pytest.mark.skipif(not os.environ.get(CHILD_FLAG), reason="runs in a child process")
def test_ticking_whole_layers_and_classes_in_the_hosted_editor(
    qgis_app, project, tmp_path
) -> None:
    world = build_world(project, tmp_path / "data")
    bands = _band_layer(tmp_path / "data" / "bands.tif")
    project.addMapLayer(bands)
    legend = next(i for i in world.layout.items() if isinstance(i, QgsLayoutItemLegend))
    main = next(i for i in world.layout.items() if isinstance(i, QgsLayoutItemMap))
    main.setLayers([*main.layers(), bands])
    le.restore_removed(world.layout, legend, project, "list the bands")

    pane = PagePane()
    pane.resize(900, 640)
    pane.show_layout(world.layout)
    pane.show()
    pane.controls.resize(420, 760)
    pane.controls.show()
    _pump()
    pane.preview._fit()
    _pump()
    centre = pane.preview.view.mapFromScene(
        QPointF(legend.sceneBoundingRect().center())
    )
    QTest.mouseDClick(
        pane.preview.view.viewport(), Qt.MouseButton.LeftButton, pos=centre
    )
    _pump()
    editor = pane.controls._legend_editor
    assert isinstance(editor, LegendEditor) and editor.isVisible()
    assert editor.legend() is legend

    stack = world.layout.undoStack().stack()
    land, rest = world.layers["land"].id(), bands.id()
    for _ in range(ROUNDS):
        for layer_id, index in (
            (rest, 1),
            (land, 3),
            (rest, 1),
            (land, 3),
            (rest, None),
            (land, None),
            (land, None),
            (rest, None),
        ):
            before = stack.index()
            _tick(editor, layer_id, index)
            assert stack.index() == before + 1
    # Every tick pair undid itself: the legend lists what it started with.
    listed = {n.layerId() for n in legend.model().rootGroup().findLayers()}
    assert land in listed and rest in listed
    pane.close()


def test_ticking_entries_never_takes_the_process_down() -> None:
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, CHILD_FLAG: "1", "QT_QPA_PLATFORM": "offscreen"}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-x",
            f"{__file__}::test_ticking_whole_layers_and_classes_in_the_hosted_editor",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
