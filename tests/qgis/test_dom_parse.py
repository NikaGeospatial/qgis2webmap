"""Malformed XML is refused, not read as an empty document.

On Qt6 `QDomDocument.setContent` returns a ParseResult tuple, truthy even when
the parse failed, so a bare truthiness test lets a broken style carry on as an
empty document. These tests hold every reader of a saved style to the
well-formed / malformed difference.
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.dom_parse import parse_dom

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


def test_style_readers_refuse_a_malformed_style() -> None:
    from nika_onlymap_exporter.core.live_count import _style_opacity, _style_parts
    from nika_onlymap_exporter.core.raster_smoothing import (
        NEAREST,
        _resampling,
        _with_resampling,
    )

    assert _style_parts(MALFORMED) == (None, False, 0.0, 0.0)
    assert _style_opacity(MALFORMED) is None
    assert _resampling(MALFORMED) == NEAREST
    assert _with_resampling(MALFORMED, "bilinear") is None
    assert _style_opacity(WELL_FORMED) == 0.5


def test_a_malformed_raster_style_stops_the_bake(
    qgis_app, make_raster_layer, tmp_path
) -> None:
    from nika_onlymap_exporter.packaging.raster_bake import StyleBakeError, bake

    layer = make_raster_layer()
    with pytest.raises(StyleBakeError):
        bake(layer.source(), MALFORMED, str(tmp_path / "baked.tif"))
