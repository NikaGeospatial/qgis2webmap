"""Live count: what a counter counts, worked out from a small dataset.

Every expectation is computed from the raw site list in
`live_count_fixtures.SITES`, never from QGIS's own answer. The counter follows
a map frame, so each test changes the frame (or its layers, filter or
renderer) the way a user would and reads the number the page would show.

Needs PyQGIS; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from live_count_fixtures import (
    SITES,
    add_counter,
    build_page,
    count_of,
    count_sites,
)

pytest.importorskip("qgis.core")
from qgis.core import (
    QgsFeatureRequest,
    QgsLayerTreeModel,
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsMapLayerStyle,
    QgsMapThemeCollection,
    QgsMarkerSymbol,
    QgsRectangle,
    QgsRuleBasedRenderer,
)
from qgis.PyQt.QtCore import QRectF, QSize, Qt

from nika_onlymap_exporter.core import live_count as lc

DISPLAY = Qt.ItemDataRole.DisplayRole


@pytest.fixture
def page(project):
    return build_page(project)


def _ok(outcome, expected):
    assert outcome.state is lc.CountState.OK, outcome.message
    assert outcome.count == expected


class TestWhatItCounts:
    def test_all_features_of_the_layer_in_view(self, page) -> None:
        number = add_counter(page)
        _ok(count_of(page, number), 30)
        assert count_of(page, number).text == "30"

    def test_matching_a_text_value(self, page) -> None:
        number = add_counter(page, mode=lc.MODE_VALUE, field="status", value="Active")
        _ok(count_of(page, number), count_sites(status="Active"))
        assert count_sites(status="Active") == 17

    def test_null_empty_text_and_the_word_null_are_three_different_values(
        self, page
    ) -> None:
        null = add_counter(page, mode=lc.MODE_VALUE, field="status", null=True)
        empty = add_counter(page, mode=lc.MODE_VALUE, field="status", value="")
        word = add_counter(page, mode=lc.MODE_VALUE, field="status", value="NULL")
        _ok(count_of(page, null), 2)
        _ok(count_of(page, empty), 1)
        _ok(count_of(page, word), 0)

    def test_matching_a_number(self, page) -> None:
        five = add_counter(page, mode=lc.MODE_VALUE, field="cap", value="5")
        spelt = add_counter(page, mode=lc.MODE_VALUE, field="cap", value="5.0")
        none = add_counter(page, mode=lc.MODE_VALUE, field="cap", null=True)
        _ok(count_of(page, five), count_sites(cap=5))
        _ok(count_of(page, spelt), count_sites(cap=5))
        _ok(count_of(page, none), count_sites(cap=None))

    def test_a_value_that_is_not_a_number_explains_itself(self, page) -> None:
        number = add_counter(page, mode=lc.MODE_VALUE, field="cap", value="lots")
        outcome = count_of(page, number)
        assert outcome.state is lc.CountState.INVALID
        assert outcome.text == "\N{EM DASH}"
        assert "lots" in outcome.message and "cap" in outcome.message


class TestWhatTheFrameShows:
    def test_a_smaller_area_counts_only_what_it_shows(self, page) -> None:
        number = add_counter(page)
        page.frame.zoomToExtent(QgsRectangle(-1, -1, 5, 5))
        shown = page.frame.extent()
        _ok(count_of(page, number), count_sites(shown))
        assert 0 < count_sites(shown) < 30

    def test_the_aspect_adjusted_extent_is_what_counts(self, page) -> None:
        # A tall area in a wide frame: the frame widens it to fit.
        page.frame.zoomToExtent(QgsRectangle(1, -1, 3, 9))
        number = add_counter(page)
        shown = page.frame.extent()
        assert shown.width() > 2.5
        _ok(count_of(page, number), count_sites(shown))

    def test_a_rotated_frame_does_not_count_its_enclosing_rectangle(self, page) -> None:
        page.frame.zoomToExtent(QgsRectangle(-1, -1, 11, 9))
        page.frame.setMapRotation(45)
        number = add_counter(page)
        outcome = count_of(page, number)
        assert outcome.state is lc.CountState.OK
        # Independent count: a site is shown when its position, turned back
        # about the frame's centre, lies inside the frame's extent.
        import math

        centre = page.frame.extent().center()
        theta = math.radians(page.frame.mapRotation())
        expected = 0
        shown = page.frame.extent()
        for x, y, _s, _c in SITES:
            dx, dy = x - centre.x(), y - centre.y()
            ux = dx * math.cos(theta) - dy * math.sin(theta)
            uy = dx * math.sin(theta) + dy * math.cos(theta)
            if abs(ux) <= shown.width() / 2 and abs(uy) <= shown.height() / 2:
                expected += 1
        assert 0 < expected < 30
        assert outcome.count == expected

    def test_a_polygon_counts_when_any_part_of_it_is_shown(self, page) -> None:
        # The wide polygon's centre (x = 4.5) is far outside this view, but
        # its east end crosses it: it counts, once.
        number = add_counter(page, layer=page.squares)
        page.frame.zoomToExtent(QgsRectangle(12, 5, 14, 8))
        _ok(count_of(page, number), 1)
        page.frame.zoomToExtent(QgsRectangle(-1, -1, 11, 9))
        _ok(count_of(page, number), 2)  # middle and wide; west is outside

    def test_a_frame_with_another_extent_reports_its_own_count(self, page) -> None:
        detail = QgsLayoutItemMap(page.layout)
        page.layout.addLayoutItem(detail)
        detail.attemptSetSceneRect(QRectF(140, 40, 40, 40))
        detail.setCrs(page.frame.crs())
        detail.zoomToExtent(QgsRectangle(-0.5, -0.5, 4.5, 4.5))
        overview = add_counter(page)
        close = add_counter(page, frame=detail)
        _ok(count_of(page, overview), 30)
        _ok(count_of(page, close), count_sites(detail.extent()))
        assert count_sites(detail.extent()) < 30


class TestWhatIsSwitchedOff:
    def test_a_layer_the_frame_does_not_draw_counts_zero(self, page) -> None:
        number = add_counter(page)
        page.frame.setKeepLayerSet(True)
        page.frame.setLayers([page.squares])
        outcome = count_of(page, number)
        _ok(outcome, 0)
        assert outcome.text == "0"

    def test_a_layer_hidden_in_the_layers_panel_counts_zero(
        self, page, project
    ) -> None:
        number = add_counter(page)
        node = project.layerTreeRoot().findLayer(page.sites.id())
        node.setItemVisibilityChecked(False)
        _ok(count_of(page, number), 0)
        node.setItemVisibilityChecked(True)
        _ok(count_of(page, number), 30)

    def test_a_category_switched_off_is_not_counted(self, page) -> None:
        number = add_counter(page)
        renderer = page.sites.renderer()
        index = renderer.categoryIndexForValue("Closed")
        assert renderer.updateCategoryRenderState(index, False)
        _ok(count_of(page, number), count_sites() - count_sites(status="Closed"))

    def test_a_category_off_and_a_value_asked_for_both_apply(self, page) -> None:
        renderer = page.sites.renderer()
        renderer.updateCategoryRenderState(
            renderer.categoryIndexForValue("Closed"), False
        )
        closed = add_counter(page, mode=lc.MODE_VALUE, field="status", value="Closed")
        active = add_counter(page, mode=lc.MODE_VALUE, field="status", value="Active")
        _ok(count_of(page, closed), 0)
        _ok(count_of(page, active), 17)

    def test_rule_visibility_is_respected_and_a_feature_matching_two_rules_counts_once(
        self, page
    ) -> None:
        root = QgsRuleBasedRenderer.Rule(None)
        for label, expression in (
            ("a", "\"status\" = 'Active'"),
            ("b", '"status" = \'Active\' OR "cap" = 5'),
            ("c", "\"status\" = 'Closed'"),
        ):
            rule = QgsRuleBasedRenderer.Rule(
                QgsMarkerSymbol.createSimple({}), 0, 0, expression, label
            )
            root.appendChild(rule)
        page.sites.setRenderer(QgsRuleBasedRenderer(root))
        number = add_counter(page)
        wanted = sum(1 for _x, _y, s, c in SITES if s in ("Active", "Closed") or c == 5)
        _ok(count_of(page, number), wanted)
        # Switch rule "c" off.
        page.sites.renderer().rootRule().children()[2].setActive(False)
        wanted = sum(1 for _x, _y, s, c in SITES if s == "Active" or c == 5)
        _ok(count_of(page, number), wanted)

    def test_the_layers_filter_applies(self, page) -> None:
        number = add_counter(page)
        assert page.sites.setSubsetString("\"status\" = 'Closed'")
        _ok(count_of(page, number), count_sites(status="Closed"))

    def test_a_layer_outside_its_scale_range_counts_zero(self, page) -> None:
        number = add_counter(page)
        scale = page.frame.scale()
        page.sites.setScaleBasedVisibility(True)
        page.sites.setMaximumScale(scale * 10)  # visible only when zoomed in
        page.sites.setMinimumScale(scale * 100)
        _ok(count_of(page, number), 0)
        page.sites.setMaximumScale(scale / 10)
        _ok(count_of(page, number), 30)

    def test_a_map_theme_with_a_category_off_is_what_its_frame_counts(
        self, page, project
    ) -> None:
        # Untick "Closed" in the Layers panel's legend, save that as a theme
        # (as "Add theme" does), then put the layer back as it was.
        root = project.layerTreeRoot()
        model = QgsLayerTreeModel(root)
        entries = model.layerLegendNodes(root.findLayer(page.sites.id()))
        closed = next(e for e in entries if e.data(DISPLAY) == "Closed")
        closed.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        record = QgsMapThemeCollection.createThemeFromCurrentState(root, model)
        project.mapThemeCollection().insert("Quiet", record)
        closed.setData(Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        number = add_counter(page)
        _ok(count_of(page, number), 30)
        page.frame.setFollowVisibilityPreset(True)
        page.frame.setFollowVisibilityPresetName("Quiet")
        _ok(count_of(page, number), count_sites() - count_sites(status="Closed"))


def _style_with_opacity(layer, opacity: float) -> str:
    """The layer's style as a map frame would keep it, drawn at `opacity`;
    the layer itself is put back as it was."""
    before = layer.opacity()
    layer.setOpacity(opacity)
    style = QgsMapLayerStyle()
    style.readFromLayer(layer)
    layer.setOpacity(before)
    return style.xmlData()


def _frame_shows_marks(page) -> bool:
    """Whether the frame, drawn by QGIS, has any site symbol in it."""
    region = QRectF(page.frame.pos().x(), page.frame.pos().y(), 120, 100)
    image = QgsLayoutExporter(page.layout).renderRegionToImage(region, QSize(), 40)
    white = 0xFFFFFFFF
    return any(
        image.pixel(x, y) != white
        for x in range(0, image.width(), 2)
        for y in range(0, image.height(), 2)
    )


def _keep_style(page, xml: str) -> None:
    page.frame.setLayers([page.sites])
    page.frame.setKeepLayerStyles(True)
    page.frame.setLayerStyleOverrides({page.sites.id(): xml})


class TestTheStyleTheFrameDrawsWith:
    """A count describes what the frame renders: its kept style or theme, not
    only the project layer's current style."""

    def test_a_transparent_frame_style_counts_nothing_over_an_opaque_layer(
        self, page
    ) -> None:
        number = add_counter(page)
        _ok(count_of(page, number), 30)
        _keep_style(page, _style_with_opacity(page.sites, 0.0))
        assert page.sites.opacity() == 1.0  # the project style is opaque
        assert not _frame_shows_marks(page)
        _ok(count_of(page, number), 0)

    def test_an_opaque_frame_style_counts_over_a_transparent_layer(self, page) -> None:
        number = add_counter(page)
        xml = _style_with_opacity(page.sites, 1.0)
        page.sites.setOpacity(0.0)  # the project style draws nothing
        _ok(count_of(page, number), 0)  # a frame that follows the project
        _keep_style(page, xml)
        assert _frame_shows_marks(page)
        _ok(count_of(page, number), 30)

    def test_a_map_theme_decides_the_opacity_both_ways(self, page, project) -> None:
        root = project.layerTreeRoot()
        root.findLayer(page.squares.id()).setItemVisibilityChecked(False)
        number = add_counter(page)
        # Two named styles of the layer, and a theme using each.
        styles = page.sites.styleManager()
        page.sites.setOpacity(0.0)
        styles.addStyleFromLayer("Faded")
        page.sites.setOpacity(1.0)
        styles.addStyleFromLayer("Opaque")
        model = QgsLayerTreeModel(root)
        for name in ("Faded", "Opaque"):
            styles.setCurrentStyle(name)
            project.mapThemeCollection().insert(
                name, QgsMapThemeCollection.createThemeFromCurrentState(root, model)
            )
        styles.setCurrentStyle("default")
        page.frame.setFollowVisibilityPreset(True)

        # The theme is transparent, the project's own style is opaque.
        page.sites.setOpacity(1.0)
        page.frame.setFollowVisibilityPresetName("Faded")
        assert not _frame_shows_marks(page)
        _ok(count_of(page, number), 0)

        # The theme is opaque, the project's own style is transparent.
        page.sites.setOpacity(0.0)
        page.frame.setFollowVisibilityPresetName("Opaque")
        assert _frame_shows_marks(page)
        _ok(count_of(page, number), 30)

    def test_a_frame_following_the_project_uses_the_projects_opacity(
        self, page
    ) -> None:
        number = add_counter(page)
        page.sites.setOpacity(0.0)
        _ok(count_of(page, number), 0)
        page.sites.setOpacity(0.5)
        _ok(count_of(page, number), 30)

    def test_an_opaque_frame_style_still_hides_its_locked_categories(
        self, page
    ) -> None:
        number = add_counter(page)
        renderer = page.sites.renderer()
        closed = next(
            i for i, c in enumerate(renderer.categories()) if c.value() == "Closed"
        )
        renderer.updateCategoryRenderState(closed, False)
        style = QgsMapLayerStyle()
        style.readFromLayer(page.sites)  # "Closed" is off in this style
        renderer.updateCategoryRenderState(closed, True)  # and on in the project
        _ok(count_of(page, number), 30)
        _keep_style(page, style.xmlData())
        _ok(count_of(page, number), count_sites() - count_sites(status="Closed"))


