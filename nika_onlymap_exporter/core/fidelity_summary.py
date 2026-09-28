"""The fidelity report, grouped and counted the way the Fidelity tab shows it.

`FidelityReportBuilder` records one flat list, in the order translation
happened to visit things. Read that way, a five-layer project is fifty rows with
the one that matters somewhere in the middle, a graduated layer repeats the same
sentence once per class, and "which layer is this about" has to be read out of
each subject string.

This module turns the list into what a person reads:

* **Grouped by layer, problems first.** A "Whole map" group for settings that
  belong to no one layer, then one group per layer. Layers with something to
  say come before layers that came through exactly.
* **Identical rows merged.** The same verdict and the same sentence for four
  classes of one layer is one row that says "4 classes".
* **Counted in words that count what they say.** "10 things change" is the
  number of rows a reader will find listed, not the number of report entries
  behind them.

Nothing is dropped on the way: every item lands in exactly one row, and Kept
rows stay reachable - the tab hides them behind a toggle, it does not delete
them.

Extension points, deliberately not built yet: a destination-aware mode (file,
own server, NIKA hosting) changes which verdicts need attention, and that
decision lives in `needs_attention` alone; new kinds of row - a colour-vision
check on a layer's classes, say - are ordinary `FidelityItem`s with a layer id
and land in their layer's group with no change here.

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from .export_ir import FidelityItem, FidelityStatus

VERDICT_LABELS = {
    FidelityStatus.PRESERVED: "Kept",
    FidelityStatus.APPROXIMATED: "Changed",
    FidelityStatus.RASTER_FALLBACK: "Rasterised",
    FidelityStatus.UNSUPPORTED: "Not exported",
    FidelityStatus.BLOCKED: "Blocked",
}

# Problems first. A report opening on a wall of "Kept" buries what matters.
VERDICT_ORDER = {
    FidelityStatus.BLOCKED: 0,
    FidelityStatus.UNSUPPORTED: 1,
    FidelityStatus.APPROXIMATED: 2,
    FidelityStatus.RASTER_FALLBACK: 3,
    FidelityStatus.PRESERVED: 4,
}

WHOLE_MAP = "Whole map"

# "Symbology of 'Roads', range 3" and "..., class 3": the per-class rows that
# `renderer_translator` records once per class.
_CLASS_SUFFIX = re.compile(r",\s*(?:range|class)\s+\d+$")

# Words joining an aspect to the layer it is about: "Labels on 'Roads'".
_CONNECTOR = re.compile(r"\s+(?:of|on|in|for)$")

# The first quoted name in a subject, for an item whose layer the caller could
# not name - a layer removed from the project since the report was built.
_QUOTED = re.compile(r"'([^']+)'")


def needs_attention(status: FidelityStatus) -> bool:
    """Whether a verdict means something the recipient will not get.

    Blocked and Not exported are losses; Changed and Rasterised arrive in some
    form. The one place a destination-aware report would change the answer.
    """
    return status in (FidelityStatus.BLOCKED, FidelityStatus.UNSUPPORTED)


@dataclass(frozen=True)
class ReportRow:
    """One line of the tab: one or more identical report items."""

    label: str
    status: FidelityStatus
    detail: str
    layer_id: str | None = None
    subjects: tuple[str, ...] = ()

    @property
    def verdict(self) -> str:
        return VERDICT_LABELS[self.status]

    @property
    def is_kept(self) -> bool:
        return self.status is FidelityStatus.PRESERVED


@dataclass(frozen=True)
class ReportGroup:
    """Everything about one layer - or, with no `layer_id`, the whole map."""

    title: str
    layer_id: str | None
    rows: tuple[ReportRow, ...]

    @property
    def is_whole_map(self) -> bool:
        return self.layer_id is None

    @property
    def changes(self) -> tuple[ReportRow, ...]:
        return tuple(row for row in self.rows if not row.is_kept)

    @property
    def kept(self) -> tuple[ReportRow, ...]:
        return tuple(row for row in self.rows if row.is_kept)

    @property
    def is_exact(self) -> bool:
        return not self.changes

    @property
    def worst(self) -> FidelityStatus:
        """The most serious verdict in the group; Kept for an exact one."""
        return min(
            (row.status for row in self.rows),
            key=VERDICT_ORDER.__getitem__,
            default=FidelityStatus.PRESERVED,
        )

    @property
    def needs_attention(self) -> bool:
        return any(needs_attention(row.status) for row in self.rows)

    def tally(self) -> str:
        """ "1 not exported · 3 changed", worst first, or "exact"."""
        counts: dict[FidelityStatus, int] = {}
        for row in self.changes:
            counts[row.status] = counts.get(row.status, 0) + 1
        if not counts:
            return "exact"
        return " · ".join(
            f"{count} {VERDICT_LABELS[status].lower()}"
            for status, count in sorted(
                counts.items(), key=lambda pair: VERDICT_ORDER[pair[0]]
            )
        )


@dataclass(frozen=True)
class ReportSummary:
    """The whole report, grouped, with the counts the tab and strip print."""

    groups: tuple[ReportGroup, ...]

    @property
    def layer_groups(self) -> tuple[ReportGroup, ...]:
        return tuple(group for group in self.groups if not group.is_whole_map)

    @property
    def whole_map(self) -> ReportGroup | None:
        return next((group for group in self.groups if group.is_whole_map), None)

    @property
    def change_count(self) -> int:
        """Rows a reader finds listed as changing - merged rows count once."""
        return sum(len(group.changes) for group in self.groups)

    @property
    def kept_count(self) -> int:
        return sum(len(group.kept) for group in self.groups)

    @property
    def blocked_rows(self) -> tuple[ReportRow, ...]:
        return tuple(
            row
            for group in self.groups
            for row in group.rows
            if row.status is FidelityStatus.BLOCKED
        )

    @property
    def is_empty(self) -> bool:
        return not self.groups

    def headline(self) -> str:
        """ "5 layers · 1 needs attention · 10 things change"."""
        if self.is_empty:
            return "Nothing to report."
        layers = len(self.layer_groups)
        parts = [_plural(layers, "layer") if layers else "No layers"]
        attention = self._attention_phrase()
        if attention:
            parts.append(attention)
        if self.change_count:
            parts.append(_changes(self.change_count))
        else:
            parts.append("everything is kept exactly")
        return " · ".join(parts)

    def strip_text(self) -> str:
        """The one line under the tabs. Empty when nothing changes.

        Empty rather than "0 things change": an always-present count trains
        people to ignore it, and the absence of a warning is itself the message.
        """
        if not self.change_count:
            return ""
        parts: list[str] = []
        blocked_layers = sum(
            1
            for group in self.layer_groups
            if any(row.status is FidelityStatus.BLOCKED for row in group.rows)
        )
        whole_map = self.whole_map
        map_blocked = (
            0
            if whole_map is None
            else sum(
                1 for row in whole_map.rows if row.status is FidelityStatus.BLOCKED
            )
        )
        if map_blocked:
            # Not every whole-map blocker means an empty map, so it is counted
            # as a thing rather than dramatised as "nothing can be exported".
            things = _plural(blocked_layers + map_blocked, "thing")
            parts.append(f"{things} cannot be exported")
        elif blocked_layers:
            parts.append(f"{_plural(blocked_layers, 'layer')} cannot be exported")
        else:
            # Named here: without the headline's "5 layers" before it, a bare
            # "1 needs attention" does not say one of what.
            attention = self._attention_phrase(name_layers=True)
            if attention:
                parts.append(attention[0].upper() + attention[1:])
        change = _changes(self.change_count)
        parts.append(change if parts else change[0].upper() + change[1:])
        return " · ".join(parts) + " on export."

    def _attention_phrase(self, name_layers: bool = False) -> str:
        layers = sum(1 for group in self.layer_groups if group.needs_attention)
        whole_map = self.whole_map
        map_needs_it = whole_map is not None and whole_map.needs_attention
        if layers and map_needs_it:
            return f"{_plural(layers, 'layer')} and the whole map need attention"
        if layers:
            counted = _plural(layers, "layer") if name_layers else str(layers)
            return f"{counted} need{'s' if layers == 1 else ''} attention"
        if map_needs_it:
            return "the whole map needs attention"
        return ""


def summarise(
    items: Iterable[FidelityItem],
    layer_names: Mapping[str, str] | None = None,
    layer_order: Sequence[str] | None = None,
) -> ReportSummary:
    """Group a report's items for reading.

    `layer_names` maps layer id to the name to show, and `layer_order` is the
    order layers appear in QGIS - both from the project, because a report item
    carries only an id. A layer the caller cannot name falls back to the name
    quoted in its subject.
    """
    names = dict(layer_names or {})
    order = {layer_id: index for index, layer_id in enumerate(layer_order or ())}

    by_group: dict[str | None, list[FidelityItem]] = {}
    for item in items:
        by_group.setdefault(item.layer_id, []).append(item)

    groups: list[ReportGroup] = []
    for layer_id, group_items in by_group.items():
        if layer_id is None:
            title = WHOLE_MAP
        else:
            title = names.get(layer_id) or _quoted_name(group_items) or "Layer"
        groups.append(
            ReportGroup(
                title=title,
                layer_id=layer_id,
                rows=_rows(group_items, None if layer_id is None else title),
            )
        )

    whole_map = [group for group in groups if group.is_whole_map]
    layers = [group for group in groups if not group.is_whole_map]
    first_seen = {group.layer_id: index for index, group in enumerate(layers)}

    def layer_key(group: ReportGroup) -> tuple[int, int, int]:
        return (
            # Worst verdict first, which also puts exact layers - all Kept -
            # after every layer with something to say.
            VERDICT_ORDER[group.worst],
            order.get(group.layer_id or "", len(order)),
            first_seen[group.layer_id],
        )

    return ReportSummary(groups=tuple(whole_map + sorted(layers, key=layer_key)))


def _rows(items: list[FidelityItem], layer_name: str | None) -> tuple[ReportRow, ...]:
    """Merge identical items, then order problems first, stable within a verdict."""
    merged: dict[tuple[str, FidelityStatus, str], list[FidelityItem]] = {}
    labels: dict[tuple[str, FidelityStatus, str], list[str]] = {}
    for item in items:
        label = _aspect(item.subject, layer_name)
        base = _CLASS_SUFFIX.sub("", label)
        key = (base, item.status, item.detail)
        merged.setdefault(key, []).append(item)
        labels.setdefault(key, []).append(label)

    rows: list[ReportRow] = []
    for key, group in merged.items():
        base, status, detail = key
        distinct = list(dict.fromkeys(labels[key]))
        if len(distinct) == 1:
            label = distinct[0]
        else:
            # Several classes said the same thing; name how many, not which.
            label = f"{base} ({len(distinct)} classes)"
        rows.append(
            ReportRow(
                label=label,
                status=status,
                detail=detail,
                layer_id=group[0].layer_id,
                subjects=tuple(item.subject for item in group),
            )
        )
    return tuple(sorted(rows, key=lambda row: VERDICT_ORDER[row.status]))


def _aspect(subject: str, layer_name: str | None) -> str:
    """ "Labels on 'Roads'" inside the Roads group is just "Labels"."""
    if not layer_name:
        return subject
    quoted = f"'{layer_name}'"
    if quoted not in subject:
        return subject
    before, _, after = subject.partition(quoted)
    before = _CONNECTOR.sub("", before.rstrip())
    label = f"{before}{after}".strip(" ,")
    return label or "Layer"


def _quoted_name(items: list[FidelityItem]) -> str | None:
    for item in items:
        match = _QUOTED.search(item.subject)
        if match:
            return match.group(1)
    return None


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _changes(count: int) -> str:
    return f"{_plural(count, 'thing')} change{'s' if count == 1 else ''}"


class ReportState(Enum):
    """Where the report on screen stands relative to the project."""

    NOT_CHECKED = "not_checked"
    CHECKING = "checking"
    CURRENT = "current"
    OUT_OF_DATE = "out_of_date"
    FAILED = "failed"


class Tone(Enum):
    """How loudly the strip speaks. The UI maps each to an icon and a role."""

    QUIET = "quiet"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class StripMessage:
    """The strip's text, tone and button label. `button` None hides it."""

    text: str
    tone: Tone
    button: str | None


