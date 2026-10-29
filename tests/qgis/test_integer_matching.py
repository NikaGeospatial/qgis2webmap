"""Whole-number fields are matched exactly: by a Live count and by the atlas
"Matching a value" filter.

IDs above 2**53 (9007199254740992 and 9007199254740993) differ by one but are
the same float, so any path through a float merges them. Expectations are the
rows of `ROWS`, and the filter is judged by the features QGIS selects with the
stored expression, not by the text shown in the box.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsPointXY,
    QgsPrintLayout,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QRectF

from nika_onlymap_exporter.core import live_count as lc
from nika_onlymap_exporter.core.atlas_filter import (
    Matching,
    condition_expression,
    read_condition,
)

BIG = 9007199254740992
INT64_MAX = 9223372036854775807
#: (name, long id, small whole number, decimal, x position)
ROWS = (
    ("big", BIG, 1, 0.1, 0.0),
    ("bigger", BIG + 1, 2, 0.30000000000000004, 1.0),
    ("five", 5, 5, 2.5, 2.0),
    ("minus", -7, -7, -2.5, 3.0),
    ("max", INT64_MAX, 3, 1e20, 4.0),
    ("negmax", -INT64_MAX, 4, -1e20, 5.0),
    ("null", None, None, None, 6.0),
)


@pytest.fixture
def layer(project) -> QgsVectorLayer:
    made = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string&field=long id:long"
        '&field=we"ird:long&field=small:integer&field=dec:double',
        "ids",
        "memory",
    )
    features = []
    for name, long_id, small, decimal, x in ROWS:
        feature = QgsFeature(made.fields())
        feature.setAttributes([name, long_id, long_id, small, decimal])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, 0)))
        features.append(feature)
    made.dataProvider().addFeatures(features)
    made.updateExtents()
    project.addMapLayer(made)
    return made


def _selected(layer, expression: str) -> list[str]:
    request = QgsFeatureRequest().setFilterExpression(expression)
    return [f["name"] for f in layer.getFeatures(request)]


def _counter(project, layer, field: str, value: str | None, null: bool = False):
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(item)
    item.attemptSetSceneRect(QRectF(10, 10, 100, 100))
    item.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    item.setLayers([layer])
    item.setKeepLayerSet(True)
    item.zoomToExtent(QgsRectangle(-1, -3, 8, 3))
    number = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(number)
    lc.write_spec(
        number,
        lc.CountSpec(
            map_uuid=item.uuid(),
            layer_id=layer.id(),
            layer_name=layer.name(),
            mode=lc.MODE_VALUE,
            field=field,
            value=value,
            value_is_null=null,
        ),
    )
    return layout, number


def _live_count(project, layer, field, value, null=False) -> lc.CountOutcome:
    layout, number = _counter(project, layer, field, value, null)
    return lc.evaluate(layout, number)


class TestLiveCountMatchesIntegersExactly:
    def test_adjacent_ids_above_2_53_are_told_apart(self, project, layer) -> None:
        assert _live_count(project, layer, "long id", str(BIG + 1)).count == 1
        assert _live_count(project, layer, "long id", str(BIG)).count == 1

    def test_ordinary_negative_and_largest_values(self, project, layer) -> None:
        for field, typed, expected in (
            ("small", "5", 1),
            ("small", " -7 ", 1),
            ("small", "5.0", 1),  # a whole number spelt with a zero fraction
            ("long id", "-7", 1),
            ("long id", str(INT64_MAX), 1),
            ("long id", str(INT64_MAX - 1), 0),
            ("long id", str(-INT64_MAX), 1),
            ("long id", "12", 0),
        ):
            outcome = _live_count(project, layer, field, typed)
            assert outcome.state is lc.CountState.OK, (field, typed)
            assert outcome.count == expected, (field, typed)

    def test_a_null_value_matches_only_null_rows(self, project, layer) -> None:
        assert _live_count(project, layer, "long id", None, null=True).count == 1
        assert _live_count(project, layer, "small", "0").count == 0

    def test_the_count_is_the_one_feature_not_both_neighbours(
        self, project, layer
    ) -> None:
        # Pin down WHICH feature matched, not just how many.
        layout, number = _counter(project, layer, "long id", str(BIG + 1))
        prepared = lc.prepare(layout, number)
        assert isinstance(prepared, lc.Prepared)
        assert prepared.matches is not None
        matched = [f["name"] for f in layer.getFeatures() if prepared.matches(f)]
        assert matched == ["bigger"]

    def test_input_that_is_not_a_whole_number_is_refused_not_rounded(
        self, project, layer
    ) -> None:
        for typed in ("1.5", "1e3", "abc", "5.5", "9007199254740992.5", ""):
            outcome = _live_count(project, layer, "long id", typed)
            assert outcome.state is lc.CountState.INVALID, typed
            assert outcome.count is None

    def test_a_decimal_field_keeps_its_tolerance_and_forms(
        self, project, layer
    ) -> None:
        assert _live_count(project, layer, "dec", "2.5").count == 1
        assert _live_count(project, layer, "dec", "-2.5").count == 1
        assert _live_count(project, layer, "dec", "1e20").count == 1
        assert _live_count(project, layer, "dec", "0.1").count == 1
        assert _live_count(project, layer, "dec", "abc").state is lc.CountState.INVALID


class TestAtlasFilterIsExact:
    def test_the_stored_expression_is_the_exact_integer(self, layer) -> None:
        assert condition_expression(layer, "long id", str(BIG + 1), False) == (
            "to_string(\"long id\") = '9007199254740993'"
        )
        assert condition_expression(layer, "long id", "-7", False) == '"long id" = -7'
        assert condition_expression(layer, "small", "5.0", False) == '"small" = 5'
        assert condition_expression(layer, "long id", str(INT64_MAX), False) == (
            "to_string(\"long id\") = '9223372036854775807'"
        )

    def test_a_field_name_with_a_quote_is_quoted_correctly(self, layer) -> None:
        expression = condition_expression(layer, 'we"ird', str(BIG + 1), False)
        assert expression == 'to_string("we""ird") = \'9007199254740993\''
        assert _selected(layer, expression) == ["bigger"]

    def test_qgis_selects_exactly_the_matching_features(self, layer) -> None:
        for typed, expected in (
            (str(BIG + 1), ["bigger"]),
            (str(BIG), ["big"]),
            ("5", ["five"]),
            ("-7", ["minus"]),
            (str(INT64_MAX), ["max"]),
            (str(-INT64_MAX), ["negmax"]),
            ("999", []),
        ):
            expression = condition_expression(layer, "long id", typed, False)
            assert _selected(layer, expression) == expected, expression

    def test_null_rows_are_chosen_by_is_null(self, layer) -> None:
        expression = condition_expression(layer, "long id", "", True)
        assert _selected(layer, expression) == ["null"]

    def test_the_atlas_itself_keeps_only_the_matching_page(
        self, project, layer
    ) -> None:
        # The filter as the atlas runs it: pages are the features it keeps.
        areas = QgsVectorLayer("Polygon?crs=EPSG:4326&field=cid:long", "a", "memory")
        features = []
        for cid in (BIG, BIG + 1):
            feature = QgsFeature(areas.fields())
            feature.setAttributes([cid])
            feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(0, 0, 1, 1)))
            features.append(feature)
        areas.dataProvider().addFeatures(features)
        project.addMapLayer(areas)
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        atlas = layout.atlas()
        atlas.setCoverageLayer(areas)
        atlas.setEnabled(True)
        atlas.setFilterFeatures(True)
        atlas.setFilterExpression(
            condition_expression(areas, "cid", str(BIG + 1), False)
        )
        assert atlas.updateFeatures() == 1
        atlas.beginRender()
        atlas.seekTo(0)
        assert layout.reportContext().feature()["cid"] == BIG + 1

    def test_input_that_is_not_a_whole_number_is_refused(self, layer) -> None:
        for typed in ("1.5", "1e3", "abc", "nan", "inf"):
            with pytest.raises(ValueError, match="not a"):
                condition_expression(layer, "long id", typed, False)

    def test_a_decimal_field_still_gets_a_decimal_expression(self, layer) -> None:
        assert condition_expression(layer, "dec", "2.5", False) == '"dec" = 2.5'
        assert _selected(layer, '"dec" = 2.5') == ["five"]
        assert condition_expression(layer, "dec", "1e3", False) == '"dec" = 1000'

    def test_the_exact_ids_read_back_as_the_same_choice(self, layer) -> None:
        for typed in (str(BIG + 1), str(BIG), "-7", str(INT64_MAX), str(-INT64_MAX)):
            expression = condition_expression(layer, "long id", typed, False)
            assert read_condition(layer, expression) == Matching("long id", typed)

    def test_small_whole_numbers_stay_a_plain_comparison(self, layer) -> None:
        # Exact below 2**53, and it can be pushed down to a database.
        assert condition_expression(layer, "long id", str(2**53 - 1), False) == (
            '"long id" = 9007199254740991'
        )

    def test_a_hand_written_big_comparison_is_custom_never_rewritten(
        self, layer
    ) -> None:
        # `=` merges neighbours from 2**53 on, so it is not a choice we own.
        assert read_condition(layer, '"long id" = 9007199254740993') is None
        assert read_condition(layer, "to_string(\"long id\") = '007'") is None
        assert read_condition(layer, "to_string(\"name\") = '5'") is None

    def test_a_decimal_literal_on_a_whole_number_field_is_custom(self, layer) -> None:
        assert read_condition(layer, '"small" = 2.5') is None