class TestWhenItCannotCount:
    def test_a_counter_with_no_layer_reads_a_dash_and_says_what_to_do(
        self, page
    ) -> None:
        number = add_counter(page)
        lc.write_spec(number, lc.CountSpec(map_uuid=page.frame.uuid()))
        outcome = count_of(page, number)
        assert outcome.state is lc.CountState.UNCONFIGURED
        assert outcome.text == "\N{EM DASH}" and outcome.message

    def test_a_removed_layer_gives_a_dash_and_names_the_layer(
        self, page, project
    ) -> None:
        number = add_counter(page)
        _ok(count_of(page, number), 30)
        lc.apply_outcome(number, count_of(page, number))
        project.removeMapLayer(page.sites.id())
        outcome = count_of(page, number)
        assert outcome.state is lc.CountState.INVALID
        assert "Monitoring sites" in outcome.message
        # The old number is not kept.
        lc.apply_outcome(number, outcome)
        assert number.text() == "\N{EM DASH}"

    def test_a_removed_field_is_reported(self, page) -> None:
        number = add_counter(page, mode=lc.MODE_VALUE, field="gone", value="x")
        outcome = count_of(page, number)
        assert outcome.state is lc.CountState.INVALID and "gone" in outcome.message

    def test_a_removed_map_is_reported(self, page) -> None:
        number = add_counter(page)
        page.layout.removeLayoutItem(page.frame)
        assert count_of(page, number).state is lc.CountState.INVALID

    def test_a_value_mode_without_a_value_is_not_set_up(self, page) -> None:
        number = add_counter(page, mode=lc.MODE_VALUE, field="status")
        assert count_of(page, number).state is lc.CountState.UNCONFIGURED

    def test_a_failing_calculation_is_unavailable_never_zero(
        self, page, monkeypatch
    ) -> None:
        number = add_counter(page)

        def boom(*_args):
            raise RuntimeError("the source went away")

        monkeypatch.setattr(lc, "_displayed_area", boom)
        outcome = count_of(page, number)
        assert outcome.state is lc.CountState.UNAVAILABLE
        assert outcome.text == "n/a" and outcome.count is None


