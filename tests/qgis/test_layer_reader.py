"""Reading one QGIS layer into the export model.

Needs PyQGIS; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    AssetDisposition,
    FidelityStatus,
    GeometryKind,
    PopupFieldMode,
    SourceKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import (
    export_geojson,
    read_layer,
    read_raster,
)
from nika_onlymap_exporter.packaging.raster_bake import _parsed

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

    def test_type_is_the_first_member_however_qgis_ordered_it(
        self, qgis_app, make_memory_layer
    ) -> None:
        """`QgsJsonExporter` writes top-level members alphabetically.

        Measured directly against a real export: a six-feature transit line
        layer put `"type"` at byte 132,172 of a 132,191-byte document, because
        `bbox` and `features` both sort before it and `features` is the bulk of
        the file. Every reader that trusts a small prefix to see the document's
        type - our own hosting conformance check included - needs `type` first,
        which is the conventional order every hand-written or GDAL-produced
        GeoJSON file already uses. `export_geojson` restores it regardless of
        what QGIS handed back.
        """
        layer = make_memory_layer("points", features=[("a", [1.0, 51.0])])
        collection = export_geojson(layer, FidelityReportBuilder())
        assert next(iter(collection)) == "type"
        assert collection["type"] == "FeatureCollection"


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


class TestReadRaster:
    """Reading a raster layer into the model.

    Every case here is one the reader has to tell apart *before* packaging gets
    involved: what reaches `RasterSpec`, what is refused, and what is refused
    loudly. Nothing in this class touches GDAL beyond writing the test file -
    converting to a COG is packaging's job, deliberately, so that a user who
    cancels an export has not had a file rewritten underneath them.
    """

    def test_a_file_backed_raster_is_read(self, qgis_app, make_raster_layer) -> None:
        layer = make_raster_layer(name="dem", west=4.0, north=52.0, pixel_size=0.25)
        report = FidelityReportBuilder()

        export_layer = read_raster(layer, report)

        assert export_layer is not None
        assert export_layer.is_raster is True
        assert export_layer.name == "dem"
        assert export_layer.geometry_kind is GeometryKind.RASTER
        assert export_layer.source_kind is SourceKind.FILE
        assert export_layer.geojson is None

        raster = export_layer.raster
        assert raster is not None
        assert raster.path == layer.source()
        assert raster.pixel_width == 4
        assert raster.pixel_height == 4
        assert raster.band_count == 1
        assert raster.source_crs == "EPSG:4326"
        # Left for packaging, which is the only half of the pipeline that has
        # GDAL and so the only one that can answer either of these.
        assert raster.src is None
        assert raster.is_cog is None

    def test_its_extent_is_wgs84_and_matches_the_file(
        self, qgis_app, make_raster_layer
    ) -> None:
        """The extent exists so the map opens on the raster - see
        `project_reader._data_extent`. A wrong one is a map pointed at the
        ocean, so the numbers are pinned rather than merely checked non-None."""
        layer = make_raster_layer(west=4.0, north=52.0, pixel_size=0.25, width=4)
        export_layer = read_raster(layer, FidelityReportBuilder())

        assert export_layer is not None and export_layer.raster is not None
        extent = export_layer.raster.extent
        assert extent is not None
        assert extent.west == pytest.approx(4.0)
        assert extent.east == pytest.approx(5.0)
        assert extent.north == pytest.approx(52.0)
        assert extent.south == pytest.approx(51.0)

    def test_a_projected_raster_is_reprojected_to_degrees(
        self, qgis_app, make_raster_layer
    ) -> None:
        """A metre extent handed to the map straight would put the layer
        thousands of degrees off the world."""
        layer = make_raster_layer(
            crs="EPSG:3857", west=445_000.0, north=6_800_000.0, pixel_size=1000.0
        )
        export_layer = read_raster(layer, FidelityReportBuilder())

        assert export_layer is not None and export_layer.raster is not None
        assert export_layer.raster.source_crs == "EPSG:3857"
        extent = export_layer.raster.extent
        assert extent is not None
        assert -180.0 <= extent.west <= 180.0
        assert -90.0 <= extent.south <= 90.0
        assert extent.west == pytest.approx(4.0, abs=0.1)

    def test_a_raster_with_no_crs_is_still_read(
        self, qgis_app, make_raster_layer
    ) -> None:
        """A file with no projection is common - a plain GeoTIFF from a
        scanner, a DEM someone stripped. It is not a reason to drop the layer:
        the pixels are still there and QGIS still draws them, so the reader
        records "no CRS" and lets packaging and the report deal with it."""
        layer = make_raster_layer(crs=None)
        report = FidelityReportBuilder()

        export_layer = read_raster(layer, report)

        assert export_layer is not None
        assert export_layer.raster is not None
        assert export_layer.raster.source_crs is None
        assert export_layer.raster.pixel_width == 4

    def test_a_missing_file_is_a_blocking_dependency(
        self, qgis_app, make_raster_layer
    ) -> None:
        """The layer is still read, and still says its pixels cannot travel.

        Returning `None` here would be worse: the export would go out one layer
        short with a note in a report nobody opens. A blocking dependency stops
        the export instead, which is the whole point of the dependency scan.
        """
        from pathlib import Path

        layer = make_raster_layer(name="gone")
        Path(layer.source()).unlink()
        report = FidelityReportBuilder()

        export_layer = read_raster(layer, report)

        assert export_layer is not None
        assert len(export_layer.dependencies) == 1
        dependency = export_layer.dependencies[0]
        assert dependency.disposition is AssetDisposition.BLOCKING
        assert "missing" in dependency.note

    def test_a_present_file_is_an_embeddable_dependency(
        self, qgis_app, make_raster_layer
    ) -> None:
        """The control for the case above."""
        layer = make_raster_layer()
        export_layer = read_raster(layer, FidelityReportBuilder())

        assert export_layer is not None
        dependency = export_layer.dependencies[0]
        assert dependency.disposition is AssetDisposition.EMBEDDABLE
        assert dependency.size_bytes is not None and dependency.size_bytes > 0

    def test_a_live_service_raster_is_refused_with_a_reason(self, qgis_app) -> None:
        """A `wms`/`xyz` layer has no file to convert, and snapshotting
        somebody else's tile server is a licensing decision this plugin does
        not get to make for the user."""
        layer = qgis_core.QgsRasterLayer(
            "type=xyz&url=https://example.invalid/%7Bz%7D/%7Bx%7D/%7By%7D.png",
            "tiles",
            "wms",
        )
        report = FidelityReportBuilder()

        assert read_raster(layer, report) is None
        details = " ".join(item.detail for item in report.items)
        assert "live raster service" in details

    def test_a_raster_reaches_read_raster_through_read_layer(
        self, qgis_app, make_raster_layer
    ) -> None:
        """The dispatch, not the reader: a caller that only knows `read_layer`
        must get the raster path without asking for it."""
        export_layer = read_layer(make_raster_layer(), FidelityReportBuilder())

        assert export_layer is not None
        assert export_layer.is_raster is True


class TestRasterColour:
    """The half of the raster story that only a real QGIS layer can check.

    The first attempt at carrying raster colour read a `colormap` name off the
    renderer's colour ramp. Every unit test of it passed, because the stand-ins
    exposed the API the code expected. Against a real project it fired for
    nobody: QGIS copies a named ramp's stops into an anonymous gradient, so a
    project loaded from disk has `sourceColorRamp() is None` and no name to
    send. These tests exist at this tier for exactly that reason - they are the
    ones the mistake could not have survived.
    """

    def test_a_ramped_raster_carries_its_style_for_baking(
        self, qgis_app, make_raster_layer
    ) -> None:
        from qgis.core import (
            QgsColorRampShader,
            QgsRasterShader,
            QgsSingleBandPseudoColorRenderer,
        )
        from qgis.PyQt.QtGui import QColor

        layer = make_raster_layer()
        shader_function = QgsColorRampShader(0, 255)
        shader_function.setColorRampItemList(
            [
                QgsColorRampShader.ColorRampItem(0, QColor("#0000ff"), "low"),
                QgsColorRampShader.ColorRampItem(255, QColor("#ff0000"), "high"),
            ]
        )
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(shader_function)
        layer.setRenderer(
            QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        )

        export_layer = read_raster(layer, FidelityReportBuilder())

        assert export_layer is not None and export_layer.raster is not None
        raster = export_layer.raster
        # The QML is what `packaging.raster_bake` renders with. A ramp built
        # like this one - which is how QGIS stores every ramp a user picks -
        # has no name anywhere in it, which is the whole point.
        assert raster.style_qml is not None
        assert "colorrampshader" in raster.style_qml.lower()
        assert raster.bands is None

    def test_the_style_can_be_applied_back_to_a_fresh_layer(
        self, qgis_app, make_raster_layer
    ) -> None:
        """The round trip the bake depends on.

        `raster_bake` reopens the file and applies this QML to it. If the
        document cannot be read back, every styled raster falls through to the
        unstyled path and the colours are lost again - silently, because the
        page still renders.
        """
        from qgis.core import QgsRasterLayer
        from qgis.PyQt.QtXml import QDomDocument

        layer = make_raster_layer()
        export_layer = read_raster(layer, FidelityReportBuilder())
        assert export_layer is not None and export_layer.raster is not None
        qml = export_layer.raster.style_qml
        assert qml is not None

        fresh = QgsRasterLayer(layer.source(), "fresh")
        document = QDomDocument()
        # Via the production helper, not a bare truthiness check: on Qt6
        # `setContent` returns a ParseResult, and a FAILED parse is a non-empty
        # and therefore truthy tuple. Asserting on the raw return here would
        # pass against a document that never parsed.
        assert _parsed(document.setContent(qml)) is True
        applied, message = fresh.importNamedStyle(document)
        assert applied is True, message

    def test_a_three_band_composite_is_carried_as_bands_not_baked(
        self, qgis_app, make_raster_layer
    ) -> None:
        # Its pixels are already the colours the author sees, and re-encoding an
        # orthophoto to RGBA would cost size and quality for nothing.
        from qgis.core import QgsMultiBandColorRenderer

        layer = make_raster_layer(bands=3)
        layer.setRenderer(QgsMultiBandColorRenderer(layer.dataProvider(), 1, 2, 3))

        export_layer = read_raster(layer, FidelityReportBuilder())

        assert export_layer is not None and export_layer.raster is not None
        assert export_layer.raster.bands == (1, 2, 3)
        assert export_layer.raster.style_qml is None

    def test_the_author_is_told_which_route_their_raster_took(
        self, qgis_app, make_raster_layer
    ) -> None:
        # Baking trades the measured values away for exact colour, and that is
        # not a trade to make silently.
        report = FidelityReportBuilder()
        read_raster(make_raster_layer(), report)

        colours = [i for i in report.items if i.subject.startswith("Colours of")]
        assert len(colours) == 1
        assert colours[0].status is FidelityStatus.PRESERVED
