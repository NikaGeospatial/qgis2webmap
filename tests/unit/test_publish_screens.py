"""The wording the hosting screens build, without a running QGIS.

These sentences are the part of `ui.main_dialog` that has no Qt in it: they take
a `DeviceFlow` or a publish conflict and return text. They had no coverage at
all, and that is exactly how the sign-in message came to read two fields -
`verification_url` and `user_code` - that `hosting.auth.DeviceFlow` has never
had, turning the first press of Host into an `AttributeError`.

Importing `ui.main_dialog` needs PyQGIS, which the unit tier deliberately does
not have, so the functions under test are lifted out of the module source and
compiled on their own. They are module-level and pure, with nothing but
`contextlib` and `datetime` behind them, which is what makes that honest rather
than clever - and a lift that stops finding a function fails loudly here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ast
import contextlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from nika_onlymap_exporter.hosting.auth import DeviceFlow
from nika_onlymap_exporter.hosting.client import PublishConflictError

DIALOG_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "nika_onlymap_exporter"
    / "ui"
    / "main_dialog.py"
)

LIFTED = ("sign_in_wait_message", "_publish_date", "republish_conflict_text")


def _lift(names: tuple[str, ...]) -> dict[str, Callable[..., str]]:
    """Compile the named module-level functions out of the dialog source.

    `compile` inherits the future statements in force where it is called, and
    this module has `annotations` on, which is what makes the lift work: every
    annotation stays a string, so a function annotated with a Qt or a client
    type still defines and runs with neither of them present.
    """
    module = ast.parse(DIALOG_SOURCE.read_text(encoding="utf-8"))
    wanted = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    missing = set(names) - {node.name for node in wanted}
    if missing:  # pragma: no cover - only when someone renames one
        pytest.fail(f"main_dialog no longer defines {', '.join(sorted(missing))}")

    namespace: dict[str, object] = {"contextlib": contextlib, "datetime": datetime}
    exec(
        compile(ast.Module(body=wanted, type_ignores=[]), "main_dialog", "exec"),
        namespace,
    )
    return {name: namespace[name] for name in names}  # type: ignore[misc]


COPY = _lift(LIFTED)


def Conflict(  # noqa: N802 - reads as the type it builds
    current_release: int,
    published_by: str,
    published_at: str,
    was_rollback: bool = False,
    rolled_back_to: int | None = None,
) -> PublishConflictError:
    """A real conflict, so the screen is pinned to the client's own attributes.

    The message the client composes is deliberately not what the screen shows:
    it is one line for a log, and the dialog has a paragraph and two buttons.
    """
    return PublishConflictError(
        "server says no",
        current_release=current_release,
        published_by=published_by,
        published_at=published_at,
        was_rollback=was_rollback,
        rolled_back_to=rolled_back_to,
    )


class TestSignInMessage:
    """The regression: the message must read fields `DeviceFlow` really has."""

    def test_it_is_built_from_a_real_device_flow(self) -> None:
        flow = DeviceFlow(
            flow_id="flow-1",
            verifier="secret",
            authorize_url="https://api.nika.eco/desktop/auth/go?flow=flow-1",
        )
        message = COPY["sign_in_wait_message"](flow)
        assert flow.authorize_url in message
        assert "approve this computer" in message

    def test_it_promises_no_code_the_flow_cannot_supply(self) -> None:
        flow = DeviceFlow(
            flow_id="flow-1", verifier="secret", authorize_url="https://x.nika.eco/a"
        )
        assert "code" not in COPY["sign_in_wait_message"](flow).lower()

    def test_the_secret_verifier_never_reaches_the_screen(self) -> None:
        flow = DeviceFlow(
            flow_id="flow-1",
            verifier="do-not-show-this",
            authorize_url="https://x.nika.eco/a",
        )
        assert "do-not-show-this" not in COPY["sign_in_wait_message"](flow)


class TestPublishDate:
    def test_an_iso_instant_reads_as_a_date(self) -> None:
        assert COPY["_publish_date"]("2026-09-08T11:30:00Z") == "8 September"

    def test_an_offset_form_is_accepted_too(self) -> None:
        assert COPY["_publish_date"]("2026-09-08T11:30:00+02:00") == "8 September"

    def test_an_unreadable_instant_is_shown_rather_than_dropped(self) -> None:
        assert COPY["_publish_date"]("last Tuesday") == "last Tuesday"

    def test_nothing_stays_nothing(self) -> None:
        assert COPY["_publish_date"]("") == ""


class TestRepublishConflictText:
    def test_it_names_the_release_the_author_and_the_date(self) -> None:
        text = COPY["republish_conflict_text"](
            Conflict(7, "Bob", "2026-09-08T09:00:00Z"), 3
        )
        assert "release 7" in text
        assert "Bob" in text
        assert "8 September" in text
        assert "based on release 3" in text
        assert "replace release 7" in text

    def test_an_ordinary_conflict_says_nothing_about_a_rollback(self) -> None:
        text = COPY["republish_conflict_text"](
            Conflict(7, "Bob", "2026-09-08T09:00:00Z"), 3
        )
        assert "rollback" not in text.lower()

    def test_a_rollback_says_so_and_says_what_it_went_back_to(self) -> None:
        """The case a release number alone gets backwards.

        Release 8 being a rollback to release 3 means the live map is showing
        *older* content, so the colleague who published release 7 is behind it
        as well. Read as ordinary newer work, it would talk someone out of an
        override they should make.
        """
        text = COPY["republish_conflict_text"](
            Conflict(8, "Bob", "2026-09-08T09:00:00Z", True, 3), 7
        )
        assert "rollback to release 3" in text
        assert "older content" in text
        assert "undoing the rollback" in text

    def test_a_rollback_with_no_target_still_says_it_was_one(self) -> None:
        text = COPY["republish_conflict_text"](
            Conflict(8, "Bob", "2026-09-08T09:00:00Z", True, None), 7
        )
        assert "rollback to an earlier release" in text

    def test_an_unknown_publisher_is_named_as_one(self) -> None:
        text = COPY["republish_conflict_text"](Conflict(2, "  ", ""), 1)
        assert "someone else" in text

    def test_a_project_with_no_release_says_that_rather_than_none(self) -> None:
        text = COPY["republish_conflict_text"](
            Conflict(2, "Bob", "2026-09-08T09:00:00Z"), None
        )
        assert "None" not in text
        assert "not based on any release" in text
