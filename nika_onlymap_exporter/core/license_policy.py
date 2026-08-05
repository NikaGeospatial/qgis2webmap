"""OnlyMap licence caps -- detection always, enforcement pluggable.

The embedded OnlyMap runtime enforces free-tier limits **inside the exported
artifact, on the recipient's machine**: 5 layers and 25,000 rows per layer. The
explanation lands on a validation stream no recipient will ever look at.

**The two caps behave differently.** Verified against the runtime bundle
(0.5.11) on 2026-08-05, because this file previously claimed both were hard
drops and that is wrong for rows:

* **Layer cap**: a hard drop. Layers past the fifth render *nothing*.
* **Row cap**: a **truncation**. The runtime slices the layer to the first
  25,000 rows *in source order* (`data.slice(0, 25000)`) and renders those. The
  rest are silently absent, and - the part that makes this worse than it sounds
  - "the legend, filter widgets and ctx.stats describe only the shown subset",
  in the runtime's own words. So a truncated layer does not look broken. It
  looks complete and is wrong, which is exactly the failure a recipient cannot
  detect.

Users report this as "it picked a random 25,000". It is not random - it is
source order - but from the map it is indistinguishable from random, and the
distinction does not help them.

That makes it our problem, not theirs. An ordinary QGIS project has more than
five layers, so without a check the plugin would cheerfully produce artifacts
that silently lose layers for whoever opens them.

Two halves, deliberately separated:

* **Detection** is unconditional and useful under every policy -- the counts feed
  the Fidelity tab whether or not they block anything.
* **Enforcement** is a swappable policy. Decided 2026-07-30: **exports ship
  without a licence key**, so `FreeTierPolicy` is the one in play. Retention does
  not need a gate - the artifact is built from `<om-map>` custom elements, so
  only OnlyMapJS renders it at all. `LicensedPolicy` stays because it costs
  nothing and saves a rewrite if that ever changes.

Worth stating because it is easy to assume otherwise: **opening the file from the
filesystem does not lift the caps.** The runtime's own licence module says "NO
localhost exemption (gates apply identically everywhere)". A `file://` artifact is
gated exactly like a hosted one.

`FreeTierPolicy` **blocks**, per the project's non-negotiable: never write a
knowingly broken artifact.

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .export_ir import ExportProject
from .fidelity_report import FidelityReportBuilder

# Published free-tier limits, from the runtime's own licence module.
FREE_TIER_MAX_LAYERS = 5
FREE_TIER_MAX_ROWS_PER_LAYER = 25_000


# What an OnlyMap licence token looks like, taken from the runtime's own
# validator: `om_live_<base64url payload>.<base64url Ed25519 signature>`.
LICENSE_KEY_PATTERN = re.compile(r"^om_live_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class LicenseKeyInfo:
    """What a key claims, read from its payload without verifying it.

    **This is not verification and must never be presented as it.** The runtime
    checks an Ed25519 signature against a public key baked into the bundle; we
    have no private key and no business duplicating that. What we can do is read
    the payload - it is plain base64url JSON - and show the user what the key
    they pasted says about itself, so a wrong or expired key is caught in the
    dialog rather than by the person who receives the map.
    """

    plan: str | None = None
    domains: tuple[str, ...] = ()
    expires: int | None = None
    key_id: str | None = None
    malformed: bool = False

    @property
    def is_expired(self) -> bool:
        if self.expires is None:
            return False
        return time.time() > self.expires

    @property
    def covers_local_files(self) -> bool:
        """Would this key license a map opened by double-clicking it?

        The runtime matches `location.hostname` against the key's domains, and
        a `file://` page has an empty hostname - so nothing but a domain entry
        that matches the empty string can cover it. `*.` is the only wildcard
        form that does. In practice a normal key issued for a real domain
        **does not license a standalone file**, which is the single most
        surprising thing about this feature and has to be said out loud.
        """
        return any(domain in ("", "*.") for domain in self.domains)


def looks_like_license_key(value: str) -> bool:
    """Shape check only, matching the runtime's own regex."""
    return bool(LICENSE_KEY_PATTERN.match(value.strip()))


