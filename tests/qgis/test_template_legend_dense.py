"""A crowded legend in every picker design: never clipped, never set below
the readable floor, and honest about what a page could not hold.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest
from legend_fixtures import EXTENT, WGS84, build_world
from qgis.core import (
    QgsLayoutItemLegend,
    QgsLayoutUtils,
    QgsLegendRenderer,
    QgsLegendStyle,
)

from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view
from nika_onlymap_exporter.core.layout_reader import LEGEND_LEFT_OUT_PROPERTY
from nika_onlymap_exporter.core.layout_templates import starter_templates

#: Designs that draw a legend at all.
LEGEND_DESIGNS = tuple(
    t.id for t in starter_templates() if t.id not in {"map-only", "report-figure"}
)
#: A box measured by QGIS may differ from its planned size by this much.
TOLERANCE_MM = 0.5


def _legend_of(made) -> QgsLayoutItemLegend:
    return next(i for i in made.layout.items() if isinstance(i, QgsLayoutItemLegend))


def _build(project, tmp_path: Path, template_id: str, page_size: str):
    world = build_world(project, tmp_path, classes=30)
    return cartograph_from_view(
        EXTENT,
        WGS84,
        list(world.layers.values()),
        project,
        template_id=template_id,
        page_size=page_size,
        orientation="landscape",
    )


@pytest.mark.parametrize("page_size", ["A4", "A3"])
@pytest.mark.parametrize("template_id", LEGEND_DESIGNS)
def test_a_dense_legend_is_never_clipped_or_shrunk(
    project, tmp_path, template_id, page_size
) -> None:
    made = _build(project, tmp_path, template_id, page_size)
    legend = _legend_of(made)
    context = QgsLayoutUtils.createRenderContextForLayout(made.layout, None)
    size = QgsLegendRenderer(legend.model(), legend.legendSettings()).minimumSize(
        context
    )
    box = legend.sizeWithUnits()
    assert size.width() <= box.width() + TOLERANCE_MM
    assert size.height() <= box.height() + TOLERANCE_MM
    rows = legend.legendSettings().style(QgsLegendStyle.Style.SymbolLabel)
    assert rows.textFormat().size() >= made.plan.legend_min_pt - 1e-6
    recorded = legend.customProperty(LEGEND_LEFT_OUT_PROPERTY)
    assert int(recorded or 0) == made.legend_left_out
