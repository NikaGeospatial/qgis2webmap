"""Accumulates what happened to every property during translation.

The report is the mechanism behind one of this project's non-negotiables:
nothing a QGIS project expresses may vanish silently. The incumbent has three
separate silent-drop failures -- a project title that is read then discarded, a
layers list that never renders, popup fields that lose their labels -- and in
each case the user's only signal is absence.

So the report covers **suppressed settings**, not only unsupported symbology.
"You set a project title; it is not shown because..." is exactly the message that
prevents a support ticket.

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import Any

from .export_ir import FidelityItem, FidelityStatus


class FidelityReportBuilder:
    """Mutable accumulator that freezes into the model's immutable tuple.

    Deliberately not a dataclass: it is the one place in `core/` that is
    stateful by design, and keeping it visibly different from the frozen model
    types makes that obvious at the call site.
    """

    def __init__(self) -> None:
        self._items: list[FidelityItem] = []

    # ---- recording ------------------------------------------------------

    def record(
        self,
        subject: str,
        status: FidelityStatus,
        detail: str,
        layer_id: str | None = None,
    ) -> None:
        self._items.append(
            FidelityItem(
                subject=subject, status=status, detail=detail, layer_id=layer_id
            )
        )

    def preserved(self, subject: str, detail: str, layer_id: str | None = None) -> None:
        """Translated exactly. Recorded so a report can show coverage, not only
        problems -- a report that lists nothing but failures reads as a list of
        defects rather than a fidelity statement."""
        self.record(subject, FidelityStatus.PRESERVED, detail, layer_id)

    def approximated(
        self, subject: str, detail: str, layer_id: str | None = None
    ) -> None:
        """Translated to something close. `detail` must say what changed."""
        self.record(subject, FidelityStatus.APPROXIMATED, detail, layer_id)

    def raster_fallback(
        self, subject: str, detail: str, layer_id: str | None = None
    ) -> None:
        self.record(subject, FidelityStatus.RASTER_FALLBACK, detail, layer_id)

    def unsupported(
        self, subject: str, detail: str, layer_id: str | None = None
    ) -> None:
        """Cannot be represented; the export continues without it."""
        self.record(subject, FidelityStatus.UNSUPPORTED, detail, layer_id)

    def blocked(self, subject: str, detail: str, layer_id: str | None = None) -> None:
        """The export must not proceed. `detail` has to tell the user what to
        change -- a blocker with no remedy is just a dead end."""
        self.record(subject, FidelityStatus.BLOCKED, detail, layer_id)

    def suppressed_setting(
        self, subject: str, reason: str, layer_id: str | None = None
    ) -> None:
        """A value the user configured that will not appear in the output.

        Its own helper because this is the class of failure the incumbent gets
        wrong repeatedly, and naming it makes it hard to forget.
        """
        self.record(subject, FidelityStatus.UNSUPPORTED, reason, layer_id)

    # ---- inspection -----------------------------------------------------

    @property
    def items(self) -> tuple[FidelityItem, ...]:
        return tuple(self._items)

    @property
    def has_blockers(self) -> bool:
        return any(i.status is FidelityStatus.BLOCKED for i in self._items)

    def by_status(self, status: FidelityStatus) -> tuple[FidelityItem, ...]:
        return tuple(i for i in self._items if i.status is status)

    def for_layer(self, layer_id: str) -> tuple[FidelityItem, ...]:
        return tuple(i for i in self._items if i.layer_id == layer_id)

    def counts(self) -> dict[str, int]:
        """Per-status totals, every status present even at zero.

        Fixed keys so a snapshot diff shows a count going 0 → 1 rather than a
        key appearing, which is much easier to read in a test failure.
        """
        counts = {status.value: 0 for status in FidelityStatus}
        for item in self._items:
            counts[item.status.value] += 1
        return counts

    def summary(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "hasBlockers": self.has_blockers,
            "total": len(self._items),
        }

    def extend(self, items: tuple[FidelityItem, ...]) -> None:
        self._items.extend(items)

    def __len__(self) -> int:
        return len(self._items)