def describe_license_key(value: str) -> LicenseKeyInfo:
    """Read a key's payload for display. Never raises on rubbish input."""
    text = value.strip()
    if not looks_like_license_key(text):
        return LicenseKeyInfo(malformed=True)

    payload_text = text[len("om_live_") :].split(".", 1)[0]
    try:
        # base64url without padding, which is what the runtime emits.
        padding = "=" * ((4 - len(payload_text) % 4) % 4)
        raw = base64.urlsafe_b64decode(payload_text + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return LicenseKeyInfo(malformed=True)

    if not isinstance(payload, dict):
        return LicenseKeyInfo(malformed=True)

    plan = payload.get("plan")
    domains = payload.get("domains")
    expires = payload.get("exp")
    key_id = payload.get("keyId")

    return LicenseKeyInfo(
        plan=plan if isinstance(plan, str) and plan else None,
        domains=tuple(str(d) for d in domains) if isinstance(domains, list) else (),
        expires=int(expires) if isinstance(expires, (int, float)) else None,
        key_id=key_id if isinstance(key_id, str) else None,
    )


@dataclass(frozen=True)
class CapViolation:
    """One breach of a licence limit, phrased for a user rather than a log."""

    subject: str
    limit: int
    actual: int
    detail: str
    layer_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "limit": self.limit,
            "actual": self.actual,
            "detail": self.detail,
            "layerId": self.layer_id,
        }


@dataclass(frozen=True)
class CapVerdict:
    """The outcome of evaluating a project against a policy."""

    allowed: bool
    violations: tuple[CapViolation, ...] = ()
    license_key: str | None = None

    @property
    def has_violations(self) -> bool:
        return bool(self.violations)

    @property
    def needs_runtime_validation(self) -> bool:
        """Whether the writer should set `validate` on `<om-map>`.

        Only when something will actually go wrong. `validate` mounts the
        runtime's error panel, which is exactly right when layers are missing and
        pure noise on a clean deliverable - a client should not receive a map
        wearing a diagnostic badge for no reason.
        """
        return self.has_violations and self.license_key is None

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "violations": [v.snapshot() for v in self.violations],
            # Never snapshot the key itself -- publishable or not, it does not
            # belong in a fixture or a diff.
            "hasLicenseKey": self.license_key is not None,
        }


class LicensePolicy(Protocol):
    """What the exporter needs to know about licensing."""

    def evaluate(self, project: ExportProject) -> CapVerdict: ...


def detect_violations(project: ExportProject) -> tuple[CapViolation, ...]:
    """Find every free-tier breach. Independent of any policy.

    Only exportable layers count: a layer we could not read is already reported
    as a fidelity problem and will not reach the manifest.
    """
    layers = project.exportable_layers
    violations: list[CapViolation] = []

    if len(layers) > FREE_TIER_MAX_LAYERS:
        dropped = layers[FREE_TIER_MAX_LAYERS:]
        names = ", ".join(layer.name for layer in dropped)
        violations.append(
            CapViolation(
                subject="Layer count",
                limit=FREE_TIER_MAX_LAYERS,
                actual=len(layers),
                detail=(
                    f"The free tier renders {FREE_TIER_MAX_LAYERS} layers; this "
                    f"project has {len(layers)}. These would not appear for "
                    f"anyone opening the map: {names}."
                ),
            )
        )

    for layer in layers:
        if layer.feature_count > FREE_TIER_MAX_ROWS_PER_LAYER:
            violations.append(
                CapViolation(
                    subject=f"Feature count in '{layer.name}'",
                    limit=FREE_TIER_MAX_ROWS_PER_LAYER,
                    actual=layer.feature_count,
                    detail=(
                        f"'{layer.name}' has {layer.feature_count:,} features; the "
                        f"free tier renders {FREE_TIER_MAX_ROWS_PER_LAYER:,} per "
                        f"layer. Only the first {FREE_TIER_MAX_ROWS_PER_LAYER:,} "
                        "are drawn, in source order, and the legend and filters "
                        "describe only those -- so the map looks complete while "
                        f"{layer.feature_count - FREE_TIER_MAX_ROWS_PER_LAYER:,} "
                        "features are missing from it."
                    ),
                    layer_id=layer.layer_id,
                )
            )

    return tuple(violations)


