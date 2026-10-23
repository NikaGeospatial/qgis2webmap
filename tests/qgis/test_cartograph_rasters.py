"""A raster in a live frame travels inside the cartograph, as it does in a
single-file web map: converted once, carried as bytes, never named by its
path on the author's machine.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import re

import pytest

from nika_onlymap_exporter.core import cartograph_export
from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.packaging.raster_embed import RASTER_SCRIPT_TYPE
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture(autouse=True)
def _fresh_raster_cache():
    cartograph_export._RASTER_EMBEDS.clear()
    yield
    cartograph_export._RASTER_EMBEDS.clear()


@pytest.fixture
def fake_runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.13.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )


@pytest.fixture
def raster_layout(project, make_raster_layer):
    """One live Web Mercator map over a small three-band raster, plus a
    second map item drawing the same raster."""
    raster = make_raster_layer(
        "photo",
        crs="EPSG:3857",
        west=0,
        north=1000,
        pixel_size=100,
        width=10,
        height=10,
        bands=3,
    )
    project.addMapLayer(raster)
    project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
    layout = qgis_core.QgsPrintLayout(project)
    layout.initializeDefaults()
    maps = []
    for x in (10, 150):
        item = qgis_core.QgsLayoutItemMap(layout)
        layout.addLayoutItem(item)
        item.attemptMove(qgis_core.QgsLayoutPoint(x, 20))
        item.attemptResize(qgis_core.QgsLayoutSize(120, 120))
        item.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
        item.setLayers([raster])
        item.setExtent(raster.extent())
        maps.append(item)
    return layout, maps, raster


def _export(layout, project, runtime, report, maps) -> str:
    prepared = prepare_cartograph(
        layout,
        project,
        runtime,
        report,
        frame_modes={item.uuid(): "live" for item in maps},
        raster_cache=None,
    )
    return finish_cartograph(prepared, project, report)


def _carried(html: str) -> list[str]:
    return re.findall(
        rf'<script type="{re.escape(RASTER_SCRIPT_TYPE)}" data-src="([^"]+)"', html
    )


class TestCarriedInThePage:
    def test_the_raster_is_in_the_page_and_its_path_is_not(
        self, raster_layout, project, fake_runtime
    ) -> None:
        layout, maps, raster = raster_layout
        report = FidelityReportBuilder()
        html = _export(layout, project, fake_runtime, report, maps[:1])
        assert raster.source() not in html
        (name,) = _carried(html)
        assert f'src="{name}"' in html
        assert any("Carried inside the page" in item.detail for item in report.items)

    def test_a_raster_in_two_frames_is_carried_once(
        self, raster_layout, project, fake_runtime
    ) -> None:
        layout, maps, _raster = raster_layout
        html = _export(layout, project, fake_runtime, FidelityReportBuilder(), maps)
        assert len(_carried(html)) == 1


class TestConvertedOncePerChange:
    """Autosave rebuilds on every edit; a raster is converted again only when
    its file or its spec changes."""

    def _count_conversions(self, monkeypatch) -> list[int]:
        calls: list[int] = []
        real = cartograph_export.stage_rasters

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(cartograph_export, "stage_rasters", counting)
        return calls

    def test_an_unchanged_raster_is_reused_with_its_rows(
        self, raster_layout, project, fake_runtime, monkeypatch
    ) -> None:
        layout, maps, _raster = raster_layout
        calls = self._count_conversions(monkeypatch)
        first = _export(
            layout, project, fake_runtime, FidelityReportBuilder(), maps[:1]
        )
        report = FidelityReportBuilder()
        second = _export(layout, project, fake_runtime, report, maps[:1])
        assert len(calls) == 1
        assert _carried(first) == _carried(second)
        assert any("Carried inside the page" in item.detail for item in report.items)

    def test_an_edited_file_is_converted_again(
        self, raster_layout, project, fake_runtime, monkeypatch
    ) -> None:
        layout, maps, raster = raster_layout
        calls = self._count_conversions(monkeypatch)
        _export(layout, project, fake_runtime, FidelityReportBuilder(), maps[:1])
        stat = os.stat(raster.source())
        os.utime(raster.source(), ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        _export(layout, project, fake_runtime, FidelityReportBuilder(), maps[:1])
        assert len(calls) == 2


def test_the_bundled_web_mercator_definition_is_proj_s_own() -> None:
    """The page answers the COG reader's EPSG:3857 lookup with this, so it must
    say what PROJ says. QGIS 3.44 cannot write PROJJSON; 4.x checks it."""
    import json

    from nika_onlymap_exporter.packaging.web_mercator_projjson import (
        WEB_MERCATOR_PROJJSON,
    )

    crs = qgis_core.QgsCoordinateReferenceSystem("EPSG:3857")
    if not hasattr(crs, "toJsonString"):
        pytest.skip("this QGIS cannot write PROJJSON")
    assert json.loads(WEB_MERCATOR_PROJJSON) == json.loads(crs.toJsonString())
