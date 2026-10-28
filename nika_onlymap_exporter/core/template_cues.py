"""What a template picker card says a design includes, checked against the
page actually planned for this project.

A design lists short cues for its card (`LayoutTemplate.includes`: "legend",
"scale bar", "notes", ...). Some are on every page the design makes; others
depend on what the project supplies -- a notes panel needs the project's
abstract, a source line needs a layer's attribution, Atlas Ledger's sheet
number needs the layout's atlas.
A card promising notes on a page that has none would be a false claim.

So each cue names the plan items that show it (`CUE_RULES`, one declarative
table for every design), and `plan_cues` splits a design's cues by what a
given `TemplatePlan` placed: the ones this page shows ("Includes") and the
ones the design can show but this project does not fill yet ("Can also
show"), each of those with a short hint on how to fill it. Without a plan,
`declared_cues` splits them by `CueRule.always` alone.

Arithmetic only, like the planner: no `qgis` or Qt import.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .layout_templates import LayoutTemplate, TemplatePlan
from .translatable import N_


@dataclass(frozen=True)
class CueRule:
    """How a page shows one cue.

    `shown_by` are plan item or frame ids; the page shows the cue when it
    placed any of them. Empty means a new layout never shows it by itself
    (Atlas Ledger's sheet number: blank until the layout's atlas is on).
    `always` claims every page of every design listing the cue has it
    (tests hold the claim to the plans); a cue that is not always gets a
    `hint` saying where its content comes from."""

    shown_by: tuple[str, ...]
    always: bool = False
    hint: str = ""


_LEGEND = CueRule(("legend",), hint=N_("Appears when the map shows layers."))
_NOTES = CueRule(
    ("notes",), hint=N_("From Project > Properties > Metadata > Abstract.")
)
_SCALE = CueRule(("scalebar",), always=True)

#: Every cue a design may list, by its English text (the `N_()` source).
CUE_RULES: Mapping[str, CueRule] = {
    "legend": _LEGEND,
    "legend band": _LEGEND,
    "large legend": _LEGEND,
    "legend strip": _LEGEND,
    "scale bar": _SCALE,
    "distance scale": _SCALE,
    "source line": CueRule(
        ("attribution",),
        hint=N_(
            "From each layer's attribution: Layer Properties > Metadata > "
            "Access > Rights."
        ),
    ),
    "notes": _NOTES,
    "notes panel": _NOTES,
    "caption": CueRule(
        ("title", "notes"),
        hint=N_(
            "From the project title and Project > Properties > Metadata > Abstract."
        ),
    ),
    "headline": CueRule(
        ("title",), hint=N_("From the project title: Project > Properties > General.")
    ),
    "sheet number": CueRule(
        (), hint=N_("Numbered on each page once the layout's atlas is turned on.")
    ),
    "locator": CueRule(("locator",), always=True),
    "grid": CueRule(("graticule",), always=True),
    "title block": CueRule(("title-block",), always=True),
    "north arrow": CueRule(("north",), always=True),
    "date": CueRule(("date",), always=True),
}

#: A cue missing from `CUE_RULES` is taken at its word (tests forbid one).
_UNKNOWN = CueRule((), always=True)


@dataclass(frozen=True)
class CardCues:
    """A card's two cue lists, in the design's own order, as English
    `N_()` text for the UI to translate. `hints` pairs with `can_show`."""

    includes: tuple[str, ...] = ()
    can_show: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()


def placed_ids(plan: TemplatePlan) -> frozenset[str]:
    """The ids of everything `plan` puts on the page: its frames and items."""
    return frozenset(
        [slot.id for slot in plan.frames] + [item.id or "" for item in plan.items]
    )


def rule_for(cue: str) -> CueRule:
    return CUE_RULES.get(cue, _UNKNOWN)


def plan_cues(template: LayoutTemplate, plan: TemplatePlan) -> CardCues:
    """`template`'s cues split by what `plan` -- one of its pages -- shows."""
    placed = placed_ids(plan)
    return _split(
        template,
        lambda rule: rule is _UNKNOWN or any(i in placed for i in rule.shown_by),
    )


def declared_cues(template: LayoutTemplate) -> CardCues:
    """`template`'s cues split by their rules alone, for a card with no plan
    to check: what every page has, and what depends on the project."""
    return _split(template, lambda rule: rule.always)


def _split(template: LayoutTemplate, shown: Callable[[CueRule], bool]) -> CardCues:
    includes: list[str] = []
    can_show: list[str] = []
    hints: list[str] = []
    for cue in template.includes:
        rule = rule_for(cue)
        if shown(rule):
            includes.append(cue)
        else:
            can_show.append(cue)
            hints.append(rule.hint)
    return CardCues(tuple(includes), tuple(can_show), tuple(hints))


__all__ = [
    "CUE_RULES",
    "CardCues",
    "CueRule",
    "declared_cues",
    "placed_ids",
    "plan_cues",
    "rule_for",
]
