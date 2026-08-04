"""Label placement - pure geometry, so it runs in CI without QGIS.

OnlyMap draws labels with a `TextLayer`, which needs one point per label. These
tests pin down where that point lands for each geometry kind, because a label
that drifts off its feature is the kind of defect that looks like a styling
problem and is actually a maths problem.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.label_points import (
    LABEL_PROPERTY,
    apply_capitalization,
    apply_wrapping,
    build_label_collection,
    collect_character_set,
    representative_point,
)

# A unit square, closed. Its centroid is unambiguous, which makes it the right
# shape for asserting the centroid maths rather than a tolerance.
SQUARE = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]


def feature(geometry, **properties):
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class TestRepresentativePoint:
    def test_point_is_its_own_label_anchor(self) -> None:
        geometry = {"type": "Point", "coordinates": [3.5, -1.25]}
        assert representative_point(geometry) == (3.5, -1.25)

    def test_polygon_uses_the_area_weighted_centroid(self) -> None:
        geometry = {"type": "Polygon", "coordinates": [SQUARE]}
        assert representative_point(geometry) == (1.0, 1.0)

    def test_dense_edge_does_not_drag_the_centroid(self) -> None:
        """The regression the vertex average would cause.

        Extra vertices along one edge change nothing about where the shape is,
        so they must change nothing about where its label sits. A plain vertex
        average fails this by a wide margin.
        """
        dense = [
            [0.0, 0.0],
            [0.5, 0.0],
            [1.0, 0.0],
            [1.5, 0.0],
            [2.0, 0.0],
            [2.0, 2.0],
            [0.0, 2.0],
            [0.0, 0.0],
        ]
        centroid = representative_point({"type": "Polygon", "coordinates": [dense]})
        assert centroid == (1.0, 1.0)

    def test_line_label_sits_on_a_vertex(self) -> None:
        """Never the bbox centre: on an L-shaped line that is off the feature."""
        geometry = {
            "type": "LineString",
            "coordinates": [[0.0, 0.0], [0.0, 10.0], [10.0, 10.0]],
        }
        assert representative_point(geometry) in {(0.0, 10.0)}

    def test_multipolygon_labels_the_largest_part(self) -> None:
        tiny = [[10.0, 10.0], [10.1, 10.0], [10.1, 10.1], [10.0, 10.1], [10.0, 10.0]]
        geometry = {"type": "MultiPolygon", "coordinates": [[tiny], [SQUARE]]}
        assert representative_point(geometry) == (1.0, 1.0)

    def test_degenerate_ring_falls_back_instead_of_dividing_by_zero(self) -> None:
        flat = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [0.0, 0.0]]
        assert representative_point({"type": "Polygon", "coordinates": [flat]}) == (
            0.75,
            0.0,
        )

    def test_an_empty_outer_ring_drops_the_label_rather_than_raising(self) -> None:
        """A cleared or half-digitised polygon must not abort the whole export.

        `_ring_centroid` returns None for a ring under three vertices, and the
        vertex-average fallback divides by the vertex count - so an empty ring
        reached ZeroDivisionError and took every other layer down with it.
        """
        assert representative_point({"type": "Polygon", "coordinates": [[]]}) is None
        two_vertices = [[0.0, 0.0], [1.0, 1.0]]
        assert (
            representative_point({"type": "Polygon", "coordinates": [two_vertices]})
            is None
        )

    def test_empty_and_unknown_geometries_yield_nothing(self) -> None:
        assert representative_point(None) is None
        assert representative_point({"type": "Point", "coordinates": []}) is None
        assert representative_point({"type": "Nonsense", "coordinates": [1]}) is None


class TestLabelCollection:
    def test_carries_one_point_per_labelled_feature(self) -> None:
        source = collection(
            feature({"type": "Point", "coordinates": [1.0, 2.0]}, name="Alpha"),
            feature({"type": "Point", "coordinates": [3.0, 4.0]}, name="Beta"),
        )
        built = build_label_collection(source, "name")

        assert built is not None
        assert [f["properties"][LABEL_PROPERTY] for f in built["features"]] == [
            "Alpha",
            "Beta",
        ]

    def test_drops_the_source_geometry(self) -> None:
        """The whole reason label points are a separate collection.

        Reusing the layer's own data would embed every polygon ring twice.
        """
        source = collection(
            feature({"type": "Polygon", "coordinates": [SQUARE]}, name="Alpha")
        )
        built = build_label_collection(source, "name")

        assert built["features"][0]["geometry"]["type"] == "Point"
        assert built["features"][0]["properties"] == {LABEL_PROPERTY: "Alpha"}

    def test_blank_and_missing_values_are_skipped(self) -> None:
        source = collection(
            feature({"type": "Point", "coordinates": [1.0, 1.0]}, name="  "),
            feature({"type": "Point", "coordinates": [2.0, 2.0]}, name=None),
            feature({"type": "Point", "coordinates": [3.0, 3.0]}),
            feature({"type": "Point", "coordinates": [4.0, 4.0]}, name="Real"),
        )
        built = build_label_collection(source, "name")

        assert len(built["features"]) == 1
        assert built["features"][0]["properties"][LABEL_PROPERTY] == "Real"

    def test_non_string_values_are_rendered_as_text(self) -> None:
        source = collection(
            feature({"type": "Point", "coordinates": [1.0, 1.0]}, count=42)
        )
        built = build_label_collection(source, "count")
        assert built["features"][0]["properties"][LABEL_PROPERTY] == "42"

    def test_nothing_labellable_yields_none(self) -> None:
        """So the caller omits the TextLayer rather than emitting an empty one."""
        source = collection(
            feature({"type": "Point", "coordinates": [1.0, 1.0]}, other="x")
        )
        assert build_label_collection(source, "name") is None
        assert build_label_collection(None, "name") is None


class TestCharacterSet:
    def test_plain_ascii_needs_no_declaration(self) -> None:
        built = build_label_collection(
            collection(feature({"type": "Point", "coordinates": [0.0, 0.0]}, n="Ash")),
            "n",
        )
        assert collect_character_set(built) is None

    def test_non_ascii_characters_are_declared_and_sorted(self) -> None:
        """Without this the glyphs render blank against the default atlas."""
        built = build_label_collection(
            collection(
                feature({"type": "Point", "coordinates": [0.0, 0.0]}, n="Zürich")
            ),
            "n",
        )
        characters = collect_character_set(built)

        assert characters is not None
        assert "ü" in characters
        assert list(characters) == sorted(characters), "must be byte-stable"


class TestCapitalization:
    """QGIS's text case reaches the string, because there is nowhere else.

    The web renderer has no `text-transform`, and neither does QGIS - it changes
    the glyphs it draws. Doing it here is the same operation in the same place.
    """

    def test_none_leaves_the_text_alone(self) -> None:
        assert apply_capitalization("mIxEd Case", "none") == "mIxEd Case"

    def test_upper_and_lower(self) -> None:
        assert apply_capitalization("Fort Yukon", "upper") == "FORT YUKON"
        assert apply_capitalization("Fort Yukon", "lower") == "fort yukon"

    def test_capitalize_keeps_the_rest_of_the_string(self) -> None:
        """QGIS's "force first letter to capital" raises the first letter only.
        `str.capitalize()` lowercases the remainder, which would turn "USA
        Route" into "Usa route" - a different name."""
        assert apply_capitalization("USA Route", "capitalize") == "USA Route"
        assert apply_capitalization("usa route", "capitalize") == "Usa route"

    def test_title_case(self) -> None:
        assert apply_capitalization("fort yukon", "title") == "Fort Yukon"

    def test_an_empty_string_survives_every_mode(self) -> None:
        for mode in ("none", "upper", "lower", "capitalize", "title"):
            assert apply_capitalization("", mode) == ""

    def test_an_unknown_mode_leaves_the_text_alone(self) -> None:
        """QGIS's UpperCamelCase has no readable label form, so it falls through
        rather than running the words together."""
        assert apply_capitalization("fort yukon", "camel") == "fort yukon"


