"""Vector layers that follow QGIS's time, read from real QGIS layers.

The oracle is QGIS itself: for each temporal mode the features the exported
slider shows at a frame (its carried times, window and opening, worked out
exactly) must be the ones QGIS's own temporal filter draws for that frame
(`QgsVectorLayerTemporalProperties.createFilterString`, which QGIS's renderer
draws with). The frames are set in UTC and in local time, on date, date and
time, and text fields, so a time zone or a date-only field read differently
from QGIS shows here. That the runtime's slider then shows what the window
says is the browser tier's (`tests/browser/test_vector_time_slider.py`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import (
    MapTime,
    follows_map_time,
    read_layer,
)

qgis_core = pytest.importorskip("qgis.core")
from qgis.core import (  # noqa: E402
    Qgis,
    QgsDateTimeRange,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsVectorLayer,
    QgsVectorLayerTemporalContext,
)
from qgis.PyQt.QtCore import QDate, QDateTime, Qt, QTime  # noqa: E402

MODES = Qgis.VectorTemporalMode


@pytest.fixture(autouse=True)
def _qgis(qgis_app):
    """Every test makes layers, which need QGIS and its providers started."""


DAY_MS = 86_400_000
UTC = Qt.TimeSpec.UTC
LOCAL = Qt.TimeSpec.LocalTime


def _moment(day: int, hour: int = 0, spec=LOCAL) -> QDateTime:
    return QDateTime(QDate(2020, 3, day), QTime(hour, 0), spec)


def _layer(kind: str, rows) -> QgsVectorLayer:
    """Points with a start `t`, an end `e` and a duration `d` in hours."""
    layer = QgsVectorLayer(
        f"Point?crs=EPSG:4326&field=n:integer&field=t:{kind}&field=e:{kind}"
        "&field=d:double",
        "Events",
        "memory",
    )
    features = []
    for index, (start, end, duration) in enumerate(rows):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([index, start, end, duration])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(index * 0.1, 51.0)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


def _datetimes(spec=UTC):
    """Starts on whole days and between them, each with an end a day later."""
    rows = []
    for day in range(2, 10):
        # 20:00 is the next day in Asia and still the day before in America:
        # a time read in another zone than QGIS compares it in shows here.
        for hour in (0, 6, 20):
            start = _moment(day, hour, spec)
            rows.append((start, start.addDays(1), 24.0))
    return rows


def _dates():
    return [
        (QDate(2020, 3, day), QDate(2020, 3, day + 2), 48.0) for day in range(2, 10)
    ]


def _texts():
    # A date, a local date and time, and a time in UTC, as QGIS parses them.
    return [(f"2020-03-0{day}", f"2020-03-0{day + 1}", 24.0) for day in range(2, 9)] + [
        ("2020-03-05T06:00:00", "2020-03-06T06:00:00", 24.0),
        ("2020-03-05T00:00:00Z", "2020-03-06T00:00:00Z", 24.0),
    ]


def _frame(begin: QDateTime) -> QgsDateTimeRange:
    # A temporal controller frame: its start in, its end out.
    return QgsDateTimeRange(begin, begin.addDays(1), True, False)


def _qgis_draws(layer: QgsVectorLayer, frame: QgsDateTimeRange) -> set[int]:
    context = QgsVectorLayerTemporalContext()
    context.setLayer(layer)
    expression = layer.temporalProperties().createFilterString(context, frame)
    request = QgsFeatureRequest()
    if expression:
        request.setFilterExpression(expression)
    return {feature["n"] for feature in layer.getFeatures(request)}


def _slider_shows(exported, t: int) -> set[int]:
    time = exported.time
    low = time.low if time.window is None else t - time.window
    return {
        feature["properties"]["n"]
        for feature in exported.geojson["features"]
        if feature["properties"][time.field_name] is not None
        and low <= feature["properties"][time.field_name] <= t
    }


def _read(layer, frame: QgsDateTimeRange | None, **kwargs):
    report = FidelityReportBuilder()
    map_time = MapTime(True, frame) if frame is not None else None
    return read_layer(layer, report, map_time=map_time, **kwargs), report


def _rows(report, status) -> str:
    return " ".join(item.detail for item in report.items if item.status is status)


def _set(layer, mode, **settings):
    properties = layer.temporalProperties()
    properties.setIsActive(True)
    properties.setMode(mode)
    properties.setStartField("t")
    properties.setEndField("e")
    properties.setDurationField("d")
    properties.setDurationUnits(Qgis.TemporalUnit.Hours)
    for name, value in settings.items():
        getattr(properties, name)(value)
    return layer


CASES = [
    (
        "instant, date and time",
        "datetime",
        _datetimes,
        MODES.FeatureDateTimeInstantFromField,
        {},
    ),
    (
        "instant, local date and time",
        "datetime",
        lambda: _datetimes(LOCAL),
        MODES.FeatureDateTimeInstantFromField,
        {},
    ),
    ("instant, date", "date", _dates, MODES.FeatureDateTimeInstantFromField, {}),
    ("instant, text", "string", _texts, MODES.FeatureDateTimeInstantFromField, {}),
    (
        "instant, accumulated",
        "date",
        lambda: [*_dates(), (None, None, None)],
        MODES.FeatureDateTimeInstantFromField,
        {"setAccumulateFeatures": True},
    ),
    (
        "instant, event duration",
        "datetime",
        _datetimes,
        MODES.FeatureDateTimeInstantFromField,
        {"setFixedDuration": 12.0, "setDurationUnits": Qgis.TemporalUnit.Hours},
    ),
    (
        "start and end",
        "datetime",
        _datetimes,
        MODES.FeatureDateTimeStartAndEndFromFields,
        {},
    ),
    (
        "start and end, end included",
        "date",
        _dates,
        MODES.FeatureDateTimeStartAndEndFromFields,
        {"setLimitMode": Qgis.VectorTemporalLimitMode.IncludeBeginIncludeEnd},
    ),
    (
        "start and duration",
        "datetime",
        _datetimes,
        MODES.FeatureDateTimeStartAndDurationFromFields,
        {},
    ),
    (
        "start and end expressions",
        "date",
        _dates,
        MODES.FeatureDateTimeStartAndEndFromExpressions,
        {
            "setStartExpression": '"t"',
            "setEndExpression": '"t" + make_interval(0,0,0,2,0,0,0)',
        },
    ),
]


@pytest.mark.parametrize(("name", "kind", "rows", "mode", "settings"), CASES)
@pytest.mark.parametrize("spec", [UTC, LOCAL], ids=["utc frame", "local frame"])
def test_the_slider_shows_what_qgis_draws(
    name, kind, rows, mode, settings, spec
) -> None:
    layer = _set(_layer(kind, rows()), mode, **settings)
    assert follows_map_time(layer)
    opening = _frame(_moment(5, 0, spec))
    exported, report = _read(layer, opening)
    assert exported is not None
    assert exported.time is not None, _rows(report, FidelityStatus.APPROXIMATED)
    start = exported.time.start
    assert _slider_shows(exported, start) == _qgis_draws(layer, opening)
    assert _slider_shows(exported, start), "the opening frame draws something"
    # The frames the temporal controller steps to from there.
    for step in (-2, -1, 1, 2, 3):
        frame = _frame(opening.begin().addDays(step))
        shown = _slider_shows(exported, start + step * DAY_MS)
        assert shown == _qgis_draws(layer, frame), (name, step)
    assert "Exported with a time slider" in _rows(report, FidelityStatus.APPROXIMATED)


def test_features_lasting_differently_long_are_a_snapshot() -> None:
    rows = [
        (_moment(4), _moment(6), 0.0),
        (_moment(5), _moment(9), 0.0),
        (_moment(7), _moment(8), 0.0),
    ]
    layer = _set(_layer("datetime", rows), MODES.FeatureDateTimeStartAndEndFromFields)
    opening = _frame(_moment(5))
    exported, report = _read(layer, opening)
    assert exported is not None and exported.time is None
    kept = {f["properties"]["n"] for f in exported.geojson["features"]}
    assert kept == _qgis_draws(layer, opening) == {0, 1}
    assert "different lengths of time" in _rows(report, FidelityStatus.APPROXIMATED)


def test_in_a_cartograph_frame_it_is_a_snapshot() -> None:
    layer = _set(_layer("date", _dates()), MODES.FeatureDateTimeInstantFromField)
    opening = _frame(_moment(5))
    exported, report = _read(layer, opening, time_slider=False)
    assert exported is not None and exported.time is None
    kept = {f["properties"]["n"] for f in exported.geojson["features"]}
    assert kept == _qgis_draws(layer, opening)
    assert "cartograph's interactive frame" in _rows(
        report, FidelityStatus.APPROXIMATED
    )


@pytest.mark.parametrize(
    ("why", "frame", "mode", "row"),
    [
        (
            "no QGIS map view",
            None,
            MODES.FeatureDateTimeInstantFromField,
            "no QGIS map view",
        ),
        (
            "controller off",
            "off",
            MODES.FeatureDateTimeInstantFromField,
            "controller is off",
        ),
        (
            "redraw only",
            _frame(_moment(5)),
            MODES.RedrawLayerOnly,
            "redraws this layer",
        ),
    ],
)
def test_without_a_time_every_feature_is_exported(why, frame, mode, row) -> None:
    layer = _set(_layer("date", _dates()), mode)
    report = FidelityReportBuilder()
    map_time = (
        MapTime(False, QgsDateTimeRange(QDateTime(), QDateTime()))
        if frame == "off"
        else MapTime(True, frame)
        if frame is not None
        else None
    )
    exported = read_layer(layer, report, map_time=map_time)
    assert exported is not None and exported.time is None
    assert len(exported.geojson["features"]) == len(_dates())
    assert exported.visible
    assert row in _rows(report, FidelityStatus.APPROXIMATED)
    assert follows_map_time(layer) is (mode != MODES.RedrawLayerOnly)


@pytest.mark.parametrize(("day", "shown"), [(5, True), (8, False)])
def test_a_fixed_range_shows_when_qgis_draws_it(day, shown) -> None:
    layer = _set(_layer("date", _dates()), MODES.FixedTemporalRange)
    layer.temporalProperties().setFixedTemporalRange(
        QgsDateTimeRange(_moment(4), _moment(6))
    )
    exported, report = _read(layer, _frame(_moment(day)))
    assert exported is not None and exported.time is None
    assert exported.visible is shown
    assert len(exported.geojson["features"]) == len(_dates())
    status = FidelityStatus.PRESERVED if shown else FidelityStatus.APPROXIMATED
    assert "shows this layer from 2020-03-04" in _rows(report, status)


def test_a_layer_without_time_is_unchanged() -> None:
    layer = _layer("date", _dates())
    exported, report = _read(layer, _frame(_moment(5)))
    assert exported is not None and exported.time is None
    assert not follows_map_time(layer)
    assert not [item for item in report.items if item.subject.startswith("Time of")]


def test_dense_points_with_a_slider_stay_points(project) -> None:
    """A count bubble would go on counting the points the slider hides."""
    from nika_onlymap_exporter.core.project_reader import read_project

    rows = [(QDate(2020, 3, 2 + i % 8), None, None) for i in range(400)]
    layer = _set(_layer("date", rows), MODES.FeatureDateTimeInstantFromField)
    layer.startEditing()
    for index, feature in enumerate(layer.getFeatures()):
        # All within a few metres: they would cluster.
        point = QgsPointXY(index * 1e-5, 51.0)
        layer.changeGeometry(feature.id(), QgsGeometry.fromPointXY(point))
    layer.commitChanges()
    project.addMapLayer(layer)
    report = FidelityReportBuilder()
    read = read_project(project, report, map_time=MapTime(True, _frame(_moment(5))))
    (exported,) = read.exportable_layers
    assert exported.time is not None
    assert exported.cluster_max_zoom is None
    markers = [i.detail for i in report.items if i.subject.startswith("Markers")]
    assert len(markers) == 1 and "has a time slider" in markers[0]
