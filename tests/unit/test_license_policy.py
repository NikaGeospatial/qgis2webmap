"""Licence-cap detection and the two enforcement policies.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    ExportProject,
    FidelityStatus,
    GeometryKind,
    SourceKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.license_policy import (
    FREE_TIER_MAX_LAYERS,
    FREE_TIER_MAX_ROWS_PER_LAYER,
    FreeTierPolicy,
    LicensedPolicy,
    default_policy,
    detect_violations,
    report_verdict,
)

EMPTY_GEOJSON = {"type": "FeatureCollection", "features": []}


def make_layer(name: str, features: int = 1) -> ExportLayer:
    return ExportLayer(
        layer_id=f"{name}_id",
        name=name,
        geometry_kind=GeometryKind.POLYGON,
        source_kind=SourceKind.FILE,
        feature_count=features,
        geojson=EMPTY_GEOJSON,
    )


def make_project(layers: list[ExportLayer]) -> ExportProject:
    return ExportProject(title="Test", layers=tuple(layers))


class TestDetectViolations:
    def test_no_violations_within_limits(self) -> None:
        project = make_project(
            [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS)]
        )
        assert detect_violations(project) == ()

    def test_layer_count_violation_names_the_dropped_layers(self) -> None:
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 2)]
        violations = detect_violations(make_project(layers))
        assert len(violations) == 1
        assert violations[0].actual == FREE_TIER_MAX_LAYERS + 2
        # The user needs to know *which* layers vanish, not just that some do.
        assert "L5" in violations[0].detail
        assert "L6" in violations[0].detail

    def test_row_count_violation_is_per_layer(self) -> None:
        project = make_project(
            [
                make_layer("small", features=10),
                make_layer("huge", features=FREE_TIER_MAX_ROWS_PER_LAYER + 1),
            ]
        )
        violations = detect_violations(project)
        assert len(violations) == 1
        assert violations[0].layer_id == "huge_id"

    def test_exactly_at_the_limit_is_allowed(self) -> None:
        project = make_project(
            [make_layer("edge", features=FREE_TIER_MAX_ROWS_PER_LAYER)]
        )
        assert detect_violations(project) == ()

    def test_unreadable_layers_do_not_count(self) -> None:
        """Layers with no geojson never reach the manifest, so they cannot
        breach a runtime cap -- they are already a fidelity problem."""
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS)]
        unreadable = ExportLayer(
            layer_id="broken",
            name="broken",
            geometry_kind=GeometryKind.POLYGON,
            source_kind=SourceKind.FILE,
            geojson=None,
        )
        assert detect_violations(make_project([*layers, unreadable])) == ()


class TestFreeTierPolicy:
    def test_allows_a_compliant_project(self) -> None:
        verdict = FreeTierPolicy().evaluate(make_project([make_layer("a")]))
        assert verdict.allowed is True
        assert verdict.license_key is None

    def test_warns_but_allows_an_over_cap_project(self) -> None:
        """Blocking would make the plugin useless for an ordinary project."""
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 1)]
        verdict = FreeTierPolicy().evaluate(make_project(layers))
        assert verdict.allowed is True
        assert verdict.has_violations

    def test_over_cap_export_asks_for_runtime_validation(self) -> None:
        """`validate` mounts the runtime's error panel, so the recipient sees
        why a layer is missing instead of an unexplained gap."""
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 1)]
        verdict = FreeTierPolicy().evaluate(make_project(layers))
        assert verdict.needs_runtime_validation is True

    def test_clean_export_carries_no_diagnostic_panel(self) -> None:
        verdict = FreeTierPolicy().evaluate(make_project([make_layer("a")]))
        assert verdict.needs_runtime_validation is False


class TestLicensedPolicy:
    def test_allows_over_cap_and_carries_the_key(self) -> None:
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 3)]
        verdict = LicensedPolicy("om_live_test").evaluate(make_project(layers))
        assert verdict.allowed is True
        assert verdict.license_key == "om_live_test"

    def test_still_reports_violations_as_advisory(self) -> None:
        """The project may later be exported by someone without a key."""
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 1)]
        verdict = LicensedPolicy("om_live_test").evaluate(make_project(layers))
        assert verdict.allowed is True
        assert verdict.has_violations

    def test_snapshot_never_leaks_the_key(self) -> None:
        verdict = LicensedPolicy("om_live_secret").evaluate(make_project([]))
        assert verdict.snapshot()["hasLicenseKey"] is True
        assert "om_live_secret" not in str(verdict.snapshot())


class TestDefaultPolicy:
    def test_no_key_gives_the_blocking_policy(self) -> None:
        assert isinstance(default_policy(), FreeTierPolicy)

    def test_key_gives_the_licensed_policy(self) -> None:
        assert isinstance(default_policy("om_live_x"), LicensedPolicy)


class TestReportVerdict:
    def test_unlicensed_verdict_reports_unsupported_not_blocked(self) -> None:
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 1)]
        verdict = FreeTierPolicy().evaluate(make_project(layers))
        report = FidelityReportBuilder()
        report_verdict(verdict, report)
        assert not report.has_blockers
        assert len(report.by_status(FidelityStatus.UNSUPPORTED)) == 1

    def test_unlicensed_report_states_the_remedy(self) -> None:
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 1)]
        verdict = FreeTierPolicy().evaluate(make_project(layers))
        report = FidelityReportBuilder()
        report_verdict(verdict, report)
        detail = report.by_status(FidelityStatus.UNSUPPORTED)[0].detail
        assert "split the project" in detail

    def test_licensed_verdict_needs_no_diagnostic_panel(self) -> None:
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 1)]
        verdict = LicensedPolicy("om_live_x").evaluate(make_project(layers))
        assert verdict.needs_runtime_validation is False

    def test_licensed_verdict_records_advisories_not_blockers(self) -> None:
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 1)]
        verdict = LicensedPolicy("om_live_x").evaluate(make_project(layers))
        report = FidelityReportBuilder()
        report_verdict(verdict, report)
        assert not report.has_blockers
        assert len(report.by_status(FidelityStatus.APPROXIMATED)) == 1
