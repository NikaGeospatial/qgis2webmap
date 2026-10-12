"""How a raster's colours reach the map, and why the first attempt did not.

Until 2026-09-17 the exporter emitted `src`, `min`/`max`, `nodata` and
`opacity` for a raster and nothing else, so a project showing a ramped DEM
published as the runtime's default grey. The fix that day read a `colormap` off
the renderer's colour ramp - and on 2026-09-18, exercised against a real QGIS
project for the first time, it turned out to fire for essentially nobody:
`sourceColorRamp()` is `None` on any project loaded from disk, because QGIS
copies a named ramp's stops into an anonymous gradient and forgets the name.

So colour is now BAKED into the pixels for every renderer except an RGB
composite, whose pixels are already the colours the author sees. These tests
pin that split, and the manifest consequences of it - a baked raster's bands are
red, green, blue and alpha, so every attribute describing the source's bands or
its measured range has to disappear with it.

The stand-ins are duck-typed because `core.raster_style` is: nothing under
`tests/unit` can import `layer_reader`, which needs `qgis.core`.
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    GeometryKind,
    PopupSpec,
    RasterSpec,
    SourceKind,
)
from nika_onlymap_exporter.core.manifest_builder import build_layer_element
from nika_onlymap_exporter.core.raster_style import (
    composite_bands,
    renderer_kind,
    should_bake,
)


class FakeRenderer:
    """Only `type()` is universal across QGIS's raster renderers."""

    def __init__(self, kind: str):
        self._kind = kind

    def type(self) -> str:
        return self._kind


class FakeMultiBand(FakeRenderer):
    def __init__(self, red=1, green=2, blue=3):
        super().__init__("multibandcolor")
        self._r, self._g, self._b = red, green, blue

    def redBand(self) -> int:  # noqa: N802 - QGIS spelling
        return self._r

    def greenBand(self) -> int:  # noqa: N802
        return self._g

    def blueBand(self) -> int:  # noqa: N802
        return self._b


class TestWhichRoute:
    @pytest.mark.parametrize(
        "kind",
        [
            "singlebandpseudocolor",
            "singlebandgray",
            "paletted",
            "hillshade",
            "contour",
        ],
    )
    def test_a_renderer_that_invents_colour_is_baked(self, kind):
        # None of these carry colour in the file: QGIS decides it from values at
        # draw time, and `COGLayer` has no attribute that can say how.
        assert should_bake(FakeRenderer(kind)) is True

    def test_a_composite_is_carried_as_data(self):
        # Re-encoding an orthophoto to RGBA would cost size and quality to
        # reproduce what the runtime already draws correctly.
        assert should_bake(FakeMultiBand()) is False

    def test_no_renderer_at_all_is_baked_rather_than_assumed(self):
        # "We cannot see how QGIS draws this" is not a reason to claim the file
        # draws itself. Letting QGIS draw is the answer that cannot be wrong.
        assert should_bake(None) is True

    def test_an_object_that_is_not_a_renderer_is_baked(self):
        assert should_bake(object()) is True

    def test_renderer_kind_is_lowercased_and_never_none(self):
        assert renderer_kind(FakeRenderer("SingleBandGray")) == "singlebandgray"
        assert renderer_kind(None) == ""


class TestCompositeBands:
    def test_the_band_order_is_carried_not_assumed(self):
        assert composite_bands(FakeMultiBand(3, 2, 1)) == (3, 2, 1)

    def test_a_baked_renderer_has_no_bands_to_report(self):
        # The two are mutually exclusive by construction; a caller that got both
        # would emit a band selection for a file whose bands are now RGBA.
        assert composite_bands(FakeRenderer("singlebandpseudocolor")) is None

    @pytest.mark.parametrize("unset", [0, -1])
    def test_an_unset_band_yields_no_triple_rather_than_a_partial_one(self, unset):
        # QGIS spells "unset" as -1 or 0; COGLayer's bands are 1-based. A pair
        # would be a composite the author never configured.
        assert composite_bands(FakeMultiBand(1, unset, 3)) is None


def raster_layer(**raster_kwargs) -> ExportLayer:
    return ExportLayer(
        layer_id="dem",
        name="Elevation",
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.FILE,
        popup=PopupSpec(enabled=False),
        raster=RasterSpec(
            path="/data/dem.tif",
            band_count=1,
            source_crs="EPSG:3857",
            pixel_width=2048,
            pixel_height=1024,
            **raster_kwargs,
        ),
    )


QML = "<qgis><pipe/></qgis>"


class TestBakedRasterManifest:
    """A baked file's bands are red, green, blue and alpha - nothing else."""

    def test_no_colormap_is_ever_emitted(self):
        # The attribute the 2026-09-17 fix added, removed: it never fired, and
        # a colormap applied on top of already-coloured pixels would recolour
        # them.
        element = build_layer_element(raster_layer(style_qml=QML), "")
        assert "colormap" not in element
        assert "reverse" not in element

    def test_a_baked_raster_emits_no_band_selection(self):
        element = build_layer_element(raster_layer(style_qml=QML, bands=(1,)), "")
        assert "bands=" not in element

    def test_a_baked_raster_emits_no_stretch(self):
        # `min`/`max` state a range of measured values. After baking, applying a
        # DEM's elevation range to its own colour channels would restretch the
        # picture into something nobody chose.
        element = build_layer_element(
            raster_layer(style_qml=QML, rescale_min=577.0, rescale_max=2807.0), ""
        )
        assert "min=" not in element
        assert "max=" not in element

    def test_a_baked_raster_emits_no_nodata(self):
        # QGIS renders nodata transparent, so the mask is in the alpha channel.
        # Restating -9999 would name a colour that matches nothing in an RGBA
        # file.
        element = build_layer_element(raster_layer(style_qml=QML, nodata=-9999.0), "")
        assert "nodata" not in element

    def test_the_source_reference_and_opacity_still_survive(self):
        # Baking changes what the pixels are, not where they live or how far
        # through them you can see. Opacity in particular is NOT baked - the
        # renderer is reset to full opacity first - so it has to be here.
        layer = raster_layer(style_qml=QML, src="dem.tif")
        element = build_layer_element(
            ExportLayer(**{**layer.__dict__, "opacity": 0.6}), ""
        )
        assert 'src="dem.tif"' in element
        assert 'opacity="0.6"' in element


class TestCarriedRasterManifest:
    def test_a_composite_is_emitted_as_a_bracketed_triple(self):
        element = build_layer_element(raster_layer(bands=(3, 2, 1)), "")
        assert 'bands="[3,2,1]"' in element

    def test_an_unbaked_raster_keeps_its_stretch_and_nodata(self):
        # The counterpart to the baked cases above: these attributes describe
        # measured values, and an unbaked file still holds them.
        element = build_layer_element(
            raster_layer(
                bands=(3, 2, 1), rescale_min=0.0, rescale_max=255.0, nodata=0.0
            ),
            "",
        )
        assert 'min="0"' in element
        assert 'max="255"' in element
        assert "nodata=" in element
