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
                        "layer. The layer would render nothing at all -- the limit "
                        "drops the whole layer rather than truncating it."
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
