"""Web sources QGIS reads for vector and raster layers: OGC API Features,
SensorThings and online Cloud Optimized GeoTIFFs.

Nothing here reaches a real service: the vector layers point at the discard
port, which is what a project opened offline looks like, and still carry
their provider key.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import SourceKind
from nika_onlymap_exporter.core.layer_reader import describe_source, source_kind

qgis_core = pytest.importorskip("qgis.core")

OFFLINE = "http://127.0.0.1:9"


class TestWebFeatureSources:
    @pytest.mark.parametrize(
        ("source", "provider"),
        [
            (f"url='{OFFLINE}/ogcapi' typename='buildings'", "OAPIF"),
            (f"url='{OFFLINE}/sta/v1.1' entity='Location'", "sensorthings"),
        ],
    )
    def test_is_a_service_with_the_service_note(
        self, qgis_app, source, provider
    ) -> None:
        layer = qgis_core.QgsVectorLayer(source, "web", provider)
        assert layer.providerType().lower() == provider.lower()

        assert source_kind(layer) is SourceKind.SERVICE
        note = describe_source(layer).note or ""
        assert "downloaded once and embedded" in note
        assert "database" not in note
