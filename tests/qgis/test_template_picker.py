"""The template picker against real Qt: the category filter, the cards, the
order previews draw in, the page cache, a failed page's retry, the size it
opens at, the keyboard and right-to-left.

The previews here are flat colours from a stand-in renderer; the real QGIS
pages are `test_layout_templates_build.TestPicker`'s business.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import time
import types

import pytest

pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import (
    QEventLoop,
    QObject,
    QSettings,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QImage
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QAbstractItemView, QWidget

from nika_onlymap_exporter.core import layout_templates
from nika_onlymap_exporter.core.canvas_cartograph import PREVIEW_LEGEND_LEFT_OUT_KEY
from nika_onlymap_exporter.core.layout_templates import (
    CHARACTERFUL,
    ESSENTIAL,
    TextSizes,
)
from nika_onlymap_exporter.ui.cartograph import template_picker
from nika_onlymap_exporter.ui.cartograph.template_picker import (
    TemplatePicker,
    dialog_sizes,
)

_NEW = ("night-desk", "marginalia", "atlas-ledger", "town-hall", "wayfinder")
_CHARACTERFUL = {
    "field-map",
    "poster",
    "locator",
    "story-map",
    "dashboard",
    "marginalia",
    "atlas-ledger",
    "town-hall",
    "wayfinder",
}


@pytest.fixture
def fifteen(monkeypatch):
    """The registry as it will be: fifteen designs, six essentials and nine
    characterful, all but one listing what they include. Classic is moved
    off the top, so the default is chosen by id, not by position."""
    base = list(layout_templates.TEMPLATES)[:10]
    designs = [
        dataclasses.replace(
            base[0],
            id=template_id,
            name=template_id.replace("-", " ").title(),
            description=f"The {template_id} design.",
        )
        for template_id in _NEW
    ]
    templates = [
        dataclasses.replace(
            t,
            category=CHARACTERFUL if t.id in _CHARACTERFUL else ESSENTIAL,
            includes=() if t.id == "map-only" else ("legend", "scale bar"),
        )
        for t in base[1:] + designs + base[:1]
    ]
    assert len(templates) == 15
    monkeypatch.setattr(layout_templates, "TEMPLATES", tuple(templates))
    return {t.id: t for t in templates}


class _Renderer:
    """Flat pages, recording every call; ids in `fail` raise. `figures`
    holds the key figures each design was last drawn with."""

    def __init__(self, fail=(), tall=False, left_out=None) -> None:
        self.calls: list[tuple[str, str, str, int]] = []
        self.figures: dict[str, tuple] = {}
        self.sizes: dict[str, object] = {}
        self.fail = set(fail)
        self.tall = tall
        #: Legend entries each design's page leaves out, as the real
        #: `template_preview` records them on the image.
        self.left_out: dict[str, int] = dict(left_out or {})

    def __call__(
        self, template_id, page_size, orientation, width_px, figures=(), sizes=None
    ):
        self.calls.append((template_id, page_size, orientation, width_px))
        self.figures[template_id] = figures
        self.sizes[template_id] = sizes
        if template_id in self.fail:
            raise RuntimeError("no")
        tall = orientation == "portrait" or (self.tall and orientation == "auto")
        height = round(width_px * 1.414) if tall else round(width_px / 1.414)
        image = QImage(width_px, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#336699"))
        image.setText(
            PREVIEW_LEGEND_LEFT_OUT_KEY, str(self.left_out.get(template_id, 0))
        )
        return image

    @property
    def ids(self) -> list[str]:
        return [c[0] for c in self.calls]


def _listed(templates) -> list[str]:
    """The ids in the order the picker lists them: essentials first."""
    return sorted(templates, key=lambda t: templates[t].category != ESSENTIAL)


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "picker.ini"), QSettings.Format.IniFormat)


def _drain(qgis_app, picker) -> None:
    """Until nothing is left to draw: no page queued, none about to draw,
    and the list not resting after a scroll."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not (
            picker._queue or picker._timer.isActive() or picker._scroll_rest.isActive()
        ):
            break
        qgis_app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    qgis_app.processEvents()


def _in_reach(picker) -> set[str]:
    """The designs the picker draws without being asked: the chosen one,
    and the cards in view or a short scroll from it."""
    reach = set(picker.near_ids())
    if not picker._items[picker.template_id].isHidden():
        reach.add(picker.template_id)
    return reach


def _shown(qgis_app, picker, size: QSize | None = None) -> None:
    picker.resize(size if size is not None else QSize(700, 480))
    picker.show()
    for _ in range(3):
        qgis_app.processEvents()


def _select(picker, template_id) -> None:
    picker.template_list.setCurrentItem(picker._items[template_id])


