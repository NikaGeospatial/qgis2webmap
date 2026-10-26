"""Template pages keep their look on the way to HTML and back: the styling a
template sets (`core/layout_templates.py`, built by `core/layout_builder.py`)
is what `core/layout_reader.py` reads and the cartograph writer emits.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest
from cartograph_fixtures import UTM, build_parcels_layer

from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view
from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.cartograph_ir import LegendSpec
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_builder import import_cartograph_file
from nika_onlymap_exporter.core.layout_reader import read_layout
from nika_onlymap_exporter.core.page_style import DAYLIGHT, PAPER
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

pytest.importorskip("qgis.core")


@pytest.fixture
def runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core",
        css=b"/* core */",
        version="0.13.1",
        sha256="0" * 64,
        cartograph_javascript=b"// carto",
        cartograph_css=b"/* carto */",
    )


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    return layer


def _export(result, project, runtime) -> str:
    report = FidelityReportBuilder()
    return finish_cartograph(
        prepare_cartograph(
            result.layout,
            project,
            runtime,
            report,
            frame_modes=result.frame_modes,
            raster_cache=None,
        ),
        project,
        report,
    )


def _build(parcels, project, template_id: str):
    extent = parcels.extent()
    extent.grow(200)
    return cartograph_from_view(
        extent, UTM, [parcels], project, template_id=template_id
    )


def test_dark_keeps_its_legend_and_frame_edge_in_the_html(
    parcels, project, runtime, tmp_path
) -> None:
    """The dark legend panel and its light ink, and the frame's dark edge,
    survive QGIS -> HTML -> QGIS; without them the legend is the runtime's
    pale panel and the frame gets a light line."""
    html = _export(_build(parcels, project, "dark"), project, runtime)
    legend = re.search(r"<om-legend [^>]*>", html).group(0)
    assert 'bg="#1d2026"' in legend
    assert 'color="#e8e6e3"' in legend
    assert 'stroke="#00000000"' in legend
    assert re.search(r'<om-frame [^>]*\bframe-stroke="#14161a"', html)
    path = tmp_path / "dark.html"
    path.write_text(html)
    built = import_cartograph_file(path, project)
    reading = read_layout(
        built.layout, FidelityReportBuilder(), frame_modes=built.frame_modes
    )
    assert [frame.frame_stroke for frame in reading.frames] == ["#14161a"]
    (spec,) = [item for item in reading.furniture if isinstance(item, LegendSpec)]
    assert (spec.bg, spec.color, spec.stroke) == ("#1d2026", "#e8e6e3", "#00000000")


def test_a_locator_keeps_its_outline_in_the_html(parcels, project, runtime) -> None:
    html = _export(_build(parcels, project, "locator"), project, runtime)
    frames = re.findall(r"<om-frame [^>]*>", html)
    assert len(frames) == 2
    assert f'frame-stroke="{DAYLIGHT.hairline}"' in frames[0]
    assert f'frame-stroke="{DAYLIGHT.rule}"' in frames[1]


def test_a_light_template_keeps_its_chosen_white_and_ink(
    parcels, project, runtime
) -> None:
    """Daylight chooses white paper, deep teal ink and a quiet frame edge:
    chosen, so carried -- the HTML theme's own paper and ink never stand
    in for them."""
    html = _export(_build(parcels, project, "classic"), project, runtime)
    legend = re.search(r"<om-legend [^>]*>", html).group(0)
    assert 'bg="#ffffff"' in legend
    assert f'color="{DAYLIGHT.ink}"' in legend
    assert f'frame-stroke="{DAYLIGHT.hairline}"' in html
    assert re.search(r'<om-cartograph [^>]*\bbg="#ffffff"', html)


@pytest.mark.parametrize(
    ("text", "width", "font_pt", "bold"),
    [
        ("Kranji parcels: land use survey, 2026", 171.0, 24.0, True),
        (
            "Flood-prone parcels along the Kranji reservoir catchment, "
            "surveyed September 2026",
            120.0,
            20.0,
            True,
        ),
        (
            "Parcels surveyed in September 2026, coloured by recorded use. " * 3,
            80,
            9,
            False,
        ),
        ("WWWWWWWW MMMMMMMM WWWWWWWW MMMMMMMM", 60.0, 12.0, True),
    ],
)
def test_the_line_estimate_is_never_short_of_qt(text, width, font_pt, bold) -> None:
    """`text_lines` must not promise fewer lines than Qt wraps the text to in
    QGIS's default face, or a box planned from it clips."""
    from qgis.PyQt.QtCore import QRectF, Qt
    from qgis.PyQt.QtGui import QFont, QFontMetricsF, QImage

    from nika_onlymap_exporter.core.layout_templates import text_lines

    dpi = 300
    device = QImage(4, 4, QImage.Format.Format_ARGB32)
    device.setDotsPerMeterX(round(dpi / 0.0254))
    device.setDotsPerMeterY(round(dpi / 0.0254))
    font = QFont("Sans Serif")
    font.setPointSizeF(font_pt)
    font.setBold(bold)
    metrics = QFontMetricsF(font, device)
    rect = metrics.boundingRect(
        QRectF(0, 0, width / 25.4 * dpi, 1e6),
        int(Qt.TextFlag.TextWordWrap),
        text,
    )
    qt_lines = round(rect.height() / metrics.lineSpacing())
    assert text_lines(text, width, font_pt, bold=bold) >= qt_lines


