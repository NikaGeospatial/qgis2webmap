"""Crowded legends before entries are left out: Wayfinder takes width from
its distance scale and Broadside height from its headline
(`core/template_designs.py`, `core/layout_templates.py`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core import layout_templates as lt
from nika_onlymap_exporter.core.cartograph_ir import LegendSpec, ScalebarSpec
from nika_onlymap_exporter.core.layout_templates import TemplateInput, plan_template
from nika_onlymap_exporter.core.page_style import type_size

#: A regional reference map's legend: 16 layers, 33 rows.
NAMES = (
    "Places",
    "Viewpoints demo OSM data",
    "Named summits demo OSM data",
    "Named features base 2001",
    "Water-quality stations base 2001",
    "Highways base 2001",
    "Roads NPS 2015 (archived)",
    "Trails NPS 2015 (archived)",
    "Colorado River base 2001",
    "Tributaries and washes base 2001",
    "Park boundary NPS 2021",
    "Land tracts by interest NPS 2021",
    "Adjacent land base 2001",
    "Contours derived from DEM",
    "Elevation (m)",
    "Shaded relief",
)
LABELS = (
    (),
    (),
    (),
    (),
    ("Stream station", "Spring station", "Lake station"),
    (),
    ("Asphalt road", "Gravel road", "Cinder road", "Natural surface", "Not recorded"),
    ("1-Minimally Developed", "4-Highly Developed", "5-Fully Developed", "Unrecorded"),
    (),
    ("Named watercourse", "Watercourse name not recorded"),
    (),
    ("Federal Land (Fee)", "Federal Land (Less than Fee)", "Private", "Public"),
    ("National forest", "Indian reservation"),
    ("Index contour (500 m)", "Intermediate contour (100 m)"),
    ("2,900 m", "500 m"),
    (),
)


def _data(width: float = 60000.0, height: float = 30000.0) -> TemplateInput:
    return TemplateInput(
        extent_width=width,
        extent_height=height,
        crs_is_geographic=False,
        crs_authid="EPSG:32612",
        page_size="A4",
        orientation="landscape",
        title="Grand Canyon regional reference",
        layer_names=NAMES,
        legend_labels=LABELS,
        credits=("NPS archived editions",),
        notes="Representative fixture; archived editions, not for navigation.",
    )


def _legend_fits(plan: lt.TemplatePlan, data: TemplateInput) -> bool:
    """Whether the planned legend box holds the legend's rows at the size
    planned for it (the planner's estimate of what QGIS needs)."""
    legend = next(i for i in plan.items if isinstance(i, LegendSpec))
    page = lt._Page(
        data, plan.page_width, plan.page_height, data.title, plan.attribution_text
    )
    columns = legend.columns or 1
    assert legend.font_size is not None
    need = min(
        lt._legend_height(page, legend.font_size, legend.box.w, columns, split=split)
        for split in (False, True)
    )
    return need <= legend.box.h + 1e-6


def test_a_crowded_legend_takes_width_from_the_scale_bar_not_entries() -> None:
    data = _data()
    plan = plan_template("wayfinder", data)
    assert _legend_fits(plan, data)
    bar = next(i for i in plan.items if isinstance(i, ScalebarSpec))
    assert bar.box.w >= 45.0


def test_a_crowded_legend_takes_height_from_the_headline_not_entries() -> None:
    data = _data(45000.0, 50000.0)
    plan = plan_template("poster", data)
    assert _legend_fits(plan, data)
    headline = next(i for i in plan.items if i.id == "title")
    assert headline.font_size < type_size("display", plan.page_width, plan.page_height)
