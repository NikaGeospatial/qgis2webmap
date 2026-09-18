"""The one writer: normalized model in, artifact out.

There is deliberately only one. qgis2web has two - a Leaflet family and an
OpenLayers family, ~7,500 lines between them - and every symbology fix has to be
made in both. Where it was not, the two diverged.

**Preview and export share this code path.** Issue #29 requires it and qgis2web
proves it works: if preview runs through a different renderer it can drift from
what actually ships, and the drift is invisible until a recipient complains.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.export_ir import (
    Color,
    ExportProject,
    FidelityItem,
    OutputMode,
    OverlayCorner,
)
from ..core.fidelity_report import FidelityReportBuilder
from ..core.license_policy import CapVerdict, LicensePolicy, default_policy
from ..core.manifest_builder import (
    build_manifest,
    collect_attributions,
    collect_data_payloads,
)
from ..packaging.asset_embedder import (
    build_bootstrap,
    build_data_inflater,
    gzip_base64,
    should_compress_data,
)
from ..packaging.cdn_runtime import pinned_runtime, runtime_script_tag
from ..packaging.dependency_scanner import ScanResult, scan
from ..packaging.hosted_assets import (
    ExternalDataFile,
    HostedModeConflictError,
    data_urls,
    write_external_data,
)
from ..packaging.raster_cog import CancelCheck, ProgressCallback
from ..packaging.raster_staging import RasterStagingResult, stage_rasters
from ..packaging.runtime_manager import (
    RuntimeBundle,
    RuntimeProvider,
    default_provider,
)

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "map.html"
METADATA_PATH = Path(__file__).resolve().parent.parent / "metadata.txt"


def _plugin_version() -> str:
    """The plugin's version, read from `metadata.txt` rather than repeated.

    This was a literal through 0.1.3, and it was never bumped: every map exported
    by 0.1.0, 0.1.2 and 0.1.3 alike stamped itself `0.1.0` in its generator
    line. Nothing caught it because nothing asserted it, and the line is not
    something a person reads on the way past - it is there for whoever opens
    the file months later asking what produced it, which is exactly when a
    wrong answer costs the most.

    `metadata.txt` is the file the QGIS plugin repository reads, so it is the
    one that always gets bumped for a release. Reading it is the only way a
    second copy cannot drift from it.

    Falls back rather than raising: a version string is not worth failing an
    export over, and "unknown" is at least not a lie.
    """
    try:
        for line in METADATA_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("version="):
                return line.partition("=")[2].strip()
    except OSError:
        pass
    return "unknown"


PLUGIN_VERSION = _plugin_version()

# The unbundled runtime's file name. Stable on purpose: a server can cache it,
# and re-exporting a map does not invalidate every other map on the site.
RUNTIME_FILE_NAME = "onlymap.js"


class ExportBlockedError(RuntimeError):
    """The export cannot produce a working artifact.

    Raised only for genuinely unrecoverable conditions - nothing to export, a
    required asset missing. Licence-cap breaches are *not* in this category: they
    warn, and the map explains itself to the recipient.
    """

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True)
class ArtifactFile:
    path: Path
    size_bytes: int

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.path.name, "sizeBytes": self.size_bytes}


@dataclass(frozen=True)
class ArtifactResult:
    """What a write produced. The shape issue #29 specifies.

    `manifest_snapshot` is the deterministic record: the same project written
    twice produces the same snapshot, which is what makes an export reproducible
    and a regression visible in a diff.
    """

    entry_path: Path
    mode: OutputMode
    files: tuple[ArtifactFile, ...] = ()
    runtime_version: str = "unknown"
    runtime_sha256: str = ""
    compressed: bool = False
    network_dependencies: tuple[str, ...] = ()
    fidelity: tuple[FidelityItem, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def is_offline(self) -> bool:
        """No network dependency means the artifact works with no internet."""
        return not self.network_dependencies

    def snapshot(self) -> dict[str, Any]:
        return {
            "entry": self.entry_path.name,
            "mode": self.mode.value,
            "files": [f.snapshot() for f in self.files],
            "runtimeVersion": self.runtime_version,
            "runtimeSha256": self.runtime_sha256,
            "compressed": self.compressed,
            "networkDependencies": list(self.network_dependencies),
            "fidelity": [i.snapshot() for i in self.fidelity],
            "warnings": list(self.warnings),
            "manifest": self.manifest_snapshot,
        }


def _inline_runtime_element(script_body: str) -> str:
    """The template's runtime `<script>`, with the body already built.

    A module because two of the three inline shapes need to be one: the folder
    tier's `import "./onlymap.js"` and the compressed tier's bootstrap both use
    `import`, which is a syntax error in a classic script. The uncompressed
    third shape does not care, so all three share this.
    """
    return f'    <script type="module">\n{script_body}\n    </script>'


def generator_line(
    runtime: RuntimeBundle, when: datetime | None = None, stamped: bool = True
) -> str:
    """The provenance comment placed at the top of every artifact.

    Written for two readers. A person wants to know what made this and when; an
    AI assistant asked to modify the file wants to know which runtime version's
    attribute vocabulary applies.

    `stamped` is the *when*, and hosted output turns it off. A build timestamp
    is content, so it changes the page's digest every time the clock moves: the
    same project exported twice a minute apart produced two different
    `index.html` digests, which meant every republish re-uploaded a page that
    had not changed and the server's "nothing changed, keep the release you
    have" path could never fire for a project that genuinely had not changed. A
    timestamp the producer writes into bytes it also hashes costs more than it
    is worth.

    Nothing about provenance is lost by dropping it there. The publish manifest
    carries `producer.name` and `producer.version`, and the release row records
    who published and when from the server's clock - which is the trustworthy
    one anyway, being the one the publisher cannot set.

    **The offline tiers keep it, and the asymmetry is deliberate.** A file on
    someone's disk has no release row, no manifest and no server that remembers
    it; the banner is the only thing that can answer "what made this and when"
    when it is opened months later, which is exactly when a wrong or absent
    answer costs the most. And nothing dedups those tiers by digest, so the
    cost that makes it wrong for hosting does not exist for them.
    """
    line = f"Generated by QGIS2WebMap by NIKA {PLUGIN_VERSION}"
    if stamped:
        stamp = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
        line += f" on {stamp}"
    return (
        f"{line}\n"
        f"  OnlyMap runtime {runtime.version} (sha256 {runtime.sha256[:16]}...)\n"
        "  Sends one anonymous usage report to NIKA on load; no map data, no\n"
        "  identifier for whoever opened it."
    )


class OnlyMapWriter:
    """Builds an OnlyMap artifact from a normalized project."""

    def __init__(
        self,
        runtime_provider: RuntimeProvider | None = None,
        license_policy: LicensePolicy | None = None,
    ) -> None:
        self.runtime_provider = runtime_provider or default_provider()
        self.license_policy = license_policy or default_policy()

    def render_html(
        self,
        project: ExportProject,
        runtime: RuntimeBundle,
        verdict: CapVerdict,
        when: datetime | None = None,
        compress: bool = True,
        compress_data: bool = False,
        preview_hook: str = "",
        runtime_file: str | None = None,
        hosted: bool = False,
        layer_data_urls: Mapping[str, str] | None = None,
    ) -> str:
        """Fill the artifact template. No string-built HTML beyond the manifest.

        `hosted` swaps the two things a served page cannot keep: the inlined
        runtime becomes an SRI-pinned `<script src>` to the CDN, and any layer
        in `layer_data_urls` references its data instead of carrying it. Both
        are refusals to run inline script, which the hosted CSP forbids.
        """
        if hosted and compress_data:
            raise HostedModeConflictError(
                "A hosted export cannot compress its layer data inline: the "
                "page's Content-Security-Policy allows no inline script, so the "
                "shim that inflates the blocks would never run and every layer "
                "would draw nothing. Hosted data is written uncompressed and "
                "compressed on the wire with Content-Encoding instead."
            )
        if hosted and runtime_file:
            raise HostedModeConflictError(
                "A hosted export loads the runtime from the CDN with an "
                "integrity pin, so it has no sibling runtime file to import."
            )
        if hosted and preview_hook:
            raise HostedModeConflictError(
                "A hosted export cannot carry a page hook: everything written "
                "into that slot is inline script, which the hosted page's "
                "Content-Security-Policy refuses to run and publish-time "
                "conformance refuses to accept. The one hook a shipped artifact "
                "uses is the relief camera clamp, and `scan` refuses a hosted "
                "export of a relief map rather than dropping it - see "
                "`dependency_scanner.hosted_relief_reason`."
            )

        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        manifest = build_manifest(
            project, verdict, compress_data=compress_data, data_urls=layer_data_urls
        )

        if hosted:
            # No inline body at all, not even an empty module: the CSP counts an
            # inline <script> as a violation whether or not it has anything in it.
            runtime_element = runtime_script_tag(pinned_runtime())
        elif runtime_file:
            # An import rather than a `src=` attribute, so the template's single
            # script block serves both shapes. The inline module resolves it
            # against the document URL, which is the sibling file we wrote.
            #
            # Compressed data needs its inflater in the SAME block, ahead of the
            # import. This branch used to emit the bare import and nothing else,
            # so a folder export large enough to gzip its data shipped base64 no
            # runtime could read - a map with rasters, legend and layer switcher
            # and not one feature, with no error anywhere. See the note on
            # `DATA_INFLATE_TEMPLATE`.
            script_body = (
                build_data_inflater(runtime_file)
                if compress_data
                else f'      import "./{runtime_file}";'
            )
            runtime_element = _inline_runtime_element(script_body)
        elif compress:
            # Gzipped base64 plus a bootstrap that inflates it. Roughly a third
            # of the raw size, and nobody edits minified runtime code anyway.
            script_body = build_bootstrap(gzip_base64(runtime.javascript))
            runtime_element = _inline_runtime_element(script_body)
        else:
            script_body = runtime.javascript.decode("utf-8")
            runtime_element = _inline_runtime_element(script_body)

        # Replacement, not format(): the template is full of CSS braces, and
        # str.format would choke on every one of them.
        replacements = {
            # Unstamped when hosted: the page is content-addressed and a clock
            # in it moves the digest. `generator_line` has the whole argument.
            "@GENERATOR@": generator_line(runtime, when, stamped=not hosted),
            "@TITLE@": _escape_text(project.title),
            "@MANIFEST@": manifest,
            "@ATTRIBUTION@": _attribution_block(project),
            "@CAPTION@": _caption_block(project),
            "@WIDGET_COLORS@": _widget_color_block(project),
            # Never compressed: the fallback's CSS gate must work without JS.
            "@RUNTIME_CSS@": runtime.css.decode("utf-8"),
            "@RUNTIME_SCRIPT@": runtime_element,
            "@PREVIEW_HOOK@": preview_hook,
        }

        # One pass, not a loop of replaces. Substituting sequentially would let
        # earlier-inserted content be scanned again: a layer named
        # "@RUNTIME_SCRIPT@" would have five megabytes of runtime pasted into its
        # label. A single regex pass can only ever match the template's own
        # tokens.
        pattern = re.compile("|".join(re.escape(t) for t in replacements))
        return pattern.sub(lambda m: replacements[m.group(0)], template)

    def _stage_rasters(
        self,
        project: ExportProject,
        destination: Path,
        mode: OutputMode,
        report: FidelityReportBuilder,
        on_progress: ProgressCallback | None,
        should_cancel: CancelCheck | None,
        hosted: bool = False,
    ) -> RasterStagingResult:
        """Convert this project's rasters into the artifact directory.

        A one-line wrapper on purpose: it exists so `write` reads as a sequence
        of steps rather than a paragraph of arguments, and so a test can drive
        the raster pass without reproducing the writer.
        """
        return stage_rasters(
            project,
            destination,
            mode,
            report,
            on_progress=on_progress,
            should_cancel=should_cancel,
            hosted=hosted,
        )

    def write(
        self,
        project: ExportProject,
        destination: Path,
        mode: OutputMode = OutputMode.STANDALONE_HTML,
        when: datetime | None = None,
        compress: bool = True,
        preview_hook: str = "",
        unbundle: bool = False,
        hosted: bool = False,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> ArtifactResult:
        """Write the artifact and describe what was produced.

        `destination` is a directory; the entry file is always `index.html` so a
        recipient never has to guess which file to open.

        `unbundle` writes the runtime beside the page instead of inside it. That
        is the whole point of the folder tier: a served map gets a small HTML
        file and a runtime the browser caches across every map on the site,
        rather than the same ~8.3 MB re-downloaded each time. It is
        **only** valid over HTTP - the runtime loads as a module, and a module
        cannot be fetched from `file://` - so the other tiers stay inlined.

        NIKA hosting does not unbundle at all - see `hosted` below. Its page
        has no sibling runtime to import, because it loads the runtime from the
        CDN under an `integrity` pin, so there is nothing for
        `exporters/hosted.py` to upload. The two tiers therefore differ in how
        the page reaches its runtime and its data, and the difference lives
        entirely in this one method: both shapes come out of the same writer,
        which is the property that keeps them from drifting apart.

        Scanning happens first: an export that cannot work for its recipient
        should fail before anything is written, not after.

        `hosted` is the served tier and the opposite trade to every other one.
        Nothing is inlined: each layer's data is written as its own uncompressed
        file, referenced by its content digest at `/assets/{sha256}.geojson`, and
        the runtime comes from the CDN pinned by `integrity`. That is not a
        preference. A hosted page is served under a Content-Security-Policy
        whose `script-src` names only that one pinned runtime, so an inlined
        runtime, an inflate shim and a gzipped data block are each an inline
        script the browser will refuse to run. Off by default, and every other
        tier is untouched by it: they have to open with no server and no
        internet, which is exactly what this mode gives up.

        The order inside hosted mode is fixed and load-bearing. Data files are
        written and hashed first, because the page has to name their digests;
        the page is written after, and hashed last, once nothing in it can still
        change. `packaging/publish_manifest.py` does that final hashing.

        `on_progress` and `should_cancel` exist for the raster pass and only for
        it. Everything else here is milliseconds; converting an orthophoto to a
        Cloud-Optimized GeoTIFF is the one part of an export that can run for
        minutes, so it is the one part with a progress line and a way out. They
        take the shape the rest of the plugin already uses -- see
        `raster_cog.ProgressCallback` -- so an existing progress widget can be
        handed straight in.
        """
        if hosted and unbundle:
            raise HostedModeConflictError(
                "A hosted export loads the runtime from the CDN, so there is no "
                "sibling runtime file to unbundle."
            )

        runtime = self.runtime_provider.load()
        verdict = self.license_policy.evaluate(project)

        scan_report = FidelityReportBuilder()
        scan_result: ScanResult = scan(project, scan_report, mode, hosted=hosted)
        if not scan_result.can_export:
            raise ExportBlockedError(scan_result.blocking_reasons)

        # Small maps keep readable data so a person or an agent can edit them.
        # Never in hosted mode, at any size: the wire already compresses, and
        # the page has no shim to undo an inline compression with.
        compress_data = (
            not hosted and compress and should_compress_data(scan_result.data_bytes)
        )

        destination.mkdir(parents=True, exist_ok=True)
        written: list[ArtifactFile] = []

        # Rasters before the page, because the page has to name them. Staging
        # is what turns `RasterSpec.path` -- a file on this machine -- into a
        # `src` the recipient can fetch, and it is also the last point at which
        # a raster that cannot be carried can stop the export instead of
        # producing a map with a blank layer in it. `staging.project` carries
        # the filled-in specs; nothing downstream may use `project` again.
        # Hosted only changes the reference the page writes: a raster is named
        # by its content digest there, the way layer data already is.
        staging = self._stage_rasters(
            project, destination, mode, scan_report, on_progress, should_cancel, hosted
        )
        if not staging.can_export:
            raise ExportBlockedError(staging.blocking_reasons)
        project = staging.project
        written.extend(
            ArtifactFile(path, path.stat().st_size) for path in staging.files
        )

        # Before the page and before the runtime, because the page names these
        # files by their digests. See the ordering note in this docstring.
        external: tuple[ExternalDataFile, ...] = ()
        layer_data_urls: Mapping[str, str] | None = None
        if hosted:
            external = write_external_data(collect_data_payloads(project), destination)
            layer_data_urls = data_urls(external, hosted=True)
            written.extend(
                ArtifactFile(file.path, file.size_bytes) for file in external
            )

        runtime_file = None
        if unbundle:
            runtime_file = RUNTIME_FILE_NAME
            runtime_path = destination / runtime_file
            runtime_path.write_bytes(runtime.javascript)
            written.append(ArtifactFile(runtime_path, runtime_path.stat().st_size))

        entry = destination / "index.html"
        entry.write_text(
            self.render_html(
                project,
                runtime,
                verdict,
                when,
                compress,
                compress_data,
                preview_hook,
                runtime_file,
                hosted,
                layer_data_urls,
            ),
            encoding="utf-8",
        )
        written.insert(0, ArtifactFile(entry, entry.stat().st_size))

        # Cap violations are deliberately NOT warned about here. Since runtime
        # 0.6.0 the free-tier caps apply only on a hosted `http(s)` page, and
        # every artifact this writer produces is opened from a file or from
        # localhost - so a breach predicts nothing that will actually happen,
        # and warning about it on every export trained people to dismiss the
        # dialog. `verdict.violations` still reaches the Fidelity report through
        # `report_verdict`, and the caps are documented in the README and in
        # `docs/supported-features.md` for anyone who does publish to a server.
        warnings: list[str] = []
        # An unexpected runtime build is worth saying out loud: the artifact is
        # still written, but "it worked on my machine" usually starts here.
        warnings.extend(runtime.lock_warnings)
        if project.settings.has_lossy_transform:
            warnings.append(
                "A lossy transform was applied, so the exported coordinates are "
                "less precise than the source data."
            )

        return ArtifactResult(
            entry_path=entry,
            mode=mode,
            files=tuple(written),
            runtime_version=runtime.version,
            runtime_sha256=runtime.sha256,
            compressed=compress,
            network_dependencies=scan_result.remote_dependencies,
            fidelity=project.fidelity + scan_report.items,
            warnings=tuple(warnings),
            manifest_snapshot=project.snapshot(),
        )


def _network_dependencies(project: ExportProject) -> tuple[str, ...]:
    """Anything the finished map would fetch when opened.

    Empty is the goal and the default: no basemap, data inlined, telemetry off.
    A non-empty result contradicts the promise in the README and should be
    surfaced, not quietly tolerated.
    """
    remote: list[str] = []
    for layer in project.layers:
        for dependency in layer.dependencies:
            if dependency.disposition.value == "remote":
                remote.append(dependency.identifier)
    return tuple(remote)


def _escape_text(value: str) -> str:
    """Escape for HTML text content (the title appears in <title> and a footer)."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CAPTION_CORNER_CLASSES = {
    OverlayCorner.TOP_LEFT: "om-caption-top-left",
    OverlayCorner.TOP_CENTER: "om-caption-top-center",
    OverlayCorner.TOP_RIGHT: "om-caption-top-right",
    OverlayCorner.BOTTOM_LEFT: "om-caption-bottom-left",
    OverlayCorner.BOTTOM_CENTER: "om-caption-bottom-center",
    OverlayCorner.BOTTOM_RIGHT: "om-caption-bottom-right",
}


