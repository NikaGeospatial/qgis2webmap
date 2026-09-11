"""What the confirmation screen says, and when the truncation warning fires.

`docs/hosting.md` commits publicly to this: publishing is "a separate, explicit
step after signing in, with a confirmation naming what is about to leave your
machine, and never a side effect of exporting". This module is that promise in
code - the wording lives here rather than inline in `main_dialog` so it can be
read and tested without a QGIS application, which is what keeps the commitment
from quietly eroding one edit at a time.

Three things the confirmation has to say, and none of them is optional:

* **Anyone with the link can open it.** A hosted map has no password.
* **The attribute data goes with it.** The features are embedded in the page,
  so publishing the map publishes every column that survived the popup
  settings, including the ones left in because it was convenient.
* **Republication rights are the author's to confirm.** Source-data licences
  routinely distinguish analysing from republishing, and the plugin cannot know
  which layers are the user's to put online.

And a fourth that only sometimes applies: on the free tier the hosted map is
subject to OnlyMap's own caps, so a project past them is published *truncated*.
That is the failure this module exists to prevent - truncation discovered by a
map's audience rather than by its author.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.license_policy import CapViolation
from .client import UploadFile, insecure_loopback_base


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


def insecure_transport_text(api_base: str | None = None) -> str:
    """The banner for a publish running under the plain-HTTP loopback exemption.

    Empty string when it is not in play, which is every ordinary publish - so
    callers can append it unconditionally and nothing changes for anyone who
    has not switched it on.

    It is here, in the confirmation, rather than in a log line, for the same
    reason the truncation warning is: the point of an exemption that weakens
    transport security is that the person accepting it knows they are. An
    exemption nobody is ever shown is one that gets left on.
    """
    base = insecure_loopback_base(api_base)
    if not base:
        return ""
    return (
        f"Local development: this publish goes to {base} over plain HTTP, "
        "unencrypted, because NIKA_ALLOW_INSECURE_LOOPBACK is set. That is "
        "only safe because the address is this machine - nothing leaves it. "
        "Unset the variable to go back to requiring HTTPS."
    )


def publish_consent_text(
    title: str,
    files: Sequence[UploadFile],
    feature_count: int,
    layer_count: int,
    api_base: str | None = None,
) -> str:
    """The body of the confirmation, naming exactly what leaves the machine.

    `api_base` is optional and only decides whether the insecure-transport
    banner appears; left out, the same environment the client itself reads is
    consulted, so a caller that does not know about the exemption still shows
    it rather than silently omitting it.
    """
    total = sum(item.size_bytes for item in files)
    names = ", ".join(item.filename for item in files)
    features = f"{feature_count:,} feature{'s' if feature_count != 1 else ''}"
    layers = f"{layer_count} layer{'s' if layer_count != 1 else ''}"
    insecure = insecure_transport_text(api_base)
    # Prepended, not appended: it is a statement about how everything below
    # travels, and a reader who stops after the first paragraph has still seen
    # the one thing they could not have guessed.
    prefix = f"{insecure}\n\n" if insecure else ""
    return (
        f"{prefix}"
        f"'{title}' is about to be uploaded to NIKA and published at a public "
        "web address.\n\n"
        f"What leaves this machine: {names} - {total / 1024 / 1024:.1f} MB, "
        f"carrying {layers} and {features}.\n\n"
        "Three things worth reading before you press Publish:\n\n"
        "- Anyone with the link can open the map. There is no password on a "
        "hosted map, and a link that has been shared cannot be unshared.\n"
        "- The attribute data is published with it. Every feature is embedded "
        "in the page, so any column you left in - including ones you only see "
        "in the attribute table - is readable by whoever opens the map. The "
        "Layers tab is where fields are hidden.\n"
        "- Publishing is your call to make. Licences on source data usually "
        "treat republishing differently from analysing, and the plugin cannot "
        "tell which of your layers are yours to put online."
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
