"""Choosing the zoom where labels stop piling up, and where points stop merging.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.declutter import (
    CLUSTER_MIN_POINTS,
    cluster_max_zoom,
    declutter_layer,
    label_min_zoom,
)
from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    GeometryKind,
    LabelingSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)


def _grid(
    count: int, spacing: float, text: str = "Label"
) -> list[tuple[float, float, str]]:
    """`count` points in a row near the equator, `spacing` degrees apart."""
    return [(i * spacing, 0.0, text) for i in range(count)]


class TestLabelMinZoom:
    def test_spread_out_labels_need_no_limit(self) -> None:
        # 90 degrees apart (~64 px at zoom 0): room for a short label at every zoom.
        assert label_min_zoom(_grid(4, 90.0), font_px=12) is None

    def test_dense_labels_wait_until_there_is_room(self) -> None:
        # ~110 m apart: they only stop overlapping well into city zooms.
        zoom = label_min_zoom(_grid(50, 0.001), font_px=12)
        assert zoom is not None
        assert 12 <= zoom <= 18

    def test_longer_text_needs_a_closer_zoom(self) -> None:
        short = label_min_zoom(_grid(50, 0.01, "A"), font_px=12)
        long = label_min_zoom(_grid(50, 0.01, "A much longer label"), font_px=12)
        assert short is not None and long is not None
        assert long > short

    def test_a_single_label_never_hides(self) -> None:
        assert label_min_zoom(_grid(1, 1.0), font_px=12) is None


class TestClusterMaxZoom:
    def test_a_small_layer_does_not_cluster(self) -> None:
        points = [(x, y) for x, y, _ in _grid(CLUSTER_MIN_POINTS - 1, 0.001)]
        assert cluster_max_zoom(points, radius_px=4) is None

    def test_a_dense_layer_clusters_until_its_points_have_room(self) -> None:
        points = [(x, y) for x, y, _ in _grid(500, 0.001)]
        zoom = cluster_max_zoom(points, radius_px=4)
        assert zoom is not None
        assert 8 <= zoom <= 18

    def test_a_large_but_spread_layer_does_not_cluster(self) -> None:
        # A 10 x 10 world grid, 16+ degrees apart: every marker has room even
        # zoomed right out.
        points = [
            (-170.0 + 34 * i, -72.0 + 16 * j) for i in range(10) for j in range(10)
        ]
        assert len(points) >= CLUSTER_MIN_POINTS
        assert cluster_max_zoom(points, radius_px=3) is None


def test_a_wrapped_label_is_as_wide_as_its_longest_line() -> None:
    one_line = label_min_zoom(_grid(50, 0.01, "Long label text here"), font_px=12)
    wrapped = label_min_zoom(_grid(50, 0.01, "Long label\ntext here"), font_px=12)
    assert one_line is not None and wrapped is not None
    assert wrapped < one_line


def _layer(
    count: int,
    spacing: float,
    kind: GeometryKind = GeometryKind.POINT,
    labelled: bool = True,
    **overrides: object,
) -> ExportLayer:
    geometry = "Point" if kind is GeometryKind.POINT else "LineString"
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": geometry,
                "coordinates": [x, y]
                if geometry == "Point"
                else [[x, y], [x + spacing / 2, y]],
            },
            "properties": {"name": f"Place {i}"},
        }
        for i, (x, y, _) in enumerate(_grid(count, spacing))
    ]
    fields: dict[str, object] = dict(
        layer_id="places",
        name="Places",
        geometry_kind=kind,
        source_kind=SourceKind.FILE,
        feature_count=count,
        geojson={"type": "FeatureCollection", "features": features},
        renderer=RendererSpec(kind=RendererKind.SINGLE, symbol=SymbolSpec(radius=4)),
        labeling=LabelingSpec(enabled=labelled, field_name="name"),
    )
    fields.update(overrides)
    return ExportLayer(**fields)  # type: ignore[arg-type]


class TestDeclutterLayer:
    def test_a_dense_point_layer_clusters_and_its_labels_wait_for_the_split(
        self,
    ) -> None:
        layer = declutter_layer(_layer(500, 0.001), default_font_px=12)
        assert layer.cluster_max_zoom is not None
        assert layer.label_min_zoom is not None
        assert layer.label_min_zoom > layer.cluster_max_zoom

    def test_lines_never_cluster_but_their_labels_still_wait(self) -> None:
        layer = declutter_layer(
            _layer(500, 0.001, kind=GeometryKind.LINE), default_font_px=12
        )
        assert layer.cluster_max_zoom is None
        assert layer.label_min_zoom is not None

    def test_an_unlabelled_layer_gets_no_label_limit(self) -> None:
        layer = declutter_layer(_layer(500, 0.001, labelled=False), default_font_px=12)
        assert layer.label_min_zoom is None

    def test_a_limit_inside_the_layers_own_range_is_left_out(self) -> None:
        layer = declutter_layer(
            _layer(50, 0.01, visible_zoom_range=(17.0, 30.0)), default_font_px=12
        )
        assert layer.label_min_zoom is None

    def test_a_sparse_layer_is_returned_unchanged(self) -> None:
        original = _layer(4, 90.0)
        assert declutter_layer(original, default_font_px=12) is original

    def test_small_labels_on_big_markers_still_wait_for_the_bubbles(self) -> None:
        # Tiny text would fit long before 30-px markers stop merging; a bubble
        # must not wear one point's name.
        layer = declutter_layer(
            _layer(
                500,
                0.001,
                renderer=RendererSpec(
                    kind=RendererKind.SINGLE, symbol=SymbolSpec(radius=30)
                ),
                labeling=LabelingSpec(enabled=True, field_name="name", font_size=2),
            ),
            default_font_px=12,
        )
        assert layer.cluster_max_zoom is not None
        assert layer.label_min_zoom == layer.cluster_max_zoom + 1