def _hex(color: Color) -> str:
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}"


def _caption_block(project: ExportProject) -> str:
    """The map's title and description, pinned to a corner of the viewport.

    **A plain block, not an `om-overlay`.** Every overlay anchor the runtime has
    is a *map* anchor - a coordinate, or the current selection - so a caption
    built from one would slide off the screen the moment the reader panned. A
    caption belongs to the viewport, so it is positioned the same way the credit
    component is.

    Both strings come from QGIS project metadata, so they are escaped rather
    than trusted as markup.
    """
    settings = project.settings
    parts: list[str] = []

    if settings.show_title and project.title:
        parts.append(
            f'      <div class="om-caption-title">{_escape_text(project.title)}</div>'
        )
    if settings.show_abstract and project.abstract:
        parts.append(
            '      <div class="om-caption-abstract">'
            f"{_escape_text(project.abstract)}</div>"
        )

    if not parts:
        return ""

    corner = CAPTION_CORNER_CLASSES[settings.title_corner]
    return "\n".join([f'    <div class="om-caption {corner}">', *parts, "    </div>"])


def _widget_color_block(project: ExportProject) -> str:
    """Widget colours as CSS custom properties.

    Custom properties are the runtime's own theming surface, and unlike ordinary
    rules they *inherit through shadow boundaries* - which is the only reason
    this reaches widgets that render into shadow roots. Empty when neither
    colour is set, so a default export is byte-identical to one built before
    this existed.
    """
    settings = project.settings
    declarations: list[str] = []

    if settings.widget_background is not None:
        background = _hex(settings.widget_background)
        declarations.append(f"        --om-widget-bg: {background};")
        declarations.append(f"        --om-widget-hover-bg: {background};")
    if settings.widget_foreground is not None:
        foreground = _hex(settings.widget_foreground)
        declarations.append(f"        --om-widget-fg: {foreground};")
        declarations.append(f"        --om-widget-muted: {foreground};")

    blocks: list[str] = []
    if declarations:
        blocks.append("\n".join(["      :root {", *declarations, "      }"]))

    scale_block = _chrome_scale_block(settings.chrome_scale)
    if scale_block:
        blocks.append(scale_block)

    return "\n\n".join(blocks)


