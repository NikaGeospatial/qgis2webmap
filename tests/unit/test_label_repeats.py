"""Where a line's labels sit along it, and how they repeat - pure geometry.

QGIS labels a line at half its length, and with "Repeat" set cuts a long line
into equal pieces at least the repeat distance long and labels each at its
middle. The web map's default repeat keeps the middle label and adds others
either side. These pin the positions and counts, and the caps that keep a big
layer's labels from multiplying. The QGIS side of the same positions is
`tests/qgis/test_line_label_repeats.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math

import pytest

from nika_onlymap_exporter.core import declutter
from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    GeometryKind,
    LabelingSpec,
    LineLabelRepeat,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
)
from nika_onlymap_exporter.core.label_points import (
    LABEL_PROPERTY,
    MAX_LABELS_PER_LAYER,
    MAX_LABELS_PER_LINE,
    build_label_collection,
    build_label_collection_noting_repeats,
    representative_point,
)


def _line(*coordinates, **properties):
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [list(c) for c in coordinates],
        },
        "properties": properties or {"name": "X"},
    }


def _collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _xs(built) -> list[float]:
    return [round(f["geometry"]["coordinates"][0], 4) for f in built["features"]]


# Measured in degrees, so a line along the equator from 0 to 10 is 10 long and
# positions read straight off as longitudes.
TEN = _line((0.0, 0.0), (10.0, 0.0))


def _repeat(distance: float, from_qgis: bool = True, units_per_pixel: float = 1e-4):
    return LineLabelRepeat(distance, units_per_pixel, from_qgis=from_qgis)


def _built(source, repeat, **kwargs):
    return build_label_collection(
        source, "name", plane="degrees", repeat=repeat, **kwargs
    )


class TestHalfTheLength:
    def test_a_two_vertex_line_is_labelled_in_its_middle_not_at_an_end(self) -> None:
        """The middle vertex of two is the second, which is an end."""
        assert representative_point(TEN["geometry"], "degrees") == (5.0, 0.0)

    def test_vertices_crowding_one_end_do_not_pull_the_label(self) -> None:
        """QGIS labels (0 0, 1 0, 2 0, 10 0) at 5, not at the middle vertex 2."""
        crowded = {
            "type": "LineString",
            "coordinates": [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [10.0, 0.0]],
        }
        assert representative_point(crowded, "degrees") == (5.0, 0.0)
        assert representative_point(crowded) == pytest.approx((5.0, 0.0))

    def test_a_z_line_labels_where_its_flat_twin_does(self) -> None:
        line = {"type": "LineString", "coordinates": [[0.0, 0.0, 9.0], [4.0, 0.0, 9.0]]}
        assert representative_point(line, "degrees") == (2.0, 0.0)

    def test_the_planes_differ_on_a_long_line_as_qgis_does_by_crs(self) -> None:
        """QGIS 4.2 labels (0 50, 1 50, 10 60) at (5.2, 54.7) in EPSG:4326,
        its degrees taken as a flat plane. Web Mercator stretches the northern
        end, so measured there the middle moves north."""
        line = {
            "type": "LineString",
            "coordinates": [[0.0, 50.0], [1.0, 50.0], [10.0, 60.0]],
        }
        lon, lat = representative_point(line, "degrees")
        assert (round(lon, 1), round(lat, 1)) == (5.2, 54.6)
        assert representative_point(line)[1] > lat + 0.3

    def test_without_a_repeat_each_line_has_one_label(self) -> None:
        built = _built(_collection(TEN), None)
        assert _xs(built) == [5.0]

    def test_a_single_vertex_line_is_labelled_at_it(self) -> None:
        dot = {"type": "LineString", "coordinates": [[3.0, 4.0]]}
        assert representative_point(dot, "degrees") == (3.0, 4.0)
        built = _built(_collection(_line((3.0, 4.0))), _repeat(1.0))
        assert _xs(built) == [3.0]


class TestQgisRepeat:
    """Each case is a QGIS 4.2 render of a 10000 m line read back with
    `QgsLabelingResults`, scaled down to a line 10 long."""

    @pytest.mark.parametrize(
        ("distance", "expected"),
        [
            (2.0, [1.0, 3.0, 5.0, 7.0, 9.0]),
            (3.0, [1.6667, 5.0, 8.3333]),
            (4.0, [2.5, 7.5]),
            (6.0, [5.0]),
            (12.0, [5.0]),
        ],
    )
    def test_equal_pieces_each_labelled_at_its_middle(self, distance, expected):
        assert _xs(_built(_collection(TEN), _repeat(distance))) == expected

    def test_a_label_longer_than_the_distance_stretches_it_to_a_multiple(self):
        """QGIS: repeat 1000 with a 1397-wide label repeats every 2000."""
        # "X" * 20 at 12 px is 144 px wide, 1.44 long at 0.01 per pixel.
        wide = _collection(_line((0.0, 0.0), (10.0, 0.0), name="X" * 20))
        built = _built(wide, _repeat(1.0, units_per_pixel=0.01))
        assert _xs(built) == [1.0, 3.0, 5.0, 7.0, 9.0]

    def test_every_part_repeats_on_its_own(self) -> None:
        """QGIS, repeat 2000 with Label every part, on parts 10000 and 4000
        long: five labels on the long part and two on the short one."""
        multi = {
            "type": "Feature",
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[[0, 0], [10, 0]], [[0, 5], [4, 5]]],
            },
            "properties": {"name": "X"},
        }
        once = _built(_collection(multi), _repeat(2.0))
        every = _built(_collection(multi), _repeat(2.0), per_part=True)
        assert _xs(once) == [1.0, 3.0, 5.0, 7.0, 9.0]
        assert _xs(every) == [1.0, 3.0, 5.0, 7.0, 9.0, 1.0, 3.0]

    def test_points_and_polygons_never_repeat(self) -> None:
        square = [[0.0, 0.0], [9.0, 0.0], [9.0, 9.0], [0.0, 9.0], [0.0, 0.0]]
        source = _collection(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [square]},
                "properties": {"name": "X"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
                "properties": {"name": "X"},
            },
        )
        assert len(_built(source, _repeat(0.5))["features"]) == 2


class TestDefaultRepeat:
    def test_the_middle_label_stays_and_others_go_either_side(self) -> None:
        built = _built(_collection(TEN), _repeat(2.0, from_qgis=False))
        assert _xs(built) == [1.0, 3.0, 5.0, 7.0, 9.0]
        built = _built(_collection(TEN), _repeat(3.0, from_qgis=False))
        assert _xs(built) == [2.0, 5.0, 8.0]

    def test_a_line_shorter_than_three_distances_keeps_its_one_label(self) -> None:
        """QGIS's own single label, unchanged, on any line not long."""
        built = _built(_collection(TEN), _repeat(3.4, from_qgis=False))
        assert _xs(built) == [5.0]

    def test_no_repeat_sits_nearer_an_end_than_half_the_distance(self) -> None:
        built = _built(_collection(TEN), _repeat(0.7, from_qgis=False))
        xs = _xs(built)
        assert 5.0 in xs
        assert min(xs) >= 0.35 and max(xs) <= 10 - 0.35
        assert all(round(b - a, 4) == 0.7 for a, b in zip(xs, xs[1:])), (
            "repeats are evenly spaced at the distance"
        )


