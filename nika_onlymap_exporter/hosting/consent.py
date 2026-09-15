"""What the confirmation screen says, and when the truncation warning fires.

`docs/hosting.md` commits publicly to this: publishing is "a separate, explicit
step after signing in, with a confirmation naming what is about to leave your
machine, and never a side effect of exporting". This module is that promise in
code - the wording lives here rather than inline in `main_dialog` so it can be
read and tested without a QGIS application, which is what keeps the commitment
from quietly eroding one edit at a time.

The confirmation names the map, the files, and their size - what leaves the
machine, plainly. It no longer walks through the license/attribute-data/
transport specifics that used to sit here; those are documented in
`docs/hosting.md` for whoever wants them, rather than repeated on every publish.

One warning still fires conditionally: on the free tier the hosted map is
subject to OnlyMap's own caps, so a project past them is published *truncated*.
That is the failure `truncation_warning_text` exists to prevent - truncation
discovered by a map's audience rather than by its author.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.license_policy import CapViolation
from .client import UploadFile


def should_warn_truncation(
    license_key: str | None, violations: Sequence[CapViolation]
) -> bool:
    """Whether the hosted map will lose data the author has not been told about.

    Both halves are required. A free account publishing a project inside the
    caps loses nothing, and a licensed account publishing one past them loses
    nothing either - the key lifts the limits. Only the pair is a problem.

    **The tier is not knowable before `POST /maps/publish/start`.** That call
    sends a title, filenames and sizes and no map data, so asking it before the
    last confirmation costs the user nothing and is what makes this warning
    possible while they can still stop.
    """
    return license_key is None and bool(violations)


def publish_consent_text(
    title: str,
    files: Sequence[UploadFile],
    feature_count: int,
    layer_count: int,
) -> str:
    """The body of the confirmation, naming exactly what leaves the machine."""
    total = sum(item.size_bytes for item in files)
    names = ", ".join(item.filename for item in files)
    features = f"{feature_count:,} feature{'s' if feature_count != 1 else ''}"
    layers = f"{layer_count} layer{'s' if layer_count != 1 else ''}"
    return (
        f"'{title}' is about to be uploaded to NIKA and published at a public "
        "web address.\n\n"
        f"What leaves this machine: {names} - {total / 1024 / 1024:.1f} MB, "
        f"carrying {layers} and {features}."
    )


def truncation_warning_text(violations: Sequence[CapViolation]) -> str:
    """The free-tier warning, shown before a single byte of the map is sent.

    Written as what the map's *audience* will see rather than as a plan limit,
    because that is the consequence the author is being asked to accept.
    """
    detail = "\n".join(f"- {violation.detail}" for violation in violations)
    return (
        "Your NIKA account is on the free tier, so the hosted map renders "
        "under OnlyMap's free-plan caps - and this project is past them.\n\n"
        "Published as it stands, whoever opens the link sees an incomplete "
        "map:\n\n"
        f"{detail}\n\n"
        "The caps do not apply to the file you export and open yourself, which "
        "is why nothing has warned you about this before now. Split the "
        "project across several maps, reduce the features, or upgrade the "
        "account - or publish anyway, knowing what is missing."
    )
