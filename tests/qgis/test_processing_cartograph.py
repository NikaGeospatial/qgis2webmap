"""The cartograph Processing algorithm: a thin adapter over the same export
the dialog's autosave runs.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

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
        # Automatic: both UTM maps of vectors are live, in their own CRS.
        assert 'mode="static"' not in html
        assert html.count("<om-map") == 2
        assert len(re.findall(r'<om-map\b[^>]*crs="EPSG:32648"', html)) == 2
        assert any("Wrote" in line for line in messages)
        assert list(destination.parent.glob("*.tmp")) == []

    def test_automatic_keeps_a_rotated_utm_map_static(self, survey, tmp_path) -> None:
        survey.layout.pageCollection().deletePage(1)
        survey.main_map.setMapRotation(15)
        destination = tmp_path / "survey.html"
        _run(
            survey.project,
            {
                "LAYOUT": "survey-utm",
                "FRAMES": 0,
                "DPI": 72,
                "OUTPUT": str(destination),
            },
        )
        html = destination.read_text(encoding="utf-8")
        assert html.count('mode="static"') == 1
        assert html.count("<om-map") == 1

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

    def test_all_live_overrides_the_default(self, survey, tmp_path) -> None:
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


@needs_runtime
class TestSavedDialogChoices:
    """A run builds what the dialog would for the layout: the choices the
    dialog saved in the project, under any parameter that is passed."""

    @staticmethod
    def _save(survey, *, static_main=False, attribution="", popups=True, prints=None):
        from nika_onlymap_exporter.core.settings import (
            FrameChoice,
            load_state,
            save_state,
        )

        state = load_state(survey.project)
        name = survey.layout.name()
        if static_main:
            state.frame_choices(name)[survey.main_map.uuid()] = FrameChoice(
                mode="static", dpi=72
            )
        state.page_choice(name).attribution = attribution
        for layer_id in survey.project.mapLayers():
            state.for_layer(layer_id).popup = popups
        if prints is not None:
            state.cartograph_print[name] = prints.to_json()
        save_state(survey.project, state)

    def test_a_frame_saved_as_an_image_stays_an_image(self, survey, tmp_path) -> None:
        survey.layout.pageCollection().deletePage(1)
        self._save(survey, static_main=True)
        destination = tmp_path / "survey.html"

        _run(survey.project, {"LAYOUT": "survey-utm", "OUTPUT": str(destination)})

        html = destination.read_text(encoding="utf-8")
        assert html.count('mode="static"') == 1
        assert html.count("<om-map") == 1

    def test_frames_passed_replace_the_saved_choice(self, survey, tmp_path) -> None:
        survey.layout.pageCollection().deletePage(1)
        self._save(survey, static_main=True)
        destination = tmp_path / "survey.html"

        _run(
            survey.project,
            {"LAYOUT": "survey-utm", "FRAMES": 1, "OUTPUT": str(destination)},
        )

        html = destination.read_text(encoding="utf-8")
        assert 'mode="static"' not in html
        assert html.count("<om-map") == 2

    def test_the_saved_page_layers_and_print_settings_reach_the_page(
        self, survey, tmp_path
    ) -> None:
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType

        from nika_onlymap_exporter.core.print_settings import PrintSettings

        survey.layout.pageCollection().deletePage(1)
        parcels = survey.parcels
        parcels.dataProvider().addAttributes(
            [QgsField("owner", QMetaType.Type.QString)]
        )
        parcels.updateFields()
        feature = next(parcels.getFeatures())
        parcels.dataProvider().changeAttributeValues({feature.id(): {0: "Ms Tan"}})
        default = tmp_path / "default.html"
        _run(survey.project, {"LAYOUT": "survey-utm", "OUTPUT": str(default)})
        before = default.read_text(encoding="utf-8")
        assert "Ms Tan" in before
        assert 'attribution="Saved credit"' not in before

        self._save(
            survey,
            attribution="Saved credit",
            popups=False,
            prints=PrintSettings(bleed="5mm", dpi=150),
        )
        destination = tmp_path / "survey.html"
        _run(survey.project, {"LAYOUT": "survey-utm", "OUTPUT": str(destination)})

        html = destination.read_text(encoding="utf-8")
        assert 'attribution="Saved credit"' in html
        assert "Ms Tan" not in html
        assert 'bleed="5"' in html
        assert 'dpi="150"' in html

    def test_an_image_frame_is_drawn_at_least_at_the_page_resolution(
        self, survey, tmp_path
    ) -> None:
        """As the dialog's Save draws it: never below the resolution the page
        is saved at - the saved Print settings', else 300 dpi - however low
        the frame's own choice, or the picture is enlarged there and goes
        soft."""
        import base64

        from qgis.PyQt.QtGui import QImage

        from nika_onlymap_exporter.core.print_settings import PrintSettings

        survey.layout.pageCollection().deletePage(1)
        width_mm = survey.main_map.sizeWithUnits().width()

        def drawn(name: str) -> tuple[str, int]:
            destination = tmp_path / f"{name}.html"
            _run(
                survey.project,
                {
                    "LAYOUT": "survey-utm",
                    "FRAMES": 2,  # all image
                    "DPI": 72,
                    "OUTPUT": str(destination),
                },
            )
            html = destination.read_text(encoding="utf-8")
            frame = re.search(
                r'<om-frame\b[^>]*mode="static"[^>]*>\s*'
                r'<img src="data:image/png;base64,([^"]+)"',
                html,
            )
            assert frame is not None
            dpi = re.search(r'\bdpi="([^"]+)"', frame.group(0))
            assert dpi is not None
            image = QImage.fromData(base64.b64decode(frame.group(1)), "PNG")
            return dpi.group(1), image.width()

        # No saved Print settings: the page is saved at 300 dpi.
        assert drawn("default") == ("300", round(width_mm / 25.4 * 300))

        self._save(survey, prints=PrintSettings(dpi=150))
        assert drawn("saved") == ("150", round(width_mm / 25.4 * 150))
