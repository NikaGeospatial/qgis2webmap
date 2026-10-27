"""The picker card's cues (`core/template_cues.py`) tell the truth about the
page planned for this project: every cue under "Includes" is on that page,
every one under "Can also show" is not, and a cue claimed for every page is
on every page -- for every design, across the content a project may or may
not supply.

The pages are checked here by what they actually carry (a legend item, a
text whose body holds the notes, a frame outlining the main map, ...), not
by the ids `CUE_RULES` names, so a rule pointing at the wrong item fails.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import replace

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    GraticuleSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.layout_templates import (
    MAIN_FRAME_ID,
    TEMPLATES,
    TemplateInput,
    TemplatePlan,
    get_template,
    plan_template,
)
from nika_onlymap_exporter.core.template_cues import (
    CUE_RULES,
    CardCues,
    declared_cues,
    plan_cues,
)

NOTES = "Parcels surveyed in September by the field team."
FIGURES = (("214", "parcels surveyed"), ("12 km", "of channel"), ("3", "teams"))
BASE = TemplateInput(extent_width=4000.0, extent_height=2600.0, crs_is_geographic=False)
LAYERS = {
    "layer_names": ("Parcels", "Roads"),
    "legend_labels": (("Housing", "Parkland", "Industry"), ()),
}

#: Content a project may or may not supply, one at a time and all together.
VARIANTS: dict[str, TemplateInput] = {
    "nothing": BASE,
    "title only": replace(BASE, title="Kranji parcels"),
    "layers only": replace(BASE, **LAYERS),
    "credits only": replace(BASE, credits=("© OpenStreetMap contributors",)),
    "notes only": replace(BASE, notes=NOTES),
    "figures only": replace(BASE, figures=FIGURES[:1]),
    "blank figures": replace(BASE, figures=(("", "a label alone"),)),
    "author only": replace(BASE, author="Field team"),
    "everything": replace(
        BASE,
        title="Kranji parcels",
        credits=("© OpenStreetMap contributors",),
        notes=NOTES,
        author="Field team",
        figures=FIGURES,
        **LAYERS,
    ),
    "everything, rotated and geographic": replace(
        BASE,
        extent_width=0.4,
        extent_height=0.25,
        crs_is_geographic=True,
        crs_authid="EPSG:4326",
        extent_center=(103.7, 1.35),
        rotation=30.0,
        title="Kranji parcels",
        credits=("© OpenStreetMap contributors",),
        notes=NOTES * 6,
        figures=FIGURES,
        **LAYERS,
    ),
    "tall extent": replace(BASE, extent_width=900.0, extent_height=4000.0, **LAYERS),
}
PAGES = (("A4", "auto"), ("A5", "portrait"), ("A3", "landscape"), ("Letter", "auto"))


def _texts(plan: TemplatePlan) -> list[TextSpec]:
    return [item for item in plan.items if isinstance(item, TextSpec)]


def _has_text(plan: TemplatePlan, needle: str) -> bool:
    return bool(needle) and any(needle in t.body for t in _texts(plan))


def _shows(plan: TemplatePlan, token: str, value: str) -> bool:
    """A text on the page reads `{{token}}`, and the page fills it in."""
    return bool(value.strip()) and _has_text(plan, "{{" + token + "}}")


def _has(plan: TemplatePlan, kind: type) -> bool:
    return any(isinstance(item, kind) for item in plan.items)


#: What each cue looks like on a page, judged from the page's content.
_ON_PAGE: dict[str, Callable[[TemplatePlan, TemplateInput], bool]] = {
    "legend": lambda plan, data: _has(plan, LegendSpec),
    "scale bar": lambda plan, data: _has(plan, ScalebarSpec),
    "source line": lambda plan, data: (
        _shows(plan, "attribution", plan.attribution_text)
        and all(credit in plan.attribution_text for credit in data.credits)
    ),
    "notes": lambda plan, data: _shows(plan, "notes", plan.notes_text),
    "caption": lambda plan, data: (
        _shows(plan, "title", plan.title_text) or _shows(plan, "notes", plan.notes_text)
    ),
    "headline": lambda plan, data: _shows(plan, "title", plan.title_text),
    "statistic callouts": lambda plan, data: any(
        value.strip() and any(t.body == value.strip() for t in _texts(plan))
        for value, _label in data.figures
    ),
    # A new layout has no atlas: the sheet number's text stays blank.
    "sheet number": lambda plan, data: False,
    "locator": lambda plan, data: any(
        slot.overview_of == MAIN_FRAME_ID for slot in plan.frames
    ),
    "grid": lambda plan, data: _has(plan, GraticuleSpec),
    "title block": lambda plan, data: any(
        isinstance(item, ShapeSpec) and item.id == "title-block" for item in plan.items
    ),
    "north arrow": lambda plan, data: _has(plan, NorthSpec),
    "date": lambda plan, data: _has_text(plan, "{{date}}"),
}
_SAME_AS = {
    "legend band": "legend",
    "large legend": "legend",
    "legend strip": "legend",
    "distance scale": "scale bar",
    "notes panel": "notes",
}


def _on_page(cue: str, plan: TemplatePlan, data: TemplateInput) -> bool:
    return _ON_PAGE[_SAME_AS.get(cue, cue)](plan, data)


def _cases() -> list:
    return [
        pytest.param(
            template.id,
            replace(data, page_size=size, orientation=orientation),
            id=f"{template.id}-{variant}-{size}-{orientation}",
        )
        for template, (variant, data), (size, orientation) in itertools.product(
            TEMPLATES, VARIANTS.items(), PAGES
        )
    ]


class TestTheRules:
    def test_every_listed_cue_has_a_rule_and_a_page_check(self):
        for template in TEMPLATES:
            for cue in template.includes:
                assert cue in CUE_RULES, (template.id, cue)
                assert _SAME_AS.get(cue, cue) in _ON_PAGE, (template.id, cue)

    def test_a_cue_that_depends_on_content_says_where_it_comes_from(self):
        for cue, rule in CUE_RULES.items():
            assert rule.always or rule.hint, cue
            assert not (rule.always and not rule.shown_by), cue

    def test_without_a_plan_only_the_always_cues_are_promised(self):
        marginalia = get_template("marginalia")
        cues = declared_cues(marginalia)
        assert "notes" in cues.can_show
        assert "scale bar" in cues.includes
        assert len(cues.hints) == len(cues.can_show)


@pytest.mark.parametrize(("template_id", "data"), _cases())
def test_the_card_tells_the_truth_about_this_page(template_id, data):
    template = get_template(template_id)
    plan = plan_template(template_id, data)
    cues = plan_cues(template, plan)
    assert set(cues.includes) | set(cues.can_show) == set(template.includes)
    for cue in cues.includes:
        assert _on_page(cue, plan, data), f"{template_id} claims {cue!r}"
    for cue in cues.can_show:
        assert not _on_page(cue, plan, data), f"{template_id} hides its {cue!r}"
    for cue in template.includes:
        if CUE_RULES[cue].always:
            assert cue in cues.includes, f"{template_id} lacks its always {cue!r}"


class TestTheReviewedCases:
    """The cards the review found promising what the page lacked."""

    @staticmethod
    def _cues(template_id: str, data: TemplateInput) -> CardCues:
        return plan_cues(get_template(template_id), plan_template(template_id, data))

    def test_marginalia_without_notes_offers_them_instead(self):
        bare = self._cues("marginalia", VARIANTS["layers only"])
        assert "notes" not in bare.includes
        assert "notes" in bare.can_show
        hint = bare.hints[bare.can_show.index("notes")]
        assert "Abstract" in hint
        assert "notes" in self._cues("marginalia", VARIANTS["everything"]).includes

    def test_signal_includes_callouts_only_once_the_page_has_one(self):
        assert (
            "statistic callouts"
            in self._cues("dashboard", VARIANTS["nothing"]).can_show
        )
        assert "statistic callouts" in (
            self._cues("dashboard", VARIANTS["blank figures"]).can_show
        )
        assert "statistic callouts" in (
            self._cues("dashboard", VARIANTS["figures only"]).includes
        )

    def test_atlas_ledger_never_promises_a_sheet_number_on_a_new_page(self):
        for data in VARIANTS.values():
            cues = self._cues("atlas-ledger", data)
            assert "sheet number" in cues.can_show
            assert "atlas" in cues.hints[cues.can_show.index("sheet number")]

    def test_no_credits_no_source_line(self):
        for template in TEMPLATES:
            if "source line" in template.includes:
                cues = self._cues(template.id, VARIANTS["layers only"])
                assert "source line" in cues.can_show, template.id