class TestRefreshingAPage:
    def test_every_counter_on_the_page_is_brought_up_to_date(self, page) -> None:
        a = add_counter(page)
        b = add_counter(page, mode=lc.MODE_VALUE, field="status", value="Closed")
        a.setText("999")
        b.setText("999")
        outcomes = lc.refresh_live_counts(page.layout)
        assert a.text() == "30" and b.text() == str(count_sites(status="Closed"))
        assert set(outcomes) == {a.uuid(), b.uuid()}


def _counted_recording_requests(page, number):
    """The outcome, and every request the count made of the layer."""
    prepared = lc.prepare(page.layout, number)
    assert isinstance(prepared, lc.Prepared)
    layer, asked = prepared.layer, []

    class Recording:
        def fields(self):
            return layer.fields()

        def getFeatures(self, request):  # noqa: N802 - QGIS's name
            asked.append(QgsFeatureRequest(request))
            return layer.getFeatures(request)

    prepared.layer = Recording()
    return lc.run(prepared), asked


def _names_read(page, request) -> list[str] | None:
    """The fields `request` reads, or `None` when it reads every one."""
    if not request.flags() & QgsFeatureRequest.Flag.SubsetOfAttributes:
        return None
    fields = page.sites.fields()
    return sorted(fields.at(index).name() for index in request.subsetOfAttributes())


