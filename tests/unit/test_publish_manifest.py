"""The publish manifest: what a built hosted output tells its publisher.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json

import pytest
from test_hosted_output import FakeRuntime, make_project

from nika_onlymap_exporter.core.export_ir import ExportSettings
from nika_onlymap_exporter.core.manifest_builder import (
    BASEMAP_PRESETS,
    TERRAIN_PRESETS,
)
from nika_onlymap_exporter.hosting.thumbnail import THUMBNAIL_FILENAME
from nika_onlymap_exporter.packaging.cdn_runtime import pinned_runtime
from nika_onlymap_exporter.packaging.publish_manifest import (
    ARTIFACT_KIND,
    BASEMAP_ORIGINS_VERIFIED_AGAINST,
    BASEMAP_TILE_ORIGINS,
    ENTRY_NAME,
    MAX_EXTERNAL_ORIGINS,
    RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST,
    SCHEMA_VERSION,
    TERRAIN_MESH_WORKER,
    TERRAIN_ORIGINS_VERIFIED_AGAINST,
    TERRAIN_SCRIPT_SOURCES,
    TERRAIN_TILE_ORIGINS,
    PublishManifestError,
    basemap_origins,
    build_publish_manifest,
    derive_external_origins,
    media_type_for,
    role_for,
    runtime_script_sources,
    terrain_origins,
    terrain_script_sources,
)
from nika_onlymap_exporter.packaging.runtime_manager import read_lock
from nika_onlymap_exporter.writers.onlymap_writer import PLUGIN_VERSION, OnlyMapWriter


def build_hosted(tmp_path, **settings):
    OnlyMapWriter(runtime_provider=FakeRuntime()).write(
        make_project(settings=ExportSettings(**settings)), tmp_path, hosted=True
    )
    return tmp_path


def build_relief_page(tmp_path):
    """A hosted directory whose page declares relief, written by hand.

    The exporter will not build one: a hosted page cannot carry the camera
    clamp a relief map needs, so `scan` refuses the export outright - see
    `dependency_scanner.hosted_relief_reason`, and the refusal itself is
    asserted in `test_hosted_output.py`.

    So the attribute is added to a real hosted page rather than a hosted relief
    export being faked up whole. That refusal is a product decision waiting on a
    runtime camera cap, and these two manifest fields are exactly what has to be
    right on the day it is lifted - a CSP that blocks the DEM tiles or the mesh
    worker is a flat surface with nothing saying why. Keeping the manifest layer
    covered here is what stops that rotting while the writer says no.
    """
    build_hosted(tmp_path)
    entry = tmp_path / ENTRY_NAME
    page = entry.read_text(encoding="utf-8")
    entry.write_text(
        page.replace("<om-map", '<om-map terrain="terrarium"', 1), encoding="utf-8"
    )
    return tmp_path


class TestRoles:
    def test_the_entry_is_the_page(self) -> None:
        assert role_for(ENTRY_NAME) == "page"

    def test_the_thumbnail_is_named_not_sniffed(self) -> None:
        """The name decides, so the constant is what the test may name.

        Spelled through `THUMBNAIL_FILENAME` rather than as a literal: the two
        drifted apart when the thumbnail became a JPEG, and a test holding the
        old string would have gone on passing against a file the exporter no
        longer writes.
        """
        assert role_for(THUMBNAIL_FILENAME) == "thumbnail"

    def test_an_image_that_is_not_the_thumbnail_is_refused(self) -> None:
        # The other half of "named, not sniffed": an image with the right
        # extension and the wrong name has no role, rather than being adopted
        # as a second thumbnail.
        with pytest.raises(PublishManifestError):
            role_for("screenshot.jpg")

    def test_geojson_is_data(self) -> None:
        assert role_for("Points.geojson") == "data"

    def test_a_cog_is_a_raster(self) -> None:
        assert role_for("layer-0-dem.tif") == "raster"

    def test_an_unexpected_file_is_refused_rather_than_guessed(self) -> None:
        with pytest.raises(PublishManifestError):
            role_for("notes.txt")


class TestMediaTypes:
    def test_geojson_gets_its_registered_type(self) -> None:
        assert media_type_for("a.geojson") == "application/geo+json"

    def test_the_page_is_html(self) -> None:
        assert media_type_for(ENTRY_NAME) == "text/html"


class TestBasemapOrigins:
    """The table that decides whether a published map can draw its basemap.

    The server turns `externalOrigins` into the page's Content-Security-Policy,
    so an origin missing from it is an origin the browser refuses to contact.
    Every check here is about a failure that only shows up in a recipient's
    browser, long after the export said it succeeded.
    """

    def test_the_table_was_verified_against_the_pinned_runtime(self) -> None:
        """The whole reason the table does not rot silently.

        Preset names resolve to style URLs inside the runtime, so bumping
        `runtime-lock.json` can change which hosts a basemap contacts without
        anything in this repo changing. Re-derive the table with
        `scripts/verify_basemap_origins.py` - it needs the network, which is
        why the offline suite can only assert that somebody did - and then move
        `BASEMAP_ORIGINS_VERIFIED_AGAINST` to the newly pinned version.
        """
        pinned = read_lock().get("version")
        assert pinned == BASEMAP_ORIGINS_VERIFIED_AGAINST, (
            f"BASEMAP_TILE_ORIGINS was read out of onlymap "
            f"{BASEMAP_ORIGINS_VERIFIED_AGAINST}, but the pinned runtime is now "
            f"{pinned}. Run scripts/verify_basemap_origins.py and update the "
            "table and BASEMAP_ORIGINS_VERIFIED_AGAINST together - an unchecked "
            "table publishes maps whose CSP blocks their own basemap tiles."
        )

    def test_every_preset_the_exporter_can_emit_resolves(self) -> None:
        """`build_manifest` only ever emits a name from this tuple."""
        for preset in BASEMAP_PRESETS:
            assert preset in BASEMAP_TILE_ORIGINS, preset

    def test_they_are_origins_and_not_bare_domains(self) -> None:
        """`BASEMAP_HOSTS` holds domains for prose; a CSP needs scheme + host."""
        for origins in BASEMAP_TILE_ORIGINS.values():
            for origin in origins:
                assert origin.startswith("https://")
                assert origin.count("/") == 2

    def test_no_basemap_contacts_nobody(self) -> None:
        assert basemap_origins("none") == ()

    def test_carto_declares_every_tile_subdomain(self) -> None:
        """MapLibre round-robins the four, so three of four is a broken map."""
        origins = basemap_origins("dark-matter")
        for letter in "abcd":
            assert f"https://tiles-{letter}.basemaps.cartocdn.com" in origins

    def test_an_unresolvable_preset_refuses_to_publish(self) -> None:
        """Blank basemaps are worse than refused publishes."""
        with pytest.raises(PublishManifestError, match="blank"):
            basemap_origins("some-future-preset")


class TestTerrainOrigins:
    """The same table, for the registry that decides where elevation comes from.

    A blocked DEM is quieter than a blocked basemap: the surface comes back flat
    and the map looks like one that was never given relief, so nothing about the
    picture suggests a policy refused something.
    """

    def test_the_table_was_verified_against_the_pinned_runtime(self) -> None:
        pinned = read_lock().get("version")
        assert pinned == TERRAIN_ORIGINS_VERIFIED_AGAINST, (
            f"TERRAIN_TILE_ORIGINS was read out of onlymap "
            f"{TERRAIN_ORIGINS_VERIFIED_AGAINST}, but the pinned runtime is now "
            f"{pinned}. Run scripts/verify_basemap_origins.py and update the "
            "table and TERRAIN_ORIGINS_VERIFIED_AGAINST together - an unchecked "
            "table publishes relief maps whose CSP blocks their own DEM tiles."
        )

    def test_every_preset_the_exporter_can_emit_resolves(self) -> None:
        for preset in TERRAIN_PRESETS:
            assert preset in TERRAIN_TILE_ORIGINS, preset

    def test_they_are_origins_and_not_bare_domains(self) -> None:
        for origins in TERRAIN_TILE_ORIGINS.values():
            for origin in origins:
                assert origin.startswith("https://")
                assert origin.count("/") == 2

    def test_no_terrain_contacts_nobody(self) -> None:
        assert terrain_origins("none") == ()

    def test_terrarium_declares_the_dem_host(self) -> None:
        """The defect: `terrain="terrarium"` named no origin at all, so a relief
        map published with a CSP that blocked its own elevation data."""
        assert terrain_origins("terrarium") == ("https://s3.amazonaws.com",)

    def test_an_unresolvable_preset_refuses_to_publish(self) -> None:
        with pytest.raises(PublishManifestError, match="elevation data"):
            terrain_origins("some-future-dem")


class TestRuntimeScriptSources:
    """The whitelist that decides what may execute on the serving origin.

    Every assertion here is about the boundary rather than about terrain: these
    values reach `script-src`, so the thing being protected is that they can
    only ever come from this repository.
    """

    def test_the_whitelist_was_verified_against_the_pinned_runtime(self) -> None:
        pinned = read_lock().get("version")
        assert pinned == RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST, (
            f"TERRAIN_SCRIPT_SOURCES was read out of onlymap "
            f"{RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST}, but the pinned runtime "
            f"is now {pinned}. Run scripts/verify_basemap_origins.py and update "
            "the whitelist and RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST together "
            "- a stale entry authorises a worker the runtime no longer fetches "
            "and blocks the one it does."
        )

    def test_every_preset_the_exporter_can_emit_resolves(self) -> None:
        for preset in TERRAIN_PRESETS:
            assert preset in TERRAIN_SCRIPT_SOURCES, preset

    def test_entries_are_path_pinned_and_not_bare_origins(self) -> None:
        """`https://unpkg.com/` would authorise the whole of npm on our origin.

        So every entry has to carry a path, and that path has to name a version
        - a package pinned to no version is every version of that package.
        """
        for sources in TERRAIN_SCRIPT_SOURCES.values():
            for source in sources:
                assert source.startswith("https://")
                assert source.count("/") > 3, source
                assert "@" in source.split("/", 3)[3], source

    def test_a_flat_map_authorises_no_script_source(self) -> None:
        """A policy entry is something that must be true. A map with no relief
        never builds a mesh, so it has nothing to say about mesh decoders."""
        assert runtime_script_sources("<om-map basemap='none'></om-map>") == ()

    def test_relief_names_the_mesh_decoder_worker(self) -> None:
        html = '<om-map terrain="terrarium"></om-map>'
        assert runtime_script_sources(html) == (TERRAIN_MESH_WORKER,)

    def test_the_page_selects_but_cannot_supply(self) -> None:
        """The whole design in one assertion: nothing a page writes reaches the
        output except as a key into the table shipped in this repository."""
        html = '<om-map terrain="terrarium" data-x="https://evil.example/x.js">'
        assert runtime_script_sources(html) == (TERRAIN_MESH_WORKER,)

    def test_an_unresolvable_preset_refuses_to_publish(self) -> None:
        with pytest.raises(PublishManifestError, match="mesh decoder"):
            terrain_script_sources("some-future-dem")


class TestExternalOrigins:
    def test_a_fetched_script_counts(self) -> None:
        html = '<script src="https://cdn.example.com/a/b.js" integrity="x"></script>'
        assert derive_external_origins(html) == ("https://cdn.example.com",)

    def test_a_clickable_link_does_not(self) -> None:
        """An href is somewhere a reader may go, not somewhere the page goes."""
        html = '<a href="https://nikaplanet.com">NIKA</a>'
        assert derive_external_origins(html) == ()

    def test_a_draped_texture_counts(self) -> None:
        html = '<om-map terrain-texture="https://tile.example.org/{z}/{x}/{y}.png">'
        assert derive_external_origins(html) == ("https://tile.example.org",)

    def test_the_cap_is_room_for_a_real_map(self) -> None:
        """CARTO plus relief is nine origins, so ten left no headroom at all."""
        html = "".join(
            f'<script src="https://h{i}.example.com/x.js"></script>'
            for i in range(MAX_EXTERNAL_ORIGINS)
        )
        assert len(derive_external_origins(html)) == MAX_EXTERNAL_ORIGINS

    def test_going_over_the_cap_refuses_rather_than_trimming(self) -> None:
        """The dangerous behaviour this replaced.

        Over the cap, the list used to be cut by sort order: the manifest still
        looked like a list of origins, the publish still succeeded, and whichever
        hosts sorted last were silently blocked in every recipient's browser.
        """
        html = "".join(
            f'<script src="https://h{i}.example.com/x.js"></script>'
            for i in range(MAX_EXTERNAL_ORIGINS + 1)
        )
        with pytest.raises(PublishManifestError) as raised:
            derive_external_origins(html)
        assert str(MAX_EXTERNAL_ORIGINS) in str(raised.value)
        assert "https://h0.example.com" in str(raised.value)

    def test_a_vector_basemap_contributes_its_tile_origins(self) -> None:
        """The defect this exists to stop: a page that named only a preset
        published with no origins at all, and the CSP blocked every tile."""
        html = '<om-map basemap="positron" zoom="4"></om-map>'
        assert derive_external_origins(html) == ("https://tiles.openfreemap.org",)

    def test_a_page_naming_no_basemap_is_not_an_unknown_one(self) -> None:
        html = '<script src="https://cdn.example.com/a/b.js"></script>'
        assert derive_external_origins(html) == ("https://cdn.example.com",)

    def test_an_unknown_preset_stops_the_publish(self) -> None:
        html = '<om-map basemap="not-a-preset"></om-map>'
        with pytest.raises(PublishManifestError, match="not-a-preset"):
            derive_external_origins(html)

    def test_a_key_attribute_is_not_the_basemap_attribute(self) -> None:
        """`basemap-key` is a different attribute and must not be read as one."""
        html = '<om-map basemap="none" basemap-key="abc"></om-map>'
        assert derive_external_origins(html) == ()

    def test_relief_contributes_its_dem_origin(self) -> None:
        html = '<om-map basemap="none" terrain="terrarium"></om-map>'
        assert derive_external_origins(html) == ("https://s3.amazonaws.com",)

    def test_the_texture_attribute_is_not_the_terrain_attribute(self) -> None:
        """`terrain-texture` spells its URL out and is read as a URL. Reading it
        as a preset name as well would refuse the publish over a valid page."""
        html = '<om-map terrain-texture="https://tile.example.org/{z}/{x}/{y}.png">'
        assert derive_external_origins(html) == ("https://tile.example.org",)

    def test_an_unknown_relief_preset_stops_the_publish(self) -> None:
        html = '<om-map terrain="not-a-preset"></om-map>'
        with pytest.raises(PublishManifestError, match="not-a-preset"):
            derive_external_origins(html)


class TestBuildPublishManifest:
    def test_the_envelope_names_itself(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        assert manifest["schemaVersion"] == SCHEMA_VERSION
        assert manifest["kind"] == ARTIFACT_KIND
        assert manifest["entry"] == ENTRY_NAME

    def test_the_title_is_read_back_from_the_pages_own_title_tag(
        self, tmp_path
    ) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        assert manifest["title"] == "Test map"

    def test_a_page_with_no_title_tag_declares_an_empty_title(self, tmp_path) -> None:
        directory = build_hosted(tmp_path)
        entry = directory / ENTRY_NAME
        entry.write_text(
            entry.read_text(encoding="utf-8").replace("<title>Test map</title>", ""),
            encoding="utf-8",
        )
        manifest = build_publish_manifest(directory)
        assert manifest["title"] == ""

    def test_the_producer_is_read_from_metadata(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        assert manifest["producer"] == {
            "name": "qgis2webmap",
            "version": PLUGIN_VERSION,
        }

    def test_the_runtime_is_read_from_the_lock_file(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        runtime = pinned_runtime()
        assert manifest["runtime"] == {
            "name": "onlymap",
            "version": runtime.version,
            "sha256": runtime.sha256,
        }

    def test_the_page_is_listed_first(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        assert manifest["files"][0]["path"] == ENTRY_NAME
        assert manifest["files"][0]["role"] == "page"

    def test_every_digest_matches_the_bytes_on_disk(self, tmp_path) -> None:
        directory = build_hosted(tmp_path)
        for entry in build_publish_manifest(directory)["files"]:
            data = (directory / entry["path"]).read_bytes()
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()
            assert entry["size"] == len(data)

    def test_the_page_is_hashed_after_the_data_it_names(self, tmp_path) -> None:
        """The ordering the whole design turns on, asserted rather than assumed.

        If the page were hashed before the data files existed it could not
        contain their digests, so finding each data digest inside the hashed
        page is the ordering, observed from the outside.
        """
        directory = build_hosted(tmp_path)
        manifest = build_publish_manifest(directory)
        page = (directory / ENTRY_NAME).read_text(encoding="utf-8")
        data = [f for f in manifest["files"] if f["role"] == "data"]
        assert data
        for entry in data:
            assert entry["sha256"] in page

    def test_the_path_is_the_local_name_not_the_served_key(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        for entry in manifest["files"]:
            assert "/" not in entry["path"]
            assert not entry["path"].startswith("assets")

    def test_the_origins_include_the_runtime_cdn(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        assert "https://cdn.jsdelivr.net" in manifest["externalOrigins"]

    def test_a_published_basemap_declares_the_hosts_it_will_ask_for(
        self, tmp_path
    ) -> None:
        """End to end, because that is where the blank map came from."""
        directory = build_hosted(tmp_path, basemap="voyager")
        origins = build_publish_manifest(directory)["externalOrigins"]
        assert "https://basemaps.cartocdn.com" in origins
        assert "https://tiles-a.basemaps.cartocdn.com" in origins

    def test_a_caller_can_state_the_origins_itself(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path), external_origins=[])
        assert manifest["externalOrigins"] == []

    def test_a_calling_override_gets_the_same_cap_and_the_same_refusal(
        self, tmp_path
    ) -> None:
        """Knowing more than the page does not buy a policy the API will trim."""
        too_many = [
            f"https://h{i}.example.com" for i in range(MAX_EXTERNAL_ORIGINS + 1)
        ]
        with pytest.raises(PublishManifestError, match=str(MAX_EXTERNAL_ORIGINS)):
            build_publish_manifest(build_hosted(tmp_path), external_origins=too_many)

    def test_a_flat_map_ships_no_script_sources(self, tmp_path) -> None:
        manifest = build_publish_manifest(build_hosted(tmp_path))
        assert manifest["runtimeScriptSources"] == []

    def test_a_relief_map_ships_the_worker_the_runtime_will_fetch(
        self, tmp_path
    ) -> None:
        """Off a whole page, because the worker is the half of relief that
        `externalOrigins` structurally cannot carry."""
        directory = build_relief_page(tmp_path)
        manifest = build_publish_manifest(directory)
        assert manifest["runtimeScriptSources"] == [TERRAIN_MESH_WORKER]
        assert "https://s3.amazonaws.com" in manifest["externalOrigins"]

    def test_the_script_sources_are_not_overridable_by_the_caller(
        self, tmp_path
    ) -> None:
        """`external_origins=` is a caller knowing more about who the page
        talks to. It is deliberately not a way to widen what may execute."""
        directory = build_relief_page(tmp_path)
        manifest = build_publish_manifest(directory, external_origins=[])
        assert manifest["runtimeScriptSources"] == [TERRAIN_MESH_WORKER]

    def test_it_serialises(self, tmp_path) -> None:
        """It exists to be sent, so it has to survive `json.dumps` unchanged."""
        manifest = build_publish_manifest(build_hosted(tmp_path))
        assert json.loads(json.dumps(manifest)) == manifest

    def test_a_directory_with_no_page_is_refused(self, tmp_path) -> None:
        with pytest.raises(PublishManifestError, match=ENTRY_NAME):
            build_publish_manifest(tmp_path)

    def test_a_nested_directory_is_refused(self, tmp_path) -> None:
        """Hosted output is flat: the page could not reach a nested file."""
        (build_hosted(tmp_path) / "nested").mkdir()
        with pytest.raises(PublishManifestError, match="flat"):
            build_publish_manifest(tmp_path)
