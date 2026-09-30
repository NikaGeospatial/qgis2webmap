"""Opt-in user research: the rules that decide what may leave the machine.

Everything here is pure Python and runs without QGIS. The wire format is the
contract in `specs/2026-09-30-user-research.md`, shared with the server.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from nika_onlymap_exporter.core.export_ir import (
    ElevationSpec,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    LabelingSpec,
    OutputMode,
    PopupFieldMode,
    PopupFieldSpec,
    PopupSpec,
    RasterSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
)
from nika_onlymap_exporter.research import consent, text
from nika_onlymap_exporter.research.contact import normalise_email
from nika_onlymap_exporter.research.envelope import Environment, os_name
from nika_onlymap_exporter.research.fingerprint import (
    map_key,
    structure_fingerprint,
)
from nika_onlymap_exporter.research.map_report import (
    FEATURES_USED,
    LAYER_KINDS,
    LAYER_SOURCES,
    LAYER_STYLES,
    MAX_FIELDS,
    MAX_LAYERS,
    MAX_REPORT_BYTES,
    OUTPUTS,
    LayerFacts,
    ProjectFacts,
    build_map_report,
    encoded_size,
    extent_band,
    extent_cell,
    feature_band,
)
from nika_onlymap_exporter.research.profile import (
    AUDIENCES,
    SECTORS,
    USE_CASES,
    Profile,
)
from nika_onlymap_exporter.research.project_facts import format_from_source
from nika_onlymap_exporter.research.queue import MAX_QUEUED, enqueue
from nika_onlymap_exporter.research.service import ResearchService
from nika_onlymap_exporter.research.state import load_state, save_state
from nika_onlymap_exporter.research.tally import (
    FAILURE_CLASSES,
    WeekCounters,
    build_tally,
    iso_week,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "specs" / "2026-09-30-user-research.md"

ENV = Environment.build("0.1.5", "3.44.1-Solothurn", "linux")
EMPTY = {"type": "FeatureCollection", "features": []}

ENVELOPE_KEYS = {"schema", "kind", "plugin_version", "qgis_version", "os"}
MAP_KEYS = ENVELOPE_KEYS | {
    "profile",
    "title",
    "layers",
    "crs",
    "extent_band",
    "cell",
    "basemap",
    "terrain",
    "features_used",
    "output",
    "temporal",
}
LAYER_KEYS = {
    "name",
    "kind",
    "source",
    "format",
    "features",
    "fields",
    "has_time_field",
    "labelled",
    "popup",
    "style",
}
TALLY_KEYS = ENVELOPE_KEYS | {
    "profile",
    "week",
    "exports",
    "previews",
    "publishes",
    "distinct_maps",
    "failures",
    "recurring_maps",
}
CONTACT_KEYS = ENVELOPE_KEYS | {
    "email",
    "audience",
    "sector",
    "use_cases",
    "use_case_other",
}
PROFILE_KEYS = {"audience", "sector", "use_cases", "use_case_other"}


def make_layer(
    name: str = "Roads",
    fields: tuple[str, ...] = ("name", "surface"),
    features: int = 250,
    **overrides: object,
) -> ExportLayer:
    layer = ExportLayer(
        layer_id=f"{name}_id",
        name=name,
        geometry_kind=GeometryKind.LINE,
        source_kind=SourceKind.FILE,
        feature_count=features,
        geojson=EMPTY,
        popup=PopupSpec(fields=tuple(PopupFieldSpec(name=f) for f in fields)),
        renderer=RendererSpec(kind=RendererKind.CATEGORIZED, field_name="surface"),
    )
    return replace(layer, **overrides)  # type: ignore[arg-type]


def make_export(
    layers: tuple[ExportLayer, ...] | None = None,
    title: str = "Kranji site survey",
    extent: Extent | None = None,
    **settings: object,
) -> ExportProject:
    return ExportProject(
        title=title,
        layers=layers if layers is not None else (make_layer(),),
        extent=extent or Extent(west=103.70, south=1.38, east=103.74, north=1.42),
        source_crs="EPSG:3414",
        settings=replace(ExportSettings(), **settings),  # type: ignore[arg-type]
    )


class Clock:
    def __init__(self, day: date) -> None:
        self.day = day

    def __call__(self) -> date:
        return self.day


@pytest.fixture
def clock() -> Clock:
    return Clock(date(2026, 10, 7))  # a Wednesday, ISO week 2026-W41


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> ResearchService:
    return ResearchService(tmp_path / "research.json", ENV, today=clock)


def queued_bodies(service: ResearchService) -> list[dict[str, object]]:
    return [json.loads(item.body) for item in service.state.queue]


# ---- Spec strings ---------------------------------------------------------


def _spec_list(name: str) -> list[str]:
    text_ = SPEC.read_text(encoding="utf-8")
    block = text_.split(f"`{name}`:", 1)[1].split("\n\n", 1)[0]
    return re.findall(r"`([a-z_]+)`", block)


class TestProfileMatchesSpec:
    def test_sectors(self) -> None:
        assert list(SECTORS) == _spec_list("SECTORS")

    def test_use_cases(self) -> None:
        assert list(USE_CASES) == _spec_list("USE_CASES")

    def test_audiences(self) -> None:
        assert list(AUDIENCES) == ["client", "public", "team", "self"]

    def test_unknown_values_are_dropped(self) -> None:
        profile = Profile.build("aliens", "space", ["monitoring", "hacking"], None)
        assert profile.audience is None
        assert profile.sector is None
        assert profile.use_cases == ("monitoring",)

    def test_other_text_is_scrubbed_to_140(self) -> None:
        profile = Profile.build(
            "team", "water", ["other"], "Call me on +65 9123 4567 " + "x" * 300
        )
        assert profile.use_case_other is not None
        assert "9123" not in profile.use_case_other
        assert len(profile.use_case_other) == 140

    def test_wire_shape(self) -> None:
        wire = Profile.build("client", "forestry", ["inventory"], "").to_wire()
        assert set(wire) == PROFILE_KEYS
        assert wire["use_case_other"] is None


# ---- Map report -----------------------------------------------------------


class TestMapReport:
    def test_only_allow_listed_keys(self) -> None:
        report = build_map_report(make_export(), ENV, Profile())
        assert set(report) == MAP_KEYS
        for layer in report["layers"]:
            assert set(layer) == LAYER_KEYS

    def test_values_are_in_their_enums(self) -> None:
        report = build_map_report(
            make_export(output_mode=OutputMode.SHARE_ZIP), ENV, Profile()
        )
        layer = report["layers"][0]
        assert layer["kind"] in LAYER_KINDS
        assert layer["source"] in LAYER_SOURCES
        assert layer["style"] == "categorised"
        assert layer["style"] in LAYER_STYLES
        assert report["output"] == "zip"
        assert report["output"] in OUTPUTS
        assert set(report["features_used"]) <= set(FEATURES_USED)
        assert report["schema"] == 1
        assert report["kind"] == "map"
        assert report["qgis_version"] == "3.44.1"

    def test_no_coordinate_finer_than_a_degree(self) -> None:
        report = build_map_report(make_export(), ENV, Profile())
        assert report["cell"] == [1, 104]
        body = json.dumps(report)
        assert "103.7" not in body
        assert "1.38" not in body

    def test_no_field_values_travel(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "SECRET-VALUE", "surface": "gravel"},
                    "geometry": {"type": "Point", "coordinates": [103.123, 1.456]},
                }
            ],
        }
        layer = make_layer(geojson=geojson)
        body = json.dumps(build_map_report(make_export((layer,)), ENV, Profile()))
        assert "SECRET-VALUE" not in body
        assert "gravel" not in body
        assert "103.123" not in body
        assert "surface" in body  # the field NAME is the point

    def test_names_are_scrubbed(self) -> None:
        layer = make_layer(
            name="/home/alice/clients/acme/parcels.shp",
            fields=("owner_email", "https://x.com/f", "tel +65 9123 4567"),
        )
        report = build_map_report(
            make_export((layer,), title="Map for bob@acme.com"), ENV, Profile()
        )
        assert report["title"] == "Map for [email]"
        assert report["layers"][0]["name"] == "parcels"
        assert report["layers"][0]["fields"] == ["owner_email", "tel [number]"]

    def test_features_are_banded(self) -> None:
        assert [feature_band(n) for n in (0, 1, 100, 101, 1000, 1001)] == [
            "0",
            "1-100",
            "1-100",
            "101-1k",
            "101-1k",
            "1k-10k",
        ]
        assert feature_band(10_000) == "1k-10k"
        assert feature_band(100_000) == "10k-100k"
        assert feature_band(100_001) == "100k+"

    @pytest.mark.parametrize(
        ("extent", "band"),
        [
            (Extent(103.80, 1.30, 103.81, 1.31), "site"),
            (Extent(103.7, 1.3, 103.9, 1.45), "city"),
            (Extent(101.0, 2.0, 103.0, 4.0), "region"),
            (Extent(-10.0, 35.0, 10.0, 55.0), "country"),
            (Extent(-170.0, -60.0, 170.0, 70.0), "world"),
        ],
    )
    def test_extent_band(self, extent: Extent, band: str) -> None:
        assert extent_band(extent) == band

    def test_antimeridian_box_is_measured_across_the_dateline(self) -> None:
        # Fiji: 179E to 179W is 2 degrees wide, not 358.
        extent = Extent(179.0, -18.0, -179.0, -16.0, crosses_antimeridian=True)
        assert extent_band(extent) == "region"
        assert extent_cell(extent) == [-17, 180]

    def test_limits(self) -> None:
        layers = tuple(
            make_layer(
                name=f"L{i}", fields=tuple(f"field_{j}_{'x' * 40}" for j in range(80))
            )
            for i in range(130)
        )
        report = build_map_report(make_export(layers), ENV, Profile())
        assert len(report["layers"]) <= MAX_LAYERS
        assert all(len(layer["fields"]) <= MAX_FIELDS for layer in report["layers"])
        assert encoded_size(report) <= MAX_REPORT_BYTES

    def test_the_largest_layers_lose_fields_first(self) -> None:
        wide = tuple(
            make_layer(
                name=f"Wide{i}", fields=tuple(f"f{j}_{'y' * 50}" for j in range(50))
            )
            for i in range(40)
        )
        narrow = make_layer(name="Narrow", fields=("a", "b", "c"))
        report = build_map_report(make_export((narrow, *wide)), ENV, Profile())
        assert encoded_size(report) <= MAX_REPORT_BYTES
        by_name = {layer["name"]: layer for layer in report["layers"]}
        assert len(by_name) == 41
        assert by_name["Narrow"]["fields"] == ["a", "b", "c"]
        assert 3 <= len(by_name["Wide0"]["fields"]) < 50

    def test_matches_the_server_patterns(self) -> None:
        # nika-cf-workers apps/control-plane/src/research/schema.ts
        format_pattern = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,19}$")
        preset_pattern = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
        version_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$")
        token_pattern = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
        report = build_map_report(
            make_export(basemap="dark-matter", terrain="terrarium"), ENV, Profile()
        )
        assert all(format_pattern.match(layer["format"]) for layer in report["layers"])
        assert preset_pattern.match(report["basemap"] or "")
        assert version_pattern.match(report["plugin_version"])
        assert version_pattern.match(report["qgis_version"])
        assert len(report["features_used"]) <= 32
        assert all(token_pattern.match(token) for token in report["features_used"])

    def test_features_used(self) -> None:
        layer = make_layer(
            labeling=LabelingSpec(enabled=True, field_name="name"),
            elevation=ElevationSpec(extruded=True),
            visible_zoom_range=(10.0, 16.0),
        )
        report = build_map_report(
            make_export((layer,), terrain="terrarium", show_legend=True),
            ENV,
            Profile(),
            hosted=True,
            password=True,
        )
        assert report["features_used"] == [
            "legend",
            "popups",
            "relief",
            "extrusion",
            "labels",
            "zoom_range",
            "password",
        ]
        assert report["terrain"] == "terrarium"
        assert report["basemap"] is None
        assert report["output"] == "hosted"

    def test_facts_supply_format_time_and_temporal(self) -> None:
        layer = make_layer()
        facts = ProjectFacts(
            layers={layer.layer_id: LayerFacts(format="gpkg", has_time_field=True)},
            temporal=True,
        )
        report = build_map_report(make_export((layer,)), ENV, Profile(), facts=facts)
        assert report["layers"][0]["format"] == "gpkg"
        assert report["layers"][0]["has_time_field"] is True
        assert report["temporal"] is True

    def test_raster_format_from_its_own_extension(self) -> None:
        raster = make_layer(
            name="DEM",
            geometry_kind=GeometryKind.RASTER,
            geojson=None,
            raster=RasterSpec(path="/secret/dir/dem.tiff"),
            feature_count=0,
        )
        report = build_map_report(make_export((raster,)), ENV, Profile())
        layer = report["layers"][0]
        assert layer["format"] == "tif"
        assert layer["kind"] == "raster"
        assert layer["fields"] == []
        assert "secret" not in json.dumps(report)

    def test_unknown_format_is_other(self) -> None:
        facts = ProjectFacts(layers={"Roads_id": LayerFacts(format="evil/../x")})
        report = build_map_report(make_export(), ENV, Profile(), facts=facts)
        assert report["layers"][0]["format"] == "other"

    def test_a_crs_that_is_not_an_authid_is_dropped(self) -> None:
        export = replace(make_export(), source_crs="+proj=tmerc +lat_0=1.366")
        assert build_map_report(export, ENV, Profile())["crs"] is None


class TestFormatFromSource:
    @pytest.mark.parametrize(
        ("provider", "source", "expected"),
        [
            ("ogr", "/home/a/roads.shp", "shp"),
            ("ogr", "C:\\x\\sites.gpkg|layername=sites", "gpkg"),
            ("ogr", "/x/y.GeoJSON", "geojson"),
            ("gdal", "/x/dem.tiff", "tif"),
            ("delimitedtext", "file:///x.csv?delimiter=,", "csv"),
            ("postgres", "dbname='x' password='secret'", "postgres"),
            ("wms", "type=xyz&url=https://tile.example/{z}/{x}/{y}.png", "xyz"),
            ("wms", "crs=EPSG:3857&url=https://example/wms", "wms"),
            ("memory", "Point?crs=EPSG:4326", "memory"),
            ("ogr", "/x/weird.zzz", "other"),
            ("mystery", "anything", "other"),
        ],
    )
    def test_format(self, provider: str, source: str, expected: str) -> None:
        assert format_from_source(provider, source) == expected


# ---- Fingerprint ----------------------------------------------------------


class TestFingerprint:
    SALT = "a" * 64

    def test_stable(self) -> None:
        assert structure_fingerprint(
            self.SALT, "/p.qgz", make_export()
        ) == structure_fingerprint(self.SALT, "/p.qgz", make_export())

    def test_layer_order_does_not_matter(self) -> None:
        a, b = make_layer("A"), make_layer("B")
        assert structure_fingerprint(
            self.SALT, "p", make_export((a, b))
        ) == structure_fingerprint(self.SALT, "p", make_export((b, a)))

    def test_changes_on_rename_add_and_field_change(self) -> None:
        base = structure_fingerprint(self.SALT, "p", make_export())
        renamed = make_export((make_layer("Streets"),))
        added = make_export((make_layer(), make_layer("Rivers")))
        fields = make_export((make_layer(fields=("name", "width")),))
        prints = {
            structure_fingerprint(self.SALT, "p", export)
            for export in (renamed, added, fields)
        }
        assert base not in prints
        assert len(prints) == 3

    def test_a_different_salt_gives_a_different_hash(self) -> None:
        export = make_export()
        assert structure_fingerprint("a", "p", export) != structure_fingerprint(
            "b", "p", export
        )
        assert map_key("a", "p") != map_key("b", "p")

    def test_style_changes_do_not_count(self) -> None:
        restyled = make_export(
            (make_layer(renderer=RendererSpec(kind=RendererKind.SINGLE)),)
        )
        assert structure_fingerprint(
            self.SALT, "p", make_export()
        ) == structure_fingerprint(self.SALT, "p", restyled)

    def test_neither_salt_nor_hash_is_ever_queued(
        self, service: ResearchService
    ) -> None:
        service.choose(True)
        service.record_output(make_export(), "/p.qgz")
        service.record_output(make_export((make_layer("Other"),)), "/p.qgz")
        salt = service.state.salt
        secrets_ = [salt, *service.state.sent_fingerprints]
        secrets_ += list(service.state.map_first_week)
        for item in service.state.queue:
            for secret in secrets_:
                assert secret not in item.body


# ---- Tally ----------------------------------------------------------------


class TestTally:
    def test_iso_week(self) -> None:
        assert iso_week(date(2026, 10, 7)) == "2026-W41"
        assert iso_week(date(2027, 1, 1)) == "2026-W53"

    def test_shape(self) -> None:
        counters = WeekCounters(exports=2, failures={"blocked": 1})
        tally = build_tally("2026-W41", counters, {}, ENV, Profile())
        assert set(tally) == TALLY_KEYS
        assert set(tally["failures"]) == set(FAILURE_CLASSES)
        assert tally["failures"]["blocked"] == 1

    def test_unknown_failure_class_is_other(self) -> None:
        counters = WeekCounters()
        counters.add_failure("disk_on_fire")
        assert counters.failures == {"other": 1}

    def test_nothing_until_the_week_is_over(
        self, service: ResearchService, clock: Clock
    ) -> None:
        service.choose(True)
        service.record_preview()
        service.record_failure("read_error")
        assert service.prepare_flush() == []

        clock.day += timedelta(days=7)
        flushed = [json.loads(item.body) for item in service.prepare_flush()]
        tallies = [body for body in flushed if body["kind"] == "tally"]
        assert len(tallies) == 1
        assert tallies[0]["week"] == "2026-W41"
        assert tallies[0]["previews"] == 1
        assert tallies[0]["failures"]["read_error"] == 1

    def test_once_per_week(self, service: ResearchService, clock: Clock) -> None:
        service.choose(True)
        service.record_preview()
        clock.day += timedelta(days=7)
        service.prepare_flush()
        service.prepare_flush()
        # A clock set back into the reported week does not report it again.
        clock.day -= timedelta(days=7)
        service.record_preview()
        clock.day += timedelta(days=7)
        service.prepare_flush()
        weeks = [b["week"] for b in queued_bodies(service) if b["kind"] == "tally"]
        assert weeks == ["2026-W41"]

    def test_distinct_and_recurring_maps(
        self, service: ResearchService, clock: Clock
    ) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        service.record_output(make_export(), "/a.qgz")
        service.record_output(make_export(), "/b.qgz")
        clock.day += timedelta(days=7)
        service.record_output(make_export(), "/a.qgz")  # a recurs
        service.record_output(make_export(), "/c.qgz")
        clock.day += timedelta(days=7)
        service.prepare_flush()
        tallies = [b for b in queued_bodies(service) if b["kind"] == "tally"]
        assert [(t["week"], t["exports"]) for t in tallies] == [
            ("2026-W41", 3),
            ("2026-W42", 2),
        ]
        assert [t["distinct_maps"] for t in tallies] == [2, 2]
        assert [t["recurring_maps"] for t in tallies] == [0, 1]

    def test_publishes_counted_separately(
        self, service: ResearchService, clock: Clock
    ) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz", hosted=True)
        clock.day += timedelta(days=7)
        service.prepare_flush()
        tally = next(b for b in queued_bodies(service) if b["kind"] == "tally")
        assert (tally["exports"], tally["publishes"]) == (0, 1)


# ---- Queue and state ------------------------------------------------------


class TestQueue:
    def test_cap_drops_oldest(self) -> None:
        queue: list = []
        for i in range(MAX_QUEUED + 7):
            enqueue(queue, f'{{"n":{i}}}')
        assert len(queue) == MAX_QUEUED
        assert json.loads(queue[0].body)["n"] == 7
        assert json.loads(queue[-1].body)["n"] == MAX_QUEUED + 6

    def test_persists_across_instances(
        self, tmp_path: Path, service: ResearchService, clock: Clock
    ) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        again = ResearchService(service.path, ENV, today=clock)
        assert [i.body for i in again.state.queue] == [
            i.body for i in service.state.queue
        ]
        assert again.consent == consent.SHARE

    def test_one_file_holds_everything(
        self, tmp_path: Path, service: ResearchService
    ) -> None:
        service.choose(True)
        service.save_profile(Profile.build("team", "water", [], None), "a@b.co")
        service.record_output(make_export(), "/a.qgz")
        assert [p.name for p in tmp_path.iterdir()] == ["research.json"]

    def test_a_corrupt_file_reads_as_unset(self, tmp_path: Path) -> None:
        path = tmp_path / "research.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_state(path).consent == consent.UNSET
        path.write_text('{"consent": "yes please"}', encoding="utf-8")
        assert load_state(path).consent == consent.UNSET

    def test_round_trip(self, tmp_path: Path, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        service.record_failure("blocked")
        path = tmp_path / "copy.json"
        save_state(path, service.state)
        assert load_state(path).to_stored() == service.state.to_stored()


# ---- Consent and the gates ------------------------------------------------


class TestConsentGate:
    def test_nothing_is_queued_while_unset(self, service: ResearchService) -> None:
        assert service.consent == consent.UNSET
        assert service.record_output(make_export(), "/a.qgz") is False
        service.record_preview()
        service.record_failure("blocked")
        service.save_profile(Profile(), "a@b.co")
        assert service.state.queue == []
        assert service.state.weeks == {}
        assert service.pending() == []

    def test_nothing_is_queued_after_dont_share(self, service: ResearchService) -> None:
        service.choose(False)
        service.record_output(make_export(), "/a.qgz")
        service.record_preview()
        assert service.state.queue == []
        assert service.state.weeks == {}

    def test_share_queues_a_map_once_and_again_on_change(
        self, service: ResearchService
    ) -> None:
        service.choose(True)
        assert service.record_output(make_export(), "/a.qgz") is True
        assert service.record_output(make_export(), "/a.qgz") is False
        changed = make_export((make_layer(fields=("name", "lanes")),))
        assert service.record_output(changed, "/a.qgz") is True
        kinds = [b["kind"] for b in queued_bodies(service)]
        assert kinds == ["map", "map"]

    def test_withdrawing_drops_the_queue(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        service.record_preview()
        service.choose(False)
        assert service.state.queue == []
        assert service.state.weeks == {}
        assert service.pending() == []

    def test_pending_is_empty_unless_share(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        assert len(service.pending()) == 1
        service.state.consent = consent.UNSET
        assert service.pending() == []

    def test_unknown_stored_choice_is_never_share(self) -> None:
        assert consent.normalise("SHARE") == consent.UNSET
        assert consent.normalise(True) == consent.UNSET


class TestResearchEnds:
    def test_end_date(self) -> None:
        assert date(2027, 1, 31) == consent.RESEARCH_ENDS
        assert text.END_DATE_TEXT == "31 January 2027"

    def test_last_day_still_collects(self) -> None:
        assert consent.may_collect(consent.SHARE, consent.RESEARCH_ENDS)

    def test_nothing_after_the_end(self, tmp_path: Path) -> None:
        after = Clock(consent.RESEARCH_ENDS + timedelta(days=1))
        service = ResearchService(tmp_path / "r.json", ENV, today=after)
        service.choose(True)
        assert service.record_output(make_export(), "/a.qgz") is False
        assert service.state.queue == []
        assert service.pending() == []
        assert not service.can_ask()

    def test_a_queue_left_over_is_deleted_not_sent(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        path = tmp_path / "r.json"
        service = ResearchService(path, ENV, today=clock)
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        assert service.state.queue
        later = Clock(consent.RESEARCH_ENDS + timedelta(days=3))
        reopened = ResearchService(path, ENV, today=later)
        assert reopened.pending() == []
        assert reopened.state.queue == []


class TestResponses:
    def _queued(self, service: ResearchService) -> str:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        return service.state.queue[0].id

    def test_2xx_dequeues(self, service: ResearchService) -> None:
        report_id = self._queued(service)
        service.on_response(report_id, 204)
        assert service.state.queue == []

    @pytest.mark.parametrize("status", [None, 429, 500, 503])
    def test_failure_keeps_it_queued(
        self, service: ResearchService, status: int | None
    ) -> None:
        report_id = self._queued(service)
        service.on_response(report_id, status)
        assert [i.id for i in service.state.queue] == [report_id]

    def test_410_ends_research_for_good(self, service: ResearchService) -> None:
        report_id = self._queued(service)
        service.record_output(make_export((make_layer("B"),)), "/b.qgz")
        service.on_response(report_id, 410)
        assert service.consent == consent.ENDED
        assert service.state.queue == []
        service.choose(True)
        assert service.consent == consent.ENDED
        assert service.record_output(make_export(), "/c.qgz") is False
        assert service.pending() == []
        reopened = ResearchService(service.path, ENV, today=service.today)
        assert reopened.consent == consent.ENDED


class TestContact:
    def test_sent_once_only_with_share(self, service: ResearchService) -> None:
        service.save_profile(Profile.build("team", "water", [], None), "a@b.co")
        assert service.state.queue == []
        service.choose(True)
        service.choose(True)
        bodies = queued_bodies(service)
        assert [b["kind"] for b in bodies] == ["contact"]
        assert set(bodies[0]) == CONTACT_KEYS
        assert bodies[0]["email"] == "a@b.co"
        assert bodies[0]["sector"] == "water"

    def test_no_email_no_contact(self, service: ResearchService) -> None:
        service.save_profile(Profile(), "not an email")
        service.choose(True)
        assert service.state.queue == []
        assert normalise_email("  a@b.co ") == "a@b.co"

    def test_map_reports_never_carry_the_email(self, service: ResearchService) -> None:
        service.save_profile(Profile(), "person@example.org")
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        maps = [i.body for i in service.state.queue if '"kind":"map"' in i.body]
        assert maps
        assert all("person@example.org" not in body for body in maps)


class TestOnce:
    def test_profile_asked_once(self, service: ResearchService) -> None:
        assert service.needs_profile()
        service.skip_profile()
        assert not service.needs_profile()

    def test_whats_new_once_per_version(self, service: ResearchService) -> None:
        assert service.needs_whats_new("0.1.5")
        service.mark_whats_new_seen("0.1.5")
        assert not service.needs_whats_new("0.1.5")
        assert service.needs_whats_new("0.1.6")


class TestEnvelope:
    @pytest.mark.parametrize(
        ("platform", "expected"),
        [("win32", "windows"), ("darwin", "macos"), ("linux", "linux"), ("x", "other")],
    )
    def test_os(self, platform: str, expected: str) -> None:
        assert os_name(platform) == expected

    def test_every_report_carries_the_envelope(
        self, service: ResearchService, clock: Clock
    ) -> None:
        service.save_profile(
            Profile.build("self", "hobby_personal", [], None), "a@b.co"
        )
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        clock.day += timedelta(days=7)
        service.prepare_flush()
        bodies = queued_bodies(service)
        assert {b["kind"] for b in bodies} == {"contact", "map", "tally"}
        for body in bodies:
            assert body["schema"] == 1
            assert body["plugin_version"] == "0.1.5"
            assert body["os"] == "linux"
            if body["kind"] != "contact":
                assert set(body["profile"]) == PROFILE_KEYS  # type: ignore[arg-type]

    def test_popup_mode_hidden_fields_are_still_names_only(self) -> None:
        layer = make_layer(
            popup=PopupSpec(
                fields=(
                    PopupFieldSpec(name="visible"),
                    PopupFieldSpec(name="hidden", mode=PopupFieldMode.HIDDEN),
                )
            )
        )
        report = build_map_report(make_export((layer,)), ENV, Profile())
        assert report["layers"][0]["fields"] == ["visible", "hidden"]