class TestOnlyWhatTheCountReadsIsFetched:
    """A wide table is counted without fetching its other columns: only what
    the renderer draws with - categories, rules, data-defined sizes - and the
    field a value is matched on. The counts are the hand-worked ones, on a
    GeoPackage as well, whose driver really leaves the other columns out (a
    memory layer hands every value over whatever it is asked)."""

    @pytest.fixture(params=["memory", "geopackage"])
    def page(self, request, project, tmp_path):
        folder = tmp_path if request.param == "geopackage" else None
        return build_page(project, folder=folder)

    def test_a_category_field_and_a_matched_field(self, page) -> None:
        number = add_counter(page, mode=lc.MODE_VALUE, field="cap", value="10")
        outcome, asked = _counted_recording_requests(page, number)
        _ok(outcome, count_sites(cap=10))
        assert [_names_read(page, request) for request in asked] == [["cap", "status"]]

    def test_rule_filters_and_a_data_defined_size(self, page) -> None:
        from qgis.core import QgsProperty, QgsSymbolLayer

        symbol = QgsMarkerSymbol.createSimple({})
        symbol.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.Size,
            QgsProperty.fromExpression('length("name")'),
        )
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(QgsRuleBasedRenderer.Rule(symbol, 0, 0, '"cap" = 10', "big"))
        page.sites.setRenderer(QgsRuleBasedRenderer(root))
        outcome, asked = _counted_recording_requests(page, add_counter(page))
        _ok(outcome, count_sites(cap=10))
        assert [_names_read(page, request) for request in asked] == [["cap", "name"]]

    def test_a_rule_reading_every_attribute_reads_them_all(self, page) -> None:
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(
            QgsRuleBasedRenderer.Rule(
                QgsMarkerSymbol.createSimple({}),
                0,
                0,
                "attributes()['status'] = 'Closed'",
                "closed",
            )
        )
        page.sites.setRenderer(QgsRuleBasedRenderer(root))
        outcome, asked = _counted_recording_requests(page, add_counter(page))
        _ok(outcome, count_sites(status="Closed"))
        assert [_names_read(page, request) for request in asked] == [None]