class TestCaps:
    def test_one_line_carries_at_most_the_cap_spread_evenly(self) -> None:
        for from_qgis in (True, False):
            built, repeated = build_label_collection_noting_repeats(
                _collection(TEN),
                "name",
                plane="degrees",
                repeat=_repeat(0.01, from_qgis=from_qgis),
            )
            xs = _xs(built)
            assert len(xs) <= MAX_LABELS_PER_LINE
            assert len(xs) >= MAX_LABELS_PER_LINE - 1
            gaps = {round(b - a, 3) for a, b in zip(xs, xs[1:])}
            assert len(gaps) == 1, "the capped repeats are spread evenly"
            assert repeated.lines_capped == 1
            assert repeated.spread == 1.0

    def test_under_the_cap_nothing_is_noted(self) -> None:
        _, repeated = build_label_collection_noting_repeats(
            _collection(TEN), "name", plane="degrees", repeat=_repeat(2.0)
        )
        assert (repeated.lines_capped, repeated.spread) == (0, 1.0)

    def test_a_layer_past_its_cap_repeats_further_apart(self) -> None:
        """200 lines of 50 repeats each would be 10,000 labels."""
        lines = [_line((0.0, i), (10.0, i)) for i in range(200)]
        built, repeated = build_label_collection_noting_repeats(
            _collection(*lines), "name", plane="degrees", repeat=_repeat(0.2)
        )
        assert len(built["features"]) <= MAX_LABELS_PER_LAYER
        assert repeated.spread == 2.0
        assert len(built["features"]) == 200 * 25

    def test_a_layer_whose_lines_alone_pass_the_cap_does_not_repeat(self) -> None:
        lines = [_line((0.0, i / 100), (10.0, i / 100)) for i in range(6000)]
        built, _ = build_label_collection_noting_repeats(
            _collection(*lines), "name", plane="degrees", repeat=_repeat(1.0)
        )
        assert len(built["features"]) == 6000