class TestTheFilter:
    def test_three_choices_and_all_by_default(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            assert list(picker.filter_buttons) == ["all", ESSENTIAL, CHARACTERFUL]
            texts = [b.text() for b in picker.filter_buttons.values()]
            assert texts == ["All", "Essentials", "Characterful"]
            assert picker.category_filter == "all"
            assert not any(i.isHidden() for i in picker._items.values())
        finally:
            picker.reject()

    def test_a_category_shows_only_its_designs(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            picker.filter_buttons[CHARACTERFUL].click()
            shown = {t for t, i in picker._items.items() if not i.isHidden()}
            assert shown == _CHARACTERFUL
            picker.filter_buttons[ESSENTIAL].click()
            shown = {t for t, i in picker._items.items() if not i.isHidden()}
            assert shown == set(fifteen) - _CHARACTERFUL
            assert len(shown) == 6
            picker.filter_buttons["all"].click()
            assert not any(i.isHidden() for i in picker._items.values())
        finally:
            picker.reject()

    def test_all_lists_the_essentials_first(self, qgis_app, tmp_path):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            listed = [
                picker.template_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(picker.template_list.count())
            ]
            assert listed == [
                "classic",
                "legend-below",
                "map-only",
                "report-figure",
                "dark",
                "night-desk",
                "field-map",
                "poster",
                "locator",
                "story-map",
                "dashboard",
                "marginalia",
                "atlas-ledger",
                "town-hall",
                "wayfinder",
            ]
        finally:
            picker.reject()

    def test_a_visible_choice_is_kept(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _select(picker, "poster")
            picker.filter_buttons[CHARACTERFUL].click()
            assert picker.template_id == "poster"
        finally:
            picker.reject()

    def test_a_hidden_choice_moves_to_the_first_shown(
        self, qgis_app, tmp_path, fifteen
    ):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            assert picker.template_id == "classic"
            picker.filter_buttons[CHARACTERFUL].click()
            first = next(t for t in fifteen if t in _CHARACTERFUL)
            assert picker.template_id == first
            assert fifteen[first].name in picker.selected_label.text()
        finally:
            picker.reject()


class TestTheCards:
    def test_fifteen_cards_each_with_a_spoken_name(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            assert list(picker.rows) == _listed(fifteen)
            assert picker.template_list.count() == 15
            for template_id, item in picker._items.items():
                t = fifteen[template_id]
                spoken = item.data(Qt.ItemDataRole.AccessibleTextRole)
                assert spoken == f"{t.name}: {t.description}"
        finally:
            picker.reject()

    def test_without_a_plan_a_card_promises_only_what_every_page_has(
        self, qgis_app, tmp_path, fifteen
    ):
        """No planner to check the page: a scale bar is on every page, a
        legend only when there are layers, so the legend is offered, not
        promised; a design that lists nothing shows neither line."""
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            poster = picker.rows["poster"]
            assert poster.includes.text() == "Includes: scale bar"
            assert poster.supports.text() == "Can also show: legend"
            assert poster.supports.toolTip() == (
                "legend: Appears when the map shows layers."
            )
            assert not poster.includes.isHidden()
            assert not poster.supports.isHidden()
            assert picker.rows["map-only"].includes.isHidden()
            assert picker.rows["map-only"].supports.isHidden()
            spoken = picker._items["poster"].data(
                Qt.ItemDataRole.AccessibleDescriptionRole
            )
            assert spoken.splitlines() == [
                "Includes: scale bar",
                "Can also show: legend",
                "legend: Appears when the map shows layers.",
            ]
        finally:
            picker.reject()

    def test_two_columns_when_wide_one_when_narrow(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:

            def columns() -> int:
                tops = [
                    picker.template_list.visualItemRect(i).top()
                    for i in picker._items.values()
                ]
                return tops.count(tops[0])

            _shown(qgis_app, picker, QSize(1100, 700))
            assert columns() == 2
            picker.resize(700, 600)
            for _ in range(3):
                qgis_app.processEvents()
            assert columns() == 1
        finally:
            picker.reject()

    def test_the_page_fills_its_box(self, qgis_app, tmp_path, fifteen):
        """No tiny page lost in a big card: the preview takes a real share
        of the card's width."""
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker, QSize(700, 480))
            _drain(qgis_app, picker)
            row = picker.rows["classic"]
            assert row.drawn
            assert row.preview.width() >= 0.4 * row.width()
            assert row.page.pixmap() is not None
        finally:
            picker.reject()

    def test_the_card_says_what_the_design_is_best_for(
        self, qgis_app, tmp_path, fifteen
    ):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker, QSize(700, 480))
            _drain(qgis_app, picker)
            line = layout_templates.get_template("classic").best_for
            assert line and line in picker._items["classic"].toolTip()
        finally:
            picker.reject()

    @pytest.mark.parametrize(
        ("orientation", "tall_pages"), [("portrait", False), ("auto", True)]
    )
    def test_a_tall_page_gets_a_tall_box(
        self, qgis_app, tmp_path, fifteen, orientation, tall_pages
    ):
        """A portrait page in a landscape-shaped box was a sliver beside a
        wide blank: the box takes the pages' shape, chosen or automatic."""
        picker = TemplatePicker(
            _Renderer(tall=tall_pages), qsettings=_settings(tmp_path)
        )
        try:
            picker.orientation_combo.setCurrentIndex(
                picker.orientation_combo.findData("landscape")
            )
            _shown(qgis_app, picker, QSize(1100, 700))
            _drain(qgis_app, picker)
            wide = picker.rows["classic"].preview.size()
            assert wide.width() > wide.height()
            picker.orientation_combo.setCurrentIndex(
                picker.orientation_combo.findData(orientation)
            )
            _drain(qgis_app, picker)
            row = picker.rows["classic"]
            assert row.drawn
            assert row.preview.height() > row.preview.width()
            assert row.preview.height() > wide.height()
        finally:
            picker.reject()


class TestPreviewOrder:
    def test_the_selected_design_then_the_ones_in_view(
        self, qgis_app, tmp_path, fifteen
    ):
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _select(picker, "wayfinder")
            picker.resize(700, 480)
            picker.show()
            bar = picker.template_list.verticalScrollBar()
            # Before anything draws: look at the top of the list.
            bar.setValue(0)
            renderer.calls.clear()
            _drain(qgis_app, picker)
            ids = renderer.ids
            assert ids[0] == "wayfinder"
            in_view = picker.visible_ids()
            assert in_view and "wayfinder" not in in_view
            assert ids[1 : 1 + len(in_view)] == in_view
            # Then the cards a short scroll away, and nothing further.
            assert set(ids) == _in_reach(picker)
            assert len(ids) < len(fifteen)
        finally:
            picker.reject()

    def test_a_card_far_down_waits_until_it_comes_near(
        self, qgis_app, tmp_path, fifteen
    ):
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            last = list(picker._items)[-1]
            assert last not in renderer.ids
            assert not picker.rows[last].drawn
            picker.template_list.scrollToBottom()
            _drain(qgis_app, picker)
            assert last in renderer.ids
            assert picker.rows[last].drawn
            assert len(renderer.ids) == len(set(renderer.ids))
        finally:
            picker.reject()

    def test_nothing_draws_while_the_list_scrolls(self, qgis_app, tmp_path, fifteen):
        """A page takes the event loop for as long as it draws: none starts
        until the list has rested after the last scroll step."""
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            renderer.calls.clear()
            bar = picker.template_list.verticalScrollBar()
            while bar.value() < bar.maximum():
                # As a drag of the scroll bar does it.
                bar.setSliderPosition(min(bar.maximum(), bar.value() + 24))
                QTest.qWait(20)
            assert renderer.calls == []
            assert picker._scroll_rest.isActive()
            _drain(qgis_app, picker)
            assert renderer.calls
            assert set(picker.visible_ids()) <= set(renderer.ids) | {
                t for t, row in picker.rows.items() if row.drawn
            }
        finally:
            picker.reject()

    def test_scrolling_draws_what_comes_into_view_next(
        self, qgis_app, tmp_path, fifteen
    ):
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _select(picker, next(iter(fifteen)))
            _shown(qgis_app, picker)
            qgis_app.processEvents()
            bar = picker.template_list.verticalScrollBar()
            bar.setValue(bar.maximum())
            seen = list(renderer.ids)
            in_view = [t for t in picker.visible_ids() if t not in seen]
            assert in_view
            _drain(qgis_app, picker)
            after = renderer.ids[len(seen) :]
            assert after[: len(in_view)] == in_view
        finally:
            picker.reject()

    def test_a_filter_never_draws_the_designs_it_hides(
        self, qgis_app, tmp_path, fifteen
    ):
        """Filtered out, a design is not drawn at all; shown again, the ones
        that come into view are."""
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            picker.filter_buttons[CHARACTERFUL].click()
            _shown(qgis_app, picker, QSize(1100, 700))
            _drain(qgis_app, picker)
            assert renderer.ids
            assert set(renderer.ids) <= _CHARACTERFUL
            essentials = set(fifteen) - _CHARACTERFUL
            assert not any(picker.rows[t].drawn for t in essentials)
            renderer.calls.clear()
            picker.filter_buttons["all"].click()
            _drain(qgis_app, picker)
            shown_now = set(picker.visible_ids()) & essentials
            assert shown_now
            assert shown_now <= set(renderer.ids)
            assert all(picker.rows[t].drawn for t in shown_now)
        finally:
            picker.reject()


class TestPageCache:
    def test_a_page_setting_never_draws_in_its_own_event(
        self, qgis_app, tmp_path, fifteen
    ):
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _drain(qgis_app, picker)
            renderer.calls.clear()
            for size in ("A3", "Letter", "A5", "Tabloid"):
                picker.page_combo.setCurrentIndex(picker.page_combo.findData(size))
            assert renderer.calls == []
            _drain(qgis_app, picker)
            # The page sizes passed over were never drawn.
            assert {c[1] for c in renderer.calls} == {"Tabloid"}
            assert {c[0] for c in renderer.calls} == _in_reach(picker)
        finally:
            picker.reject()

    def test_going_back_to_a_page_size_draws_nothing(self, qgis_app, tmp_path, fifteen):
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _drain(qgis_app, picker)
            picker.page_combo.setCurrentIndex(picker.page_combo.findData("A3"))
            _drain(qgis_app, picker)
            renderer.calls.clear()
            picker.page_combo.setCurrentIndex(picker.page_combo.findData("A4"))
            _drain(qgis_app, picker)
            assert renderer.calls == []
            assert all(picker.rows[t].drawn for t in _in_reach(picker))
        finally:
            picker.reject()

    def test_a_canvas_key_shares_pages_with_the_next_picker(
        self, qgis_app, tmp_path, fifteen
    ):
        renderer = _Renderer()
        key = f"canvas-{tmp_path.name}"
        first = TemplatePicker(renderer, qsettings=_settings(tmp_path), canvas_key=key)
        _shown(qgis_app, first)
        _drain(qgis_app, first)
        drawn = set(renderer.ids)
        assert drawn == _in_reach(first)
        first.reject()
        renderer.calls.clear()
        again = TemplatePicker(renderer, qsettings=_settings(tmp_path), canvas_key=key)
        try:
            _shown(qgis_app, again)
            _drain(qgis_app, again)
            assert renderer.calls == []
            assert all(again.rows[t].drawn for t in drawn)
        finally:
            again.reject()
        other = TemplatePicker(
            renderer, qsettings=_settings(tmp_path), canvas_key=key + "-moved"
        )
        try:
            # The canvas moved: the pages drawn before are of no more use.
            assert not any(
                k[template_picker._CANVAS] == key
                for k in template_picker._SHARED_PAGES._pages
            )
            _shown(qgis_app, other)
            _drain(qgis_app, other)
            assert set(renderer.ids) == drawn
        finally:
            other.reject()

    def test_without_a_canvas_key_nothing_is_shared(self, qgis_app, tmp_path, fifteen):
        renderer = _Renderer()
        first = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        _shown(qgis_app, first)
        _drain(qgis_app, first)
        drawn = set(renderer.ids)
        first.reject()
        renderer.calls.clear()
        again = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, again)
            _drain(qgis_app, again)
            assert set(renderer.ids) == drawn
        finally:
            again.reject()

    @staticmethod
    def _page(width: int = 10, height: int = 10) -> QImage:
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#336699"))
        return image

    @staticmethod
    def _key(template_id: str, canvas: str = "c") -> tuple:
        return (template_id, "A4", "auto", 360, canvas, ())

    def test_the_shared_cache_is_bounded_by_count(self):
        cache = template_picker._PageCache(limit=3)
        for index in range(5):
            cache.put(self._key(f"t{index}"), self._page())
        assert len(cache) == 3
        assert self._key("t0") not in cache and self._key("t1") not in cache
        assert cache.get(self._key("t4")) is not None

    def test_the_shared_cache_is_bounded_by_bytes(self):
        page = self._page(100, 100)
        cache = template_picker._PageCache(max_bytes=3 * page.sizeInBytes())
        for index in range(6):
            cache.put(self._key(f"t{index}"), self._page(100, 100))
        assert len(cache) == 3
        assert cache.size_bytes == 3 * page.sizeInBytes()
        # Used, a page is kept over one put in after it but not used since.
        cache.get(self._key("t3"))
        cache.put(self._key("t6"), self._page(100, 100))
        assert self._key("t3") in cache
        assert self._key("t4") not in cache

    def test_a_page_put_again_is_counted_once(self):
        cache = template_picker._PageCache(max_bytes=10**9)
        for _ in range(3):
            cache.put(self._key("t"), self._page(100, 100))
        assert len(cache) == 1
        assert cache.size_bytes == self._page(100, 100).sizeInBytes()

    def test_the_real_bound_holds_every_design_at_twice_the_pixels(self):
        """Fifteen tall pages at a 2x screen fit, so going back to a page
        setting just seen draws nothing."""
        tall = self._page(2 * template_picker.RENDER_WIDTH, 2 * 509)
        assert 15 * tall.sizeInBytes() <= template_picker._SHARED_CACHE_BYTES
        assert template_picker._SHARED_CACHE_LIMIT >= 15

    def test_another_canvas_state_is_dropped(self):
        cache = template_picker._PageCache()
        cache.put(self._key("a", "old"), self._page())
        cache.put(self._key("b", "new"), self._page())
        cache.keep_only("new")
        assert self._key("a", "old") not in cache
        assert self._key("b", "new") in cache
        assert cache.size_bytes == self._page().sizeInBytes()


class TestAFailedPreview:
    def test_it_says_so_with_a_retry_that_draws_it(self, qgis_app, tmp_path, fifteen):
        renderer = _Renderer(fail=("poster",))
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _select(picker, "poster")
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            row = picker.rows["poster"]
            assert row.failed and not row.drawn
            assert not row.failure.isHidden()
            assert row.page.isHidden()
            assert row.retry.text() == "Retry"
            assert "could not be drawn" in picker._items["poster"].toolTip()
            assert all(picker.rows[t].drawn for t in _in_reach(picker) if t != "poster")
            # Not tried again by itself...
            renderer.calls.clear()
            _drain(qgis_app, picker)
            assert renderer.calls == []
            # ...but Retry does, and a page that now draws replaces the error.
            renderer.fail.clear()
            row.retry.click()
            _drain(qgis_app, picker)
            assert renderer.ids == ["poster"]
            assert row.drawn and not row.failed
            assert row.failure.isHidden()
        finally:
            picker.reject()


class TestTheOpeningSize:
    @pytest.mark.parametrize(
        ("screen", "at_least"),
        [
            ((860, 560), (640, 480)),
            ((1400, 900), (860, 720)),
            ((2560, 1440), (1600, 1100)),
        ],
    )
    def test_it_fits_the_screen_and_uses_a_big_one(self, screen, at_least) -> None:
        available = QSize(*screen)
        size, minimum = dialog_sizes(available)
        assert size.width() <= int(screen[0] * 0.9)
        assert size.height() <= int(screen[1] * 0.88)
        assert minimum.width() <= size.width()
        assert minimum.height() <= size.height()
        assert size.width() >= at_least[0]
        assert size.height() >= at_least[1]

    def test_the_layout_fits_a_small_screen(self, qgis_app, tmp_path, fifteen):
        """At 860x560 nothing in the dialog asks for more room than the
        screen has: only the list scrolls."""
        size, _minimum = dialog_sizes(QSize(860, 560))
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker, size)
            needed = picker.minimumSizeHint()
            assert needed.width() <= size.width()
            assert needed.height() <= size.height()
            assert picker.size() == size
        finally:
            picker.reject()

    def test_it_opens_within_its_screen(self, qgis_app, tmp_path, fifteen):
        from qgis.PyQt.QtGui import QGuiApplication

        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            available = QGuiApplication.primaryScreen().availableGeometry()
            assert picker.width() <= available.width()
            assert picker.height() <= available.height()
        finally:
            picker.reject()


class TestTheDefault:
    def test_a_first_time_user_starts_on_daylight(self, qgis_app, tmp_path, fifteen):
        assert next(iter(fifteen)) != "classic"
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            assert picker.template_id == "classic"
        finally:
            picker.reject()

    def test_a_stored_design_that_is_gone_falls_back_to_daylight(
        self, qgis_app, tmp_path, fifteen
    ):
        settings = _settings(tmp_path)
        settings.setValue(template_picker.SETTINGS_KEY, "gone|A3|portrait")
        picker = TemplatePicker(_Renderer(), qsettings=settings)
        try:
            assert (picker.template_id, picker.page_size, picker.orientation) == (
                "classic",
                "A3",
                "portrait",
            )
        finally:
            picker.reject()

    def test_a_valid_choice_is_remembered(self, qgis_app, tmp_path, fifteen):
        settings = _settings(tmp_path)
        settings.setValue(template_picker.SETTINGS_KEY, "wayfinder|A2|landscape")
        picker = TemplatePicker(_Renderer(), qsettings=settings)
        try:
            assert picker.template_id == "wayfinder"
        finally:
            picker.reject()


class TestTheKeyboard:
    def test_arrows_choose_and_enter_creates(self, qgis_app, tmp_path, fifteen):
        settings = _settings(tmp_path)
        picker = TemplatePicker(_Renderer(), qsettings=settings)
        try:
            _shown(qgis_app, picker)
            picker.activateWindow()
            qgis_app.processEvents()
            assert picker.focusWidget() is picker.template_list
            order = _listed(fifteen)
            start = order.index(picker.template_id)
            QTest.keyClick(picker.template_list, Qt.Key.Key_Up)
            assert picker.template_id == order[start - 1]
            QTest.keyClick(picker.template_list, Qt.Key.Key_Down)
            assert picker.template_id == order[start]
            QTest.keyClick(picker.template_list, Qt.Key.Key_Return)
            assert picker.result() == 1
            assert settings.value(template_picker.SETTINGS_KEY).startswith("classic|")
        finally:
            picker.reject()

    def test_a_double_click_creates(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            row = picker.rows["classic"]
            QTest.mouseDClick(row.text, Qt.MouseButton.LeftButton)
            assert picker.result() == 1
        finally:
            picker.reject()

    def test_a_click_on_a_card_chooses_it(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker, QSize(1100, 700))
            QTest.mouseClick(
                picker.rows["legend-below"].name, Qt.MouseButton.LeftButton
            )
            assert picker.template_id == "legend-below"
            assert picker.rows["legend-below"].tick.text()
            assert not picker.rows["classic"].tick.text()
        finally:
            picker.reject()

    def test_tab_runs_filter_page_settings_list_buttons(
        self, qgis_app, tmp_path, fifteen
    ):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            picker.activateWindow()
            first = picker.filter_buttons["all"]
            first.setFocus(Qt.FocusReason.TabFocusReason)
            qgis_app.processEvents()
            seen = [picker.focusWidget()]
            for _ in range(12):
                QTest.keyClick(picker.focusWidget(), Qt.Key.Key_Tab)
                qgis_app.processEvents()
                seen.append(picker.focusWidget())
            wanted = [
                picker.page_combo,
                picker.orientation_combo,
                picker.template_list,
                picker.create_button,
                picker.cancel_button,
            ]
            positions = [seen.index(w) for w in wanted]
            assert positions == sorted(positions)
            assert seen.index(picker.page_combo) > 0
            # ...and round again to the filter.
            assert first in seen[positions[-1] :]
        finally:
            picker.reject()


class TestDirectionAndLength:
    def test_right_to_left_puts_the_page_on_the_right(
        self, qgis_app, tmp_path, fifteen
    ):
        host = QWidget()
        host.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path), parent=host)
        try:
            _shown(qgis_app, picker)
            assert picker.isRightToLeft()
            row = picker.rows["classic"]
            assert row.preview.x() > row.text.x()
            # An unchosen card's name starts at the text's edge: no gap kept
            # for a blank tick (which showed as an indent right to left).
            other = picker.rows["legend-below"]
            assert other.name.width() == other.text.width()
            filters = [picker.filter_buttons[k].x() for k in ("all", ESSENTIAL)]
            assert filters[0] > filters[1]
        finally:
            picker.reject()
            host.deleteLater()

    def test_long_translations_wrap_rather_than_clip(
        self, qgis_app, tmp_path, fifteen, monkeypatch
    ):
        original = template_picker.tr

        def long(text: str) -> str:
            out = original(text)
            if "{" in out:
                return out
            return " ".join([out] * 4)

        monkeypatch.setattr(template_picker, "tr", long)
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker, QSize(640, 480))
            row = picker.rows["classic"]
            assert row.name.text().count("Daylight") == 4
            for label in (row.name, row.description, row.includes):
                assert label.wordWrap()
                assert label.height() >= label.heightForWidth(label.width()), label
            item = picker._items["classic"]
            card = picker.template_list.visualItemRect(item)
            assert row.text.geometry().bottom() <= card.height()
            # The page settings moved under the filter rather than squeezing.
            assert picker.page_settings.y() > picker.filter_group.y()
        finally:
            picker.reject()


