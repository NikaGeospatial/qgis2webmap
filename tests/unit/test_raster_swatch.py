"""`core/raster_swatch.py`: the swatch the browser's legend can give a styled
raster.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.raster_swatch import MID_GREY, raster_swatch


def _qml(renderer: str) -> str:
    return f"<qgis><pipe>{renderer}</pipe></qgis>"


PSEUDO = (
    '<rasterrenderer type="singlebandpseudocolor"><rastershader>'
    '<colorrampshader colorRampType="{kind}">'
    '<item color="#6f948a" label="Below 1,000 m" value="1000"/>'
    '<item color="#e2d6a1" label="1,000 - 1,400 m" value="1400"/>'
    '<item color="#b9805f" label="Above 2,600 m" value="9999"/>'
    "</colorrampshader></rastershader></rasterrenderer>"
)


class TestRasterSwatch:
    def test_discrete_classes_are_squares_in_the_first_class_colour(self) -> None:
        swatch = raster_swatch(_qml(PSEUDO.format(kind="DISCRETE")))
        assert swatch is not None
        assert (swatch.shape, swatch.color, swatch.is_ramp) == (
            "polygon",
            "#6f948a",
            False,
        )
        assert [label for _c, label in swatch.entries] == [
            "Below 1,000 m",
            "1,000 - 1,400 m",
            "Above 2,600 m",
        ]

    def test_an_interpolated_ramp_is_a_ramp_swatch(self) -> None:
        swatch = raster_swatch(_qml(PSEUDO.format(kind="INTERPOLATED")))
        assert swatch is not None and swatch.shape == "ramp" and swatch.is_ramp
        assert swatch.color == "#6f948a"

    def test_a_paletted_raster_lists_its_entries(self) -> None:
        qml = _qml(
            '<rasterrenderer type="paletted"><colorPalette>'
            '<paletteEntry value="1" color="#00ff00" label="Forest" alpha="255"/>'
            '<paletteEntry value="2" color="#0000ff" label="Water" alpha="255"/>'
            "</colorPalette></rasterrenderer>"
        )
        swatch = raster_swatch(qml)
        assert swatch is not None and swatch.shape == "polygon"
        assert swatch.entries == (("#00ff00", "Forest"), ("#0000ff", "Water"))

    def test_a_grey_raster_is_a_grey_ramp(self) -> None:
        swatch = raster_swatch(_qml('<rasterrenderer type="singlebandgray"/>'))
        assert swatch is not None and swatch.shape == "ramp"
        assert swatch.color == MID_GREY

    def test_a_composite_or_nothing_has_no_swatch(self) -> None:
        assert raster_swatch(_qml('<rasterrenderer type="multibandcolor"/>')) is None
        assert raster_swatch(None) is None
        assert raster_swatch("not xml") is None
