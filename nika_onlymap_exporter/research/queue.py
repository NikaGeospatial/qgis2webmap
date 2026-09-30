"""The outbound queue: reports waiting to be sent, oldest first.

At most `MAX_QUEUED` reports; when full, the oldest is dropped to make room.
Each entry is the report's exact encoded JSON plus a local id used only to
remove it once the server has answered - the id is never sent - and how many
answers so far refused it without saying it was refused (see
`service.counts_against_report`).

The queue lives inside the one research state file (`state.py`), so it is
persisted with everything else in a single write.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace

MAX_QUEUED = 50


@dataclass(frozen=True)
class QueuedReport:
    id: str
    body: str
    attempts: int = 0

    def to_stored(self) -> dict[str, str | int]:
        return {"id": self.id, "body": self.body, "attempts": self.attempts}

    @classmethod
    def from_stored(cls, stored: object) -> QueuedReport | None:
        if not isinstance(stored, Mapping):
            return None
        report_id = stored.get("id")
        body = stored.get("body")
        if not isinstance(report_id, str) or not isinstance(body, str):
            return None
        attempts = stored.get("attempts")
        # Missing in a queue saved before attempts were counted: none yet.
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
            attempts = 0
        return cls(id=report_id, body=body, attempts=attempts)


def enqueue(
    queue: list[QueuedReport], body: str, cap: int = MAX_QUEUED
) -> QueuedReport:
    """Append a report, dropping the oldest ones past `cap`."""
    item = QueuedReport(id=secrets.token_hex(8), body=body)
    queue.append(item)
    if len(queue) > cap:
        del queue[: len(queue) - cap]
    return item


def count_attempt(queue: list[QueuedReport], report_id: str) -> int | None:
    """One more unexplained refusal of `report_id`: its new count, or `None`."""
    for index, item in enumerate(queue):
        if item.id == report_id:
            queue[index] = replace(item, attempts=item.attempts + 1)
            return item.attempts + 1
    return None


def remove(queue: list[QueuedReport], report_id: str) -> bool:
    for index, item in enumerate(queue):
        if item.id == report_id:
            del queue[index]
            return True
    return False
