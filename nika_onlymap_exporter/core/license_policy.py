"""OnlyMap licence caps -- detection always, enforcement pluggable.

The embedded OnlyMap runtime enforces free-tier limits **inside the exported
artifact, on the recipient's machine**: 5 layers and 25,000 rows per layer.
Violations are per-layer hard drops -- an over-cap layer renders *nothing*, and
the explanation lands on a validation stream no recipient will ever look at.

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

from dataclasses import dataclass
from typing import Any, Protocol

from .export_ir import ExportProject
from .fidelity_report import FidelityReportBuilder

# Published free-tier limits, from the runtime's own licence module.
FREE_TIER_MAX_LAYERS = 5
FREE_TIER_MAX_ROWS_PER_LAYER = 25_000


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
                        "layer. The layer would render nothing at all -- the limit "
                        "drops the whole layer rather than truncating it."
                    ),
                    layer_id=layer.layer_id,
                )
            )

    return tuple(violations)


class FreeTierPolicy:
    """No licence key: caps apply and a breach blocks the export.

    Blocking rather than warning is deliberate. A warning would let the user
    produce a file that looks fine locally and is missing layers for the person
    they send it to -- the exact silent failure this project exists to avoid.
    """

    def evaluate(self, project: ExportProject) -> CapVerdict:
        violations = detect_violations(project)
        return CapVerdict(allowed=not violations, violations=violations)


class LicensedPolicy:
    """A licence key lifts the caps and hides the corner badge.

    Keys are signed, publishable and origin-restricted, so carrying one in
    generated markup is safe by design. Note that a `file://` artifact has no
    hostname, so a key intended for offline exports has to be issued for all
    domains -- confirm before relying on this in a shipped artifact.
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

    Under a blocking policy a violation is a blocker; under a licensed one the
    same finding is advisory, since the key lifts it. Same detection, different
    severity -- which is the whole point of separating the two.
    """
    for violation in verdict.violations:
        if verdict.allowed:
            report.approximated(
                violation.subject,
                f"{violation.detail} Your licence key lifts this limit.",
                violation.layer_id,
            )
        else:
            report.blocked(violation.subject, violation.detail, violation.layer_id)
