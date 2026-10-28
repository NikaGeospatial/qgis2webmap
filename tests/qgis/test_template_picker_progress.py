"""The picker's cards fill in one at a time however long that takes: their
waiting / drawing / ready / failed states, a failed or stalled card not
stopping the others, results for settings no longer shown being dropped, and
the enlarged preview keeping the card's thumbnail while the sharper page is
drawn.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

from qgis.PyQt.QtCore import QEventLoop, QSize, QTimer
from qgis.PyQt.QtGui import QColor, QImage
from qgis.PyQt.QtTest import QTest
from test_template_picker import (
    _PROJECT,
    _drain,
    _Pending,
    _planned,
    _planner,
    _Renderer,
    _settings,
    _shown,
    _SlowRenderer,
)

from nika_onlymap_exporter.core.layout_templates import TemplateInput, plan_template
from nika_onlymap_exporter.ui.cartograph import template_picker
from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker
from nika_onlymap_exporter.ui.cartograph.template_preview_dialog import (
    TemplatePreviewDialog,
)

_TALL = QSize(900, 4000)


def _ready(row) -> bool:
    """The card shows its real page: no label over it, no failure face."""
    return (
        row.status.isHidden()
        and row.failure.isHidden()
        and not row.page.pixmap().isNull()
    )


def _unavailable(row) -> bool:
    return not row.failure.isHidden() and row.retry.text() == "Retry"


def _until(qgis_app, condition, seconds: float = 30.0) -> bool:
    """Spin the event loop until `condition()` holds (or time runs out)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qgis_app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if condition():
            return True
    return False


def _centre(pixmap) -> str:
    image = pixmap.toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2).name()


class _Red(_Pending):
    """A sharper page, drawn red so it can be told from the card's blue."""

    def _done(self) -> None:
        image = QImage(self._width, 600, QImage.Format.Format_ARGB32)
        image.fill(QColor("#cc2222"))
        self.finished.emit(image)


class _FailingFirst(_SlowRenderer):
    """The first page reports that it could not be drawn."""

    def __call__(self, template_id, page_size, orientation, width_px):
        pending = super().__call__(template_id, page_size, orientation, width_px)
        if len(self.pending) == 1:
            pending._timer.timeout.disconnect()
            pending._timer.timeout.connect(lambda: pending.finished.emit(None))
        return pending


def test_a_card_walks_waiting_drawing_ready(qgis_app, tmp_path):
    renderer = _SlowRenderer(delay_ms=150)
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
    try:
        # Landscape, as the stand-in renderer's pages are.
        picker.orientation_combo.setCurrentIndex(
            picker.orientation_combo.findData("landscape")
        )
        _shown(qgis_app, picker, _TALL)
        qgis_app.processEvents()
        chosen = picker.rows[picker.template_id]
        later = [r for r in picker.rows.values() if r is not chosen]
        assert chosen.status.text() == "Drawing your map\u2026"
        assert not chosen.status.isHidden()
        assert later[0].status.text() == "Waiting to draw"
        assert not later[0].status.isHidden()
        assert not later[0].page.pixmap().isNull()  # the layout guide
        box = chosen.preview.size()
        assert _until(qgis_app, lambda: _ready(chosen))
        assert chosen.preview.size() == box
    finally:
        picker.reject()


def test_more_than_five_seconds_of_rendering_still_completes_later_cards(
    qgis_app, tmp_path
):
    renderer = _SlowRenderer(delay_ms=520)
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
    try:
        _shown(qgis_app, picker, _TALL)
        started = time.monotonic()
        wanted = set(picker.near_ids()) | {picker.template_id}
        assert len(wanted) >= 10
        assert _until(qgis_app, lambda: all(_ready(picker.rows[t]) for t in wanted))
        assert time.monotonic() - started > 5.0
    finally:
        picker.reject()