class FreeTierPolicy:
    """No licence key: caps apply, and a breach warns rather than blocks.

    Decided 2026-07-30. Blocking an ordinary six-layer project would make the
    plugin useless for the common case, and the breakage is not silent from
    either end:

    * **At export time** the fidelity report names every layer that will not
      render, and the dialog surfaces it before the user clicks Export.
    * **In the artifact** the runtime enforces the caps itself and reports each
      violation on its validation stream. The writer sets `validate` on
      `<om-map>` when violations exist, which mounts the runtime's error panel -
      a corner badge that expands to the list and flashes the offending element -
      so a recipient sees an explanation rather than an unexplained gap.

    That is the distinction that matters: never write a *silently* broken
    artifact. Informed breakage is the user's call to make.
    """

    def evaluate(self, project: ExportProject) -> CapVerdict:
        return CapVerdict(allowed=True, violations=detect_violations(project))


class LicensedPolicy:
    """A licence key lifts the caps and hides the corner badge.

    Keys are signed, publishable and origin-restricted, so carrying one in
    generated markup is safe by design - the signature is what protects it, not
    secrecy, and it is meant to be served to browsers.

    **The domain restriction is the catch, and it is a big one.** Confirmed
    against the runtime bundle (0.5.11) on 2026-08-05: the key's payload carries
    a `domains` list, and the runtime compares it to `location.hostname`. A
    `file://` page has an **empty** hostname, so a key issued for `example.com`
    does not license a map someone opens by double-clicking it - the runtime
    logs "not licensed for" and silently falls back to the free plan and its
    caps.

    That makes a licensed Standalone HTML export the one combination that looks
    like it should work and does not. `LicenseKeyInfo.covers_local_files` is how
    the dialog spots it, so the user hears it from us rather than from whoever
    they sent the map to.
    """

    def __init__(self, license_key: str) -> None:
        self.license_key = license_key

    def evaluate(self, project: ExportProject) -> CapVerdict:
        # Violations are still reported, because the user may later share the
        # project with someone exporting without a key.
        return CapVerdict(
            allowed=True,
            violations=detect_violations(project),
            license_key=self.license_key,
        )


def default_policy(license_key: str | None = None) -> LicensePolicy:
    """Pick a policy. One place to change when the product decision lands."""
    if license_key:
        return LicensedPolicy(license_key)
    return FreeTierPolicy()


def report_verdict(verdict: CapVerdict, report: FidelityReportBuilder) -> None:
    """Fold a verdict into the fidelity report.

    Severity follows the policy. With a key the caps are lifted, so a violation
    is advisory - worth knowing because someone else may export the same project
    without one. Without a key the layer genuinely will not render, so it is
    reported as unsupported, with the remedy stated.
    """
    for violation in verdict.violations:
        if verdict.license_key is not None:
            report.approximated(
                violation.subject,
                f"{violation.detail} Your licence key lifts this limit, so the "
                "map you export is complete.",
                violation.layer_id,
            )
        else:
            report.unsupported(
                violation.subject,
                f"{violation.detail} The exported map shows an explanation in "
                "its corner so the recipient is not left guessing. To include "
                "everything, split the project across several maps or reduce the "
                "number of features.",
                violation.layer_id,
            )