class _Watched:
    """The prepared renderer, delegated to, with its start and stop calls
    noted; `discovery` stands in for its `usedAttributes` when set."""

    def __init__(self, renderer, discovery=None) -> None:
        self._renderer = renderer
        self._discovery = discovery
        self.calls: list[str] = []

    def startRender(self, context, fields) -> None:  # noqa: N802 - QGIS's name
        self.calls.append("start")
        self._renderer.startRender(context, fields)

    def stopRender(self, context) -> None:  # noqa: N802 - QGIS's name
        self.calls.append("stop")
        self._renderer.stopRender(context)

    def usedAttributes(self, context):  # noqa: N802 - QGIS's name
        if self._discovery is not None:
            return self._discovery()
        return self._renderer.usedAttributes(context)

    def willRenderFeature(self, feature, context) -> bool:  # noqa: N802
        return self._renderer.willRenderFeature(feature, context)


class TestTheRendererIsAlwaysStopped:
    """A started renderer is stopped however the count ends, and a renderer
    that cannot name the fields it reads is counted as before it was asked:
    on every field."""

    def test_a_renderer_whose_attribute_discovery_fails(self, page) -> None:
        number = add_counter(page, mode=lc.MODE_VALUE, field="status", value="Active")
        prepared = lc.prepare(page.layout, number)
        assert isinstance(prepared, lc.Prepared)

        def fails():
            raise RuntimeError("cannot say")

        watched = _Watched(prepared.renderer, fails)
        prepared.renderer = watched
        layer, asked = prepared.layer, []

        class Recording:
            def fields(self):
                return layer.fields()

            def getFeatures(self, request):  # noqa: N802 - QGIS's name
                asked.append(QgsFeatureRequest(request))
                return layer.getFeatures(request)

        prepared.layer = Recording()
        _ok(lc.run(prepared), count_sites(status="Active"))
        assert [_names_read(page, request) for request in asked] == [None]
        assert watched.calls == ["start", "stop"]

    def test_a_layer_that_fails_to_answer(self, page) -> None:
        prepared = lc.prepare(page.layout, add_counter(page))
        assert isinstance(prepared, lc.Prepared)
        watched = _Watched(prepared.renderer)
        prepared.renderer = watched
        layer = prepared.layer

        class Failing:
            def fields(self):
                return layer.fields()

            def getFeatures(self, request):  # noqa: N802 - QGIS's name
                raise RuntimeError("the source went away")

        prepared.layer = Failing()
        outcome = lc.run(prepared)
        assert outcome.state is lc.CountState.UNAVAILABLE
        assert watched.calls == ["start", "stop"]
