"""How an export is told to stop, and where it can no longer be stopped.

An export has a point of no return: the moment the finished artifact starts
being moved into place (`atomic_write.staged_beside`'s `os.replace`). Before it,
cancelling leaves the user's files exactly as they were. After it the file is
written, so reporting "cancelled" would be a lie - the work is done and the
honest answer is success. `build_artifact` therefore checks for cancellation
once, immediately before the commit, and never again.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable

CancelCheck = Callable[[], bool]


class ExportCancelledError(Exception):
    """The user (or a Processing feedback) asked the export to stop.

    Not an error: it unwinds the work before anything is committed and is
    reported as a cancellation, never as a failure to investigate.
    """


def raise_if_cancelled(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise ExportCancelledError
