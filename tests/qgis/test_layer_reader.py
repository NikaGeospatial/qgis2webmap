"""Reading one QGIS layer into the export model.

Needs PyQGIS; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import export_geojson

pytest.importorskip("qgis.core")


class TestClipToExtent:
    """Exporting only what is on screen.

    The team's report: choosing "current QGIS view" exported every feature
    anyway, because that setting only frames the map. This is the missing half -
    and the practical way to bring a very large layer under the free plan's
    25,000-feature limit.
    """

    @staticmethod
    def _extent(west, south, east, north):
        from nika_onlymap_exporter.core.export_ir import Extent

        return Extent(west=west, south=south, east=east, north=north)

    def test_features_outside_the_view_are_not_exported(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = make_memory_layer(
            "points",
            features=[("inside", [1.0, 51.0]), ("outside", [40.0, 51.0])],
        )
        report = FidelityReportBuilder()

        collection = export_geojson(
            layer, report, clip_extent=self._extent(0.0, 50.0, 2.0, 52.0)
        )
        names = [f["properties"]["name"] for f in collection["features"]]
        assert names == ["inside"]

    def test_without_a_clip_every_feature_is_exported(
        self, qgis_app, make_memory_layer
    ) -> None:
        """The control: clipping must be the only thing that removes anything."""
        layer = make_memory_layer(
            "points",
            features=[("inside", [1.0, 51.0]), ("outside", [40.0, 51.0])],
        )
        collection = export_geojson(layer, FidelityReportBuilder())
        assert len(collection["features"]) == 2

    def test_the_report_says_how_many_were_dropped(
        self, qgis_app, make_memory_layer
    ) -> None:
        """What is missing leaves no gap on the map, so it must be reported."""
        layer = make_memory_layer(
            "points",
            features=[("inside", [1.0, 51.0]), ("outside", [40.0, 51.0])],
        )
        report = FidelityReportBuilder()
        export_geojson(layer, report, clip_extent=self._extent(0.0, 50.0, 2.0, 52.0))

        details = " ".join(item.detail for item in report.items)
        assert "1 of 2" in details
        assert "1 are left out" in details

    def test_a_clip_containing_everything_says_so(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = make_memory_layer("points", features=[("a", [1.0, 51.0])])
        report = FidelityReportBuilder()
        export_geojson(layer, report, clip_extent=self._extent(-10.0, 40.0, 10.0, 60.0))

        details = " ".join(item.detail for item in report.items)
        assert "removed nothing" in details
