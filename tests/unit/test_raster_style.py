"""The QGIS raster renderer must survive the trip into `COGLayer`.

Until 2026-09-17 it did not. The exporter emitted `src`, `min`/`max`, `nodata`
and `opacity` and nothing else, so a project showing a Viridis-ramped DEM
published as the runtime's default grey - and `manifest_builder`'s own docstring
still described the 0.6.20 schema, three minor versions after `bands`,
`colormap`, `reverse`, `stretch` and `gamma` arrived in 0.7.0.

`core.raster_style` is duck-typed against the renderer rather than
`isinstance`-based, and lives apart from `layer_reader` (which imports
`qgis.core` and so cannot be imported by a unit test). The stand-ins below
expose exactly the methods the real QGIS renderers do.
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
from nika_onlymap_exporter.core.raster_style import style_from_renderer


class FakeRamp:
    def __init__(self, name: str = "", scheme: str = "", inverted: bool = False):
        self._name, self._scheme, self._inverted = name, scheme, inverted

    def name(self) -> str:
        return self._name

    def schemeName(self) -> str:  # noqa: N802 - QGIS spelling
        return self._scheme

    def isInverted(self) -> bool:  # noqa: N802 - QGIS spelling
        return self._inverted


class FakeShaderFunction:
    def __init__(self, ramp):
        self._ramp = ramp

    def sourceColorRamp(self):  # noqa: N802 - QGIS spelling
        return self._ramp


class FakeShader:
    def __init__(self, ramp):
        self._fn = FakeShaderFunction(ramp)

    def rasterShaderFunction(self):  # noqa: N802 - QGIS spelling
        return self._fn


class FakePseudoColor:
    def __init__(self, band: int = 1, ramp=None):
        self._band, self._shader = band, FakeShader(ramp)

    def type(self) -> str:
        return "singlebandpseudocolor"

    def band(self) -> int:
        return self._band

    def shader(self):
        return self._shader


class FakeMultiBand:
    def __init__(self, red=1, green=2, blue=3):
        self._r, self._g, self._b = red, green, blue

    def type(self) -> str:
        return "multibandcolor"

    def redBand(self) -> int:  # noqa: N802
        return self._r

    def greenBand(self) -> int:  # noqa: N802
        return self._g

    def blueBand(self) -> int:  # noqa: N802
        return self._b


class FakeGray:
    def type(self) -> str:
        return "singlebandgray"

    def grayBand(self) -> int:  # noqa: N802
        return 1


class FakePaletted:
    def type(self) -> str:
        return "paletted"


class TestRasterStyle:
    @pytest.mark.parametrize(
        "ramp_name,expected",
        [
            ("Viridis", "viridis"),
            ("Magma", "magma"),
            ("YlOrRd", "ylorrd"),
            ("RdBu", "rdbu"),
            ("Spectral", "spectral"),
            ("Greys", "gray"),
        ],
    )
    def test_qgis_ramp_names_map_onto_the_runtime_vocabulary(self, ramp_name, expected):
        # QGIS spells ramps for humans; the runtime takes lowercase identifiers.
        colormap, bands, reverse = style_from_renderer(
            FakePseudoColor(ramp=FakeRamp(name=ramp_name))
        )
        assert colormap == expected
        assert bands == (1,)
        assert reverse is False

    def test_an_unknown_ramp_yields_no_colormap_rather_than_a_near_miss(self):
        # A wrong-but-plausible colormap looks deliberate, which is worse than
        # the runtime's honest default.
        colormap, _, _ = style_from_renderer(
            FakePseudoColor(ramp=FakeRamp(name="Blue to Red"))
        )
        assert colormap is None

    def test_an_inverted_ramp_is_carried_as_reverse(self):
        _, _, reverse = style_from_renderer(
            FakePseudoColor(ramp=FakeRamp(name="Viridis", inverted=True))
        )
        assert reverse is True

    def test_the_band_number_is_carried_not_assumed(self):
        _, bands, _ = style_from_renderer(
            FakePseudoColor(band=4, ramp=FakeRamp(name="Viridis"))
        )
        assert bands == (4,)

    def test_a_composite_carries_three_bands_and_no_colormap(self):
        # A composite is its own colour; a colormap on top of one is a
        # contradiction the runtime warns about.
        colormap, bands, _ = style_from_renderer(FakeMultiBand(3, 2, 1))
        assert bands == (3, 2, 1)
        assert colormap is None

    def test_a_gray_renderer_names_gray_rather_than_leaving_it_implicit(self):
        colormap, bands, _ = style_from_renderer(FakeGray())
        assert (colormap, bands) == ("gray", (1,))

    def test_a_renderer_we_cannot_express_changes_nothing(self):
        # Not a guess and not a crash: the runtime's defaults apply.
        assert style_from_renderer(FakePaletted()) == (None, None, False)

    def test_no_renderer_at_all_changes_nothing(self):
        assert style_from_renderer(None) == (None, None, False)


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


class TestRasterManifest:
    def test_the_colormap_reaches_the_manifest(self):
        # The regression this file exists for: the style was read and then
        # dropped on the floor by the emitter.
        element = build_layer_element(raster_layer(colormap="viridis", bands=(1,)), "")
        assert 'colormap="viridis"' in element
        assert 'bands="1"' in element

    def test_a_composite_is_emitted_as_a_bracketed_triple(self):
        element = build_layer_element(raster_layer(bands=(3, 2, 1)), "")
        assert 'bands="[3,2,1]"' in element

    def test_an_inverted_ramp_reaches_the_manifest(self):
        # Explicit value, not bare: the runtime types `reverse` as a boolean,
        # and this module's `None` means "omit the attribute".
        element = build_layer_element(
            raster_layer(colormap="viridis", reverse_colormap=True), ""
        )
        assert 'reverse="true"' in element

    def test_an_unstyled_raster_emits_neither(self):
        element = build_layer_element(raster_layer(), "")
        assert "colormap" not in element
        assert "bands" not in element
        assert "reverse" not in element


class TestCogCrsOrigin:
    """A COG's CRS lookup must be allowed through the page's own CSP.

    The runtime fetches `https://epsg.io/{code}.json` for any raster CRS - even
    EPSG:3857, which it has hardcoded - so a page whose `connect-src` omits that
    origin renders its COG layers as empty legend entries and nothing else.
    Observed on a hosted Grand Canyon DEM, 2026-09-17.
    """

    def test_a_raster_page_may_reach_the_crs_service(self):
        from nika_onlymap_exporter.packaging.publish_manifest import (
            derive_external_origins,
        )

        page = (
            '<om-map><om-layer type="COGLayer" src="/assets/a.tif"></om-layer></om-map>'
        )
        assert "https://epsg.io" in derive_external_origins(page)

    def test_a_vector_only_page_does_not(self):
        # Widening the CSP for a dependency the page never exercises gives away
        # a restriction for nothing.
        from nika_onlymap_exporter.packaging.publish_manifest import (
            derive_external_origins,
        )

        page = (
            '<om-map><om-layer type="GeoJsonLayer" '
            'data="/assets/a.geojson"></om-layer></om-map>'
        )
        assert "https://epsg.io" not in derive_external_origins(page)
