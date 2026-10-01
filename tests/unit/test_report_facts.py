"""The per-layer and whole-map facts the Fidelity tab prints.

Read from the export model alone, so these run without QGIS: each fact is
checked against the field it comes from, and a missing measurement leaves its
fact out rather than printing a guess.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    ClassificationMethod,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    GraduatedClassSpec,
    LabelingSpec,
    PopupFieldMode,
    PopupFieldSpec,
    PopupSpec,
    RasterSpec,
    RendererKind,
    RendererSpec,
    ScaleRange,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.report_facts import (
    Fact,
    facts_line,
    facts_lines,
    format_bytes,
    layer_facts,
    map_facts,
    report_facts,
)

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            "properties": {"name": "a", "kind": "school", "visitors": 3},
        }
    ],
}


def sites(**overrides) -> ExportLayer:
    defaults = dict(
        layer_id="sites",
        name="Sites",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=1240,
        geojson=GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="kind",
            categories=tuple(
                CategorySpec(value=v, label=v, symbol=SymbolSpec())
                for v in ("school", "clinic", "market", "temple", "park")
            ),
        ),
        labeling=LabelingSpec(enabled=True, field_name="name"),
        popup=PopupSpec(
            fields=(
                PopupFieldSpec("name"),
                PopupFieldSpec("kind"),
                PopupFieldSpec("visitors"),
                PopupFieldSpec("notes", mode=PopupFieldMode.HIDDEN),
            )
        ),
    )
    defaults.update(overrides)
    return ExportLayer(**defaults)


def values(facts: tuple[Fact, ...]) -> list[str]:
    return [fact.value for fact in facts]


class TestVectorLayer:
    def test_it_says_what_the_layer_is_and_how_it_draws(self) -> None:
        assert values(layer_facts(sites())) == [
            "Points",
            "1,240 features",
            "Categorized on 'kind', 5 classes",
            "Labels from 'name'",
            "Popups show 3 of 4 fields",
            "3 fields in the file",
            "From a file",
        ]

    def test_a_graduated_layer_names_its_method(self) -> None:
        layer = sites(
            renderer=RendererSpec(
                kind=RendererKind.GRADUATED,
                field_name="pop",
                classes=(GraduatedClassSpec(0, 1, "a", SymbolSpec()),),
                classification=ClassificationMethod.NATURAL_BREAKS,
            )
        )
        assert "Graduated on 'pop', 1 class (natural breaks)" in values(
            layer_facts(layer)
        )

    def test_display_settings_are_listed_when_set(self) -> None:
        layer = sites(
            visible=False,
            opacity=0.7,
            scale_range=ScaleRange(min_scale=50000, max_scale=1000),
            visible_zoom_range=(12.5, 18.2),
            group_path=("Base", "Roads"),
            popup=PopupSpec(enabled=False),
            labeling=LabelingSpec(enabled=False),
        )
        shown = values(layer_facts(layer, data_bytes=2048))
        for expected in (
            "Hidden in QGIS",
            "70% opacity",
            "Shown at zoom 12.5-18.2",
            "In Base > Roads",
            "Popups off",
            "No labels",
            "2 KB of data",
        ):
            assert expected in shown

    def test_no_size_fact_without_a_measurement(self) -> None:
        assert not any("of data" in v for v in values(layer_facts(sites())))


class TestRasterLayer:
    def test_it_says_size_bands_and_crs(self) -> None:
        layer = ExportLayer(
            layer_id="dem",
            name="DEM",
            geometry_kind=GeometryKind.RASTER,
            source_kind=SourceKind.FILE,
            raster=RasterSpec(
                path="/x/dem.tif",
                band_count=1,
                source_crs="EPSG:32648",
                pixel_width=120,
                pixel_height=80,
                resolution_x=30.0,
                resolution_y=-30.0,
                style_qml="<qgis/>",
            ),
        )
        shown = values(layer_facts(layer))
        assert shown[:4] == ["Raster", "120 x 80 pixels", "1 band", "EPSG:32648"]
        assert "30 x 30 per pixel (CRS units)" in shown
        assert "QGIS colours baked into the pixels" in shown
        assert not any("features" in v for v in shown)


class TestWholeMap:
    def test_it_sums_up_the_map(self) -> None:
        project = ExportProject(
            title="Map",
            layers=(sites(),),
            extent=Extent(west=103.6, south=1.2, east=104.05, north=1.5),
            source_crs="EPSG:3414",
            settings=ExportSettings(basemap="osm", quantize_precision=6),
        )
        assert values(map_facts(project, 3 * 1024 * 1024)) == [
            "1 layer",
            "1,240 features",
            "About 3.0 MB of data",
            "Standalone HTML",
            "Project CRS EPSG:3414",
            "Opens on the data: 103.60°E to 104.05°E, 1.20°N to 1.50°N",
            "Coordinates rounded to 6 places",
            "Basemap 'osm'",
        ]

    def test_report_facts_keys_layers_by_id_and_totals_the_sizes(self) -> None:
        project = ExportProject(title="Map", layers=(sites(),))
        facts = report_facts(project, {"sites": 1000})
        assert "1000 bytes of data" in values(facts.for_layer("sites"))
        assert "About 1000 bytes of data" in values(facts.map)
        assert facts.for_layer(None) == ()
        assert facts.for_layer("gone") == ()


def test_formatting() -> None:
    assert format_bytes(1) == "1 byte"
    assert format_bytes(5 * 1024) == "5 KB"
    assert format_bytes(int(2.5 * 1024 * 1024)) == "2.5 MB"
    assert format_bytes(40 * 1024 * 1024) == "40 MB"
    facts = (Fact("A", "one"), Fact("B", "two"))
    assert facts_line(facts) == "one · two"
    assert facts_lines(facts) == "A: one\nB: two"
