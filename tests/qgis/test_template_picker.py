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
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QSlider,
    QToolButton,
    QWidget,
)

from nika_onlymap_exporter.core import layout_templates
from nika_onlymap_exporter.core.canvas_cartograph import PREVIEW_LEGEND_LEFT_OUT_KEY
from nika_onlymap_exporter.core.layout_templates import CHARACTERFUL, ESSENTIAL
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
    monkeypatch.setattr(
        layout_templates, "starter_templates", lambda: layout_templates.TEMPLATES
    )
    return {t.id: t for t in templates}


class _Renderer:
    """Flat pages, recording every call; ids in `fail` raise."""

    def __init__(self, fail=(), tall=False, left_out=None) -> None:
        self.calls: list[tuple[str, str, str, int]] = []
        self.fail = set(fail)
        self.tall = tall
        #: Legend entries each design's page leaves out, as the real
        #: `template_preview` records them on the image.
        self.left_out: dict[str, int] = dict(left_out or {})

    def __call__(self, template_id, page_size, orientation, width_px):
        self.calls.append((template_id, page_size, orientation, width_px))
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
    """Essentials, then characterful designs, then the two dark sheets."""
    return sorted(
        templates,
        key=lambda t: (t in {"dark", "night-desk"}, templates[t].category != ESSENTIAL),
    )


def _settings(tmp_path) -> QSettings:
    tmp_path.mkdir(exist_ok=True)
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