def test_one_failed_card_does_not_stop_the_queue(qgis_app, tmp_path):
    renderer = _FailingFirst(delay_ms=20)
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
    try:
        _shown(qgis_app, picker, _TALL)
        first = picker.rows[picker.template_id]
        others = [r for t, r in picker.rows.items() if t != picker.template_id]
        assert _until(qgis_app, lambda: sum(_ready(r) for r in others) >= 5)
        assert _unavailable(first)
        renderer.only_first = True
        first.retry.click()
        assert _until(qgis_app, lambda: _ready(first))
    finally:
        picker.reject()


def test_a_card_keeps_its_box_when_it_fails(qgis_app, tmp_path):
    renderer = _FailingFirst(delay_ms=300)

    def tall_map(template_id, page_size, orientation):
        return plan_template(
            template_id,
            TemplateInput(
                700, 1000, False, page_size=page_size, orientation=orientation
            ),
        )

    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path), planner=tall_map)
    try:
        _shown(qgis_app, picker, _TALL)
        first = picker.rows[picker.template_id]
        assert first.preview.height() > first.preview.width()  # a tall map
        others = [r for t, r in picker.rows.items() if t != picker.template_id]
        before = first.preview.size()
        positions = [r.mapTo(picker, r.rect().topLeft()) for r in others]
        assert _until(qgis_app, lambda: _unavailable(first))
        after = first.preview.size()
        assert abs(after.width() - before.width()) <= 2  # the page's edge rounds
        assert after.height() == before.height()
        assert [r.mapTo(picker, r.rect().topLeft()) for r in others] == positions
    finally:
        picker.reject()


def test_a_stalled_card_is_given_up_alone(qgis_app, tmp_path, monkeypatch):
    """One timeout fails that card, with a Retry; it does not switch the
    picker to showing guides only."""
    monkeypatch.setattr(template_picker, "_CARD_RENDER_TIMEOUT_MS", 60)
    renderer = _SlowRenderer(delay_ms=5000, only_first=True)
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
    try:
        _shown(qgis_app, picker)
        QTest.qWait(500)
        first = picker.rows[picker.template_id]
        assert _unavailable(first)
        assert any(_ready(r) for r in picker.rows.values())
        renderer.pending[0]._done()  # arrives after the timeout
        assert _unavailable(first)
    finally:
        picker.reject()


def test_a_result_for_settings_no_longer_shown_is_discarded(qgis_app, tmp_path):
    renderer = _SlowRenderer(delay_ms=5000)
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
    try:
        _shown(qgis_app, picker)
        qgis_app.processEvents()
        first = renderer.pending[0]
        chosen = picker.rows[picker.template_id]
        picker.page_combo.setCurrentIndex(picker.page_combo.findData("A3"))
        for _ in range(5):
            qgis_app.processEvents()
        first._done()  # the A4 page, finished late
        assert not _ready(chosen)
        picker.page_combo.setCurrentIndex(picker.page_combo.findData("A4"))
        for _ in range(5):
            qgis_app.processEvents()
        assert not _ready(chosen)  # the late A4 page was not kept
    finally:
        picker.reject()


def test_a_page_is_drawn_for_the_card_not_for_the_widest_box(qgis_app, tmp_path):
    renderer = _Renderer()
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
    try:
        _shown(qgis_app, picker, QSize(560, 480))
        _drain(qgis_app, picker)
        qgis_app.processEvents()
        shown = picker.rows[picker.template_id].preview.width()
        ratio = max(1.0, picker.devicePixelRatioF())
        widths = {c[3] for c in renderer.calls}
        assert len(widths) == 1
        (width,) = widths
        assert round(shown * ratio) <= width <= round(shown * ratio) * 1.25 + 40
    finally:
        picker.reject()


def _thumbnail_window(parent, pending):
    thumbnail = QImage(360, 255, QImage.Format.Format_ARGB32)
    thumbnail.fill(QColor("#336699"))
    window = TemplatePreviewDialog(
        "Daylight", "", "A4", lambda width: pending, parent, thumbnail
    )
    window.show()
    window.start()
    QTest.qWait(30)
    return window


