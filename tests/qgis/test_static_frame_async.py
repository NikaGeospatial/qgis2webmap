"""A static map frame is drawn without freezing the dialog, and is the same
picture it always was.

Autosave rebuilds the cartograph about 600 ms after any edit, and a static
frame whose content changed (a map content drag) has to be drawn again - at a
dense map's resolution that took several seconds, all on the GUI thread, so the
preview could not redraw and nothing could be dragged. The frame is now drawn
by QGIS's own render job. What these tests hold on to is the RESULT: the saved
cartograph carries exactly the pixels a synchronous render makes, and while the
autosave runs the event loop keeps turning.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import os
import random
import re
import time

import pytest
from cartograph_fixtures import build_survey_layout

qgis_core = pytest.importorskip("qgis.core")

from test_cartograph_dialog import (  # noqa: E402, F401
    _choose_cartograph,
    _dialog,
    _wait_for,
    fake_cartograph_export,
)
from test_cartograph_freshness import opened  # noqa: E402, F401

from nika_onlymap_exporter.exporters.frame_raster import (  # noqa: E402
    AsyncFrameRender,
    render_static_frame,
)

#: The longest the event loop may go without turning while an autosave draws a
#: heavy static frame. The same frame drawn on the GUI thread takes several
#: times this (`HEAVY_AT_LEAST_S`), so staying under it means the drawing is
#: off the GUI thread, not that the machine is quick.
LOOP_STALL_BOUND_S = 1.0
HEAVY_AT_LEAST_S = 2.5


def _decoded(png_bytes: bytes):
    from qgis.PyQt.QtGui import QImage

    image = QImage.fromData(png_bytes)
    assert not image.isNull()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _drawn_async(qgis_app, layout, map_item, dpi):
    """The picture and corners `AsyncFrameRender` makes, waiting (bounded) for
    its job, with the event loop running."""
    drawing = AsyncFrameRender(layout, map_item, dpi)
    assert AsyncFrameRender.supports(layout, map_item)
    drawing.start()
    assert _wait_for(qgis_app, lambda: drawing.done, timeout=60.0), "never finished"
    from qgis.PyQt.QtGui import QImage

    image = drawing.take_image()
    return image.convertToFormat(QImage.Format.Format_ARGB32), drawing


# ---- The picture is the one a synchronous render makes ---------------------


class TestAsyncPictureEqualsTheSynchronousOne:
    """Pixel for pixel, whatever the item looks like."""

    @pytest.fixture
    def survey(self, project, tmp_path):
        return build_survey_layout(project, tmp_path)

    def _same(self, qgis_app, survey, dpi=150) -> None:
        layout, item = survey.layout, survey.main_map
        expected = render_static_frame(layout, item, dpi=dpi)
        image, drawing = _drawn_async(qgis_app, layout, item, dpi)
        reference = _decoded(expected.png_bytes)
        assert image.size() == reference.size()
        assert image == reference
        assert drawing.corners == expected.corners
        assert drawing.crs_authid == expected.crs_authid
        assert drawing.crs_def == expected.crs_def

    def test_a_plain_frame(self, qgis_app, survey) -> None:
        self._same(qgis_app, survey)

    def test_without_background_and_frame(self, qgis_app, survey) -> None:
        survey.main_map.setBackgroundEnabled(False)
        survey.main_map.setFrameEnabled(False)
        self._same(qgis_app, survey)

    def test_with_a_thick_coloured_frame_and_background(self, qgis_app, survey) -> None:
        from qgis.core import QgsLayoutMeasurement
        from qgis.PyQt.QtGui import QColor

        survey.main_map.setBackgroundEnabled(True)
        survey.main_map.setBackgroundColor(QColor("#e8efe3"))
        survey.main_map.setFrameEnabled(True)
        survey.main_map.setFrameStrokeColor(QColor("#aa3300"))
        survey.main_map.setFrameStrokeWidth(QgsLayoutMeasurement(1.7))
        self._same(qgis_app, survey)

    def test_with_a_blend_mode_layer(self, qgis_app, survey) -> None:
        """A layer that has to meet the background in one flat picture."""
        from qgis.PyQt.QtGui import QPainter

        survey.parcels.setBlendMode(QPainter.CompositionMode.CompositionMode_Multiply)
        assert survey.main_map.containsAdvancedEffects()
        self._same(qgis_app, survey)

    def test_with_a_rotated_map_content(self, qgis_app, survey) -> None:
        survey.main_map.setMapRotation(25)
        survey.main_map.refresh()
        self._same(qgis_app, survey)

    def test_with_a_rotated_item(self, qgis_app, survey) -> None:
        survey.main_map.setItemRotation(18)
        self._same(qgis_app, survey)

    def test_with_a_translucent_item(self, qgis_app, survey) -> None:
        survey.main_map.setItemOpacity(0.4)
        self._same(qgis_app, survey)

    def test_at_a_high_resolution(self, qgis_app, survey) -> None:
        self._same(qgis_app, survey, dpi=300)

    def test_with_dense_dashed_lines(self, qgis_app, project) -> None:
        """Many-vertex dashed lines draw differently when the renderer is told
        it is previewing (simplified geometry, other dash phases) than when a
        layout is exported; the picture has to be the exported one."""
        layout, item = _heavy_layout(project)
        expected = render_static_frame(layout, item, dpi=120)
        image, _drawing = _drawn_async(qgis_app, layout, item, 120)
        assert image == _decoded(expected.png_bytes)

    def test_the_layout_is_left_as_it_was(self, qgis_app, survey) -> None:
        """Every other item visible again, the item's own state restored,
        before the picture is even finished."""
        layout, item = survey.layout, survey.main_map
        visible = {
            i.uuid(): i.isVisible() for i in layout.items() if hasattr(i, "uuid")
        }
        item.setItemRotation(10)
        opacity = item.itemOpacity()
        drawing = AsyncFrameRender(layout, item, 100)
        drawing.start()
        try:
            assert item.itemRotation() == pytest.approx(10)
            assert item.itemOpacity() == opacity
            assert visible == {
                i.uuid(): i.isVisible() for i in layout.items() if hasattr(i, "uuid")
            }
        finally:
            assert _wait_for(qgis_app, lambda: drawing.done, timeout=60.0)


# ---- A heavy static frame, through the real dialog -------------------------


def _heavy_layout(project):
    """A projected map of many long dashed lines: slow to draw at a high
    resolution, and static (the frame choice below)."""
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsGeometry,
        QgsLayoutItemMap,
        QgsLineSymbol,
        QgsPointXY,
        QgsPrintLayout,
        QgsRectangle,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QRectF

    crs = QgsCoordinateReferenceSystem("EPSG:32648")
    layer = QgsVectorLayer("LineString?crs=EPSG:32648", "tracks", "memory")
    rng = random.Random(7)
    features = []
    for _ in range(2500):
        x, y = rng.uniform(360000, 372000), rng.uniform(140000, 152000)
        points = []
        for _step in range(120):
            x += rng.uniform(-40, 40)
            y += rng.uniform(-40, 40)
            points.append(QgsPointXY(x, y))
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolylineXY(points))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    layer.renderer().setSymbol(
        QgsLineSymbol.createSimple(
            {
                "line_color": "#7a3b1d",
                "line_width": "0.3",
                "use_custom_dash": "1",
                "customdash": "3;2",
            }
        )
    )
    project.addMapLayer(layer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Heavy")
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptSetSceneRect(QRectF(10, 20, 270, 170))
    map_item.setCrs(crs)
    map_item.setLayers([layer])
    map_item.setExtent(QgsRectangle(361000, 141000, 369000, 148500))
    map_item.setId("Overview")
    layout.addLayoutItem(map_item)
    project.layoutManager().addLayout(layout)
    return layout, map_item


def _dpi_that_is_heavy(layout, map_item) -> int:
    """The lowest resolution at which drawing the frame on the GUI thread takes
    `HEAVY_AT_LEAST_S`, so the stall bound below proves something."""
    for dpi in (300, 450, 600, 800, 1000):
        started = time.perf_counter()
        render_static_frame(layout, map_item, dpi=dpi)
        if time.perf_counter() - started >= HEAVY_AT_LEAST_S:
            return dpi
    pytest.fail("the heavy fixture is not heavy enough to prove anything")


def _saved_image(html: str):
    """The first embedded PNG of a saved cartograph, decoded."""
    pictures = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)
    assert pictures, "the saved cartograph has no static frame picture"
    return _decoded(base64.b64decode(pictures[0]))


class TestAutosaveOfAHeavyStaticFrame:
    @pytest.fixture
    def heavy(self, qgis_app, project, tmp_path):
        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR (see CONTRIBUTING.md)")
        from nika_onlymap_exporter.core.settings import FrameChoice

        layout, map_item = _heavy_layout(project)
        dpi = _dpi_that_is_heavy(layout, map_item)
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        cartograph.show_layout_named(layout.name())
        dialog.state.frame_choices(layout.name())[map_item.uuid()] = FrameChoice(
            mode="static", dpi=dpi
        )
        target = tmp_path / "heavy.html"
        cartograph.file_edit.setText(str(target))
        cartograph._on_file_edited(str(target))
        assert _wait_for(qgis_app, lambda: target.exists(), timeout=240)
        assert _wait_for(qgis_app, lambda: self._idle(cartograph), timeout=240)
        yield dialog, layout, map_item, target
        dialog.close()

    @staticmethod
    def _idle(cartograph) -> bool:
        return cartograph._job is None and not cartograph._autosave_timer.isActive()

    @staticmethod
    def _drag_the_content(layout, map_item) -> None:
        """What a finished map content drag commits: one undo command that
        moves the extent."""
        extent = map_item.extent()
        shift = extent.width() * 0.07
        layout.undoStack().beginCommand(map_item, "Move map content")
        extent.setXMinimum(extent.xMinimum() + shift)
        extent.setXMaximum(extent.xMaximum() + shift)
        map_item.setExtent(extent)
        layout.undoStack().endCommand()

    def test_the_event_loop_keeps_turning_and_the_page_is_the_same(
        self, qgis_app, heavy
    ) -> None:
        from qgis.PyQt.QtCore import QTimer

        dialog, layout, map_item, target = heavy
        cartograph = dialog._cartograph
        before = target.stat().st_mtime_ns
        gaps: list[float] = []
        last = [time.perf_counter()]

        def beat() -> None:
            now = time.perf_counter()
            gaps.append(now - last[0])
            last[0] = now

        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(beat)
        heartbeat.start()
        try:
            self._drag_the_content(layout, map_item)
            assert cartograph._autosave_timer.isActive()
            # The whole autosave, debounce to written file.
            assert _wait_for(
                qgis_app,
                lambda: target.stat().st_mtime_ns != before,
                timeout=240,
            )
            assert _wait_for(qgis_app, lambda: self._idle(cartograph), timeout=240)
        finally:
            heartbeat.stop()
        assert gaps, "the heartbeat never ran"
        assert max(gaps) < LOOP_STALL_BOUND_S, (
            f"the event loop stalled for {max(gaps):.2f} s while the autosave ran"
        )
        # Complete, and the very picture a synchronous render makes now.
        html = target.read_text(encoding="utf-8")
        assert html.rstrip().endswith("</html>")
        saved = _saved_image(html)
        dpi = round(saved.width() / (map_item.sizeWithUnits().width() / 25.4))
        expected = _decoded(render_static_frame(layout, map_item, dpi=dpi).png_bytes)
        assert saved.size() == expected.size()
        assert saved == expected

    def test_preview_right_after_a_drag_opens_the_dragged_picture(
        self,
        qgis_app,
        heavy,
        opened,  # noqa: F811
    ) -> None:
        """Preview waits for the save carrying the latest edit, even while the
        frame is still being drawn."""
        dialog, layout, map_item, _target = heavy
        self._drag_the_content(layout, map_item)
        dialog._cartograph.preview()
        assert _wait_for(qgis_app, lambda: opened, timeout=240)
        _url, text = opened[0]
        saved = _saved_image(text)
        dpi = round(saved.width() / (map_item.sizeWithUnits().width() / 25.4))
        expected = _decoded(render_static_frame(layout, map_item, dpi=dpi).png_bytes)
        assert saved == expected


# ---- The autosave lets the user finish first --------------------------------


class TestAutosaveWaitsForTheUser:
    """Mechanism checks (advisory): the result tests above are the gates."""

    def _ready(self, project, qgis_app, tmp_path):
        from test_cartograph_dialog import _add_layout

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        cartograph.file_edit.setText(str(tmp_path / "survey.html"))
        return dialog, cartograph

    def test_no_build_starts_while_a_mouse_button_is_held(
        self,
        qgis_app,
        project,
        fake_cartograph_export,
        tmp_path,
        monkeypatch,  # noqa: F811
    ) -> None:
        """Mechanism: a build is not begun mid-drag, and is begun after."""
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtGui import QGuiApplication

        dialog, cartograph = self._ready(project, qgis_app, tmp_path)
        try:
            cartograph._autosave_timer.stop()
            fake_cartograph_export.calls.clear()
            held = {"down": True}
            monkeypatch.setattr(
                QGuiApplication,
                "mouseButtons",
                staticmethod(
                    lambda: (
                        Qt.MouseButton.LeftButton
                        if held["down"]
                        else Qt.MouseButton.NoButton
                    )
                ),
            )
            cartograph.schedule_autosave()
            assert not _wait_for(
                qgis_app, lambda: fake_cartograph_export.calls, timeout=1.8
            )
            held["down"] = False
            assert _wait_for(qgis_app, lambda: fake_cartograph_export.calls, timeout=10)
        finally:
            dialog.close()

    def test_a_save_is_never_held_back_longer_than_the_limit(
        self,
        qgis_app,
        project,
        fake_cartograph_export,
        tmp_path,
        monkeypatch,  # noqa: F811
    ) -> None:
        """Mechanism: a button that is never released cannot stop the file
        being written."""
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtGui import QGuiApplication

        import nika_onlymap_exporter.ui.cartograph.controller as controller_module

        dialog, cartograph = self._ready(project, qgis_app, tmp_path)
        try:
            cartograph._autosave_timer.stop()
            fake_cartograph_export.calls.clear()
            monkeypatch.setattr(controller_module, "AUTOSAVE_DEFER_LIMIT_S", 0.8)
            monkeypatch.setattr(
                QGuiApplication,
                "mouseButtons",
                staticmethod(lambda: Qt.MouseButton.LeftButton),
            )
            cartograph.schedule_autosave()
            assert _wait_for(qgis_app, lambda: fake_cartograph_export.calls, timeout=15)
        finally:
            dialog.close()
