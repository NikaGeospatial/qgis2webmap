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
from datetime import date, datetime, timedelta, timezone
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
from nika_onlymap_exporter.research.envelope import (
    Environment,
    os_name,
    utc_offset_hours,
)
from nika_onlymap_exporter.research.fingerprint import (
    is_map_id,
    map_key,
    new_map_id,
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
    MapReportWire,
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
from nika_onlymap_exporter.research.queue import MAX_QUEUED, QueuedReport, enqueue
from nika_onlymap_exporter.research.service import (
    GAVE_UP,
    MAX_REPORT_ATTEMPTS,
    RETRY,
    ResearchService,
)
from nika_onlymap_exporter.research.state import load_state, save_state
from nika_onlymap_exporter.research.tally import (
    FAILURE_CLASSES,
    TIME_BLOCKS,
    WeekCounters,
    build_tally,
    iso_week,
    time_block,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "specs" / "2026-09-30-user-research.md"

ENV = Environment.build("0.1.5", "3.44.1-Solothurn", "linux")
EMPTY = {"type": "FeatureCollection", "features": []}

ENVELOPE_KEYS = {"schema", "kind", "plugin_version", "qgis_version", "os"}
MAP_KEYS = ENVELOPE_KEYS | {
    "profile",
    "map_id",
    "utc_offset_hours",
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
    "utc_offset_hours",
    "week",
    "exports",
    "exports_by_block",
    "previews",
    "publishes",
    "distinct_maps",
    "failures",
    "recurring_maps",
}
PROFILE_KEYS = {"audience", "sector", "use_cases", "use_case_other"}


# A valid `map_id` and offset for reports built outside the service.
MAP_ID = "AbCdEfGhIjKlMnOpQrSt-_"
OFFSET = 8


def map_report(
    export: ExportProject,
    env: Environment,
    profile: Profile,
    *,
    facts: ProjectFacts | None = None,
    hosted: bool = False,
    password: bool = False,
) -> MapReportWire:
    return build_map_report(
        export,
        env,
        profile,
        map_id=MAP_ID,
        utc_offset_hours=OFFSET,
        facts=facts,
        hosted=hosted,
        password=password,
    )


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


class LocalTime:
    """The service's local clock: a time of day with a UTC offset."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


SINGAPORE = timezone(timedelta(hours=8))


@pytest.fixture
def clock() -> Clock:
    return Clock(date(2026, 10, 7))  # a Wednesday, ISO week 2026-W41


@pytest.fixture
def local_time() -> LocalTime:
    return LocalTime(datetime(2026, 10, 7, 9, 30, tzinfo=SINGAPORE))


@pytest.fixture
def service(tmp_path: Path, clock: Clock, local_time: LocalTime) -> ResearchService:
    return ResearchService(tmp_path / "research.json", ENV, today=clock, now=local_time)


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
        report = map_report(make_export(), ENV, Profile())
        assert set(report) == MAP_KEYS
        for layer in report["layers"]:
            assert set(layer) == LAYER_KEYS

    def test_values_are_in_their_enums(self) -> None:
        report = map_report(
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
        assert report["schema"] == 2
        assert report["kind"] == "map"
        assert report["qgis_version"] == "3.44.1"

    def test_no_coordinate_finer_than_a_degree(self) -> None:
        report = map_report(make_export(), ENV, Profile())
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
        body = json.dumps(map_report(make_export((layer,)), ENV, Profile()))
        assert "SECRET-VALUE" not in body
        assert "gravel" not in body
        assert "103.123" not in body
        assert "surface" in body  # the field NAME is the point

    def test_names_are_scrubbed(self) -> None:
        layer = make_layer(
            name="/home/alice/clients/acme/parcels.shp",
            fields=("owner_email", "https://x.com/f", "tel +65 9123 4567"),
        )
        report = map_report(
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
        report = map_report(make_export(layers), ENV, Profile())
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
        report = map_report(make_export((narrow, *wide)), ENV, Profile())
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
        report = map_report(
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
        report = map_report(
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
        report = map_report(make_export((layer,)), ENV, Profile(), facts=facts)
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
        report = map_report(make_export((raster,)), ENV, Profile())
        layer = report["layers"][0]
        assert layer["format"] == "tif"
        assert layer["kind"] == "raster"
        assert layer["fields"] == []
        assert "secret" not in json.dumps(report)

    def test_unknown_format_is_other(self) -> None:
        facts = ProjectFacts(layers={"Roads_id": LayerFacts(format="evil/../x")})
        report = map_report(make_export(), ENV, Profile(), facts=facts)
        assert report["layers"][0]["format"] == "other"

    def test_a_crs_that_is_not_an_authid_is_dropped(self) -> None:
        export = replace(make_export(), source_crs="+proj=tmerc +lat_0=1.366")
        assert map_report(export, ENV, Profile())["crs"] is None


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
        tally = build_tally("2026-W41", counters, {}, ENV, Profile(), OFFSET)
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
        service.save_profile(Profile.build("team", "water", [], None))
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
        service.save_profile(Profile())
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

    @pytest.mark.parametrize("status", [403, 404, 409, 302])
    def test_an_unexplained_refusal_is_dropped_after_bounded_attempts(
        self, tmp_path: Path, clock: Clock, status: int
    ) -> None:
        path = tmp_path / "research.json"
        service = ResearchService(path, ENV, today=clock)
        report_id = self._queued(service)
        for attempt in range(1, MAX_REPORT_ATTEMPTS):
            # A fresh load each time: the count survives between sends.
            service = ResearchService(path, ENV, today=clock)
            assert service.on_response(report_id, status) == RETRY
            assert [(i.id, i.attempts) for i in service.state.queue] == [
                (report_id, attempt)
            ]
        service = ResearchService(path, ENV, today=clock)
        assert service.on_response(report_id, status) == GAVE_UP
        assert service.state.queue == []
        assert load_state(path).queue == []

    @pytest.mark.parametrize("status", [None, 408, 429, 500, 502, 503])
    def test_server_or_network_trouble_never_counts(
        self, service: ResearchService, status: int | None
    ) -> None:
        report_id = self._queued(service)
        for _ in range(MAX_REPORT_ATTEMPTS * 3):
            assert service.on_response(report_id, status) == RETRY
        assert [(i.id, i.attempts) for i in service.state.queue] == [(report_id, 0)]

    def test_attempts_are_per_report(self, service: ResearchService) -> None:
        first = self._queued(service)
        service.record_output(make_export((make_layer("B"),)), "/b.qgz")
        second = service.state.queue[1].id
        for _ in range(MAX_REPORT_ATTEMPTS - 1):
            service.on_response(first, 403)
        service.on_response(second, 403)
        assert [i.attempts for i in service.state.queue] == [
            MAX_REPORT_ATTEMPTS - 1,
            1,
        ]

    def test_a_queue_saved_before_attempts_reads_as_none_yet(self) -> None:
        item = QueuedReport.from_stored({"id": "a", "body": "{}"})
        assert item is not None and item.attempts == 0
        for bad in (-1, "3", True, 2.5):
            again = QueuedReport.from_stored({"id": "a", "body": "{}", "attempts": bad})
            assert again is not None and again.attempts == 0

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


class TestNoContact:
    """The optional email was removed before release; nothing may bring it back."""

    def _legacy(self, tmp_path: Path) -> Path:
        # What a 0.2.0-dev build that still had the email left behind.
        contact = {
            "schema": 2,
            "kind": "contact",
            "plugin_version": "0.2.0",
            "qgis_version": "3.44",
            "os": "linux",
            "email": "person@example.org",
            "audience": None,
            "sector": None,
            "use_cases": [],
            "use_case_other": None,
        }
        path = tmp_path / "research.json"
        path.write_text(
            json.dumps(
                {
                    "consent": "share",
                    "salt": "s" * 32,
                    "profile_answered": True,
                    "email": "person@example.org",
                    "contact_sent": True,
                    "queue": [
                        {"id": "c1", "body": json.dumps(contact)},
                        {"id": "m1", "body": '{"kind":"map"}'},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_a_stored_email_and_queued_contact_are_dropped_on_load(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        path = self._legacy(tmp_path)
        service = ResearchService(path, ENV, today=clock)
        assert [item.id for item in service.pending()] == ["m1"]
        assert "email" not in service.state.to_stored()
        # Rewritten at once, not only at the next save.
        on_disk = path.read_text(encoding="utf-8")
        assert "person@example.org" not in on_disk
        assert "contact_sent" not in on_disk
        assert json.loads(on_disk)["consent"] == "share"

    def test_a_clean_state_is_not_rewritten_on_load(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        path = tmp_path / "research.json"
        path.write_text('{"consent": "share"}', encoding="utf-8")
        ResearchService(path, ENV, today=clock)
        assert path.read_text(encoding="utf-8") == '{"consent": "share"}'

    def test_sharing_queues_nothing_but_maps_and_tallies(
        self, service: ResearchService, clock: Clock
    ) -> None:
        service.save_profile(Profile.build("team", "water", [], None))
        service.choose(True)
        assert service.state.queue == []
        service.record_output(make_export(), "/a.qgz")
        clock.day += timedelta(days=7)
        service.prepare_flush()
        assert {b["kind"] for b in queued_bodies(service)} == {"map", "tally"}

    def test_the_preview_shows_no_contact_report(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        from nika_onlymap_exporter.research.preview import payload_preview_text

        service = ResearchService(self._legacy(tmp_path), ENV, today=clock)
        text = payload_preview_text(service, None)
        assert '"contact"' not in text
        assert "person@example.org" not in text


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
        service.save_profile(Profile.build("self", "hobby_personal", [], None))
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        clock.day += timedelta(days=7)
        service.prepare_flush()
        bodies = queued_bodies(service)
        assert {b["kind"] for b in bodies} == {"map", "tally"}
        for body in bodies:
            assert body["schema"] == 2
            assert body["plugin_version"] == "0.1.5"
            assert body["os"] == "linux"
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
        report = map_report(make_export((layer,)), ENV, Profile())
        assert report["layers"][0]["fields"] == ["visible", "hidden"]


class TestWhatsNewExcerpt:
    def test_ships_inside_the_plugin_with_the_research_marker(self) -> None:
        import importlib.util

        from nika_onlymap_exporter.research.preview import (
            RESEARCH_MARKER,
            WHATS_NEW_PATH,
            read_whats_new,
        )

        spec = importlib.util.spec_from_file_location(
            "_package_plugin", REPO_ROOT / "scripts" / "package_plugin.py"
        )
        assert spec is not None and spec.loader is not None
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)

        assert WHATS_NEW_PATH.parent == packager.PACKAGE_DIR
        assert packager.should_include(WHATS_NEW_PATH)
        assert RESEARCH_MARKER in WHATS_NEW_PATH.read_text(encoding="utf-8")
        before, after = read_whats_new()
        assert before and after

    def test_a_missing_excerpt_is_not_an_error(self, tmp_path: Path) -> None:
        from nika_onlymap_exporter.research.preview import read_whats_new

        assert read_whats_new(tmp_path / "absent.md") == ("", "")


class TestPayloadPreview:
    def test_sample_when_nothing_has_been_read(self, service: ResearchService) -> None:
        from nika_onlymap_exporter.research.preview import (
            SAMPLE_NOTE,
            payload_preview_text,
        )

        text_ = payload_preview_text(service, None)
        assert SAMPLE_NOTE in text_
        assert '"kind": "map"' in text_
        assert '"kind": "tally"' in text_
        assert service.state.queue == []

    def test_the_real_project_when_there_is_one(self, service: ResearchService) -> None:
        from nika_onlymap_exporter.research.preview import (
            SAMPLE_NOTE,
            payload_preview_text,
        )

        text_ = payload_preview_text(service, make_export())
        assert SAMPLE_NOTE not in text_
        assert "Kranji site survey" in text_


# ---- v2: map_id -----------------------------------------------------------

MAP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22}$")


def map_bodies(service: ResearchService) -> list[dict[str, object]]:
    return [body for body in queued_bodies(service) if body["kind"] == "map"]


class TestMapId:
    def test_shape(self) -> None:
        ids = {new_map_id() for _ in range(200)}
        assert len(ids) == 200
        assert all(MAP_ID_PATTERN.match(map_id) for map_id in ids)
        assert all(is_map_id(map_id) for map_id in ids)
        assert not is_map_id("x" * 21)
        assert not is_map_id("x" * 23)
        assert not is_map_id("x" * 21 + "=")
        assert not is_map_id("x" * 22 + "\n")

    def test_every_map_report_carries_one(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        (body,) = map_bodies(service)
        assert isinstance(body["map_id"], str)
        assert MAP_ID_PATTERN.match(body["map_id"])

    def test_reused_when_the_same_map_changes(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        # A layer added: the structure changes, so a second report goes, but
        # it is still the same map and must say so.
        service.record_output(
            make_export((make_layer(), make_layer("Rivers"))), "/a.qgz"
        )
        # And again after a restart, from the stored state.
        reopened = ResearchService(service.path, ENV, today=service.today)
        reopened.record_output(make_export((make_layer("Only"),)), "/a.qgz")
        bodies = map_bodies(reopened)
        assert len(bodies) == 3
        assert len({body["map_id"] for body in bodies}) == 1

    def test_different_maps_get_different_ids(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        service.record_output(make_export(), "/b.qgz")
        first, second = map_bodies(service)
        assert first["map_id"] != second["map_id"]

    def test_random_not_derived_from_the_map(self, tmp_path: Path) -> None:
        """The same project on two installs gets two unrelated ids."""
        ids = set()
        for name in ("one", "two"):
            other = ResearchService(tmp_path / f"{name}.json", ENV)
            other.choose(True)
            other.record_output(make_export(), "/a.qgz")
            ids.add(map_bodies(other)[0]["map_id"])
        assert len(ids) == 2

    def test_stored_beside_the_fingerprints_keyed_by_map(
        self, service: ResearchService
    ) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        key = map_key(service.state.salt, "/a.qgz")
        assert list(service.state.map_ids) == [key]
        assert service.state.map_ids[key] == map_bodies(service)[0]["map_id"]
        assert "/a.qgz" not in json.dumps(service.state.to_stored()["map_ids"])

    def test_not_made_when_nothing_is_reported(self, service: ResearchService) -> None:
        service.record_output(make_export(), "/a.qgz")  # no Share yet
        assert service.state.map_ids == {}
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        service.record_output(make_export(), "/a.qgz")  # unchanged: no report
        assert len(service.state.map_ids) == 1

    def test_dont_share_forgets_them(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        before = map_bodies(service)[0]["map_id"]
        service.choose(False)
        assert service.state.map_ids == {}
        assert "map_ids" in service.state.to_stored()
        assert load_state(service.path).map_ids == {}
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        assert map_bodies(service)[0]["map_id"] != before

    def test_the_410_forgets_them(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        service.on_response(service.state.queue[0].id, 410)
        assert service.state.map_ids == {}

    def test_a_malformed_stored_id_is_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "research.json"
        good = "AbCdEfGhIjKlMnOpQrSt-_"
        path.write_text(
            json.dumps({"map_ids": {"k1": good, "k2": "short", "k3": 7}}),
            encoding="utf-8",
        )
        assert load_state(path).map_ids == {"k1": good}

    def test_bounded(self, service: ResearchService) -> None:
        from nika_onlymap_exporter.research.state import MAX_KNOWN_MAPS

        state = service.state
        for i in range(MAX_KNOWN_MAPS + 3):
            state.map_id_for(f"key{i}")
        assert len(state.map_ids) == MAX_KNOWN_MAPS
        assert "key0" not in state.map_ids
        assert f"key{MAX_KNOWN_MAPS + 2}" in state.map_ids

    def test_preview_changes_nothing(self, service: ResearchService) -> None:
        from nika_onlymap_exporter.research.preview import payload_preview_text

        service.choose(True)
        before = service.state.to_stored()
        report = service.preview_map_report(make_export(), identity="/a.qgz")
        assert MAP_ID_PATTERN.match(report["map_id"])
        payload_preview_text(service, make_export(), identity="/a.qgz")
        assert service.state.to_stored() == before

    def test_preview_shows_the_maps_own_id(self, service: ResearchService) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        real = map_bodies(service)[0]["map_id"]
        preview = service.preview_map_report(make_export(), identity="/a.qgz")
        assert preview["map_id"] == real

    def test_tallies_carry_no_map_id(
        self, service: ResearchService, clock: Clock
    ) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")
        map_id = map_bodies(service)[0]["map_id"]
        clock.day += timedelta(days=7)
        service.prepare_flush()
        for item in service.state.queue:
            if json.loads(item.body)["kind"] != "map":
                assert "map_id" not in item.body
                assert str(map_id) not in item.body


# ---- v2: utc_offset_hours -------------------------------------------------


def offset(hours: int, minutes: int = 0) -> datetime:
    sign = -1 if hours < 0 else 1
    delta = timedelta(hours=hours, minutes=sign * minutes)
    return datetime(2026, 10, 7, 12, 0, tzinfo=timezone(delta))


class TestUtcOffset:
    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            (offset(0), 0),
            (offset(8), 8),
            (offset(-5), -5),
            (offset(5, 30), 6),  # India: half up
            (offset(-3, 30), -3),  # Newfoundland: half up is towards zero
            (offset(5, 45), 6),  # Nepal
            (offset(9, 30), 10),  # Adelaide
            (offset(-9, 30), -9),  # Marquesas
            (offset(3, 29), 3),
            (offset(-3, 31), -4),
            (offset(14), 14),  # Kiribati, the server's top
            (offset(-12), -12),  # the server's bottom
            (offset(13, 45), 14),  # Chatham in summer
            (offset(15), 14),  # a strange clock, kept in range
            (offset(-13), -12),
            (datetime(2026, 10, 7, 12, 0), 0),  # naive counts as UTC
        ],
    )
    def test_rounded_to_the_nearest_hour_half_up(
        self, moment: datetime, expected: int
    ) -> None:
        assert utc_offset_hours(moment) == expected

    def test_on_map_reports_from_the_local_clock(
        self, service: ResearchService, local_time: LocalTime
    ) -> None:
        service.choose(True)
        local_time.moment = offset(5, 30)
        service.record_output(make_export(), "/a.qgz")
        assert map_bodies(service)[0]["utc_offset_hours"] == 6

    def test_on_tallies_as_of_when_they_are_built(
        self, service: ResearchService, clock: Clock, local_time: LocalTime
    ) -> None:
        service.choose(True)
        service.record_output(make_export(), "/a.qgz")  # at +8
        clock.day += timedelta(days=7)
        local_time.moment = offset(-3, 30)
        service.prepare_flush()
        tally = next(b for b in queued_bodies(service) if b["kind"] == "tally")
        assert tally["utc_offset_hours"] == -3


# ---- v2: exports_by_block -------------------------------------------------


class TestExportsByBlock:
    @pytest.mark.parametrize(
        ("hour", "block"),
        [
            (0, 0),
            (3, 0),
            (4, 1),
            (7, 1),
            (8, 2),
            (11, 2),
            (12, 3),
            (16, 4),
            (20, 5),
            (23, 5),
        ],
    )
    def test_four_hour_blocks(self, hour: int, block: int) -> None:
        assert time_block(hour) == block

    def test_counted_by_local_hour_like_exports(
        self, service: ResearchService, clock: Clock, local_time: LocalTime
    ) -> None:
        service.choose(True)
        for hour in (0, 3, 9, 23, 23):
            local_time.moment = datetime(2026, 10, 7, hour, 15, tzinfo=SINGAPORE)
            service.record_output(make_export(), "/a.qgz")
        # A publish is not an export, here as in `exports`.
        service.record_output(make_export(), "/a.qgz", hosted=True)
        clock.day += timedelta(days=7)
        service.prepare_flush()
        tally = next(b for b in queued_bodies(service) if b["kind"] == "tally")
        assert tally["exports_by_block"] == [2, 0, 1, 0, 0, 2]
        assert tally["exports"] == 5
        assert tally["publishes"] == 1

    def test_the_local_hour_not_utc(
        self, service: ResearchService, clock: Clock, local_time: LocalTime
    ) -> None:
        service.choose(True)
        # 01:00 in Singapore is 17:00 UTC the day before: block 0, not 4.
        local_time.moment = datetime(2026, 10, 7, 1, 0, tzinfo=SINGAPORE)
        service.record_output(make_export(), "/a.qgz")
        clock.day += timedelta(days=7)
        service.prepare_flush()
        tally = next(b for b in queued_bodies(service) if b["kind"] == "tally")
        assert tally["exports_by_block"] == [1, 0, 0, 0, 0, 0]

    def test_always_six_non_negative_counts(self) -> None:
        tally = build_tally("2026-W41", WeekCounters(), {}, ENV, Profile(), 0)
        assert tally["exports_by_block"] == [0] * TIME_BLOCKS == [0] * 6

    def test_stored_and_restored(self) -> None:
        counters = WeekCounters()
        counters.add_export(13)
        assert WeekCounters.from_stored(counters.to_stored()) == counters
        assert counters.exports_by_block == [0, 0, 0, 1, 0, 0]

    @pytest.mark.parametrize(
        "stored",
        [None, "x", [1, 2, 3], [1, 2, 3, 4, 5, 6, 7]],
    )
    def test_a_malformed_stored_block_list_reads_as_zeros(self, stored: object) -> None:
        counters = WeekCounters.from_stored({"exports": 2, "exports_by_block": stored})
        assert counters.exports_by_block == [0] * 6

    def test_a_bad_count_inside_reads_as_zero(self) -> None:
        counters = WeekCounters.from_stored(
            {"exports_by_block": [1, -1, "2", True, 3.0, 4]}
        )
        assert counters.exports_by_block == [1, 0, 0, 0, 0, 4]