class TestNeighbours:
    def test_labels_along_one_line_are_paired_and_lines_are_not(self) -> None:
        source = _collection(TEN, _line((0.0, 5.0), (4.0, 5.0)))
        built, repeated = build_label_collection_noting_repeats(
            source, "name", plane="degrees", repeat=_repeat(2.0)
        )
        assert _xs(built) == [1.0, 3.0, 5.0, 7.0, 9.0, 1.0, 3.0]
        assert repeated.neighbours == ((0, 1), (1, 2), (2, 3), (3, 4), (5, 6))
        # Each line's label nearest its middle is the one the greedy share
        # counts; the rest are held to their neighbours.
        assert repeated.extras == {0, 1, 3, 4, 5}
        assert {f["properties"][LABEL_PROPERTY] for f in built["features"]} == {"X"}


def _line_layer(features, repeat) -> ExportLayer:
    return ExportLayer(
        layer_id="roads",
        name="Roads",
        geometry_kind=GeometryKind.LINE,
        source_kind=SourceKind.FILE,
        feature_count=len(features),
        geojson=_collection(*features),
        renderer=RendererSpec(kind=RendererKind.SINGLE),
        labeling=LabelingSpec(
            enabled=True,
            field_name="name",
            font_size=12.0,
            measure_plane="degrees",
            repeat=repeat,
        ),
        popup=PopupSpec(enabled=False),
    )


class TestRepeatsWaitForRoom:
    """Repeats are fixed along the line, so zoomed out they close up. The
    labels wait for the zoom where no repeat overlaps the next."""

    def test_the_zoom_is_where_neighbours_stop_overlapping(self) -> None:
        # "Main" at 12 px is 4 * 0.6 * 12 + 4 = 32.8 px wide; two labels a
        # degree apart on the equator are 1/360 of the runtime's 512 * 2**zoom
        # pixel world apart, so they clear each other from zoom 5.
        labels = [(0.0, 0.0, "Main"), (1.0, 0.0, "Main")]
        assert declutter.repeats_have_room(labels, [(0, 1)], 12.0) == 5
        assert 512 * 2**5 / 360 >= 32.8 > 512 * 2**4 / 360

    def test_a_few_long_lines_cannot_smear_inside_the_fit_share(self) -> None:
        """255 short lines spread over the world and one long one whose
        twenty repeats crowd: under a tenth of the labels, which the greedy
        share alone lets through, so without the room check they would smear."""
        short = [
            _line((lon, lat), (lon + 0.01, lat))
            for lon in range(-170, 170, 20)
            for lat in range(-60, 60, 8)
        ]
        long_line = _line((0.5, 0.5), (1.5, 0.5), name="Long road")
        repeat = LineLabelRepeat(0.05, 1e-4)
        layer = declutter.declutter_layer(
            _line_layer([*short, long_line], repeat), default_font_px=12
        )
        without = declutter.declutter_layer(
            _line_layer([*short, long_line], None), default_font_px=12
        )
        built, repeated = build_label_collection_noting_repeats(
            layer.geojson, "name", plane="degrees", repeat=repeat, font_px=12.0
        )
        placed = [
            (*f["geometry"]["coordinates"], f["properties"][LABEL_PROPERTY])
            for f in built["features"]
        ]
        room = declutter.repeats_have_room(placed, repeated.neighbours, 12.0)
        assert room is not None and room > (without.label_min_zoom or 0)
        assert layer.label_min_zoom == room

    def test_a_lone_roads_repeats_show_on_the_view_the_map_opens_on(self) -> None:
        """A 10 km road at 50 degrees north, alone on its layer: the reader
        fixes 250 px at the layer's reference view (its 10 km across 1000 px),
        and the map opens on the road's span across 512 px
        (`manifest_builder._zoom_for_extent`). Its labels must be there."""
        span = 10_000 / (111_320 * math.cos(math.radians(50)))
        per_pixel = 10.0 / math.cos(math.radians(50))  # Web Mercator metres
        road = _line((0.0, 50.0), (span, 50.0), name="Main Road")
        layer = ExportLayer(
            layer_id="road",
            name="Road",
            geometry_kind=GeometryKind.LINE,
            source_kind=SourceKind.FILE,
            feature_count=1,
            geojson=_collection(road),
            renderer=RendererSpec(kind=RendererKind.SINGLE),
            labeling=LabelingSpec(
                enabled=True,
                field_name="name",
                font_size=13.3,
                repeat=LineLabelRepeat(250 * per_pixel, per_pixel),
            ),
            popup=PopupSpec(enabled=False),
        )
        decluttered = declutter.declutter_layer(layer, default_font_px=12)
        opening = math.log2(360.0 / span)
        assert decluttered.label_min_zoom is not None
        assert decluttered.label_min_zoom <= opening

    def test_unrepeated_layers_declutter_as_before(self) -> None:
        lines = [_line((i * 0.001, 0.0), (i * 0.001 + 0.0005, 0.0)) for i in range(50)]
        layer = declutter.declutter_layer(_line_layer(lines, None), default_font_px=12)
        assert layer.label_min_zoom is not None
