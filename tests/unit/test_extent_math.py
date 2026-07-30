"""Extent computation, especially the antimeridian case.

These run without PyQGIS. The Alaska numbers are real: measured from the QGIS
sample dataset, which spans -179.13 to +179.78 and makes the incumbent open a
regional map at 99.8% of the width of the world.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.extent_math import (
    extent_from_geojson,
    extent_from_longitudes_latitudes,
    iter_coordinates,
    largest_longitude_gap,
    normalize_longitude,
    union_extents,
)


class TestNormalizeLongitude:
    def test_leaves_in_range_values_alone(self) -> None:
        assert normalize_longitude(0.0) == 0.0
        assert normalize_longitude(179.9) == 179.9
        assert normalize_longitude(-179.9) == -179.9

    def test_wraps_out_of_range(self) -> None:
        assert normalize_longitude(190.0) == -170.0
        assert normalize_longitude(-190.0) == 170.0
        assert normalize_longitude(540.0) == -180.0

    def test_positive_180_normalizes_to_negative_180(self) -> None:
        """Python's % would return +180 here; the range is half-open."""
        assert normalize_longitude(180.0) == -180.0


class TestLargestLongitudeGap:
    def test_no_gap_for_single_value(self) -> None:
        assert largest_longitude_gap([42.0]) == (42.0, 42.0, 0.0)

    def test_finds_interior_gap(self) -> None:
        """Two clusters either side of the prime meridian.

        The 220-degree gap between them beats the 120-degree wraparound, so the
        data is treated as occupying the eastern arc through the antimeridian.
        """
        start, end, width = largest_longitude_gap([-170.0, -160.0, 60.0, 70.0])
        assert (start, end) == (-160.0, 60.0)
        assert width == 220.0

    def test_wraparound_gap_wins_when_it_is_larger(self) -> None:
        """Both clusters in the western hemisphere: the empty arc is the wrap."""
        start, end, width = largest_longitude_gap([-10.0, -5.0, 100.0, 105.0])
        assert (start, end) == (105.0, -10.0)
        assert width == 245.0

    def test_finds_wraparound_gap(self) -> None:
        """Data clustered near the antimeridian: the empty arc is the far side."""
        start, end, width = largest_longitude_gap([170.0, 175.0, -175.0, -170.0])
        assert (start, end) == (-170.0, 170.0)
        assert width == 340.0


class TestExtentFromLongitudesLatitudes:
    def test_returns_none_for_empty_input(self) -> None:
        assert extent_from_longitudes_latitudes([], []) is None

    def test_simple_extent_does_not_wrap(self) -> None:
        extent = extent_from_longitudes_latitudes([10.0, 20.0, 15.0], [0.0, 5.0])
        assert extent is not None
        assert (extent.west, extent.east) == (10.0, 20.0)
        assert extent.crosses_antimeridian is False
        assert extent.width_degrees == 10.0

    def test_antimeridian_crossing_is_detected(self) -> None:
        """The case the incumbent gets wrong."""
        extent = extent_from_longitudes_latitudes(
            [178.0, 179.5, -179.0, -177.0], [50.0, 60.0]
        )
        assert extent is not None
        assert extent.crosses_antimeridian is True
        assert extent.west == 178.0
        assert extent.east == -177.0
        # 5 degrees wide, not the 356.5 a naive min/max would give.
        assert extent.width_degrees == 5.0

    def test_alaska_sample_is_tight_not_global(self) -> None:
        """Regression guard using the real dataset's span.

        Naive min/max over these gives 358.9 degrees -- 99.7% of the world.
        """
        longitudes = [-179.13, -160.0, -140.0, 172.0, 179.78]
        extent = extent_from_longitudes_latitudes(longitudes, [51.0, 71.0])
        assert extent is not None
        assert extent.crosses_antimeridian is True
        assert extent.width_degrees < 70.0

    def test_genuinely_wide_data_does_not_wrap(self) -> None:
        """Dense worldwide data must keep its naive box.

        Without the meaningful-gap floor this would find a spurious gap and
        report a wrapped extent for a global dataset.
        """
        longitudes = [float(v) for v in range(-179, 180, 1)]
        extent = extent_from_longitudes_latitudes(longitudes, [-60.0, 70.0])
        assert extent is not None
        assert extent.crosses_antimeridian is False
        assert extent.width_degrees > 350.0

    def test_center_of_wrapped_extent_is_on_the_data(self) -> None:
        extent = extent_from_longitudes_latitudes([179.0, -179.0], [10.0, 20.0])
        assert extent is not None
        lon, lat = extent.center
        assert lat == 15.0
        # The midpoint sits on the antimeridian, not at longitude 0.
        assert abs(lon) > 179.0