def _close(window, parent):
    window.reject()
    parent.reject()
    window.deleteLater()
    parent.deleteLater()
    QTest.qWait(1)


def test_the_large_preview_keeps_the_thumbnail_while_it_refines(qgis_app, tmp_path):
    parent = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
    pending = _Red(960, 60000)
    window = _thumbnail_window(parent, pending)
    try:
        assert _centre(window.page.pixmap()) == "#336699"
        assert window.progress.text() == "Refining preview\u2026"
        assert not window.progress.isHidden()
        pending._done()
        assert _centre(window.page.pixmap()) == "#cc2222"
        assert window.progress.isHidden()
    finally:
        _close(window, parent)


def test_a_failed_refinement_keeps_the_thumbnail_and_offers_a_retry(qgis_app, tmp_path):
    parent = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
    pending = _Red(960, 60000)
    window = _thumbnail_window(parent, pending)
    try:
        pending.finished.emit(None)
        assert _centre(window.page.pixmap()) == "#336699"
        assert window.retry_button.isVisible()
        assert not window.progress.isHidden()
    finally:
        _close(window, parent)


def test_the_large_preview_waits_for_a_cancelled_page_to_let_go(
    qgis_app, tmp_path, monkeypatch
):
    from nika_onlymap_exporter.ui.cartograph import template_preview_dialog as dlg

    busy = [True]
    monkeypatch.setattr(dlg, "render_slot_busy", lambda: busy[0])
    drawn: list[int] = []
    parent = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
    window = TemplatePreviewDialog(
        "Daylight", "", "A4", lambda width: drawn.append(width), parent
    )
    try:
        window.show()
        window.start()
        QTest.qWait(250)
        assert drawn == []
        busy[0] = False
        QTest.qWait(250)
        assert len(drawn) == 1
    finally:
        _close(window, parent)


def _slow_layer(project):
    """Two features of a million vertices, buffered while drawn: the draw
    takes seconds, and one buffer cannot be interrupted part way."""
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFillSymbol,
        QgsGeometry,
        QgsGeometryGeneratorSymbolLayer,
        QgsLineString,
        QgsPolygon,
        QgsRectangle,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
    )

    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857&field=n:integer", "Slow", "memory")
    import math

    features = []
    count = 1_000_000
    for index in range(2):
        feature = QgsFeature(layer.fields())
        radii = [8000 + 400 * math.sin(k * 0.01) for k in range(count)]
        ring = QgsLineString(
            [
                25000 + index * 50000 + r * math.cos(2 * math.pi * k / count)
                for k, r in enumerate(radii)
            ],
            [
                35000 + r * math.sin(2 * math.pi * k / count)
                for k, r in enumerate(radii)
            ],
        )
        ring.close()
        polygon = QgsPolygon()
        polygon.setExteriorRing(ring)
        feature.setGeometry(QgsGeometry(polygon))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": "buffer($geometry, 40, 8)", "SymbolType": "Fill"}
    )
    generator.setSubSymbol(QgsFillSymbol.createSimple({"color": "#4f9d6a"}))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([generator])))
    project.addMapLayer(layer)
    return layer, crs, QgsRectangle(0, 0, 100000, 70000)


