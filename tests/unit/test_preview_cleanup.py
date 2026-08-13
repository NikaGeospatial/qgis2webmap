"""Preview directories are removed on close and swept when stale.

The preview exists only to be served by the dialog's own localhost server, so
once the dialog closes the files are dead weight - and on Windows nothing ever
clears the temp directory, so without cleanup they accumulate per project,
per machine, for years.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import time

from nika_onlymap_exporter.ui.preview import (
    preview_directory,
    prune_stale_previews,
    remove_preview,
)


class TestPreviewCleanup:
    def test_remove_preview_deletes_the_project_directory(self) -> None:
        identity = "unit-test-project-remove"
        directory = preview_directory(identity)
        (directory / "index.html").write_text("<om-map></om-map>")
        remove_preview(identity)
        assert not directory.exists()

    def test_remove_preview_tolerates_an_absent_directory(self) -> None:
        remove_preview("never-previewed-project")  # must not raise

    def test_prune_removes_only_stale_directories(self) -> None:
        stale = preview_directory("unit-test-project-stale")
        fresh = preview_directory("unit-test-project-fresh")
        (stale / "index.html").write_text("old")
        (fresh / "index.html").write_text("new")
        week_and_a_day_ago = time.time() - 8 * 86400
        os.utime(stale, (week_and_a_day_ago, week_and_a_day_ago))

        prune_stale_previews(max_age_days=7.0)

        assert not stale.exists()
        assert fresh.exists()
        remove_preview("unit-test-project-fresh")
