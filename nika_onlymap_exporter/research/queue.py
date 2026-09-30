"""The outbound queue: reports waiting to be sent, oldest first.

At most `MAX_QUEUED` reports; when full, the oldest is dropped to make room.
Each entry is the report's exact encoded JSON plus a local id used only to
remove it once the server has answered - the id is never sent.

The queue lives inside the one research state file (`state.py`), so it is
persisted with everything else in a single write.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass

MAX_QUEUED = 50


@dataclass(frozen=True)
class QueuedReport:
    id: str
    body: str

    def to_stored(self) -> dict[str, str]:
        return {"id": self.id, "body": self.body}

    @classmethod
    def from_stored(cls, stored: object) -> QueuedReport | None:
        if not isinstance(stored, Mapping):
            return None
        report_id = stored.get("id")
        body = stored.get("body")
        if not isinstance(report_id, str) or not isinstance(body, str):
            return None
        return cls(id=report_id, body=body)


def enqueue(
    queue: list[QueuedReport], body: str, cap: int = MAX_QUEUED
) -> QueuedReport:
    """Append a report, dropping the oldest ones past `cap`."""
    item = QueuedReport(id=secrets.token_hex(8), body=body)
    queue.append(item)
    if len(queue) > cap:
        del queue[: len(queue) - cap]
    return item


def remove(queue: list[QueuedReport], report_id: str) -> bool:
    for index, item in enumerate(queue):
        if item.id == report_id:
            del queue[index]
            return True
    return False
