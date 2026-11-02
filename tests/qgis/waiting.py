"""Wait for a condition while Qt runs its event loop, instead of sleeping.

A fixed sleep is either too short on a slow machine (the test flakes) or too
long everywhere else (the suite crawls). `wait_until` runs the event loop and
returns the moment the state a test needs is there, and fails with a message
naming what it waited for when it never comes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")

# Between two passes of the event loop: short enough that a ready state is seen
# almost at once, long enough not to spin a core while a thread works.
POLL_S = 0.005


def wait_until(
    qgis_app,
    condition: Callable[[], T],
    what: str,
    seconds: float = 10.0,
    passes: int = 1,
) -> T:
    """Run the event loop until `condition()` is truthy and return its value.

    Events are processed before every check, so what a posted event or a due
    timer changes is seen on the first pass. `passes` asks for that many
    truthy checks in a row: a widget that is quiet for three passes has also
    run the zero-delay timers the first two started. Raises AssertionError
    naming `what` once `seconds` have gone by without it."""
    deadline = time.monotonic() + seconds
    streak = 0
    while True:
        qgis_app.processEvents()
        value = condition()
        streak = streak + 1 if value else 0
        if streak >= passes:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {seconds:g} s waiting for {what}")
        time.sleep(POLL_S)
