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

import re
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


class TestMeasureToolParameter:
    def test_it_is_offered_and_on_by_default(self, algorithm) -> None:
        """On by default like the dialog's switch, so a batch run and a dialog
        export of the same project produce the same map."""
        definition = algorithm.parameterDefinition("MEASURE")
        assert definition is not None
        assert definition.description() == "Measure tool"
        assert definition.defaultValue() is True

    @pytest.mark.parametrize("value", [True, False])
    def test_it_decides_whether_the_map_has_one(
        self, project, make_memory_layer, tmp_path, value
    ) -> None:
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        project.addMapLayer(
            make_memory_layer("pts", features=[("Ashford", [0.87, 51.15])])
        )
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()

        results = algorithm.processAlgorithm(
            {"OUTPUT": str(tmp_path / "map.html"), "MODE": 0, "MEASURE": value},
            context,
            qgis_core.QgsProcessingFeedback(),
        )

        html = Path(results["OUTPUT"]).read_text(encoding="utf-8")
        assert ('<om-widget type="measure"' in html) is value


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


class _RecordingFeedback(qgis_core.QgsProcessingFeedback):
    """Keeps what the algorithm tells the Processing log, by severity."""

    def __init__(self) -> None:
        super().__init__()
        self.warnings: list[str] = []

    def pushWarning(self, warning: str) -> None:  # noqa: N802 -- QGIS API
        self.warnings.append(warning)
        super().pushWarning(warning)


