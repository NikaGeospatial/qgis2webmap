"""The hosted tier: data in its own files, runtime from a pinned CDN copy.

Every test here is about something a hosted page is forbidden to do - inline a
script, inline a compressed block, name a file by anything but its digest - so
they are written as refusals rather than as shapes. A shape assertion would pass
on a page that also did the forbidden thing somewhere else in it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    IconAtlasSpec,
    LabelingSpec,
    OutputMode,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.manifest_builder import (
    build_manifest,
    collect_data_payloads,
)
from nika_onlymap_exporter.packaging.artifact_builder import terrain_zoom_clamp
from nika_onlymap_exporter.packaging.cdn_runtime import (
    CDN_URL_TEMPLATE,
    RuntimeLockError,
    pinned_runtime,
    runtime_script_tag,
    sri_from_hex,
)
from nika_onlymap_exporter.packaging.hosted_assets import (
    DATA_EXTENSION,
    FLAT_NAME_PATTERN,
    HostedModeConflictError,
    data_urls,
    flat_file_name,
    write_external_data,
)
from nika_onlymap_exporter.packaging.publish_manifest import build_publish_manifest
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle, sha256_of
from nika_onlymap_exporter.writers.onlymap_writer import (
    ExportBlockedError,
    OnlyMapWriter,
)

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            "properties": {"name": "a"},
        }
    ],
}


def make_layer(**overrides) -> ExportLayer:
    defaults = dict(
        layer_id="pts",
        name="Points",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson=GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=255, g=0, b=0)),
        ),
    )
    defaults.update(overrides)
    return ExportLayer(**defaults)


def make_project(layers=None, **overrides) -> ExportProject:
    defaults = dict(
        title="Test map",
        layers=tuple([make_layer()] if layers is None else layers),
        extent=Extent(west=0.0, south=0.0, east=10.0, north=10.0),
        settings=ExportSettings(),
    )
    defaults.update(overrides)
    return ExportProject(**defaults)


LINES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[1.0, 2.0], [3.0, 4.0]],
            },
            "properties": {"name": "road", "surface": "laterite"},
        }
    ],
}

AREAS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]],
            },
            "properties": {"name": "ward 3", "households": 412},
        }
    ],
}

# A one-pixel PNG, standing in for the sheet QGIS rasterises a marker symbol
# into. The bytes do not matter; that it reaches the page as a `data:` URI
# does, because that is the one subresource form a hosted page may carry
# inline - it is not fetched, so there is nothing for a policy to allow.
ICON_SHEET = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def realistic_project(**overrides) -> ExportProject:
    """A project with the things a real one has, not the smallest one that runs.

    Conformance is a statement about every reference an artifact can contain, so
    the project it is asserted on has to be able to contain them: three layers
    so there is more than one data file, labels so there is a second payload per
    layer, a rasterised icon atlas so a `data:` image reaches the page, popups
    so author-controlled strings are interpolated into markup, and a basemap so
    the manifest has an external origin to declare.
    """
    defaults = dict(
        title="Lagos housing",
        abstract="Ward boundaries and access roads, surveyed 2026.",
        layers=(
            make_layer(
                layer_id="pts",
                name="Clinics",
                renderer=RendererSpec(
                    kind=RendererKind.SINGLE,
                    symbol=SymbolSpec(
                        fill_color=Color(r=255, g=0, b=0), icon_name="i0"
                    ),
                ),
                icon_atlas=IconAtlasSpec(
                    data_uri=ICON_SHEET,
                    mapping={"i0": {"x": 0, "y": 0, "width": 1, "height": 1}},
                    swatches={"i0": ICON_SHEET},
                ),
                labeling=LabelingSpec(enabled=True, field_name="name"),
                popup=PopupSpec(fields=(PopupFieldSpec(name="name", alias="Name"),)),
            ),
            make_layer(
                layer_id="roads",
                name="Access roads",
                geometry_kind=GeometryKind.LINE,
                geojson=LINES,
                popup=PopupSpec(
                    fields=(
                        PopupFieldSpec(name="name"),
                        PopupFieldSpec(name="surface", alias="Surface"),
                    )
                ),
            ),
            make_layer(
                layer_id="wards",
                name="Wards",
                geometry_kind=GeometryKind.POLYGON,
                geojson=AREAS,
                labeling=LabelingSpec(enabled=True, field_name="name"),
            ),
        ),
        settings=ExportSettings(basemap="positron", show_abstract=True),
    )
    defaults.update(overrides)
    return make_project(**defaults)


def strip_comments(html: str) -> str:
    """The markup with its comments removed.

    Always before a conformance assertion. The template carries a paragraph of
    guidance to whoever opens the file, and it talks about `src=` and about the
    runtime `<script>` block -- prose that would fail every check below while
    being, to a browser, nothing at all.
    """
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def subresource_references(markup: str) -> list[str]:
    """Everything the page will fetch, as it spells it.

    `src`, `data` and `icon-atlas` are the attributes an artifact puts a payload
    in: the runtime script, each layer's externalised data, and the sprite sheet
    a rasterised marker symbol draws from. A stylesheet `href` would be a fourth
    and there is none - the runtime's CSS is inlined - which is why `<link>` is
    read here and found absent rather than assumed to be.

    An `<a href>` is deliberately not in this list. A link is somewhere the
    reader may choose to go, not something the page loads, and counting the two
    credit links as fetches would claim the map contacts a host it only points
    at.
    """
    references = re.findall(r'\b(?:src|data|icon-atlas)="([^"]+)"', markup)
    references += re.findall(r'<link\b[^>]*\bhref="([^"]+)"', markup)
    return references


class FakeRuntime:
    """A stand-in so writer tests do not need the real 8 MB bundle."""

    def load(self) -> RuntimeBundle:
        js = b"/* fake runtime */"
        return RuntimeBundle(
            javascript=js,
            css=b"om-map:not(:defined) om-fallback { display: block; }",
            version="0.0.0-test",
            sha256=sha256_of(js),
        )


def write_hosted(tmp_path, project=None, **overrides):
    return OnlyMapWriter(runtime_provider=FakeRuntime()).write(
        project or make_project(), tmp_path, hosted=True, **overrides
    )


class TestFlatFileName:
    def test_a_readable_name_survives(self) -> None:
        assert flat_file_name("Points of interest", ".geojson") == (
            "Points-of-interest.geojson"
        )

    def test_every_result_obeys_the_flat_rule(self) -> None:
        for hint in ("a/b", "..", "Ц", "x" * 400, "-leading", "e" * 63):
            name = flat_file_name(hint, DATA_EXTENSION)
            assert FLAT_NAME_PATTERN.match(name), name

    def test_a_name_that_sanitises_to_nothing_still_gets_one(self) -> None:
        assert flat_file_name("///", ".geojson") == "layer.geojson"

    def test_collisions_do_not_overwrite(self) -> None:
        """Two layers can share a name; their data cannot share a file."""
        first = flat_file_name("Roads", DATA_EXTENSION)
        second = flat_file_name("Roads", DATA_EXTENSION, {first})
        assert first != second

    def test_a_suffixed_collision_still_fits(self) -> None:
        """The suffix comes out of the stem, not out of the 64-character limit."""
        taken = {flat_file_name("z" * 200, DATA_EXTENSION)}
        name = flat_file_name("z" * 200, DATA_EXTENSION, taken)
        assert FLAT_NAME_PATTERN.match(name), name
        assert name not in taken


class TestExternalData:
    def test_the_url_is_the_digest_of_what_was_written(self, tmp_path) -> None:
        written = write_external_data(collect_data_payloads(make_project()), tmp_path)
        assert len(written) == 1
        file = written[0]
        digest = hashlib.sha256(file.path.read_bytes()).hexdigest()
        assert file.url == f"assets/{digest}{DATA_EXTENSION}"
        assert file.hosted_url == f"/assets/{digest}{DATA_EXTENSION}"

    def test_only_the_hosted_form_is_root_absolute(self, tmp_path) -> None:
        """A folder or ZIP may be opened from a subdirectory, and a single file
        over `file://`, where a leading slash means the root of the disk."""
        file = write_external_data(collect_data_payloads(make_project()), tmp_path)[0]
        assert not file.url.startswith("/")
        assert file.hosted_url.startswith("/")

    def test_the_file_is_parseable_geojson(self, tmp_path) -> None:
        """The escaping the inline path needs must still leave valid JSON."""
        written = write_external_data(collect_data_payloads(make_project()), tmp_path)
        assert json.loads(written[0].path.read_text(encoding="utf-8")) == GEOJSON

    def test_labels_get_their_own_file(self, tmp_path) -> None:
        project = make_project(
            [
                make_layer(
                    labeling=LabelingSpec(enabled=True, field_name="name"),
                )
            ]
        )
        written = write_external_data(collect_data_payloads(project), tmp_path)
        assert {file.key for file in written} == {"pts", "pts-labels"}

    def test_identical_data_hashes_identically(self, tmp_path) -> None:
        """Content addressing is only worth anything if it is deterministic."""
        first = write_external_data(collect_data_payloads(make_project()), tmp_path)
        second = write_external_data(
            collect_data_payloads(make_project()), tmp_path / "again"
        )
        assert first[0].sha256 == second[0].sha256


class TestExternalisedManifest:
    def test_the_layer_references_the_file_and_carries_no_child(self) -> None:
        markup = build_manifest(make_project(), data_urls={"pts": "assets/abc.geojson"})
        assert 'data="assets/abc.geojson"' in markup
        assert '<script type="application/json">' not in markup

    def test_it_is_data_and_never_src(self) -> None:
        """`src` on om-layer belongs to COGLayer and friends, not to GeoJSON."""
        markup = build_manifest(make_project(), data_urls={"pts": "assets/abc.geojson"})
        assert "src=" not in markup

    def test_an_unmapped_layer_stays_inline(self) -> None:
        """A partial mapping degrades to the default, not to an empty layer."""
        markup = build_manifest(make_project(), data_urls={"other": "assets/x.geojson"})
        assert '<script type="application/json">' in markup

    def test_the_default_is_still_inline(self) -> None:
        markup = build_manifest(make_project())
        assert '<script type="application/json">' in markup
        assert "data=" not in markup


class TestPinnedRuntime:
    def test_the_lock_file_supplies_everything(self) -> None:
        runtime = pinned_runtime()
        assert runtime.version
        assert runtime.size_bytes > 0
        assert runtime.url == CDN_URL_TEMPLATE.format(
            package=runtime.package,
            version=runtime.version,
            file="onlymap.standalone.js",
        )

    def test_the_integrity_value_is_the_recorded_digest(self) -> None:
        runtime = pinned_runtime()
        assert runtime.integrity == sri_from_hex(runtime.sha256)

    def test_a_hex_digest_converts_rather_than_rehashes(self) -> None:
        digest = hashlib.sha256(b"abc").hexdigest()
        assert sri_from_hex(digest) == "sha256-" + __import__("base64").b64encode(
            bytes.fromhex(digest)
        ).decode("ascii")

    def test_something_that_is_not_a_digest_is_refused(self) -> None:
        with pytest.raises(RuntimeLockError):
            sri_from_hex("not a digest")

    def test_a_truncated_digest_is_refused(self) -> None:
        with pytest.raises(RuntimeLockError):
            sri_from_hex("abcd")

    def test_the_split_build_is_not_offered(self) -> None:
        """`onlymapjs.js` pulls further chunks one integrity value cannot pin."""
        with pytest.raises(RuntimeLockError):
            pinned_runtime("onlymapjs.js")

    def test_the_tag_pins_and_opts_out_of_credentials(self) -> None:
        tag = runtime_script_tag(pinned_runtime())
        assert 'integrity="sha256-' in tag
        assert 'crossorigin="anonymous"' in tag
        assert 'type="module"' not in tag


class TestHostedWrite:
    def test_the_page_carries_no_inline_script(self, tmp_path) -> None:
        """The whole reason for this mode: the CSP would refuse one."""
        html = write_hosted(tmp_path).entry_path.read_text(encoding="utf-8")
        # Comments stripped first: the template's own guidance to an editor
        # mentions the runtime `<script>` block, and a comment is not markup.
        markup = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        for opening in re.findall(r"<script\b[^>]*>", markup):
            assert "src=" in opening, opening

    def test_the_runtime_comes_from_the_cdn_with_an_integrity_pin(
        self, tmp_path
    ) -> None:
        html = write_hosted(tmp_path).entry_path.read_text(encoding="utf-8")
        runtime = pinned_runtime()
        assert runtime.url in html
        assert runtime.integrity in html
        assert 'import "./onlymap.js"' not in html

    def test_the_page_names_each_file_by_its_digest(self, tmp_path) -> None:
        result = write_hosted(tmp_path)
        html = result.entry_path.read_text(encoding="utf-8")
        data_files = [f for f in result.files if f.path.suffix == DATA_EXTENSION]
        assert data_files
        for file in data_files:
            digest = hashlib.sha256(file.path.read_bytes()).hexdigest()
            assert f'data="/assets/{digest}{DATA_EXTENSION}"' in html

    def test_the_data_file_is_written_beside_the_page(self, tmp_path) -> None:
        write_hosted(tmp_path)
        assert list(tmp_path.glob(f"*{DATA_EXTENSION}"))
        assert not [p for p in tmp_path.iterdir() if p.is_dir()]

    def test_nothing_is_gzipped(self, tmp_path) -> None:
        html = write_hosted(tmp_path).entry_path.read_text(encoding="utf-8")
        assert "x-om-gzip" not in html
        assert "DecompressionStream" not in html

    def test_a_large_map_is_still_not_compressed_inline(self, tmp_path) -> None:
        """Size normally flips data compression on; hosted mode overrides it."""
        big = dict(GEOJSON)
        big["features"] = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                "properties": {"name": "x" * 400},
            }
            for _ in range(8000)
        ]
        result = write_hosted(tmp_path, make_project([make_layer(geojson=big)]))
        html = result.entry_path.read_text(encoding="utf-8")
        assert "x-om-gzip" not in html

    def test_asking_for_both_is_an_error_rather_than_a_silent_choice(
        self, tmp_path
    ) -> None:
        writer = OnlyMapWriter(runtime_provider=FakeRuntime())
        with pytest.raises(HostedModeConflictError):
            writer.render_html(
                make_project(),
                FakeRuntime().load(),
                verdict=None,
                compress_data=True,
                hosted=True,
            )

    def test_hosted_and_unbundle_are_an_error(self, tmp_path) -> None:
        with pytest.raises(HostedModeConflictError):
            write_hosted(tmp_path, unbundle=True)

    def test_the_data_files_are_listed_in_the_result(self, tmp_path) -> None:
        result = write_hosted(tmp_path)
        names = {f.path.name for f in result.files}
        assert "index.html" in names
        assert any(name.endswith(DATA_EXTENSION) for name in names)


class TestDefaultsAreUnchanged:
    """The whole mode is additive; the other three tiers must not notice it."""

    def test_the_default_export_still_inlines_the_runtime(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, compress=False
        )
        html = result.entry_path.read_text(encoding="utf-8")
        assert "/* fake runtime */" in html
        assert "cdn.jsdelivr.net" not in html

    def test_the_default_export_still_inlines_the_data(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, compress=False
        )
        html = result.entry_path.read_text(encoding="utf-8")
        assert '<script type="application/json">' in html
        assert not list(tmp_path.glob(f"*{DATA_EXTENSION}"))

    def test_unbundling_still_imports_the_sibling(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, unbundle=True
        )
        html = result.entry_path.read_text(encoding="utf-8")
        assert 'import "./onlymap.js"' in html
        assert (tmp_path / "onlymap.js").exists()


class TestDataUrlMapping:
    def test_it_keys_on_the_element_id(self, tmp_path) -> None:
        written = write_external_data(collect_data_payloads(make_project()), tmp_path)
        assert set(data_urls(written, hosted=True)) == {"pts"}

    def test_the_tier_decides_the_reference_form(self, tmp_path) -> None:
        """No default on `hosted`: the wrong form fails in a browser, not here."""
        written = write_external_data(collect_data_payloads(make_project()), tmp_path)
        assert data_urls(written, hosted=True)["pts"].startswith("/assets/")
        assert data_urls(written, hosted=False)["pts"].startswith("assets/")


class TestPublishConformance:
    """The checks the platform runs on a published artifact, run here instead.

    `apps/control-plane` refuses an artifact at publish time for every one of
    these: exactly one `<script>` and it has to be the pinned runtime import,
    no inline script, no event-handler attribute, and no subresource reference
    that is not either content-addressed or a declared external origin. The
    served page's Content-Security-Policy then enforces the same shape a second
    time in the browser.

    They are asserted here as well because a mismatch found here is one person
    reading a failing test, and the same mismatch found there is a user who has
    signed in, waited for a build and been told no by a server. The two sets
    must say the same thing; if one of them changes, this is the file that has
    to change with it.
    """

    def page(self, tmp_path) -> str:
        write_hosted(tmp_path, realistic_project())
        return (tmp_path / "index.html").read_text(encoding="utf-8")

    def test_there_is_exactly_one_script_and_it_is_the_pinned_runtime(
        self, tmp_path
    ) -> None:
        markup = strip_comments(self.page(tmp_path))
        openings = re.findall(r"<script\b[^>]*>", markup)
        assert len(openings) == 1, openings

        runtime = pinned_runtime()
        assert f'src="{runtime.url}"' in openings[0]
        assert f'integrity="{runtime.integrity}"' in openings[0]
        assert re.search(r'integrity="sha256-[^"]+"', openings[0])

    def test_nothing_inline_can_execute(self, tmp_path) -> None:
        """Two ways a page runs code the CSP has not pinned, and both are out.

        The event-handler half is not hypothetical: the artifact interpolates
        author-controlled strings - layer names, field labels, a project
        abstract - into markup, and an `on*` attribute is what one of those
        turns into if an escape is ever missed.
        """
        markup = strip_comments(self.page(tmp_path))
        for opening in re.findall(r"<script\b[^>]*>", markup):
            assert "src=" in opening, opening
        assert not re.search(r"<\s*script\b[^>]*>\s*[^\s<]", markup)
        assert not re.search(r"\son[a-z]+\s*=", markup, re.IGNORECASE)

    def test_every_subresource_is_addressed_by_a_digest_the_manifest_declares(
        self, tmp_path
    ) -> None:
        """The rule that makes a hosted page servable at all.

        Anything the page fetches has to be one of three things: an asset the
        store holds under its own digest, an image carried in the page, or a
        host the manifest declared so the CSP will allow it. A fourth kind -
        a relative sibling name, the way every offline tier refers to its files
        - resolves against the visitor's URL rather than the asset store, and
        404s on a link that happened to carry a trailing slash.
        """
        write_hosted(tmp_path, realistic_project())
        manifest = build_publish_manifest(tmp_path)
        digests = {entry["sha256"] for entry in manifest["files"]}
        origins = set(manifest["externalOrigins"])
        markup = strip_comments((tmp_path / "index.html").read_text(encoding="utf-8"))

        references = subresource_references(markup)
        assert any(reference.startswith("/assets/") for reference in references)
        assert any(reference.startswith("data:image/") for reference in references)
        for reference in references:
            if reference.startswith("data:image/"):
                continue
            if reference.startswith("https://"):
                parts = urlsplit(reference)
                assert f"{parts.scheme}://{parts.netloc}" in origins, reference
                continue
            match = re.fullmatch(r"/assets/([0-9a-f]{64})\.[a-z]+", reference)
            assert match is not None, reference
            assert match.group(1) in digests, reference

    def test_a_relief_map_is_refused_rather_than_quietly_losing_its_clamp(
        self, tmp_path
    ) -> None:
        """Relief is the one feature whose export needs a script.

        Dropping `terrain_zoom_clamp` for hosted output would publish a map
        that zooms past the last elevation tiles and blanks its own terrain,
        with nothing at any point saying so. There is no declarative
        replacement - `<om-map>` has no camera clamp in the pinned runtime, and
        `terrain-max-zoom` caps the DEM tileset rather than the camera - so the
        export stops instead.
        """
        project = realistic_project(settings=ExportSettings(terrain="terrarium"))
        with pytest.raises(ExportBlockedError) as raised:
            write_hosted(tmp_path, project)

        message = str(raised.value)
        assert "relief" in message.lower()
        # A blocker with no remedy is a dead end. Both ways out are named.
        assert "Turn relief off" in message
        assert "Folder" in message

    def test_the_same_relief_map_still_exports_to_every_other_tier(
        self, tmp_path
    ) -> None:
        """The refusal is about hosting, not about relief."""
        project = realistic_project(settings=ExportSettings(terrain="terrarium"))
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            project,
            tmp_path,
            mode=OutputMode.FOLDER,
            compress=False,
            preview_hook=terrain_zoom_clamp(project),
            unbundle=True,
        )
        html = result.entry_path.read_text(encoding="utf-8")
        assert "setViewInternal" in html

    def test_a_page_hook_is_refused_rather_than_dropped(self, tmp_path) -> None:
        """The slot itself, not just the one thing that goes in it.

        Whatever a future caller composes into the preview hook is inline
        script, so the writer refuses the combination rather than trusting each
        caller to remember which tier it is building for.
        """
        with pytest.raises(HostedModeConflictError):
            write_hosted(tmp_path, preview_hook="<script>console.log(1)</script>")
