"""Busy keys and real project notes must not disappear behind key figures."""

import json
from pathlib import Path

import pytest
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsLayoutItemLegend,
    QgsLayoutUtils,
    QgsLegendRenderer,
    QgsLegendStyle,
    QgsMarkerSymbol,
    QgsRectangle,
    QgsRendererCategory,
    QgsVectorLayer,
)

from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view


@pytest.mark.parametrize("size", ["A4", "A3"])
def test_signal_keeps_every_class_with_three_figures_and_long_notes(project, size):
    data = json.loads(
        (Path(__file__).parents[1] / "fixtures/dense_template_input.json").read_text()
    )
    project.setTitle(data["title"])
    metadata = project.metadata()
    metadata.setAbstract(data["notes"])
    project.setMetadata(metadata)
    layers = []
    for name, labels in zip(data["layer_names"], data["legend_labels"]):
        layer = QgsVectorLayer(
            "Point?crs=EPSG:32612&field=class:string", name, "memory"
        )
        if len(labels) > 1:
            symbol = QgsMarkerSymbol.createSimple({"name": "circle", "size": "2"})
            layer.setRenderer(
                QgsCategorizedSymbolRenderer(
                    "class",
                    [
                        QgsRendererCategory(str(i), symbol.clone(), label)
                        for i, label in enumerate(labels)
                    ],
                )
            )
        project.addMapLayer(layer)
        layers.append(layer)
    extent = QgsRectangle(
        300000, 4000000, 300000 + data["extent_width"], 4000000 + data["extent_height"]
    )
    result = cartograph_from_view(
        extent,
        QgsCoordinateReferenceSystem("EPSG:32612"),
        layers,
        project,
        template_id="dashboard",
        page_size=size,
        orientation="landscape",
        figures=[(value, label) for value, label in data["figures"]],
    )
    assert result.legend_left_out == 0
    legend = next(
        item for item in result.layout.items() if isinstance(item, QgsLayoutItemLegend)
    )
    assert len(legend.model().rootGroup().findLayers()) == len(layers)
    assert legend.rstyle(QgsLegendStyle.SymbolLabel).textFormat().size() >= 8
    context = QgsLayoutUtils.createRenderContextForLayout(result.layout, None)
    needed = QgsLegendRenderer(legend.model(), legend.legendSettings()).minimumSize(
        context
    )
    assert needed.width() <= legend.sizeWithUnits().width() + 0.1
    assert needed.height() <= legend.sizeWithUnits().height() + 0.1
    for i, (value, _) in enumerate(data["figures"]):
        assert result.layout.itemById(f"figure-{i}").text() == value
