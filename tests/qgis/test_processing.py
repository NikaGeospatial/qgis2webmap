"""The Processing provider and its one algorithm.

Issue #29 wants export reachable outside the dialog, for batch and modeller
workflows. The thing worth testing is not that Processing works -- it is that
this algorithm stays a thin adapter over the same reader, writer and exporters
the dialog uses, because a second export implementation drifting from the first
is exactly what went wrong in qgis2web.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir
from nika_onlymap_exporter.processing.export_project import ExportProjectAlgorithm
from nika_onlymap_exporter.processing.provider import Qgis2WebMapProvider

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def algorithm(qgis_app):
    instance = ExportProjectAlgorithm()
    instance.initAlgorithm()
    return instance


class TestProvider:
    def test_it_registers_and_unregisters(self, qgis_app) -> None:
        """Unload must be complete - CONTRIBUTING rule 2."""
        registry = qgis_core.QgsApplication.processingRegistry()
        provider = Qgis2WebMapProvider()

        registry.addProvider(provider)
        assert registry.providerById("qgis2webmap") is not None

        registry.removeProvider(provider)
        assert registry.providerById("qgis2webmap") is None

    def test_it_exposes_the_export_algorithm(self, qgis_app) -> None:
        registry = qgis_core.QgsApplication.processingRegistry()
        provider = Qgis2WebMapProvider()
        registry.addProvider(provider)
        try:
            names = [a.name() for a in provider.algorithms()]
            assert "exportwebmap" in names
        finally:
            registry.removeProvider(provider)


class TestAlgorithmShape:
    def test_it_declares_the_parameters_a_batch_run_needs(self, algorithm) -> None:
        names = {p.name() for p in algorithm.parameterDefinitions()}
        assert {"OUTPUT", "MODE", "TITLE"} <= names

    def test_the_mode_order_is_stable(self, algorithm) -> None:
        """A stored model records the enum *index*, so reordering rewrites it."""
        from nika_onlymap_exporter.core.export_ir import OutputMode
        from nika_onlymap_exporter.processing.export_project import OUTPUT_MODES

        assert [mode for mode, _ in OUTPUT_MODES] == [
            OutputMode.STANDALONE_HTML,
            OutputMode.SHARE_ZIP,
            OutputMode.FOLDER,
        ]

    def test_the_help_points_at_the_dialog_for_the_fidelity_report(
        self, algorithm
    ) -> None:
        """Processing has no Fidelity tab; the user must be told where it is."""
        assert "Fidelity" in algorithm.shortHelpString() or (
            "fidelity" in algorithm.shortHelpString()
        )


class TestAlgorithmRuns:
    def test_it_exports_through_the_same_writer(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")

        project.addMapLayer(
            make_memory_layer("pts", features=[("Ashford", [0.87, 51.15])])
        )

        destination = tmp_path / "map.html"
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)

        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()
        results = algorithm.processAlgorithm(
            {"OUTPUT": str(destination), "MODE": 0, "TITLE": "From Processing"},
            context,
            qgis_core.QgsProcessingFeedback(),
        )

        written = Path(results["OUTPUT"])
        assert written.is_file()
        html = written.read_text(encoding="utf-8")
        # The same artifact the dialog would produce: title, credit component.
        assert "From Processing" in html
        assert 'class="om-credit"' in html

    def test_an_empty_project_fails_rather_than_writing_a_broken_map(
        self, project, tmp_path
    ) -> None:
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)

        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()

        with pytest.raises(qgis_core.QgsProcessingException, match="nothing to export"):
            algorithm.processAlgorithm(
                {"OUTPUT": str(tmp_path / "map.html"), "MODE": 0, "TITLE": ""},
                context,
                qgis_core.QgsProcessingFeedback(),
            )


class TestRuntimePreflight:
    """The licence gate has to fire before the work, not after it.

    A team member's first Processing run read the project, translated 28
    symbology classes and logged the whole fidelity report - then failed after
    50 seconds because the runtime licence had not been accepted. Every second
    of that was knowable up front, and a refusal at the end of a long job reads
    as a crash where the same message at the start reads as a prompt.
    """

    def test_it_refuses_before_reading_the_project(self, qgis_app, monkeypatch) -> None:
        from nika_onlymap_exporter.packaging.runtime_manager import (
            RuntimeNotAcceptedError,
        )
        from nika_onlymap_exporter.processing import export_project as module

        class Unaccepted:
            def preflight(self) -> None:
                raise RuntimeNotAcceptedError("licence not accepted")

            def load(self):  # pragma: no cover - must never be reached
                raise AssertionError("load() ran despite the preflight refusing")

        read_calls: list[object] = []
        monkeypatch.setattr(
            module.runtime_manager if hasattr(module, "runtime_manager") else module,
            "read_project",
            lambda *a, **k: read_calls.append(1),
            raising=False,
        )
        monkeypatch.setattr(
            "nika_onlymap_exporter.packaging.runtime_manager.default_provider",
            lambda *a, **k: Unaccepted(),
        )

        algorithm = module.ExportProjectAlgorithm()
        with pytest.raises(module.QgsProcessingException) as excinfo:
            algorithm._check_runtime_first()

        assert "licence not accepted" in str(excinfo.value)
        # The guidance is the actionable half; without it the message is a dead end.
        assert "QGIS2WebMap by NIKA" in str(excinfo.value)
        assert read_calls == [], "the project was read despite the runtime refusing"

    def test_an_available_runtime_passes_quietly(self, qgis_app, monkeypatch) -> None:
        from nika_onlymap_exporter.processing import export_project as module

        class Ready:
            def preflight(self) -> None:
                return None

        monkeypatch.setattr(
            "nika_onlymap_exporter.packaging.runtime_manager.default_provider",
            lambda *a, **k: Ready(),
        )
        module.ExportProjectAlgorithm()._check_runtime_first()

    def test_a_local_runtime_needs_no_acceptance(self, qgis_app, tmp_path) -> None:
        """`LocalRuntime` answers `preflight()` too, so callers need no
        `hasattr` dance - and a vendored or ONLYMAP_RUNTIME_DIR copy carries no
        licence gate to trip over."""
        from nika_onlymap_exporter.packaging.runtime_manager import LocalRuntime

        LocalRuntime(tmp_path).preflight()