class TestIterCoordinates:
    def test_point(self) -> None:
        coords = list(iter_coordinates({"type": "Point", "coordinates": [1.0, 2.0]}))
        assert coords == [(1.0, 2.0)]

    def test_polygon_with_hole(self) -> None:
        geometry = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]],
                [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.2]],
            ],
        }
        # 4 vertices on the outer ring plus 4 on the hole.
        assert len(list(iter_coordinates(geometry))) == 8

    def test_geometry_collection(self) -> None:
        geometry = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [1.0, 2.0]},
                {"type": "Point", "coordinates": [3.0, 4.0]},
            ],
        }
        assert list(iter_coordinates(geometry)) == [(1.0, 2.0), (3.0, 4.0)]

    def test_ignores_elevation_in_xyz_positions(self) -> None:
        coords = list(
            iter_coordinates({"type": "Point", "coordinates": [1.0, 2.0, 300.0]})
        )
        assert coords == [(1.0, 2.0)]


class TestExtentFromGeojson:
    def test_feature_collection(self) -> None:
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"geometry": {"type": "Point", "coordinates": [10.0, 40.0]}},
                {"geometry": {"type": "Point", "coordinates": [20.0, 50.0]}},
            ],
        }
        extent = extent_from_geojson(fc)
        assert extent is not None
        assert (extent.west, extent.south, extent.east, extent.north) == (
            10.0,
            40.0,
            20.0,
            50.0,
        )

    def test_tolerates_features_without_geometry(self) -> None:
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"geometry": None},
                {"geometry": {"type": "Point", "coordinates": [5.0, 5.0]}},
            ],
        }
        extent = extent_from_geojson(fc)
        assert extent is not None
        assert extent.west == 5.0

    def test_empty_collection_returns_none(self) -> None:
        assert (
            extent_from_geojson({"type": "FeatureCollection", "features": []}) is None
        )


class TestUnionExtents:
    def test_ignores_none(self) -> None:
        extent = extent_from_longitudes_latitudes([1.0, 2.0], [1.0, 2.0])
        assert union_extents([None, extent, None]) == extent

    def test_all_none_returns_none(self) -> None:
        assert union_extents([None, None]) is None

    def test_combines_disjoint_extents(self) -> None:
        a = extent_from_longitudes_latitudes([0.0, 10.0], [0.0, 10.0])
        b = extent_from_longitudes_latitudes([20.0, 30.0], [-5.0, 5.0])
        union = union_extents([a, b])
        assert union is not None
        assert (union.west, union.east) == (0.0, 30.0)
        assert (union.south, union.north) == (-5.0, 10.0)

    def test_union_can_detect_a_crossing_neither_input_had(self) -> None:
        a = extent_from_longitudes_latitudes([170.0, 179.0], [0.0, 10.0])
        b = extent_from_longitudes_latitudes([-179.0, -170.0], [0.0, 10.0])
        union = union_extents([a, b])
        assert union is not None
        assert union.crosses_antimeridian is True
        assert union.width_degrees == 20.0

    def test_wide_extent_survives_union_without_wrapping(self) -> None:
        """The densification guard: a single wide extent must not be wrapped."""
        wide = extent_from_longitudes_latitudes(
            [float(v) for v in range(-179, 180, 1)], [-10.0, 10.0]
        )
        union = union_extents([wide])
        assert union is not None
        assert union.crosses_antimeridian is False
        assert union.width_degrees > 350.0
