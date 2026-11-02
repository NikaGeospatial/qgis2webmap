"""The shared wait (`tests/qgis/waiting.py`): it returns as soon as the state
is there, and a state that never comes fails in bounded time with a message
naming it - never a hang, never a silent pass.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

import pytest
from waiting import wait_until

pytest.importorskip("qgis.core")


def test_it_returns_the_value_once_a_timer_has_set_it(qgis_app) -> None:
    from qgis.PyQt.QtCore import QTimer

    seen: list[str] = []
    QTimer.singleShot(50, lambda: seen.append("fired"))
    start = time.monotonic()
    assert wait_until(qgis_app, lambda: list(seen), "the timer") == ["fired"]
    assert time.monotonic() - start < 2.0


def test_a_zero_delay_timer_is_seen_on_the_first_pass(qgis_app) -> None:
    from qgis.PyQt.QtCore import QTimer

    seen: list[str] = []
    QTimer.singleShot(0, lambda: seen.append("fired"))
    # No time to wait at all: only the pass before the first check runs it.
    assert wait_until(qgis_app, lambda: seen, "the zero-delay timer", seconds=0)


def test_a_state_that_never_comes_fails_in_time_and_names_it(qgis_app) -> None:
    start = time.monotonic()
    with pytest.raises(AssertionError) as raised:
        wait_until(qgis_app, lambda: False, "the flag that never rises", seconds=0.3)
    elapsed = time.monotonic() - start
    assert 0.3 <= elapsed < 3.0
    assert str(raised.value) == (
        "timed out after 0.3 s waiting for the flag that never rises"
    )


def test_passes_need_that_many_truthy_checks_in_a_row(qgis_app) -> None:
    answers = iter([True, False, True, True, True, False])
    calls: list[bool] = []

    def condition() -> bool:
        calls.append(next(answers))
        return calls[-1]

    assert wait_until(qgis_app, condition, "three in a row", passes=3)
    # The lone True at the start did not count: the streak restarted after it.
    assert calls == [True, False, True, True, True]