class TestTheMouse:
    def test_a_single_click_selects_and_keeps_the_picker_open(
        self, qgis_app, tmp_path, fifteen
    ) -> None:
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            picker.show()
            qgis_app.processEvents()
            row = picker.rows["map-only"]
            QTest.mouseClick(
                row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
            )
            qgis_app.processEvents()
            assert picker.isVisible()
            assert picker.result() == 0
            assert picker.template_id == "map-only"
        finally:
            picker.reject()

    def test_a_double_click_accepts_the_card(self, qgis_app, tmp_path, fifteen) -> None:
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            picker.show()
            qgis_app.processEvents()
            QTest.mouseDClick(
                picker.rows["map-only"],
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            assert picker.result() == QDialog.DialogCode.Accepted
            assert picker.template_id == "map-only"
        finally:
            picker.reject()


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
            listed = list(picker.rows)
            assert listed == [
                "classic",
                "legend-below",
                "map-only",
                "report-figure",
                "field-map",
                "poster",
                "locator",
                "dashboard",
                "wayfinder",
                "dark",
                "night-desk",
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
            assert picker.template_list.count() == 16  # designs plus request action
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
                picker.framing_combo,
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
            assert other.name.geometry().right() == other.text.rect().right()
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


class TestTheContentControlsAreGone:
    """The picker chooses a design, a page size, an orientation and framing.
    What goes on the page -- text sizes, a Signal page's statistic callouts --
    is edited on the layout afterwards, so none of it is asked here."""

    def test_no_key_figures_or_text_size_widgets(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            assert not hasattr(picker, "figures_group")
            assert not hasattr(picker, "size_controls")
            assert picker.findChildren(QSlider) == []
            assert picker.findChildren(QLineEdit) == []
            named = [
                w.accessibleName()
                for w in picker.findChildren(QWidget)
                if w.accessibleName()
            ]
            assert not [n for n in named if n.startswith(("Key figure", "Text size"))]
            texts = [b.text() for b in picker.findChildren(QToolButton)]
            assert not [t for t in texts if t.startswith("Text sizes")]
        finally:
            picker.reject()

    def test_the_footer_is_create_and_cancel(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _shown(qgis_app, picker)
            buttons = picker.findChildren(QDialogButtonBox)
            assert len(buttons) == 1
            assert [b.text() for b in buttons[0].buttons()] == [
                "Create layout",
                "Cancel",
            ]
            foot = picker.create_button.parentWidget().geometry()
            # Only the choice in words shares the line with the two buttons.
            beside = [
                w
                for w in picker.findChildren(QWidget)
                if w.isVisibleTo(picker)
                and w.parentWidget() is picker
                and abs(w.geometry().center().y() - foot.center().y()) < foot.height()
                and w is not buttons[0]
            ]
            assert beside == [picker.selected_label]
        finally:
            picker.reject()

    def test_the_renderer_is_asked_only_for_the_page(self, qgis_app, tmp_path, fifteen):
        """No figures or sizes reach the preview: the stand-in renderer takes
        four arguments, so any extra would raise and fail the card."""
        renderer = _Renderer()
        picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            assert "dashboard" in renderer.ids
            assert picker._failed == set()
        finally:
            picker.reject()

    def test_older_project_values_are_left_as_they_were(
        self, qgis_app, tmp_path, fifteen, project
    ):
        before = {
            "nika_figure_1": "214 | parcels",
            "nika_size_title": "90",
            "nika_size_notes": "130",
        }
        project.setCustomVariables(dict(before))
        picker = TemplatePicker(
            _Renderer(), qsettings=_settings(tmp_path), project=project
        )
        try:
            _select(picker, "dashboard")
            picker.accept()
        finally:
            picker.reject()
        variables = project.customVariables()
        assert {k: variables[k] for k in before} == before
        project.setCustomVariables({})

    def test_signal_says_what_its_callouts_are(self, qgis_app, tmp_path):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            signal = picker.rows["dashboard"]
            text = " ".join(
                label.text() for label in signal.findChildren(QLabel) if label.text()
            )
            assert "optional statistic callouts" in text
            assert "Edit tab" in text
            assert "statistic callouts" in signal.supports.text()
        finally:
            picker.reject()

    def test_opening_on_signal_in_a_small_dialog_shows_its_card(
        self, qgis_app, tmp_path, fifteen
    ):
        settings = _settings(tmp_path)
        settings.setValue(template_picker.SETTINGS_KEY, "dashboard|A4|auto")
        picker = TemplatePicker(_Renderer(), qsettings=settings)
        try:
            _shown(qgis_app, picker, QSize(640, 480))
            _drain(qgis_app, picker)
            view = picker.template_list
            card = view.visualItemRect(picker._items["dashboard"])
            assert view.viewport().rect().contains(card)
        finally:
            picker.reject()

    @pytest.mark.parametrize("font_scale", [1.0, 1.25])
    def test_big_fonts_keep_the_cards(self, qgis_app, tmp_path, fifteen, font_scale):
        """Larger text widens the controls; the page keeps its share of the
        card and a card still fits the list."""
        original = qgis_app.font()
        scaled = qgis_app.font()
        if original.pointSizeF() > 0:
            scaled.setPointSizeF(original.pointSizeF() * font_scale)
        else:
            scaled.setPixelSize(round(original.pixelSize() * font_scale))
        if font_scale != 1.0:
            qgis_app.setFont(scaled)
        try:
            settings = _settings(tmp_path)
            settings.setValue(template_picker.SETTINGS_KEY, "dashboard|A4|auto")
            small = TemplatePicker(_Renderer(), qsettings=settings)
            try:
                _shown(qgis_app, small, QSize(640, 480))
                _drain(qgis_app, small)
                view = small.template_list
                card = view.visualItemRect(small._items["dashboard"])
                assert view.viewport().rect().contains(card)
            finally:
                small.reject()
            picker = TemplatePicker(
                _Renderer(), qsettings=_settings(tmp_path / "second")
            )
            try:
                _shown(qgis_app, picker, QSize(700, 480))
                _drain(qgis_app, picker)
                row = picker.rows["classic"]
                assert row.preview.width() >= 0.4 * row.width()
            finally:
                picker.reject()
        finally:
            qgis_app.setFont(original)

    def test_tab_runs_list_request_card_buttons(self, qgis_app, tmp_path, fifteen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker)
            picker.activateWindow()
            picker.template_list.setFocus(Qt.FocusReason.TabFocusReason)
            qgis_app.processEvents()
            seen = []
            for _ in range(2):
                QTest.keyClick(picker.focusWidget(), Qt.Key.Key_Tab)
                qgis_app.processEvents()
                seen.append(picker.focusWidget())
            assert seen == [picker.request_button, picker.create_button]
        finally:
            picker.reject()

    @pytest.mark.parametrize("screen", [(860, 560), (640, 480)])
    def test_signal_fits_a_small_dialog(self, qgis_app, tmp_path, fifteen, screen):
        picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
        try:
            _select(picker, "dashboard")
            _shown(qgis_app, picker, QSize(*screen))
            needed = picker.minimumSizeHint()
            assert needed.width() <= screen[0]
            assert needed.height() <= screen[1]
            assert picker.template_list.height() >= 140
            card = picker.template_list.visualItemRect(picker._items["dashboard"])
            assert card.intersects(picker.template_list.viewport().rect())
        finally:
            picker.reject()


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

    def plan(template_id, page_size, orientation):
        if calls is not None:
            calls.append((template_id, page_size, orientation))
        return layout_templates.plan_template(
            template_id,
            dataclasses.replace(data, page_size=page_size, orientation=orientation),
        )

    return plan


@pytest.fixture
def all_designs(monkeypatch):
    """Exercise content cues independently of the curated starter subset."""
    monkeypatch.setattr(
        layout_templates, "starter_templates", lambda: layout_templates.TEMPLATES
    )


@pytest.mark.usefixtures("all_designs")
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
            assert "statistic callouts" in signal.supports.text()
            assert "Edit tab" in signal.supports.toolTip()
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
        def failing(template_id, page_size, orientation):
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

        def render(template_id, page_size, orientation, width_px):
            return canvas_cartograph.template_preview(
                layer.extent(),
                UTM,
                [layer],
                project,
                template_id=template_id,
                width_px=width_px,
                page_size=page_size,
                orientation=orientation,
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
    def __init__(self, delay_ms: int = 150, only_first: bool = False) -> None:
        self.delay_ms = delay_ms
        #: Only the first page is slow; the rest draw at once.
        self.only_first = only_first
        self.started: list[tuple[str, str]] = []
        self.pending: list[_Pending] = []

    def __call__(self, template_id, page_size, orientation, width_px):
        self.started.append((template_id, page_size))
        pending = _Pending(
            width_px, 10 if self.only_first and self.pending else self.delay_ms
        )
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


class TestMapFraming:
    def test_trying_whole_view_uses_a_distinct_preview_and_cancel_leaves_no_setting(
        self, qgis_app, tmp_path, project
    ):
        from nika_onlymap_exporter.core.template_framing import (
            FRAMING_VARIABLE,
            project_framing,
        )

        modes = []
        renderer = _Renderer()

        def render(*args, **kwargs):
            modes.append(project_framing(project))
            return renderer(*args, **kwargs)

        project.setDirty(False)
        picker = TemplatePicker(render, qsettings=_settings(tmp_path), project=project)
        try:
            _shown(qgis_app, picker)
            _drain(qgis_app, picker)
            filled_key = picker._key(picker.template_id)
            assert modes and set(modes) == {"fill"}
            modes.clear()
            picker.framing_combo.setCurrentIndex(picker.framing_combo.findData("whole"))
            _drain(qgis_app, picker)
            assert picker._key(picker.template_id) != filled_key
            assert modes and set(modes) == {"whole"}
        finally:
            picker.reject()
        assert FRAMING_VARIABLE not in project.customVariables()
        assert not project.isDirty()

    @pytest.mark.parametrize("mode", ["fill", "whole"])
    def test_create_remembers_the_framing_for_the_builder_and_next_picker(
        self, qgis_app, tmp_path, project, mode
    ):
        from nika_onlymap_exporter.core.template_framing import project_framing

        settings = _settings(tmp_path)
        picker = TemplatePicker(_Renderer(), qsettings=settings, project=project)
        picker.framing_combo.setCurrentIndex(picker.framing_combo.findData(mode))
        picker.accept()
        assert project_framing(project) == mode
        reopened = TemplatePicker(_Renderer(), qsettings=settings, project=project)
        try:
            assert reopened.framing_combo.currentData() == mode
        finally:
            reopened.reject()