def _chosen_defaults_spec():
    from nika_onlymap_exporter.core.cartograph_ir import Box, CartographSpec, FrameSpec

    return CartographSpec(
        cartograph_id="chosen",
        size="A4",
        orientation="landscape",
        bg="#ffffff",
        items=(
            FrameSpec(
                id="main",
                box=Box(10, 10, 200, 150),
                center=(103.8, 1.3),
                zoom=12,
                frame_bg="#ffffff",
                frame_stroke="#000000",
            ),
            LegendSpec(
                id="legend",
                box=Box(220, 10, 60, 40),
                for_="main",
                bg="#ffffff",
                color="#000000",
            ),
        ),
    )


def test_white_and_black_a_design_chose_are_carried(project) -> None:
    """QGIS's own white page, map and legend and black text and frame stay
    the theme's in an ordinary layout; when a design chose them they are
    the design, and reach the HTML."""
    from nika_onlymap_exporter.core.layout_builder import build_layout

    built = build_layout(_chosen_defaults_spec(), project)
    reading = read_layout(built.layout, FidelityReportBuilder())
    assert reading.bg == "#ffffff"
    (frame,) = reading.frames
    assert (frame.frame_bg, frame.frame_stroke) == ("#ffffff", "#000000")
    (legend,) = [item for item in reading.furniture if isinstance(item, LegendSpec)]
    assert (legend.bg, legend.color, legend.stroke) == (
        "#ffffff",
        "#000000",
        "#00000000",
    )


def test_a_colour_changed_after_the_design_follows_the_usual_rule(project) -> None:
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.core.layout_builder import build_layout

    built = build_layout(_chosen_defaults_spec(), project)
    legend_item = built.items_by_id["legend"]
    legend_item.setBackgroundColor(QColor("#fefefe"))
    map_item = built.items_by_id["main"]
    map_item.setBackgroundColor(QColor("#336699"))
    reading = read_layout(built.layout, FidelityReportBuilder())
    assert reading.frames[0].frame_bg == "#336699"
    (legend,) = [item for item in reading.furniture if isinstance(item, LegendSpec)]
    assert legend.bg == "#fefefe"


def test_a_default_colour_over_a_chosen_one_is_the_default_again(project) -> None:
    """The record is of the colour chosen; the user setting the item back to
    QGIS's white afterwards makes it an ordinary default again."""
    from dataclasses import replace

    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.core.cartograph_ir import FrameSpec
    from nika_onlymap_exporter.core.layout_builder import build_layout

    spec = _chosen_defaults_spec()
    frame = replace(spec.items[0], frame_bg="#336699")
    assert isinstance(frame, FrameSpec)
    built = build_layout(replace(spec, items=(frame, *spec.items[1:])), project)
    built.items_by_id["main"].setBackgroundColor(QColor("#ffffff"))
    reading = read_layout(built.layout, FidelityReportBuilder())
    assert reading.frames[0].frame_bg is None


def test_a_legend_size_a_design_chose_is_carried(project) -> None:
    from dataclasses import replace

    from nika_onlymap_exporter.core.layout_builder import build_layout

    spec = _chosen_defaults_spec()
    legend = replace(spec.items[1], font_size=8.0)
    built = build_layout(replace(spec, items=(spec.items[0], legend)), project)
    reading = read_layout(built.layout, FidelityReportBuilder())
    (read,) = [item for item in reading.furniture if isinstance(item, LegendSpec)]
    assert read.font_size == 8.0
    # A legend whose size nobody chose keeps the runtime's.
    plain = build_layout(spec, project)
    reading = read_layout(plain.layout, FidelityReportBuilder())
    (read,) = [item for item in reading.furniture if isinstance(item, LegendSpec)]
    assert read.font_size is None


@pytest.mark.parametrize(
    ("template_id", "accent"),
    [("classic", DAYLIGHT.accent), ("poster", PAPER.accent)],
)
def test_an_accent_rule_keeps_its_colour_in_the_html(
    template_id, accent, parcels, project, runtime
) -> None:
    """A thin filled rule names its edge in its own colour: with no edge
    colour the runtime draws its default dark line, which on a rule under
    a millimetre high hides the accent altogether."""
    html = _export(_build(parcels, project, template_id), project, runtime)
    rule = re.search(r'<om-shape id="[^"]*rule[^"]*"[^>]*>', html).group(0)
    assert f'fill="{accent}"' in rule
    assert f'stroke="{accent}"' in rule