class TestWrapping:
    """QGIS line breaks become real newlines, which deck.gl honours."""

    def test_the_wrap_character_becomes_a_newline(self) -> None:
        assert apply_wrapping("Fort|Yukon", "|", 0) == "Fort\nYukon"

    def test_no_wrapping_configured_changes_nothing(self) -> None:
        assert apply_wrapping("Fort Yukon", "", 0) == "Fort Yukon"

    def test_auto_wrap_breaks_on_whitespace(self) -> None:
        assert apply_wrapping("aaa bbb ccc", "", 7) == "aaa bbb\nccc"

    def test_auto_wrap_never_splits_a_word(self) -> None:
        """A broken word is unreadable, and QGIS does not do it either - so a
        word longer than the wrap width overflows rather than being cut."""
        assert apply_wrapping("Chugach Mountains", "", 5) == "Chugach\nMountains"
        assert apply_wrapping("Antidisestablishmentarianism", "", 5) == (
            "Antidisestablishmentarianism"
        )

    def test_a_short_label_keeps_its_own_spacing(self) -> None:
        """`textwrap` collapses runs of whitespace and strips, so a label short
        enough to need no wrapping would still come out changed."""
        assert apply_wrapping("A  B", "", 40) == "A  B"

    def test_the_explicit_break_is_applied_before_the_automatic_one(self) -> None:
        assert apply_wrapping("aaa bbb|ccc ddd", "|", 7) == "aaa bbb\nccc ddd"


class TestLabelCollectionTransforms:
    def test_the_collection_carries_the_transformed_text(self) -> None:
        collection = build_label_collection(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                        "properties": {"name": "fort yukon"},
                    }
                ],
            },
            "name",
            capitalization="upper",
        )
        assert collection["features"][0]["properties"][LABEL_PROPERTY] == "FORT YUKON"

    def test_the_default_is_the_untouched_value(self) -> None:
        collection = build_label_collection(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                        "properties": {"name": "fort yukon"},
                    }
                ],
            },
            "name",
        )
        assert collection["features"][0]["properties"][LABEL_PROPERTY] == "fort yukon"

    def test_a_wrapped_label_declares_its_newline_in_the_character_set(self) -> None:
        """`text-character-set` replaces the atlas, so a newline that is not in
        it would break the very labels the wrap was meant to lay out."""
        collection = build_label_collection(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                        "properties": {"name": "Fort|Yukon"},
                    }
                ],
            },
            "name",
            wrap_char="|",
        )
        assert "\n" in collection["features"][0]["properties"][LABEL_PROPERTY]
