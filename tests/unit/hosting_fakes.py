"""A transport that answers from a script and never opens a socket.

Shared by every hosting test. Not named `test_*` on purpose: it holds no
assertions, and a fake that pytest collects as a test module is a fake that
gains one by accident.

An unscripted request raises rather than falling through, which is what makes
"no test may touch the network" an enforced property rather than a convention.

A scripted answer may be a callable rather than a response. The publish
handshake is content-addressed - what the server offers an upload slot for
depends on the digests it was just sent - so some answers can only be written
once the request is in hand.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

from nika_onlymap_exporter.hosting.client import HttpRequest, HttpResponse


class FakeTransport:
    """Answers from a script, and records what it was asked."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected request to {request.url}")
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(request)
        return answer


def ok(payload: dict) -> HttpResponse:
    return HttpResponse(status=200, body=json.dumps(payload).encode("utf-8"))
