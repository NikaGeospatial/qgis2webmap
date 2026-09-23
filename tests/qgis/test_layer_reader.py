"""Reading one QGIS layer into the export model.

Needs PyQGIS; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import PopupFieldMode
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import export_geojson, read_layer

qgis_core = pytest.importorskip("qgis.core")


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


class TestHiddenDataLeavesTheFile:
    """Attributes the user hid must not be in the artifact.

    The dialog and the guide both say unticking Popups keeps attribute data out
    of the exported file, and people send maps on the strength of that. The
    export wrote every attribute regardless, so the values sat in plain text in
    the GeoJSON. These tests are the promise.
    """

    @staticmethod
    def _properties(export_layer):
        return [f["properties"] for f in export_layer.geojson["features"]]

    def _layer(self, make_memory_layer):
        return make_memory_layer(
            "people",
            fields="name:string&field=salary:string",
            features=[("Ada", [1.0, 51.0])],
        )

    def test_unticking_popups_strips_every_attribute(
        self, qgis_app, make_memory_layer
    ) -> None:
        export_layer = read_layer(
            self._layer(make_memory_layer),
            FidelityReportBuilder(),
            with_popup=False,
        )

        assert self._properties(export_layer) == [{}]

    def test_a_hidden_field_leaves_while_the_shown_ones_stay(
        self, qgis_app, make_memory_layer
    ) -> None:
        export_layer = read_layer(
            self._layer(make_memory_layer),
            FidelityReportBuilder(),
            field_modes={"salary": PopupFieldMode.HIDDEN.value},
        )

        properties = self._properties(export_layer)[0]
        assert "salary" not in properties
        assert properties["name"] == "Ada"

    def test_popups_on_keep_the_attributes_they_show(
        self, qgis_app, make_memory_layer
    ) -> None:
        """The control: stripping must only ever remove what was hidden."""
        export_layer = read_layer(
            self._layer(make_memory_layer), FidelityReportBuilder()
        )

        properties = self._properties(export_layer)[0]
        assert set(properties) == {"name", "salary"}
        assert properties["name"] == "Ada"

    def test_a_field_the_map_labels_with_survives_being_hidden(
        self, qgis_app, make_memory_layer
    ) -> None:
        """Stripping data must not break drawing.

        A label reads its text from the attribute, so removing the field the
        user labelled with would empty every label on the map rather than hide
        anything - the field is on screen either way.
        """
        layer = self._layer(make_memory_layer)
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)

        export_layer = read_layer(layer, FidelityReportBuilder(), with_popup=False)

        assert self._properties(export_layer) == [{"name": "Ada"}]


class TestZGeometryReachesLabelling:
    """The gap that let a PolygonZ layer abort a whole export.

    Every demo project, fixture and unit case in this repo is flat, so nothing
    ever proved what QGIS emits for a layer whose wkbType carries Z - and the
    label centroid read each vertex as `(x, y)`. A user on 0.1.4 with a Z
    polygon and labelling on got `ValueError: too many values to unpack`.

    These run against real PyQGIS on purpose: the unit tier can assert what the
    centroid does with a three-element vertex, but only this tier can prove
    QGIS hands us one.
    """

    @staticmethod
    def _z_polygon_layer(qgis_core_module, wkt: str):
        layer = qgis_core_module.QgsVectorLayer(
            "PolygonZ?crs=EPSG:4326&field=name:string", "peaks", "memory"
        )
        assert layer.isValid()
        feature = qgis_core_module.QgsFeature(layer.fields())
        feature.setAttribute(0, "Everest")
        feature.setGeometry(qgis_core_module.QgsGeometry.fromWkt(wkt))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        return layer

    def test_qgis_exports_z_polygons_with_three_element_vertices(
        self, qgis_app
    ) -> None:
        """Pins the upstream behaviour the bug depended on.

        If a future QGIS or a `setPrecision` change ever drops Z here, this
        fails and tells us the guard below is no longer load-bearing - rather
        than leaving a test that passes for the wrong reason.
        """
        layer = self._z_polygon_layer(
            qgis_core, "POLYGON Z ((0 0 8849,1 0 8849,1 1 8849,0 1 8849,0 0 8849))"
        )

        collection = export_geojson(layer, FidelityReportBuilder())

        assert collection is not None
        ring = collection["features"][0]["geometry"]["coordinates"][0]
        assert len(ring[0]) == 3

    def test_a_labelled_z_polygon_exports_instead_of_raising(self, qgis_app) -> None:
        from nika_onlymap_exporter.core.label_points import (
            LABEL_PROPERTY,
            build_label_collection,
        )

        layer = self._z_polygon_layer(
            qgis_core, "POLYGON Z ((0 0 8849,2 0 8849,2 2 8849,0 2 8849,0 0 8849))"
        )

        collection = export_geojson(layer, FidelityReportBuilder())
        labels = build_label_collection(collection, "name")

        assert labels is not None
        assert labels["features"][0]["properties"][LABEL_PROPERTY] == "Everest"
        assert labels["features"][0]["geometry"]["coordinates"] == [1.0, 1.0]

    def test_a_labelled_z_multipolygon_exports_too(self, qgis_app) -> None:
        from nika_onlymap_exporter.core.label_points import build_label_collection

        layer = self._z_polygon_layer(
            qgis_core,
            "MULTIPOLYGON Z (((10 10 1,10.1 10 1,10.1 10.1 1,10 10.1 1,10 10 1)),"
            "((0 0 1,2 0 1,2 2 1,0 2 1,0 0 1)))",
        )

        collection = export_geojson(layer, FidelityReportBuilder())
        labels = build_label_collection(collection, "name")

        assert labels is not None
        # The larger part, as for a flat multipolygon.
        assert labels["features"][0]["geometry"]["coordinates"] == [1.0, 1.0]