# Which corner each widget is pinned to, so a scale transform grows it *into*
# the map rather than off the edge. Mirrors `WIDGET_POSITIONS` in the manifest
# builder; kept here because it is a CSS concern, not a manifest one.
_WIDGET_ORIGINS = {
    "legend": "top right",
    "layer-switcher": "top left",
    "zoom-controls": "bottom left",
    "scale-bar": "bottom left",
}


def _chrome_scale_block(scale: float) -> str:
    """Scale the map chrome with a transform, not a font size.

    **The runtime exposes no size property.** Its theming surface is six custom
    properties and all six are colours, so there is nothing to set.

    Setting `font-size` on the host was the obvious alternative and it is not
    enough. It does reach inside - inheritance crosses a shadow boundary where
    ordinary rules do not - and measuring it confirmed the text grew from 12px to
    24px at scale 2. But the controls did not: the zoom buttons stayed 30x60 and
    the scale bar stayed 81x23, because their internals are sized in pixels.
    Text alone got bigger while the things you click did not.

    `transform: scale()` scales the rendered box and everything in it, which is
    what "make the map controls bigger" has to mean. `transform-origin` is set
    per widget so each one grows into the map from the corner it is pinned to,
    instead of sliding off the edge.

    The credit component is deliberately absent. Attribution carries licence
    obligations, and a control that can shrink it towards illegibility is a
    control for quietly failing to attribute.
    """
    if abs(scale - 1.0) < 0.01:
        return ""

    rules: list[str] = []
    for widget, origin in _WIDGET_ORIGINS.items():
        rules.extend(
            [
                f'      om-widget[type="{widget}"] {{',
                f"        transform: scale({scale});",
                f"        transform-origin: {origin};",
                "      }",
            ]
        )

    # `map.html` stacks these two in the bottom-left corner with fixed offsets,
    # to clear the runtime's licence notice. Those offsets are in unscaled
    # pixels, so a scaled widget grows straight through its neighbour - measured
    # at scale 2, where the scale bar landed on top of the zoom controls. The
    # gaps have to scale with the things they are separating.
    rules.extend(
        [
            '      om-widget[type="zoom-controls"] {',
            f"        bottom: {round(58.0 * scale, 1)}px !important;",
            "      }",
            '      om-widget[type="scale-bar"] {',
            f"        bottom: {round(30.0 * scale, 1)}px !important;",
            "      }",
        ]
    )

    # The caption is our own element, so it scales by type rather than by
    # transform - sharper text, and it reflows instead of overflowing.
    rules.extend(
        [
            "      .om-caption-title {",
            f"        font-size: {round(15.0 * scale, 2)}px;",
            "      }",
            "      .om-caption-abstract {",
            f"        font-size: {round(12.0 * scale, 2)}px;",
            "      }",
        ]
    )
    return "\n".join(rules)


def _attribution_block(project: ExportProject) -> str:
    """The data-credit line for the artifact's credit component.

    Empty when no layer carries a credit -- an export of unattributed data
    should not grow a "Data: " label with nothing after it.

    These strings come from `QgsMapLayer` metadata, which is author-controlled
    text, so they are escaped rather than trusted as markup.
    """
    credits = collect_attributions(project)
    if not credits:
        return ""
    joined = "; ".join(_escape_text(credit) for credit in credits)
    return f'      <span class="om-credit-data">Data: {joined}</span>'
