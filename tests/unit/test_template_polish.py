"""Starter choices and readable, editable page furniture."""

import json
from pathlib import Path

from nika_onlymap_exporter.core.cartograph_ir import ShapeSpec, TextSpec
from nika_onlymap_exporter.core.layout_templates import (
    TemplateInput,
    plan_template,
    starter_templates,
)
from nika_onlymap_exporter.core.page_style import contrast_ratio, parse_color


def test_starters_have_distinct_jobs_without_cosmetic_variants():
    assert {t.id for t in starter_templates()} == {
        "classic",
        "legend-below",
        "map-only",
        "report-figure",
        "field-map",
        "poster",
        "locator",
        "dashboard",
        "dark",
        "wayfinder",
    }


def test_clearline_masthead_has_readable_type_on_its_band():
    plan = plan_template(
        "legend-below", TemplateInput(4000, 2000, False, title="A field atlas")
    )
    band = next(i for i in plan.items if i.id == "panel-masthead")
    title = next(i for i in plan.items if i.id == "title")
    assert isinstance(band, ShapeSpec) and isinstance(title, TextSpec)
    assert contrast_ratio(parse_color(title.color), parse_color(band.fill)) >= 4.5
    assert band.box.x < title.box.x
    assert title.box.x + title.box.w < band.box.x + band.box.w
    assert title.box.y >= band.box.y
    assert title.box.y + title.box.h <= band.box.y + band.box.h


def test_adapted_title_block_ships_its_permission_notice():
    root = Path(__file__).resolve().parents[2] / "nika_onlymap_exporter" / "licenses"
    manifest = json.loads((root / "template-sources.json").read_text())
    for source in manifest["sources"]:
        if "notice" in source:
            text = (root / source["notice"]).read_text()
            assert "Permission is hereby granted" in text
            assert "Copyright" in text
