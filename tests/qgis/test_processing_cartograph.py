"""The cartograph Processing algorithm: a thin adapter over the same export
the dialog's autosave runs.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import build_survey_layout

from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir
from nika_onlymap_exporter.processing.export_layout import (
    FRAME_MODES,
    ExportLayoutAlgorithm,
)
from nika_onlymap_exporter.processing.provider import Qgis2WebMapProvider

qgis_core = pytest.importorskip("qgis.core")

needs_runtime = pytest.mark.skipif(
    discover_runtime_dir() is None, reason="needs ONLYMAP_RUNTIME_DIR"
)


@pytest.fixture
def survey(project, tmp_path):
    built = build_survey_layout(project, tmp_path)
    project.layoutManager().addLayout(built.layout)
    return built


def _run(project, parameters):
    from qgis.core import (
        QgsProcessingContext,
        QgsProcessingFeedback,
    )

    algorithm = ExportLayoutAlgorithm()
    algorithm.initAlgorithm()
    context = QgsProcessingContext()
    context.setProject(project)
    messages: list[str] = []

    class Feedback(QgsProcessingFeedback):
        def pushInfo(self, info):  # noqa: N802 - QGIS API
            messages.append(info)

        def pushWarning(self, warning):  # noqa: N802 - QGIS API
            messages.append(warning)

    ok_parameters = algorithm.checkParameterValues(parameters, context)
    assert ok_parameters[0], ok_parameters[1]
    result = algorithm.processAlgorithm(parameters, context, Feedback())
    return result, messages


class TestRegistration:
    def test_the_provider_offers_the_cartograph_algorithm(self, qgis_app) -> None:
        registry = qgis_core.QgsApplication.processingRegistry()
        provider = Qgis2WebMapProvider()
        registry.addProvider(provider)
        try:
            names = [a.name() for a in provider.algorithms()]
            assert "exportcartograph" in names
        finally:
            registry.removeProvider(provider)

    def test_it_never_runs_on_a_worker_thread(self, qgis_app) -> None:
        """It renders the layout, which is GUI-thread only."""
        from qgis.core import Qgis

        flags = ExportLayoutAlgorithm().flags()
        assert flags & Qgis.ProcessingAlgorithmFlag.NoThreading

    def test_the_frame_mode_order_is_stable(self) -> None:
        assert [key for key, _ in FRAME_MODES] == ["auto", "live", "static"]


@needs_runtime
class TestExport:
    def test_a_single_page_layout_exports(self, survey, tmp_path) -> None:
        survey.layout.pageCollection().deletePage(1)
        destination = tmp_path / "out" / "survey.html"
        result, messages = _run(
            survey.project,
            {
                "LAYOUT": "survey-utm",
                "FRAMES": 0,
                "DPI": 100,
                "OUTPUT": str(destination),
            },
        )
        assert result["OUTPUT"] == str(destination)
        html = destination.read_text(encoding="utf-8")
        assert html.count('mode="static"') == 2
        assert any("Wrote" in line for line in messages)
        assert list(destination.parent.glob("*.tmp")) == []

    def test_a_blocked_item_stops_the_run_unless_ticked(
        self, project, tmp_path
    ) -> None:
        """An atlas on a static map cannot page: Blocked, so a batch run with
        nobody to ask stops unless told to go ahead."""
        from atlas_fixtures import build_atlas_layout
        from qgis.core import QgsProcessingException

        built = build_atlas_layout(project)
        project.layoutManager().addLayout(built.layout)
        destination = tmp_path / "atlas.html"
        parameters = {
            "LAYOUT": built.layout.name(),
            "FRAMES": 2,  # all static
            "DPI": 72,
            "OUTPUT": str(destination),
        }
        with pytest.raises(QgsProcessingException, match="Atlas"):
            _run(project, parameters)
        assert not destination.exists()

        _run(project, {**parameters, "EXPORT_BLOCKED": True})
        assert destination.exists()

    def test_a_second_page_is_logged_and_left_out(self, survey, tmp_path) -> None:
        destination = tmp_path / "survey.html"
        _, messages = _run(
            survey.project,
            {"LAYOUT": "survey-utm", "DPI": 72, "OUTPUT": str(destination)},
        )
        assert destination.exists()
        assert any("Page count" in line for line in messages)

    def test_all_live_overrides_the_crs_default(self, survey, tmp_path) -> None:
        survey.layout.pageCollection().deletePage(1)
        destination = tmp_path / "survey.html"
        _run(
            survey.project,
            {"LAYOUT": "survey-utm", "FRAMES": 1, "OUTPUT": str(destination)},
        )
        html = destination.read_text(encoding="utf-8")
        assert 'mode="static"' not in html
        assert html.count("<om-map") == 2

    def test_a_missing_suffix_still_writes_html(self, survey, tmp_path) -> None:
        survey.layout.pageCollection().deletePage(1)
        result, _ = _run(
            survey.project,
            {"LAYOUT": "survey-utm", "DPI": 100, "OUTPUT": str(tmp_path / "plain")},
        )
        assert result["OUTPUT"].endswith("plain.html")