def test_closing_during_a_slow_render_stays_responsive_and_frees_the_slot(
    qgis_app, tmp_path, project
):
    from nika_onlymap_exporter.core.canvas_cartograph import (
        render_slot_busy,
        template_preview_async,
    )

    layer, crs, extent = _slow_layer(project)
    started: list[str] = []

    def render(template_id, page_size, orientation, width_px):
        started.append(template_id)
        return template_preview_async(
            extent,
            crs,
            [layer],
            project,
            template_id=template_id,
            width_px=width_px,
            page_size=page_size,
            orientation=orientation,
        )

    gaps: list[float] = []
    last = [time.monotonic()]

    def beat() -> None:
        now = time.monotonic()
        gaps.append(now - last[0])
        last[0] = now

    heartbeat = QTimer()
    heartbeat.timeout.connect(beat)
    heartbeat.start(10)
    first = TemplatePicker(render, qsettings=_settings(tmp_path))
    second = None
    try:
        _shown(qgis_app, first)
        chosen = first.rows[first.template_id]
        assert _until(qgis_app, lambda: bool(started))
        QTest.qWait(400)  # the page is part way through its drawing
        assert chosen.status.text() == "Drawing your map…"
        gaps.clear()
        closing = time.monotonic()
        first.reject()
        assert time.monotonic() - closing < 0.15
        QTest.qWait(50)
        assert max(gaps) < 0.15
        # The abandoned page still draws in the background: the next picker
        # starts no second page beside it, and draws once it has finished.
        assert render_slot_busy()
        started.clear()
        second = TemplatePicker(render, qsettings=_settings(tmp_path))
        _shown(qgis_app, second)
        QTest.qWait(500)
        assert started == []
        assert _until(qgis_app, lambda: bool(started), 40.0)
        assert not render_slot_busy()
    finally:
        heartbeat.stop()
        if second is not None:
            second.reject()
        assert _until(qgis_app, lambda: not render_slot_busy(), 40.0)
        project.removeMapLayer(layer)


class TestPlanningDoesNotHoldTheWindow:
    """Planning every card (its cues and layout guide) used to run in the
    constructor, so a heavy project showed nothing for a second or more.
    Now the window is made at once and the cards are planned after it shows."""

    def _slow_planner(self, delay_s: float):
        inner = _planner(_PROJECT)

        def plan(template_id, page_size, orientation):
            time.sleep(delay_s)
            return inner(template_id, page_size, orientation)

        return plan

    def test_the_window_is_made_at_once_however_slow_the_planner(
        self, qgis_app, tmp_path
    ):
        started = time.monotonic()
        picker = TemplatePicker(
            _Renderer(),
            qsettings=_settings(tmp_path),
            planner=self._slow_planner(0.15),
        )
        made = time.monotonic() - started
        try:
            # Eleven cards at 0.15 s each is over a second of planning.
            assert made < 0.5
            _shown(qgis_app, picker, _TALL)
        finally:
            picker.reject()

    def test_every_card_ends_up_as_if_planned_up_front(self, qgis_app, tmp_path):
        eager = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), planner=_planner(_PROJECT)
        )
        try:
            _shown(qgis_app, eager, _TALL)
            _planned(qgis_app, eager)
            _drain(qgis_app, eager)
            want = {
                t: (r.includes.text(), r.supports.text(), r.status.isHidden())
                for t, r in eager.rows.items()
            }
            want_pixels = {t: _centre(r.page.pixmap()) for t, r in eager.rows.items()}
        finally:
            eager.reject()
        lazy = TemplatePicker(
            _Renderer(),
            qsettings=_settings(tmp_path / "again"),
            planner=self._slow_planner(0.01),
        )
        try:
            _shown(qgis_app, lazy, _TALL)
            assert _until(
                qgis_app,
                lambda: not (lazy._plan_queue or lazy._plan_timer.isActive()),
            )
            _drain(qgis_app, lazy)
            got = {
                t: (r.includes.text(), r.supports.text(), r.status.isHidden())
                for t, r in lazy.rows.items()
            }
            assert got == want
            for t, row in lazy.rows.items():
                assert _centre(row.page.pixmap()) == want_pixels[t]
        finally:
            lazy.reject()

    def test_a_card_chosen_before_its_turn_is_planned_at_once(self, qgis_app, tmp_path):
        picker = TemplatePicker(
            _Renderer(),
            qsettings=_settings(tmp_path),
            planner=self._slow_planner(0.05),
        )
        try:
            _shown(qgis_app, picker, _TALL)
            last = list(picker.rows)[-1]
            row = picker.rows[last]
            assert not picker._is_planned(last)
            picker.choose(last)
            assert picker._is_planned(last)
            assert row.includes.text().startswith("Includes")
        finally:
            picker.reject()
