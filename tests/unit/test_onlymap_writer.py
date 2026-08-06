"""The writer and its artifact result.

Needs the OnlyMap runtime on disk; skips cleanly without it, like the QGIS tier.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    OutputMode,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.license_policy import CapVerdict
from nika_onlymap_exporter.packaging import runtime_manager
from nika_onlymap_exporter.packaging.runtime_manager import (
    LocalRuntime,
    RuntimeBundle,
    RuntimeUnavailableError,
    discover_runtime_dir,
    lock_mismatches,
    read_lock,
    sha256_of,
)
from nika_onlymap_exporter.writers.onlymap_writer import (
    OnlyMapWriter,
    generator_line,
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


def make_project(**overrides) -> ExportProject:
    layer = ExportLayer(
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
    defaults = dict(
        title="Test map",
        layers=(layer,),
        extent=Extent(west=0.0, south=0.0, east=10.0, north=10.0),
        settings=ExportSettings(),
    )
    defaults.update(overrides)
    return ExportProject(**defaults)


class FakeRuntime:
    """A stand-in so writer tests do not need the real 5 MB bundle."""

    def load(self) -> RuntimeBundle:
        js = b"/* fake runtime */"
        return RuntimeBundle(
            javascript=js,
            css=b"om-map:not(:defined) om-fallback { display: block; }",
            version="0.0.0-test",
            sha256=sha256_of(js),
        )


class TestGeneratorLine:
    def test_names_both_versions_and_the_privacy_promise(self) -> None:
        line = generator_line(
            FakeRuntime().load(), when=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        )
        assert "QGIS2WebMap by NIKA" in line
        assert "0.0.0-test" in line
        assert "2026-07-30" in line
        assert "no tracking" in line


class TestWriter:
    def test_writes_an_index_html(self, tmp_path) -> None:
        """Always `index.html` - a recipient should never have to choose."""
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.entry_path.name == "index.html"
        assert result.entry_path.exists()

    def test_uncompressed_artifact_inlines_the_runtime_verbatim(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, compress=False
        )
        html = result.entry_path.read_text()
        assert "/* fake runtime */" in html
        assert '<script type="application/json">' in html
        assert "om-map:not(:defined)" in html, "the fallback CSS gate must be present"

    def test_compressed_artifact_carries_a_bootstrap(self, tmp_path) -> None:
        """Compressed by default: the runtime becomes base64 plus an inflater."""
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, compress=True
        )
        html = result.entry_path.read_text()
        assert "DecompressionStream" in html
        assert "/* fake runtime */" not in html
        # Small data stays readable so the map can still be hand-edited.
        assert '<script type="application/json">' in html

    def test_reports_itself_as_offline(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.is_offline
        assert result.network_dependencies == ()

    def test_records_runtime_provenance(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.runtime_version == "0.0.0-test"
        assert len(result.runtime_sha256) == 64

    def test_two_writes_produce_the_same_manifest_snapshot(self, tmp_path) -> None:
        """Reproducibility: the same project must always describe itself the same."""
        writer = OnlyMapWriter(runtime_provider=FakeRuntime())
        first = writer.write(make_project(), tmp_path / "a")
        second = writer.write(make_project(), tmp_path / "b")
        assert first.manifest_snapshot == second.manifest_snapshot

    def test_lossy_transform_produces_a_warning(self, tmp_path) -> None:
        project = make_project(settings=ExportSettings(quantize_precision=6))
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(project, tmp_path)
        assert any("less precise" in w for w in result.warnings)

    def test_clean_export_has_no_warnings(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.warnings == ()

    def test_title_is_escaped_in_the_document(self, tmp_path) -> None:
        project = make_project(title="Roads & <Paths>")
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(project, tmp_path)
        html = result.entry_path.read_text()
        assert "&amp;" in html
        assert "<Paths>" not in html

    def test_mode_is_recorded(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, mode=OutputMode.STANDALONE_HTML
        )
        assert result.mode is OutputMode.STANDALONE_HTML


class TestCreditComponent:
    """The OnlyMap corner component and its data credits.

    Issue #29 makes both acquisition calls to action part of the artifact
    contract, and sets release gates on them: keyboard accessible, not covering
    map controls, and no exfiltration of map data.
    """

    def html(self, **project_overrides) -> str:
        return OnlyMapWriter(runtime_provider=FakeRuntime()).render_html(
            make_project(**project_overrides),
            FakeRuntime().load(),
            verdict=CapVerdict(allowed=True),
        )

    def test_the_attribution_is_present(self) -> None:
        markup = self.html()
        assert "Built with" in markup
        assert "OnlyMap" in markup

    def test_no_link_in_the_credit_is_dead(self) -> None:
        """ "Enhance" and "Host" pointed at pages that returned 404.

        Every exported map carried them, so every recipient met a dead link. They
        are gone until there is something behind them - for Enhance, something
        that hands the file to an AI assistant rather than describing how to.
        """
        markup = self.html()
        assert ">Enhance</a>" not in markup
        assert ">Host</a>" not in markup
        assert "enhance-with-ai" not in markup
        assert "onlymap/hosting" not in markup

    def test_every_credit_link_opens_in_a_new_tab(self) -> None:
        """Following one in place unloads the map and loses the reader's view.

        Coming back means a full reload of an inlined dataset, at whatever
        default viewport the map was authored with. `noopener noreferrer` rides
        along: the first denies the opened page `window.opener`, the second
        keeps a local file path out of the Referer header.
        """
        credit = (
            self.html().split('<footer class="om-credit">')[1].split("</footer>")[0]
        )
        anchors = credit.split("<a")[1:]
        assert anchors, "the credit is supposed to carry links"
        for anchor in anchors:
            opening = anchor.split(">")[0]
            assert 'target="_blank"' in opening
            assert 'rel="noopener noreferrer"' in opening

    def test_the_runtime_attribution_sits_above_the_credit_chip(self) -> None:
        """Bottom-right, but not flush with the corner any more.

        Runtime 0.6.0 mounts its own provider-attribution control into the
        `bottom-end` widget slot, and that slot container is anchored at exactly
        `bottom: 12px; inset-inline-end: 12px` - the coordinates this chip used
        to claim. They drew on top of each other. The control measures 24px, so
        the chip clears it by 12 + 24 + 8, the last being the runtime's own
        `--om-widget-gap-y`.

        An `om-widget` in that slot would be better than an offset and was
        tried: the runtime only adopts an `om-widget` that is a child of
        `<om-map>`, and this component is a sibling of it in the template body,
        so it was never slotted - it just lost its positioning and landed on the
        zoom controls. Moving it inside means moving it into `build_manifest`.
        """
        markup = self.html()
        credit_css = markup.split(".om-credit {")[1].split("}")[0]
        assert "position: absolute" in credit_css
        assert "right: 12px" in credit_css
        assert "bottom: 12px" in credit_css

        # The attribution is the thing that moves, lifted clear of the chip.
        # Two reservations: the chip is one line, or two once it carries a data
        # credit, and it grows upward from its anchor.
        assert '[data-om-widget-slot="bottom-end"]' in markup
        assert "calc(12px + 36px + 8px)" in markup
        assert "body:has(.om-credit-data)" in markup
        assert "calc(12px + 54px + 8px)" in markup

    def test_the_widget_stack_is_not_hand_offset(self) -> None:
        """The bottom-left corner is the runtime's flex slot to lay out.

        The template used to force `bottom: 58px !important` on the zoom
        controls and `30px` on the scale bar, to clear a licence notice the
        runtime drew loose in that corner. 0.6.0 puts the badge in the
        `bottom-start` slot with them, and the slot spaces all three - so the
        offsets only shifted widgets out of a flow that still reserved their
        space, which is the ragged gap in the corner.
        """
        markup = self.html()
        assert "58px !important" not in markup
        assert 'om-widget[type="scale-bar"] {' not in markup

    def test_it_is_reachable_by_keyboard(self) -> None:
        """`:focus-within` is what expands it for a tabbing user, no script."""
        markup = self.html()
        assert ".om-credit:focus-within .om-credit-row" in markup
        assert "<button" in markup.split('class="om-credit"')[1]

    def test_it_never_posts_anywhere(self) -> None:
        """The release gate: opening the map must not move its data."""
        credit = (
            self.html().split('<footer class="om-credit">')[1].split("</footer>")[0]
        )
        for forbidden in ("fetch(", "XMLHttpRequest", "<form", "navigator.sendBeacon"):
            assert forbidden not in credit

    def test_no_credits_means_no_data_line(self) -> None:
        assert 'class="om-credit-data"' not in self.html()

    def test_layer_attribution_reaches_the_artifact(self) -> None:
        """The gap this closes: read from QGIS, stored, and never rendered."""
        layer = make_project().layers[0]
        credited = ExportLayer(
            **{
                **{f.name: getattr(layer, f.name) for f in fields(layer)},
                "attribution": "© OSM contributors",
            }
        )
        markup = self.html(layers=(credited,))

        assert 'class="om-credit-data"' in markup
        assert "© OSM contributors" in markup

    def test_a_credit_cannot_inject_markup(self) -> None:
        """Layer metadata is author-controlled text, not trusted markup."""
        layer = make_project().layers[0]
        hostile = ExportLayer(
            **{
                **{f.name: getattr(layer, f.name) for f in fields(layer)},
                "attribution": "<img src=x onerror=alert(1)>",
            }
        )
        markup = self.html(layers=(hostile,))

        assert "<img src=x" not in markup
        assert "&lt;img src=x" in markup


class TestRuntimeManager:
    def test_missing_runtime_raises_rather_than_degrading(self, tmp_path) -> None:
        """A placeholder runtime is a blank page for the recipient."""
        with pytest.raises(RuntimeUnavailableError):
            LocalRuntime(tmp_path).load()

    def test_missing_stylesheet_is_also_fatal(self, tmp_path) -> None:
        """Without the CSS the no-JavaScript fallback silently stops working."""
        (tmp_path / "onlymap.standalone.js").write_bytes(b"x")
        with pytest.raises(RuntimeUnavailableError, match="stylesheet"):
            LocalRuntime(tmp_path).load()

    def test_real_runtime_loads_when_present(self) -> None:
        directory = discover_runtime_dir()
        if directory is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        bundle = LocalRuntime(directory).load()
        assert bundle.total_bytes > 1_000_000
        assert bundle.version != ""
        assert len(bundle.sha256) == 64

    def test_no_developer_paths_anywhere_in_the_module(self) -> None:
        """It ships to macOS and Windows, where `~/Nika/...` does not exist.

        A hardcoded checkout path also *hides* a missing runtime on the one
        machine that has a checkout, so the packaging gap only ever surfaces on
        a user's machine.

        `Path.home()` itself is fine and necessary - `default_cache_dir` builds
        the per-platform cache location from it. What must never appear is a
        path naming somebody's project directory.
        """
        source = Path(runtime_manager.__file__).read_text(encoding="utf-8")
        for developer_path in ("Nika/nika-agent", "/Users/", "C:\\", "/nix/store"):
            assert developer_path not in source, f"{developer_path} is hardcoded"

    def test_discovery_uses_only_relative_candidates(self) -> None:
        """Discovery must resolve from a checkout and resolve to nothing else."""
        source = Path(runtime_manager.__file__).read_text(encoding="utf-8")
        body = source.split("def discover_runtime_dir")[1].split("\ndef ")[0]
        assert "Path.home()" not in body


class TestRuntimeLock:
    """`runtime-lock.json` pins the OnlyMap build the plugin is tested against."""

    def test_the_lock_file_ships_and_parses(self) -> None:
        lock = read_lock()
        assert lock, "runtime-lock.json is missing or unreadable"
        assert lock["version"]
        assert set(lock["files"]) == {"onlymap.standalone.js", "onlymapjs.css"}

    def test_matching_bytes_produce_no_warning(self) -> None:
        directory = discover_runtime_dir()
        if directory is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        bundle = LocalRuntime(directory).load()
        assert bundle.lock_warnings == (), (
            "the runtime on disk is not the pinned build; "
            "run scripts/lock_runtime.py if the move was deliberate"
        )

    def test_an_unexpected_build_is_reported(self) -> None:
        assert lock_mismatches(b"not the runtime", b"not the css")

    def test_a_test_double_trips_nothing(self) -> None:
        """Providers own the check, so a fake bundle carries no warnings."""
        assert FakeRuntime().load().lock_warnings == ()
