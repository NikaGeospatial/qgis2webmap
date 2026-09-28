"""Keeps the dialog's idea of the hosted map in step with the server.

The deciding is done in `hosting.map_state`, which has no Qt in it. This is only
the plumbing: when to ask, on which thread, and where the answer goes.

**When.** On request - the dialog asks when it is shown, when it is focused
again, when a different project is opened and after a publish - and on a slow
timer while the dialog is on screen, so a map taken down from the dashboard in
another window is noticed without the user doing anything. Requests landing
while a check is already in flight are folded into one follow-up, never
queued; requests closer together than `MIN_INTERVAL_SECONDS` are dropped,
because focusing a window twice is not two questions.

**Never in the way.** The request runs on its own thread through the same
QGIS-backed transport a publish uses, so the dialog never waits on it, and it is
not a `BackgroundJob`: it neither shows the progress bar nor takes the one-job
slot the export pipeline depends on. A failure is a state (`offline`,
`signed_out`), never a message box. With no sign-in token nothing is sent at
all, and a token the server has refused is not sent again on the timer.

**What it sends.** One `GET /maps/{map id}` to the NIKA API, carrying the map id
and the sign-in token. Nothing about the project's contents.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time
from collections.abc import Callable

from qgis.PyQt.QtCore import QObject, QThread, QTimer, pyqtSignal

from ..hosting.auth import authorized_client, resolve_token
from ..hosting.map_state import (
    PRESENCE_OFFLINE,
    PRESENCE_SIGNED_OUT,
    RemoteMapState,
    unpublished,
)
from .hosting_transport import make_qgis_transport

# Slow enough to be invisible in any server's traffic, quick enough that a
# takedown in the dashboard is reflected before the user has come back to QGIS
# and read the button.
POLL_INTERVAL_MS = 60_000

# Focus changes arrive in bursts; this is the floor between two real checks.
MIN_INTERVAL_SECONDS = 10.0

# Fetch threads still running are held here rather than by the dialog, so
# closing the dialog mid-request never destroys a QThread that is still running
# - which Qt answers by aborting the process. Each removes itself on finishing.
_RUNNING: set[_StateFetch] = set()


class _StateFetch(QThread):
    """One `GET /maps/{id}`, off the GUI thread."""

    # (token, map id, state): the request's own identity travels with the answer,
    # so the slot can be a bound method - which Qt disconnects by itself if the
    # watch is destroyed while the request is still out.
    answered = pyqtSignal(str, str, object)

    def __init__(self, token: str, map_id: str) -> None:
        super().__init__(None)
        self._token = token
        self._map_id = map_id

    def run(self) -> None:
        try:
            client = authorized_client(self._token, transport=make_qgis_transport())
            state = client.map_state(self._map_id)
        except Exception:  # never let a status check take anything down
            state = RemoteMapState(map_id=self._map_id, presence=PRESENCE_OFFLINE)
        self.answered.emit(self._token, self._map_id, state)


class HostedMapWatch(QObject):
    """Asks the server about the project's map, and says when the answer changes."""

    changed = pyqtSignal(object)

    def __init__(self, parent: QObject, stored_map_id: Callable[[], str]) -> None:
        super().__init__(parent)
        self._stored_map_id = stored_map_id
        self._in_flight: _StateFetch | None = None
        self._again = False
        self._last_started = 0.0
        # The token the server last refused, so the timer does not keep
        # presenting it. A new sign-in produces a different token and clears it.
        self._refused_token: str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(lambda: self.check(from_timer=True))

    def start(self) -> None:
        """Begin the slow poll, and check now."""
        if not self._timer.isActive():
            self._timer.start()
        self.check(force=True)

    def stop(self) -> None:
        self._timer.stop()

    def check(self, force: bool = False, from_timer: bool = False) -> None:
        """Ask again, unless an answer is already on its way or very fresh."""
        map_id = self._stored_map_id()
        if not map_id:
            self.changed.emit(unpublished())
            return
        token = resolve_token()
        if token is None:
            self.changed.emit(
                RemoteMapState(map_id=map_id, presence=PRESENCE_SIGNED_OUT)
            )
            return
        if from_timer and token == self._refused_token:
            return
        if self._in_flight is not None:
            self._again = True
            return
        if not force and time.monotonic() - self._last_started < MIN_INTERVAL_SECONDS:
            return

        self._last_started = time.monotonic()
        fetch = _StateFetch(token, map_id)
        self._in_flight = fetch
        _RUNNING.add(fetch)
        fetch.answered.connect(self._on_answer)
        fetch.finished.connect(lambda: _RUNNING.discard(fetch))
        fetch.finished.connect(fetch.deleteLater)
        fetch.start()

    def _on_answer(self, token: str, map_id: str, state: RemoteMapState) -> None:
        self._in_flight = None
        self._refused_token = token if state.presence == PRESENCE_SIGNED_OUT else None
        # An answer about an id the project no longer holds is about somebody
        # else's map now; the follow-up below asks about the right one.
        if map_id == self._stored_map_id():
            self.changed.emit(state)
        else:
            self._again = True
        if self._again:
            self._again = False
            self.check(force=True)