def strip_message(
    state: ReportState, summary: ReportSummary | None = None, error: str = ""
) -> StripMessage:
    """What the always-visible strip says, for every state the report can be in.

    It used to have one state: a count, or nothing. So before the first check
    it said nothing, which reads as "nothing changes" - the inversion the
    Fidelity tab computes on open to avoid - and after a settings change it
    went on showing the old count as if it were current.
    """
    if state is ReportState.NOT_CHECKED:
        return StripMessage("Not checked yet.", Tone.QUIET, "Check now")
    if state is ReportState.CHECKING:
        return StripMessage("Checking what the export changes...", Tone.QUIET, None)
    if state is ReportState.FAILED:
        return StripMessage(
            f"The check could not finish: {error}" if error else "The check failed.",
            Tone.ERROR,
            "See why",
        )
    if state is ReportState.OUT_OF_DATE:
        return StripMessage(
            "Out of date - something changed since the last check.",
            Tone.WARNING,
            "Check again",
        )

    if summary is None or not summary.change_count:
        return StripMessage("", Tone.QUIET, None)
    if summary.blocked_rows:
        return StripMessage(summary.strip_text(), Tone.ERROR, "Review problems")
    if any(group.needs_attention for group in summary.groups):
        return StripMessage(summary.strip_text(), Tone.WARNING, "See what changes")
    return StripMessage(summary.strip_text(), Tone.INFO, "See what changes")
