"""The published release number, and why it lives in the `.qgz`.

`hostedReleaseN` is read straight back out of the project, so a republish can
say which release it is based on and be told when the map has moved on since.
`core.settings` touches `QgsProject` only through `readEntry`/`writeEntry`, and
imports it for typing alone, so this needs no PyQGIS.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses

from nika_onlymap_exporter.core import settings as core_settings
from nika_onlymap_exporter.core.settings import (
    KEY_HOSTED_RELEASE_N,
    SCOPE,
    DialogState,
    load_hosted_release_n,
    save_hosted_release_n,
)


class FakeProject:
    """`readEntry`/`writeEntry` over a dict, with the QGIS return shape."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def readEntry(  # noqa: N802 - QGIS API
        self, scope: str, key: str, default: str = ""
    ) -> tuple[str, bool]:
        value = self.entries.get((scope, key))
        return (default, False) if value is None else (value, True)

    def writeEntry(self, scope: str, key: str, value: str) -> bool:  # noqa: N802
        self.entries[(scope, key)] = value
        return True


class TestRoundTrip:
    def test_an_unpublished_project_has_no_release(self) -> None:
        assert load_hosted_release_n(FakeProject()) is None

    def test_what_was_saved_comes_back(self) -> None:
        project = FakeProject()
        save_hosted_release_n(project, 7)
        assert load_hosted_release_n(project) == 7

    def test_it_is_written_under_the_shared_scope(self) -> None:
        project = FakeProject()
        save_hosted_release_n(project, 7)
        assert project.entries[(SCOPE, KEY_HOSTED_RELEASE_N)] == "7"

    def test_an_unreadable_entry_reads_as_never_published(self) -> None:
        """Hand-edited or written by something else. Not a failed load."""
        project = FakeProject()
        project.entries[(SCOPE, KEY_HOSTED_RELEASE_N)] = "the seventh"
        assert load_hosted_release_n(project) is None

    def test_release_zero_is_not_mistaken_for_absence(self) -> None:
        project = FakeProject()
        save_hosted_release_n(project, 0)
        assert load_hosted_release_n(project) == 0


class TestItNeverReachesTheSnapshot:
    """Publishing changes not one byte of what the writer produces.

    A release number folded into `DialogState` would invalidate the cached read
    and rebuild the live preview to record a publish - see the comment beside
    `KEY_HOSTED_RELEASE_N`.
    """

    def test_no_dialog_state_field_carries_it(self) -> None:
        names = {field.name for field in dataclasses.fields(DialogState)}
        assert not {name for name in names if "release" in name.lower()}

    def test_save_state_writes_no_release_entry(self) -> None:
        project = FakeProject()
        core_settings.save_state(project, DialogState())
        assert (SCOPE, KEY_HOSTED_RELEASE_N) not in project.entries