_BLANK = (("", ""), ("", ""), ("", ""))


def _typed(qgis_app, picker) -> None:
    """Let the pause after typing pass, and the pages it asks for draw."""
    QTest.qWait(template_picker._FIGURES_DELAY_MS + 50)
    _drain(qgis_app, picker)


def _type(picker, row: int, value: str | None = None, label: str | None = None):
    """Replace a figure's value and/or label as a user would: select all,
    then type (an empty string is a cleared field)."""
    group = picker.figures_group
    for edit, text in ((group.values[row], value), (group.labels[row], label)):
        if text is None:
            continue
        edit.setFocus()
        edit.selectAll()
        QTest.keyClick(edit, Qt.Key.Key_Delete)
        if text:
            QTest.keyClicks(edit, text)


@pytest.fixture
def figures_project(project):
    project.setCustomVariables({})
    yield project
    project.setCustomVariables({})


def _slide(qgis_app, picker, element: str, percent: int) -> None:
    """Move a size slider to `percent` as the keyboard would, and let the pause
    after it pass and the pages it asks for draw."""
    slider = picker.size_controls.sliders[element]
    slider.setValue(percent)
    QTest.qWait(template_picker._SIZES_DELAY_MS + 50)
    _drain(qgis_app, picker)


def _wheel(widget, delta: int = -120):
    """Send `widget` a mouse wheel turn; returns the event, to read whether
    anything accepted it."""
    from qgis.PyQt.QtCore import QPoint, QPointF
    from qgis.PyQt.QtGui import QWheelEvent
    from qgis.PyQt.QtWidgets import QApplication

    centre = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(
        centre,
        centre,
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    event.setAccepted(True)
    QApplication.sendEvent(widget, event)
    return event


class TestTextSizeControls:
    def test_three_keyboard_reachable_percent_sliders(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            controls = picker.size_controls
            assert not controls.isVisibleTo(picker)
            controls.toggle.click()
            assert controls.isVisibleTo(picker)
            assert list(controls.sliders) == ["title", "legend", "notes"]
            for name, slider in controls.sliders.items():
                assert slider.accessibleName() == name.capitalize()
                assert slider.focusPolicy() == Qt.FocusPolicy.TabFocus
                assert (slider.minimum(), slider.maximum()) == (80, 130)
                assert slider.singleStep() == 1
                assert slider.value() == 100
                assert slider.accessibleDescription() == "100%"
                assert controls._captions[name].text() == f"{name.capitalize()}: 100%"
            assert picker.text_sizes == TextSizes()
        finally:
            picker.reject()

    def test_keys_step_the_slider_and_the_label_shows_the_percent(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            picker.size_controls.toggle.click()
            slider = picker.size_controls.sliders["legend"]
            slider.setFocus()
            QTest.keyClick(slider, Qt.Key.Key_Left)
            assert picker.size_controls._captions["legend"].text() == "Legend: 99%"
            QTest.keyClick(slider, Qt.Key.Key_Home)
            assert picker.size_controls._captions["legend"].text() == "Legend: 80%"
            QTest.keyClick(slider, Qt.Key.Key_PageUp)
            assert slider.value() == 85
            assert "85%" in slider.toolTip()
            assert picker.size_controls.toggle.text() == "Text sizes (changed)"
            slider.setValue(100)
            assert picker.size_controls.toggle.text() == "Text sizes"
        finally:
            picker.reject()

    def test_a_wheel_turn_over_a_slider_changes_nothing_and_is_not_taken(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            picker.size_controls.toggle.click()
            for slider in picker.size_controls.sliders.values():
                for delta in (-120, 120):
                    event = _wheel(slider, delta)
                    assert slider.value() == 100
                    # Not accepted: it goes on to scroll the panel behind.
                    assert not event.isAccepted()
            assert not picker.size_controls._timer.isActive()
        finally:
            picker.reject()

    def test_a_click_does_not_leave_the_slider_focused(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            picker.size_controls.toggle.click()
            slider = picker.size_controls.sliders["title"]
            QTest.mouseClick(slider, Qt.MouseButton.LeftButton)
            assert not slider.hasFocus()
            assert slider.focusPolicy() == Qt.FocusPolicy.TabFocus
        finally:
            picker.reject()

    def test_a_drag_updates_the_label_live_and_applies_once_on_release(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            controls = picker.size_controls
            controls.toggle.click()
            applied: list[TextSizes] = []
            controls.changed.connect(lambda: applied.append(controls.sizes()))
            slider = controls.sliders["notes"]
            slider.setSliderDown(True)
            for value in (95, 107, 120, 107):
                slider.setValue(value)
                assert controls._captions["notes"].text() == f"Notes: {value}%"
            QTest.qWait(template_picker._SIZES_DELAY_MS + 50)
            assert applied == []
            slider.setSliderDown(False)
            QTest.qWait(template_picker._SIZES_DELAY_MS + 50)
            assert applied == [TextSizes(notes=107)]
        finally:
            picker.reject()

    def test_a_change_makes_a_new_page_key_and_redraws_once_after_the_pause(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        renderer = _Renderer()
        picker = TemplatePicker(
            renderer, qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            before = picker._key("classic")
            drawn = len(renderer.calls)
            slider = picker.size_controls.sliders["legend"]
            # Moving through the values changes nothing until it rests.
            for value in (107, 130):
                slider.setValue(value)
            assert picker._key("classic") == before
            assert len(renderer.calls) == drawn
            QTest.qWait(template_picker._SIZES_DELAY_MS + 50)
            _drain(qgis_app, picker)
            after = picker._key("classic")
            assert after != before
            assert after[6] == TextSizes(legend=130)
            assert len(renderer.calls) > drawn
            assert renderer.sizes["classic"] == TextSizes(legend=130)
        finally:
            picker.reject()

    def test_an_intermediate_percent_is_drawn_and_scales_the_page(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        renderer = _Renderer()
        picker = TemplatePicker(
            renderer, qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            _slide(qgis_app, picker, "title", 107)
            assert renderer.sizes["classic"] == TextSizes(title=107)
            assert TextSizes(title=107).apply(_PROJECT).title_scale == 1.07
        finally:
            picker.reject()

    def test_accept_saves_to_the_project_and_a_new_picker_reads_it_back(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            # Accepted straight after the move, before the pause has passed.
            picker.size_controls.sliders["title"].setValue(90)
            picker.size_controls.sliders["notes"].setValue(130)
            picker.accept()
        finally:
            picker.reject()
        variables = figures_project.customVariables()
        assert variables["nika_size_title"] == "90"
        assert variables["nika_size_notes"] == "130"
        assert "nika_size_legend" not in variables
        reopened = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            assert reopened.text_sizes == TextSizes(90, 100, 130)
            assert reopened.size_controls.sliders["notes"].value() == 130
        finally:
            reopened.reject()

    def test_a_project_saved_with_step_ids_opens_at_their_percent(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        figures_project.setCustomVariables(
            {"nika_size_title": "small", "nika_size_notes": "larger"}
        )
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            assert picker.text_sizes == TextSizes(90, 100, 130)
            assert picker.size_controls.sliders["title"].value() == 90
            assert picker.size_controls.sliders["notes"].value() == 130
        finally:
            picker.reject()

    def test_cancel_saves_no_size(self, qgis_app, tmp_path, fifteen, figures_project):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            picker.size_controls.sliders["title"].setValue(130)
            picker.cancel_button.click()
        finally:
            picker.reject()
        assert "nika_size_title" not in figures_project.customVariables()


class TestKeyFigures:
    def test_only_a_design_that_takes_figures_shows_them(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            assert picker.template_id == "classic"
            assert picker.figures_group.isHidden()
            _select(picker, "dashboard")
            assert not picker.figures_group.isHidden()
            assert picker.figures_group.isVisible()
            _select(picker, "poster")
            assert picker.figures_group.isHidden()
        finally:
            picker.reject()

    def test_the_design_says_so_not_its_id(
        self, qgis_app, tmp_path, fifteen, figures_project, monkeypatch
    ):
        """A declared property, not the id string: another design that
        takes figures shows them, Signal without the property does not."""
        designs = tuple(
            dataclasses.replace(t, takes_figures=t.id == "poster")
            for t in layout_templates.TEMPLATES
        )
        monkeypatch.setattr(layout_templates, "TEMPLATES", designs)
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker)
            _select(picker, "poster")
            assert not picker.figures_group.isHidden()
            _select(picker, "dashboard")
            assert picker.figures_group.isHidden()
        finally:
            picker.reject()

    def test_signal_is_the_registry_design_that_takes_figures(self):
        takes = [t.id for t in layout_templates.TEMPLATES if t.takes_figures]
        assert takes == ["dashboard"]

    def test_the_fields_start_from_the_project(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        figures_project.setCustomVariables(
            {"nika_figure_1": "214 | parcels surveyed", "nika_figure_3": "12 km"}
        )
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            group = picker.figures_group
            assert [e.text() for e in group.values] == ["214", "", "12 km"]
            assert [e.text() for e in group.labels] == ["parcels surveyed", "", ""]
            assert picker.figures == (
                ("214", "parcels surveyed"),
                ("", ""),
                ("12 km", ""),
            )
        finally:
            picker.reject()

    def test_typing_redraws_signal_after_a_pause_and_nothing_else(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        renderer = _Renderer()
        picker = TemplatePicker(
            renderer, qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            assert renderer.figures["dashboard"] == _BLANK
            # A design without figures is drawn without them.
            others = [t for t in renderer.figures if t != "dashboard"]
            assert others
            assert all(renderer.figures[t] == () for t in others)
            renderer.calls.clear()
            _type(picker, 0, "214", "parcels surveyed")
            # Not drawn per keystroke...
            qgis_app.processEvents()
            assert renderer.calls == []
            # ...but once typing pauses, only Signal, with what was typed.
            _typed(qgis_app, picker)
            assert renderer.ids == ["dashboard"]
            assert renderer.figures["dashboard"] == (
                ("214", "parcels surveyed"),
                ("", ""),
                ("", ""),
            )
            assert picker.rows["dashboard"].drawn
            # One page per design and page setting, not one per pause.
            _type(picker, 1, "3")
            _typed(qgis_app, picker)
            signal_pages = [k for k in picker._pages._pages if k[0] == "dashboard"]
            assert len(signal_pages) == 1
            assert signal_pages[0][5][1] == ("3", "")
        finally:
            picker.reject()

    def test_typed_figures_reach_the_planned_page(
        self, qgis_app, tmp_path, fifteen, figures_project, monkeypatch
    ):
        """End to end through the real preview: what is typed is what the
        Signal page's `TemplateInput.figures` gets, and what it draws."""
        from cartograph_fixtures import UTM, build_parcels_layer

        from nika_onlymap_exporter.core import canvas_cartograph

        layer = build_parcels_layer()
        figures_project.addMapLayer(layer)
        view = layer.extent()
        planned: list[tuple] = []
        plan = canvas_cartograph.plan_template

        def recording(template_id, data):
            result = plan(template_id, data)
            if template_id == "dashboard":
                texts = {
                    i.id: i.body
                    for i in result.items
                    if getattr(i, "id", "").startswith("figure-")
                }
                planned.append((tuple(data.figures), texts))
            return result

        monkeypatch.setattr(canvas_cartograph, "plan_template", recording)
        flat = _Renderer()

        def render(
            template_id, page_size, orientation, width_px, figures=(), sizes=None
        ):
            if template_id != "dashboard":
                return flat(template_id, page_size, orientation, width_px)
            return canvas_cartograph.template_preview(
                view,
                UTM,
                [layer],
                figures_project,
                template_id=template_id,
                width_px=width_px,
                page_size=page_size,
                orientation=orientation,
                figures=figures,
            )

        picker = TemplatePicker(
            render, qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            _type(picker, 0, "214", "parcels surveyed")
            _type(picker, 1, "", "a label alone")
            _type(picker, 2, "12 km")
            _typed(qgis_app, picker)
            figures, texts = planned[-1]
            assert figures == (
                ("214", "parcels surveyed"),
                ("", "a label alone"),
                ("12 km", ""),
            )
            # The value alone is drawn; the label alone is left out.
            assert texts == {
                "figure-0": "214",
                "figure-0-label": "parcels surveyed",
                "figure-1": "12 km",
            }
            assert picker.rows["dashboard"].drawn
        finally:
            picker.reject()

    def test_creating_saves_them_to_the_project(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        from nika_onlymap_exporter.core.canvas_cartograph import project_figures

        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _type(picker, 0, "214", "parcels surveyed")
            _type(picker, 2, "12 km")
            picker.create_button.click()
            assert picker.result() == 1
            variables = figures_project.customVariables()
            assert variables["nika_figure_1"] == "214 | parcels surveyed"
            assert "nika_figure_2" not in variables
            assert variables["nika_figure_3"] == "12 km"
            assert project_figures(figures_project) == (
                ("214", "parcels surveyed"),
                ("12 km", ""),
            )
        finally:
            picker.reject()

    def test_a_cleared_row_removes_its_variable(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        figures_project.setCustomVariables(
            {"nika_figure_1": "214 | parcels surveyed", "nika_figure_2": "9 | wells"}
        )
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _type(picker, 0, "", "")
            picker.accept()
            variables = figures_project.customVariables()
            assert "nika_figure_1" not in variables
            assert variables["nika_figure_2"] == "9 | wells"
        finally:
            picker.reject()

    def test_a_value_alone_is_kept_a_label_alone_is_not_drawn(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        """A value without a label is a figure; a label without a value is
        kept for the user to finish, but never reaches the page."""
        from nika_onlymap_exporter.core.canvas_cartograph import project_figures
        from nika_onlymap_exporter.core.layout_templates import (
            TemplateInput,
            plan_template,
        )

        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _type(picker, 0, "12 km")
            _type(picker, 1, "", "a label alone")
            figures = picker.figures
            picker.accept()
            variables = figures_project.customVariables()
            assert variables["nika_figure_1"] == "12 km"
            assert variables["nika_figure_2"] == "| a label alone"
            assert project_figures(figures_project) == (("12 km", ""),)
            plan = plan_template(
                "dashboard",
                TemplateInput(
                    extent_width=1000.0,
                    extent_height=700.0,
                    crs_is_geographic=False,
                    figures=figures,
                ),
            )
            texts = {
                i.id: i.body
                for i in plan.items
                if getattr(i, "id", "").startswith("figure-")
            }
            assert texts == {"figure-0": "12 km"}
        finally:
            picker.reject()

    def test_cancel_saves_nothing(self, qgis_app, tmp_path, fifteen, figures_project):
        figures_project.setCustomVariables({"nika_figure_1": "214 | parcels"})
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _type(picker, 0, "999", "")
            picker.cancel_button.click()
            assert figures_project.customVariables() == {
                "nika_figure_1": "214 | parcels"
            }
        finally:
            picker.reject()

    def test_unchanged_figures_leave_the_project_untouched(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        """Nothing typed, nothing written: not even a variable stored in a
        looser form than ours is rewritten."""
        figures_project.setCustomVariables({"nika_figure_1": "214|parcels"})
        figures_project.setDirty(False)
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            picker.accept()
            assert not figures_project.isDirty()
            assert figures_project.customVariables() == {"nika_figure_1": "214|parcels"}
        finally:
            picker.reject()

    def test_the_chosen_card_stays_in_view_as_the_fields_appear(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _shown(qgis_app, picker, QSize(860, 560))
            _drain(qgis_app, picker)
            item = picker._items["dashboard"]
            view = picker.template_list
            view.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtBottom)
            qgis_app.processEvents()
            assert view.viewport().rect().contains(view.visualItemRect(item))
            _select(picker, "dashboard")
            for _ in range(3):
                qgis_app.processEvents()
            assert not picker.figures_group.isHidden()
            assert view.viewport().rect().contains(view.visualItemRect(item))
        finally:
            picker.reject()

    def test_opening_on_signal_in_a_small_dialog_shows_its_card(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        settings = _settings(tmp_path)
        settings.setValue(template_picker.SETTINGS_KEY, "dashboard|A4|auto")
        picker = TemplatePicker(
            _Renderer(), qsettings=settings, project=figures_project
        )
        try:
            _shown(qgis_app, picker, QSize(640, 480))
            _drain(qgis_app, picker)
            view = picker.template_list
            card = view.visualItemRect(picker._items["dashboard"])
            assert view.viewport().rect().contains(card)
        finally:
            picker.reject()

    def test_they_come_back_when_the_picker_opens_again(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        first = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(first, "dashboard")
            _shown(qgis_app, first)
            _type(first, 0, "214", "parcels surveyed")
            _type(first, 1, "3")
            first.accept()
        finally:
            first.reject()
        again = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            # The remembered design is Signal, and its fields are filled.
            assert again.template_id == "dashboard"
            assert not again.figures_group.isHidden()
            assert again.figures == (
                ("214", "parcels surveyed"),
                ("3", ""),
                ("", ""),
            )
        finally:
            again.reject()

    def test_a_bar_cannot_be_typed_into_a_value(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _type(picker, 0, "1|2", "a | b")
            assert picker.figures[0] == ("12", "a | b")
        finally:
            picker.reject()

    def test_tab_runs_list_figures_sizes_buttons(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            picker.activateWindow()
            picker.template_list.setFocus(Qt.FocusReason.TabFocusReason)
            qgis_app.processEvents()
            seen = []
            for _ in range(8):
                QTest.keyClick(picker.focusWidget(), Qt.Key.Key_Tab)
                qgis_app.processEvents()
                seen.append(picker.focusWidget())
            group = picker.figures_group
            fields = [edit for pair in zip(group.values, group.labels) for edit in pair]
            # The text sizes are closed: their header, not the sliders.
            toggle = picker.size_controls.toggle
            assert seen == [*fields, toggle, picker.create_button]
            for edit in fields:
                assert edit.accessibleName().startswith("Key figure ")
        finally:
            picker.reject()

    @pytest.mark.parametrize("screen", [(860, 560), (640, 480)])
    def test_signal_with_its_figures_fits_a_small_dialog(
        self, qgis_app, tmp_path, fifteen, figures_project, screen
    ):
        """With the fields showing, the dialog still fits, the list keeps
        room for a card, and every field is wide enough to type in."""
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=figures_project
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker, QSize(*screen))
            needed = picker.minimumSizeHint()
            assert needed.width() <= screen[0]
            assert needed.height() <= screen[1]
            group = picker.figures_group
            assert (
                group.geometry().bottom()
                < picker.create_button.mapTo(
                    picker, picker.create_button.rect().topLeft()
                ).y()
            )
            assert picker.template_list.height() >= 140
            for edit in [*group.values, *group.labels]:
                assert edit.width() >= 48
                assert edit.mapTo(group, edit.rect().topRight()).x() < group.width()
            note = group.note
            assert note.height() >= note.heightForWidth(note.width())
            # The chosen card is in the list's view.
            card = picker.template_list.visualItemRect(picker._items["dashboard"])
            assert card.intersects(picker.template_list.viewport().rect())
        finally:
            picker.reject()

    def test_right_to_left_mirrors_the_fields(
        self, qgis_app, tmp_path, fifteen, figures_project
    ):
        host = QWidget()
        host.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        picker = TemplatePicker(
            _Renderer(),
            qsettings=_settings(tmp_path),
            project=figures_project,
            parent=host,
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            group = picker.figures_group
            assert group.isRightToLeft()
            # The first figure starts on the right, its value before its label.
            assert group.values[0].x() > group.labels[0].x()
            assert group.labels[0].x() > group.values[1].x()
        finally:
            picker.reject()
            host.deleteLater()


# ---- What a card says it includes ------------------------------------------

_PROJECT = layout_templates.TemplateInput(
    extent_width=4000.0,
    extent_height=2600.0,
    crs_is_geographic=False,
    title="Kranji parcels",
    layer_names=("Parcels",),
    legend_labels=(("Housing", "Parkland"),),
)


def _planner(data, calls: list | None = None):
    """The controller's planner for a project described by `data`."""

    def plan(template_id, page_size, orientation, *, figures, sizes=None):
        if calls is not None:
            calls.append((template_id, page_size, orientation, figures))
        return layout_templates.plan_template(
            template_id,
            (sizes or TextSizes()).apply(
                dataclasses.replace(
                    data,
                    page_size=page_size,
                    orientation=orientation,
                    figures=tuple(figures),
                )
            ),
        )

    return plan


class TestTheCues:
    """Each card checked against the page this project gets (the real
    registry and planner): what the page shows, what it could."""

    def test_a_card_includes_what_this_page_has_and_offers_the_rest(
        self, qgis_app, tmp_path
    ):
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), planner=_planner(_PROJECT)
        )
        try:
            marginalia = picker.rows["marginalia"]
            assert marginalia.includes.text() == "Includes: legend, scale bar"
            assert marginalia.supports.text() == "Can also show: notes, source line"
            tip = marginalia.supports.toolTip().splitlines()
            assert tip[0] == "notes: From Project > Properties > Metadata > Abstract."
            assert tip[1].startswith("source line: From each layer's attribution")
            ledger = picker.rows["atlas-ledger"]
            assert "sheet number" in ledger.supports.text()
            assert "sheet number" not in ledger.includes.text()
            assert "atlas" in ledger.supports.toolTip()
            signal = picker.rows["dashboard"]
            assert "key figures" in signal.supports.text()
            assert "Key figures fields" in signal.supports.toolTip()
            # Always on the page: never offered.
            assert (
                picker.rows["locator"].includes.text().startswith("Includes: locator")
            )
        finally:
            picker.reject()

    def test_with_notes_and_credits_they_are_included(self, qgis_app, tmp_path):
        data = dataclasses.replace(
            _PROJECT, notes="Surveyed in September.", credits=("© OSM",)
        )
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), planner=_planner(data)
        )
        try:
            marginalia = picker.rows["marginalia"]
            assert marginalia.includes.text() == (
                "Includes: notes, legend, scale bar, source line"
            )
            assert marginalia.supports.isHidden()
            spoken = picker._items["marginalia"].data(
                Qt.ItemDataRole.AccessibleDescriptionRole
            )
            assert spoken == "Includes: notes, legend, scale bar, source line"
        finally:
            picker.reject()

    def test_typed_figures_move_key_figures_into_includes(
        self, qgis_app, tmp_path, figures_project
    ):
        picker = TemplatePicker(
            _Renderer(),
            qsettings=_settings(tmp_path),
            project=figures_project,
            planner=_planner(_PROJECT),
        )
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            signal = picker.rows["dashboard"]
            assert "key figures" not in signal.includes.text()
            _type(picker, 0, "214", "parcels surveyed")
            _typed(qgis_app, picker)
            assert signal.includes.text().startswith("Includes: key figures")
            assert "key figures" not in signal.supports.text()
            spoken = picker._items["dashboard"].data(
                Qt.ItemDataRole.AccessibleDescriptionRole
            )
            assert spoken.startswith("Includes: key figures")
            # A label alone is no figure: offered again.
            _type(picker, 0, "", "parcels surveyed")
            _typed(qgis_app, picker)
            assert "key figures" in signal.supports.text()
        finally:
            picker.reject()

    def test_each_page_setting_is_planned_once(self, qgis_app, tmp_path):
        calls: list = []
        picker = TemplatePicker(
            _Renderer(),
            qsettings=_settings(tmp_path),
            planner=_planner(_PROJECT, calls),
        )
        try:
            count = len(layout_templates.TEMPLATES)
            assert len(calls) == count
            assert {c[1] for c in calls} == {"A4"}
            picker.page_combo.setCurrentIndex(picker.page_combo.findData("A5"))
            assert len(calls) == 2 * count
            picker.page_combo.setCurrentIndex(picker.page_combo.findData("A4"))
            assert len(calls) == 2 * count
        finally:
            picker.reject()

    def test_a_page_the_planner_cannot_lay_out_keeps_the_safe_cues(
        self, qgis_app, tmp_path
    ):
        def failing(template_id, page_size, orientation, *, figures, sizes=None):
            raise ValueError("no")

        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), planner=failing
        )
        try:
            marginalia = picker.rows["marginalia"]
            assert marginalia.includes.text() == "Includes: scale bar"
            assert "notes" in marginalia.supports.text()
        finally:
            picker.reject()


# ---- What a preview depends on ----------------------------------------------


class TestACrowdedLegend:
    """A page whose legend left entries out to stay readable says so on its
    card; the others say nothing."""

    def test_the_card_counts_what_was_left_out(self, qgis_app, tmp_path):
        render = _Renderer(left_out={"classic": 3, "dark": 1})
        picker = TemplatePicker(render, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker, QSize(1100, 760))
            for template_id in ("classic", "dark", "legend-below"):
                _select(picker, template_id)
                _drain(qgis_app, picker)
            crowded = picker.rows["classic"].crowded
            assert crowded.isVisibleTo(picker)
            assert crowded.text().startswith("Legend: 3 entries do not fit")
            assert (
                picker.rows["dark"]
                .crowded.text()
                .startswith("Legend: 1 entry does not fit")
            )
            assert not picker.rows["legend-below"].crowded.isVisibleTo(picker)
        finally:
            picker.reject()

    def test_the_warning_goes_once_the_page_fits(self, qgis_app, tmp_path):
        render = _Renderer(left_out={"classic": 2})
        picker = TemplatePicker(render, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            _select(picker, "classic")
            _drain(qgis_app, picker)
            row = picker.rows["classic"]
            assert row.crowded.isVisibleTo(picker)
            image = QImage(80, 56, QImage.Format.Format_ARGB32)
            image.setText(PREVIEW_LEGEND_LEFT_OUT_KEY, "0")
            assert row.show_page(image)
            assert not row.crowded.isVisibleTo(picker)
            assert row.crowded.text() == ""
        finally:
            picker.reject()


class TestThePreviewKey:
    """`canvas_cartograph.preview_state_key`, as the controller passes it:
    the same for the same canvas and project, so the next picker shows the
    pages already drawn, and new after any change a preview would show."""

    @pytest.fixture
    def view(self, project):
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsFeature,
            QgsGeometry,
            QgsRectangle,
            QgsVectorLayer,
        )

        from nika_onlymap_exporter.core.canvas_cartograph import LayerRevisions

        layers = []
        for name in ("parks", "roads"):
            layer = QgsVectorLayer(
                "Polygon?crs=EPSG:4326&field=kind:string", name, "memory"
            )
            feature = QgsFeature(layer.fields())
            feature.setGeometry(
                QgsGeometry.fromRect(QgsRectangle(103.7, 1.25, 103.9, 1.4))
            )
            feature.setAttribute(0, "park")
            layer.dataProvider().addFeatures([feature])
            layer.updateExtents()
            project.addMapLayer(layer)
            layers.append(layer)
        project.setTitle("Parks")
        project.setCustomVariables({})
        state = types.SimpleNamespace(
            project=project,
            layers=layers,
            extent=QgsRectangle(103.7, 1.25, 103.9, 1.4),
            crs=QgsCoordinateReferenceSystem("EPSG:4326"),
            rotation=0.0,
            revisions=LayerRevisions(),
        )
        yield state
        project.setCustomVariables({})

    @staticmethod
    def _key(view) -> str:
        from nika_onlymap_exporter.core.canvas_cartograph import preview_state_key

        return preview_state_key(
            view.extent,
            view.crs,
            view.layers,
            view.project,
            rotation=view.rotation,
            revisions=view.revisions.track(view.layers),
        )

    def test_unchanged_is_the_same_key(self, qgis_app, view):
        assert self._key(view) == self._key(view)

    @staticmethod
    def _restyle(view):
        from qgis.PyQt.QtGui import QColor as Colour

        symbol = view.layers[0].renderer().symbol()
        symbol.setColor(Colour("#ff00ff"))
        view.layers[0].triggerRepaint()

    @staticmethod
    def _edit(view):
        layer = view.layers[0]
        layer.startEditing()
        fid = next(layer.getFeatures()).id()
        layer.changeAttributeValue(fid, 0, "lake")
        layer.commitChanges()

    @staticmethod
    def _reproject(view):
        from qgis.core import QgsCoordinateReferenceSystem

        view.crs = QgsCoordinateReferenceSystem("EPSG:3857")

    @staticmethod
    def _abstract(view):
        metadata = view.project.metadata()
        metadata.setAbstract("Parks of the west.")
        view.project.setMetadata(metadata)

    @staticmethod
    def _author(view):
        metadata = view.project.metadata()
        metadata.setAuthor("Field team")
        view.project.setMetadata(metadata)

    @pytest.mark.parametrize(
        "change",
        [
            pytest.param(_restyle, id="style"),
            pytest.param(lambda v: v.layers[0].setOpacity(0.5), id="opacity"),
            pytest.param(lambda v: v.layers.pop(), id="layer toggled off"),
            pytest.param(lambda v: v.layers.reverse(), id="layer order"),
            pytest.param(lambda v: v.layers[0].setName("lakes"), id="layer renamed"),
            pytest.param(
                lambda v: v.layers[0].setSubsetString("kind = 'park'"), id="filter"
            ),
            pytest.param(_edit, id="feature edited"),
            pytest.param(lambda v: v.extent.scale(0.5), id="extent"),
            pytest.param(lambda v: setattr(v, "rotation", 15.0), id="rotation"),
            pytest.param(_reproject, id="crs"),
            pytest.param(lambda v: v.project.setTitle("Lakes"), id="title"),
            pytest.param(_abstract, id="abstract"),
            pytest.param(_author, id="author"),
            pytest.param(
                lambda v: v.project.setCustomVariables({"nika_figure_1": "214"}),
                id="figures",
            ),
        ],
    )
    def test_each_change_a_preview_shows_is_a_new_key(self, qgis_app, view, change):
        before = self._key(view)
        change(view)
        assert self._key(view) != before


class TestEachOpening:
    def test_the_picker_is_deleted_once_answered(self, qgis_app, monkeypatch):
        """A child of the plugin's dialog, a picker left alive kept its cards
        and pages until that dialog closed: about 15 MB an opening on a
        real project."""
        from qgis.PyQt.QtCore import QCoreApplication, QEvent
        from qgis.PyQt.QtWidgets import QDialog

        monkeypatch.setattr(
            TemplatePicker, "exec", lambda self: QDialog.DialogCode.Rejected
        )
        host = QWidget()
        try:
            for _ in range(3):
                assert template_picker.ask_template(_Renderer(), parent=host) is None
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert host.findChildren(TemplatePicker) == []
        finally:
            host.deleteLater()


class TestOpeningLeavesTheProject:
    def test_cancelling_after_every_thumbnail_leaves_it_clean(
        self, qgis_app, tmp_path, project
    ):
        """Real QGIS pages for every design: the project is not marked
        changed (no save prompt after Cancel) and gains no layout."""
        from cartograph_fixtures import UTM, build_parcels_layer

        from nika_onlymap_exporter.core import canvas_cartograph

        layer = build_parcels_layer()
        project.addMapLayer(layer)
        project.setDirty(False)
        layouts = list(project.layoutManager().layouts())

        def render(
            template_id, page_size, orientation, width_px, figures=(), sizes=None
        ):
            return canvas_cartograph.template_preview(
                layer.extent(),
                UTM,
                [layer],
                project,
                template_id=template_id,
                width_px=width_px,
                page_size=page_size,
                orientation=orientation,
                figures=figures,
            )

        picker = TemplatePicker(render, qsettings=_settings(tmp_path), project=project)
        try:
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            for template_id in picker._templates:
                key = picker._key(template_id)
                if key not in picker._pages:
                    picker._pages.put(key, render(template_id, key[1], key[2], 120))
            assert picker.rows[picker.template_id].drawn
        finally:
            picker.reject()
        assert not project.isDirty()
        assert list(project.layoutManager().layouts()) == layouts


class _Pending(QObject):
    """A page drawn in the background: reports after `delay_ms` of the event
    loop running, the way `PendingPreview` does. Never blocks."""

    finished = pyqtSignal(object)

    def __init__(self, width_px: int, delay_ms: int) -> None:
        super().__init__()
        self.cancelled = False
        self._width = width_px
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._done)
        self._timer.start(delay_ms)

    def _done(self) -> None:
        image = QImage(
            self._width, round(self._width / 1.414), QImage.Format.Format_ARGB32
        )
        image.fill(QColor("#336699"))
        self.finished.emit(image)

    def cancel(self) -> None:
        self.cancelled = True
        self._timer.stop()


class _SlowRenderer:
    def __init__(self, delay_ms: int = 150) -> None:
        self.delay_ms = delay_ms
        self.started: list[tuple[str, str]] = []
        self.pending: list[_Pending] = []

    def __call__(
        self, template_id, page_size, orientation, width_px, figures=(), sizes=None
    ):
        self.started.append((template_id, page_size))
        pending = _Pending(width_px, self.delay_ms)
        self.pending.append(pending)
        return pending


class TestBackgroundPages:
    def test_the_picker_keeps_running_while_a_page_draws(
        self, qgis_app, tmp_path, fifteen
    ):
        """No step of a full pass holds the event loop: a heartbeat timer
        never waits long, and the cards still fill in."""
        renderer = _SlowRenderer(delay_ms=120)
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        gaps: list[float] = []
        last = [time.monotonic()]

        def beat() -> None:
            now = time.monotonic()
            gaps.append(now - last[0])
            last[0] = now

        heartbeat = QTimer()
        heartbeat.timeout.connect(beat)
        heartbeat.start(10)
        try:
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            for _ in range(20):
                qgis_app.processEvents()
            assert picker.rows[picker.template_id].drawn
            assert len(renderer.started) > 1
            # Pages took >100 ms each, in sequence, yet no gap came close.
            assert max(gaps) < 0.1
        finally:
            heartbeat.stop()
            picker.reject()

    def test_pages_draw_one_at_a_time_selected_first(self, qgis_app, tmp_path, fifteen):
        renderer = _SlowRenderer(delay_ms=30)
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            qgis_app.processEvents()
            assert [t for t, _s in renderer.started] == ["classic"]
            _drain(qgis_app, picker)
            assert renderer.started[0][0] == picker.template_id
        finally:
            picker.reject()

    def test_closing_cancels_the_page_in_flight(self, qgis_app, tmp_path, fifteen):
        renderer = _SlowRenderer(delay_ms=5000)
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        _shown(qgis_app, picker)
        qgis_app.processEvents()
        (pending,) = renderer.pending
        picker.reject()
        assert pending.cancelled

    def test_a_newer_request_cancels_the_obsolete_page(
        self, qgis_app, tmp_path, fifteen
    ):
        """Changing the page size makes the page being drawn the wrong one:
        it is cancelled and the design is drawn again for the new size."""
        renderer = _SlowRenderer(delay_ms=5000)
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            qgis_app.processEvents()
            first = renderer.pending[0]
            other = picker.page_combo.findData("A3")
            picker.page_combo.setCurrentIndex(other)
            for _ in range(5):
                qgis_app.processEvents()
            assert first.cancelled
            assert renderer.started[-1] == ("classic", "A3")
        finally:
            picker.reject()