class TestBlockedItems:
    """A Blocked item - part of the project QGIS could not read - stops a batch
    export unless the run says in advance to export the rest anyway."""

    @staticmethod
    def _project_with_a_missing_layer(project, make_memory_layer):
        project.addMapLayer(
            make_memory_layer("pts", features=[("Ashford", [0.87, 51.15])])
        )
        ghost = qgis_core.QgsVectorLayer("/nonexistent/ghost.shp", "ghost", "ogr")
        assert not ghost.isValid()
        project.addMapLayer(ghost)
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)
        return context

    def test_the_parameter_exists_and_defaults_to_refusing(self, algorithm) -> None:
        definition = algorithm.parameterDefinition("EXPORT_BLOCKED")
        assert definition is not None
        assert definition.description() == "Export even if some items are blocked"
        assert definition.defaultValue() is False

    def test_by_default_a_blocked_item_refuses_and_lists_the_problem(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        context = self._project_with_a_missing_layer(project, make_memory_layer)
        destination = tmp_path / "map.html"
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()

        with pytest.raises(qgis_core.QgsProcessingException) as excinfo:
            algorithm.processAlgorithm(
                {"OUTPUT": str(destination), "MODE": 0, "TITLE": ""},
                context,
                qgis_core.QgsProcessingFeedback(),
            )
        message = str(excinfo.value)
        assert "could not be read" in message
        assert "could not load this layer" in message
        assert "Export even if some items are blocked" in message
        assert not destination.exists()

    def test_when_asked_it_exports_the_rest_and_warns_about_each_blocked_item(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        context = self._project_with_a_missing_layer(project, make_memory_layer)
        destination = tmp_path / "map.html"
        feedback = _RecordingFeedback()
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()

        results = algorithm.processAlgorithm(
            {
                "OUTPUT": str(destination),
                "MODE": 0,
                "TITLE": "",
                "EXPORT_BLOCKED": True,
            },
            context,
            feedback,
        )

        assert Path(results["OUTPUT"]).is_file()
        missing = [w for w in feedback.warnings if w.startswith("Missing from the map")]
        assert len(missing) == 1
        assert "ghost" in missing[0]
        assert any("Exporting anyway" in w for w in feedback.warnings)


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


def _labelled_z_layer(kind: str, name: str, wkts: list[str]):
    """A labelled `kind`Z memory layer: what Z-on digitising, a DEM drape or a
    CityGML import gives QGIS."""
    layer = qgis_core.QgsVectorLayer(
        f"{kind}Z?crs=EPSG:4326&field=name:string", name, "memory"
    )
    features = []
    for index, wkt in enumerate(wkts):
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setAttribute(0, f"{name} {index}")
        feature.setGeometry(qgis_core.QgsGeometry.fromWkt(wkt))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = "name"
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


@pytest.mark.parametrize("mode", [0, 1, 2], ids=["standalone", "zip", "folder"])
def test_labelled_3d_layers_export_end_to_end(project, tmp_path, mode) -> None:
    """The 0.1.4 'too many values to unpack' crash, through the whole export.

    A labelled PolygonZ layer aborted the export at the label step. Run here
    through the real reader and writer, in every output type, alongside Z
    lines and a dense Z point layer - which also reaches this release's
    declutter and clustering, both of which read coordinates.
    """
    if discover_runtime_dir() is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")

    project.addMapLayer(
        _labelled_z_layer(
            "Polygon",
            "Peaks",
            [
                "POLYGON Z ((86.9 27.9 8849,87.0 27.9 8849,87.0 28.0 8849,"
                "86.9 28.0 8849,86.9 27.9 8849))",
                "MULTIPOLYGON Z (((86.5 27.5 1,86.6 27.5 1,86.6 27.6 1,"
                "86.5 27.6 1,86.5 27.5 1)))",
            ],
        )
    )
    project.addMapLayer(
        _labelled_z_layer(
            "LineString", "Trails", ["LINESTRING Z (86.9 27.9 5000,87.0 28.0 6000)"]
        )
    )
    project.addMapLayer(
        _labelled_z_layer(
            "Point",
            "Camps",
            [f"POINT Z ({86.9 + i * 0.0001} 27.9 {5000 + i})" for i in range(150)],
        )
    )

    destination = tmp_path / ("map.html", "map.zip", "map")[mode]
    context = qgis_core.QgsProcessingContext()
    context.setProject(project)
    feedback = _RecordingFeedback()
    algorithm = ExportProjectAlgorithm()
    algorithm.initAlgorithm()

    results = algorithm.processAlgorithm(
        {"OUTPUT": str(destination), "MODE": mode, "TITLE": "Everest"},
        context,
        feedback,
    )

    written = Path(results["OUTPUT"])
    assert written.exists()
    logged = " ".join(feedback.warnings)
    assert "too many values" not in logged
    if mode == 0:
        html = written.read_text(encoding="utf-8")
        # Each layer's labels made it, and the dense Z points clustered.
        assert html.count('type="TextLayer"') == 3
        assert 'cluster="true"' in html


class TestProgressAndCancel:
    """Processing's feedback drives the export: progress up, Cancel honoured
    until the artifact is being put in place, and not after."""

    @staticmethod
    def _setup(project, make_memory_layer, tmp_path):
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        for name in ("one", "two", "three"):
            project.addMapLayer(
                make_memory_layer(name, features=[("a", [0.87, 51.15])])
            )
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()
        return algorithm, context, tmp_path / "map.html"

    @staticmethod
    def _feedback(cancel_at=None):
        """A real feedback that records progress and cancels itself at a value."""
        feedback = qgis_core.QgsProcessingFeedback()
        reported: list[float] = []

        def on_progress(value: float) -> None:
            reported.append(value)
            if cancel_at is not None and value >= cancel_at:
                feedback.cancel()

        feedback.progressChanged.connect(on_progress)
        return feedback, reported

    def test_progress_only_moves_forward_and_ends_complete(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        algorithm, context, destination = self._setup(
            project, make_memory_layer, tmp_path
        )
        feedback, reported = self._feedback()

        results = algorithm.processAlgorithm(
            {"OUTPUT": str(destination), "MODE": 0, "TITLE": "t"}, context, feedback
        )

        assert Path(results["OUTPUT"]).is_file()
        assert reported == sorted(reported), reported
        assert len({round(v) for v in reported}) >= 4, reported
        assert reported[-1] == 100

    def test_a_cancel_while_reading_writes_nothing(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        algorithm, context, destination = self._setup(
            project, make_memory_layer, tmp_path
        )
        destination.write_bytes(b"ORIGINAL VALID MAP")
        # Three layers report 0, 23 and 46: stop as the second one is reached.
        feedback, reported = self._feedback(cancel_at=20)

        results = algorithm.processAlgorithm(
            {"OUTPUT": str(destination), "MODE": 0, "TITLE": "t"}, context, feedback
        )

        assert results == {}
        assert max(reported) < 50, "kept going after the cancel"
        assert destination.read_bytes() == b"ORIGINAL VALID MAP"
        assert [p.name for p in tmp_path.iterdir()] == ["map.html"]

    def test_a_cancel_at_the_commit_still_reports_success(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        from nika_onlymap_exporter.processing.export_project import COMMIT_AT

        algorithm, context, destination = self._setup(
            project, make_memory_layer, tmp_path
        )
        destination.write_bytes(b"ORIGINAL VALID MAP")
        # Cancel lands the instant the commit is announced - after the last check.
        feedback, _reported = self._feedback(cancel_at=COMMIT_AT)

        results = algorithm.processAlgorithm(
            {"OUTPUT": str(destination), "MODE": 0, "TITLE": "t"}, context, feedback
        )

        assert feedback.isCanceled()
        assert results == {"OUTPUT": str(destination)}
        assert destination.read_bytes() != b"ORIGINAL VALID MAP"
        assert b"om-map" in destination.read_bytes()

    def test_the_algorithm_runs_from_its_snapshot_with_the_project_cleared(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        """`prepareAlgorithm` reads the project on the main thread; the worker's
        `processAlgorithm` then needs nothing the project owns."""
        import threading

        algorithm, context, destination = self._setup(
            project, make_memory_layer, tmp_path
        )
        parameters = {"OUTPUT": str(destination), "MODE": 0, "TITLE": "From snapshot"}
        # No Python slot on it: a signal emitted from the worker would queue to
        # the main thread and outlive the test.
        feedback = qgis_core.QgsProcessingFeedback()

        assert algorithm.prepareAlgorithm(parameters, context, feedback)
        project.removeAllMapLayers()

        results: list = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                results.append(
                    algorithm.processAlgorithm(parameters, context, feedback)
                )
            except BaseException as exc:  # reported on the test thread
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(120)

        assert not errors, errors
        html = Path(results[0]["OUTPUT"]).read_text(encoding="utf-8")
        assert "From snapshot" in html
        for name in ("one", "two", "three"):
            assert name in html


class TestSavedDialogChoices:
    """A run exports what the dialog would for the same saved project: its
    layers, popups and Appearance, not every layer with the defaults."""

    @staticmethod
    def _saved_project(project, make_memory_layer):
        from nika_onlymap_exporter.core.settings import load_state, save_state

        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        layers = {
            name: make_memory_layer(name, features=[(f"{name} 1", [0.87, 51.15])])
            for name in ("Kept", "Dropped", "Quiet")
        }
        for layer in layers.values():
            project.addMapLayer(layer)
        state = load_state(project)
        state.for_layer(layers["Dropped"].id()).include = False
        state.for_layer(layers["Quiet"].id()).popup = False
        state.show_legend = False
        state.widget_background = "#40e0d0"
        state.measure_units = "nautical"
        state.map_name = "Saved name"
        save_state(project, state)
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)
        return context

    @staticmethod
    def _run(context, parameters) -> str:
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()
        results = algorithm.processAlgorithm(
            parameters, context, qgis_core.QgsProcessingFeedback()
        )
        return Path(results["OUTPUT"]).read_text(encoding="utf-8")

    @staticmethod
    def _dialog_export(qgis_app, destination: Path) -> str:
        """The dialog's own read and writer, as its Export button runs them."""
        from nika_onlymap_exporter.core.export_ir import OutputMode
        from nika_onlymap_exporter.packaging.artifact_builder import build_artifact
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        dialog = MainDialog(FakeIface(), None)
        dialog._pending_canvas_extent = None
        dialog._pending_map_time = None
        dialog._pending_license_key = dialog._license_key()
        export, _report = dialog._read_current_project()
        _result, outcome = build_artifact(
            export,
            destination,
            mode=OutputMode.STANDALONE_HTML,
            writer=dialog._writer(),
        )
        dialog.close()
        return outcome.path.read_text(encoding="utf-8")

    def test_it_writes_the_map_the_dialog_writes(
        self, project, make_memory_layer, tmp_path, qgis_app
    ) -> None:
        context = self._saved_project(project, make_memory_layer)

        batch = self._run(context, {"OUTPUT": str(tmp_path / "batch.html")})
        dialog = self._dialog_export(qgis_app, tmp_path / "dialog.html")

        # The same file, but for the minute each was generated in.
        assert _undated(batch) == _undated(dialog)
        # And what that map is: the saved choices, not the defaults.
        assert 'label="Kept"' in batch
        assert 'label="Dropped"' not in batch
        assert '<div class="om-popup-title">Kept</div>' in batch
        assert '<div class="om-popup-title">Quiet</div>' not in batch
        assert '<om-widget type="legend"' not in batch
        assert "--om-widget-bg: #40e0d0" in batch
        assert '<om-widget type="measure" units="nautical" ' in batch
        assert "<title>Saved name</title>" in batch

    def test_a_parameter_that_is_passed_wins_over_the_saved_choice(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        context = self._saved_project(project, make_memory_layer)

        html = self._run(
            context,
            {"OUTPUT": str(tmp_path / "map.html"), "LEGEND": True, "TITLE": "Given"},
        )

        assert '<om-widget type="legend"' in html
        assert "<title>Given</title>" in html
        # What was not passed still comes from the project.
        assert 'label="Dropped"' not in html
        assert "--om-widget-bg: #40e0d0" in html

    def test_a_project_never_opened_in_the_dialog_exports_every_layer(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        for name in ("First", "Second"):
            project.addMapLayer(
                make_memory_layer(name, features=[("a", [0.87, 51.15])])
            )
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)

        html = self._run(context, {"OUTPUT": str(tmp_path / "map.html")})

        assert 'label="First"' in html
        assert 'label="Second"' in html
        assert '<div class="om-popup-title">Second</div>' in html
        assert '<om-widget type="legend"' in html


def _undated(html: str) -> str:
    return re.sub(r"(Generated by .*) on [^\n]*", r"\1", html)


class TestAClipToTheView:
    """A run has no QGIS view, and a clip to it is what keeps the features
    outside the view off the map: such a run refuses, never exports them."""

    def test_a_saved_clip_refuses_and_writes_nothing(
        self, project, make_memory_layer, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.settings import load_state, save_state

        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        project.addMapLayer(
            make_memory_layer("Sites", features=[("private", [0.87, 51.15])])
        )
        state = load_state(project)
        state.clip_to_extent = True
        save_state(project, state)
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()
        destination = tmp_path / "map.html"

        with pytest.raises(qgis_core.QgsProcessingException) as raised:
            algorithm.prepareAlgorithm(
                {"OUTPUT": str(destination)}, context, qgis_core.QgsProcessingFeedback()
            )
        assert "only the features in the QGIS view" in str(raised.value)
        assert "Export only the features in this view" in str(raised.value)
        with pytest.raises(qgis_core.QgsProcessingException):
            algorithm.processAlgorithm(
                {"OUTPUT": str(destination)}, context, qgis_core.QgsProcessingFeedback()
            )
        assert not destination.exists()
