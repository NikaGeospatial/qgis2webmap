"""Malformed XML is refused where it must be, and read as 0.3.0 read it elsewhere.

On Qt6 `QDomDocument.setContent` returns a ParseResult tuple, truthy even when
the parse failed, so a bare truthiness test lets a broken style carry on as an
empty document. `parse_dom` refuses it, for the raster bake. The saved-style
readers of Live counts and raster smoothing go through `read_dom` and keep
0.3.0's results: whatever Qt read before the error.
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.dom_parse import parse_dom, read_dom

pytest.importorskip("qgis.core")

MALFORMED = "<qgis><layerOpacity>0.5</qgis"
WELL_FORMED = '<qgis minScale="5"><layerOpacity>0.5</layerOpacity></qgis>'


def test_a_malformed_document_is_refused() -> None:
    assert parse_dom(MALFORMED) is None
    assert parse_dom("") is None


def test_a_well_formed_document_is_returned() -> None:
    document = parse_dom(WELL_FORMED)
    assert document is not None
    assert document.documentElement().tagName() == "qgis"


def test_read_dom_keeps_what_came_before_the_error() -> None:
    document = read_dom("<qgis><layerOpacity>0</layerOpacity></qgis>junk")
    assert document.documentElement().tagName() == "qgis"
    assert document.documentElement().firstChildElement("layerOpacity").text() == "0"
    assert read_dom("junk").documentElement().isNull()
    assert read_dom("").documentElement().isNull()


# Each style, and what 0.3.0's readers made of it: opacity, the scale part of
# the style, the zoomed-in resampling, and whether bilinear could be set.
SCALED = '<qgis hasScaleBasedVisibilityFlag="1" minScale="9" maxScale="2"/>'
RESAMPLED = (
    '<qgis><provider><resampling zoomedInResamplingMethod="bilinear"/>'
    "</provider></qgis>"
)
AS_030 = [
    (WELL_FORMED, 0.5, (False, 5.0, 0.0), "nearestNeighbour", False),
    (MALFORMED, 0.5, (False, 0.0, 0.0), "nearestNeighbour", False),
    ("", None, (False, 0.0, 0.0), "nearestNeighbour", False),
    ("junk", None, (False, 0.0, 0.0), "nearestNeighbour", False),
    (
        "<qgis><layerOpacity>0</layerOpacity></qgis>junk",
        0.0,
        (False, 0.0, 0.0),
        "nearestNeighbour",
        False,
    ),
    (
        "<qgis><layerOpacity>0</layerOpacity></qgis><extra/>",
        0.0,
        (False, 0.0, 0.0),
        "nearestNeighbour",
        False,
    ),
    (
        "<qgis><layerOpacity>0</layerOpacity>",
        0.0,
        (False, 0.0, 0.0),
        "nearestNeighbour",
        False,
    ),
    (SCALED + "junk", None, (True, 9.0, 2.0), "nearestNeighbour", False),
    (RESAMPLED + "junk", None, (False, 0.0, 0.0), "bilinear", True),
    (RESAMPLED + "<", None, (False, 0.0, 0.0), "bilinear", True),
]


@pytest.mark.parametrize(("xml", "opacity", "scale", "zoomed_in", "settable"), AS_030)
def test_style_readers_read_as_030(xml, opacity, scale, zoomed_in, settable) -> None:
    from nika_onlymap_exporter.core.live_count import _style_opacity, _style_parts
    from nika_onlymap_exporter.core.raster_smoothing import (
        _resampling,
        _with_resampling,
    )

    assert _style_opacity(xml) == opacity
    renderer, *got_scale = _style_parts(xml)
    assert renderer is None
    assert tuple(got_scale) == scale
    assert _resampling(xml) == zoomed_in
    rewritten = _with_resampling(xml, "bilinear")
    assert (rewritten is not None) is settable
    if settable:
        assert _resampling(rewritten) == "bilinear"


def test_a_malformed_raster_style_stops_the_bake(
    qgis_app, make_raster_layer, tmp_path
) -> None:
    from nika_onlymap_exporter.packaging.raster_bake import StyleBakeError, bake

    layer = make_raster_layer()
    with pytest.raises(StyleBakeError):
        bake(layer.source(), MALFORMED, str(tmp_path / "baked.tif"))
