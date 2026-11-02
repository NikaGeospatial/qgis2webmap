"""A template's title fits the box the template planned for it: the line
estimate that sizes the box agrees with how QGIS really wraps the text, so a
long title never runs past its block onto the map or the rule under it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from qgis.core import QgsCoordinateReferenceSystem, QgsLayoutItemLabel, QgsRectangle
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QFontMetricsF

from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view
from nika_onlymap_exporter.core.template_registry import starter_templates

PICKER_DESIGNS = tuple(t.id for t in starter_templates())
#: Titles that wrap to several lines in a narrow title block.
TITLES = (
    "Grand Canyon | standard cartography test project",
    "Colorado River corridor: water-quality stations and land tenure",
    "Regional reference map of the northern rim and inner canyon",
)
#: Slack for the box's own padding, in millimetres.
TOLERANCE_MM = 0.5
MM_PER_POINT = 25.4 / 72


def _wrapped_height_mm(label: QgsLayoutItemLabel, text: str) -> float:
    """How tall QGIS draws `text` wrapped to the label's width: Qt's own word
    wrap in the label's font, measured in points (a font of N points taking N
    units of height) and converted to millimetres."""
    text_format = label.textFormat()
    font = text_format.font()
    font.setPixelSize(round(text_format.size() * 100))
    metrics = QFontMetricsF(font)
    width = label.rect().width() / MM_PER_POINT * 100
    box = metrics.boundingRect(
        QRectF(0, 0, width, 1e7), int(Qt.TextFlag.TextWordWrap), text
    )
    line = text_format.lineHeight() if text_format.lineHeightUnit() == 1 else 1.0
    return box.height() / 100 * MM_PER_POINT * max(line, 1.0)


@pytest.mark.parametrize("page_size", ["A4", "A3"])
@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("template_id", PICKER_DESIGNS)
def test_the_title_fits_its_box(project, template_id, title, page_size) -> None:
    project.setTitle(title)
    crs = QgsCoordinateReferenceSystem("EPSG:32612")
    made = cartograph_from_view(
        QgsRectangle(350000, 3980000, 440000, 4040000),
        crs,
        [],
        project,
        template_id=template_id,
        page_size=page_size,
        orientation="landscape",
    )
    label = made.layout.itemById("title")
    if label is None:
        pytest.skip("this design shows no title")
    assert isinstance(label, QgsLayoutItemLabel)
    needed = _wrapped_height_mm(label, title)
    assert needed <= label.rect().height() + TOLERANCE_MM, (
        f"{template_id} {page_size}: title needs {needed:.1f} mm "
        f"in a {label.rect().height():.1f} mm box"
    )
