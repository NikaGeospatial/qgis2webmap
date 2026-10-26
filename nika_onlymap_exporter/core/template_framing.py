"""Template framing: crop the view, never rotate or rewrite source data.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

import math
from contextlib import contextmanager
from contextvars import ContextVar

FRAMING_VARIABLE = "nika_template_framing"
_preview = ContextVar("template_framing", default=None)


def project_framing(project):
    from qgis.core import QgsExpressionContextUtils

    value = _preview.get()
    if value is None:
        value = QgsExpressionContextUtils.projectScope(project).variable(
            FRAMING_VARIABLE
        )
    return "fill" if value == "fill" else "whole"


@contextmanager
def preview_framing(mode):
    """A picker preview can try a fit without changing the user's project."""
    token = _preview.set(mode)
    try:
        yield
    finally:
        _preview.reset(token)


def set_project_framing(project, mode):
    from qgis.core import QgsExpressionContextUtils

    scope = QgsExpressionContextUtils.projectScope(project)
    if scope.variable(FRAMING_VARIABLE) != mode:
        QgsExpressionContextUtils.setProjectVariable(project, FRAMING_VARIABLE, mode)


def fill_view(view, aspect, crs, layers, project, rotation=0.0):
    """Largest centred frame inside the view and local raster footprints.

    Source edges are densified before projection. A rotated map is tested in
    its actual orientation. Remote layers and raster pixels are never read.
    An irregular no-data mask is outside this extent-only framing operation.
    """
    from qgis.core import (
        QgsCoordinateTransform,
        QgsGeometry,
        QgsPointXY,
        QgsRasterLayer,
        QgsRectangle,
    )

    width = min(view.width(), view.height() * aspect)
    height = width / aspect
    centre = view.center()
    angle = math.radians(rotation)
    cosine, sine = math.cos(angle), math.sin(angle)

    def rectangle(scale):
        points = []
        for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)):
            x, y = dx * width * scale / 2, dy * height * scale / 2
            points.append(
                QgsPointXY(
                    centre.x() + x * cosine - y * sine,
                    centre.y() + x * sine + y * cosine,
                )
            )
        return QgsGeometry.fromPolygonXY([points])

    footprints = []
    for layer in layers:
        if not isinstance(layer, QgsRasterLayer) or layer.providerType() != "gdal":
            continue
        bounds = layer.extent()
        if bounds.isEmpty() or not layer.crs().isValid():
            continue
        transform = QgsCoordinateTransform(layer.crs(), crs, project)
        corners = [
            (bounds.xMinimum(), bounds.yMinimum()),
            (bounds.xMaximum(), bounds.yMinimum()),
            (bounds.xMaximum(), bounds.yMaximum()),
            (bounds.xMinimum(), bounds.yMaximum()),
        ]
        ring = []
        try:
            for i, start in enumerate(corners):
                end = corners[(i + 1) % 4]
                for step in range(16):
                    fraction = step / 16
                    ring.append(
                        transform.transform(
                            QgsPointXY(
                                start[0] + fraction * (end[0] - start[0]),
                                start[1] + fraction * (end[1] - start[1]),
                            )
                        )
                    )
        except Exception:  # an unavailable transform leaves normal view fitting
            continue
        if not all(math.isfinite(p.x()) and math.isfinite(p.y()) for p in ring):
            continue
        ring.append(ring[0])
        footprints.append(QgsGeometry.fromPolygonXY([ring]))
    scale = 1.0
    if footprints:
        coverage = QgsGeometry.unaryUnion(footprints)
        # Keep the requested centre. A view centred outside the data should
        # not unexpectedly jump to a different place.
        if coverage.contains(QgsGeometry.fromPointXY(centre)):
            low, high = 0.0, 1.0
            for _ in range(35):
                middle = (low + high) / 2
                if coverage.contains(rectangle(middle)):
                    low = middle
                else:
                    high = middle
            scale = low * 0.997  # avoid exposing a half-pixel source edge
    return QgsRectangle(
        centre.x() - width * scale / 2,
        centre.y() - height * scale / 2,
        centre.x() + width * scale / 2,
        centre.y() + height * scale / 2,
    )
