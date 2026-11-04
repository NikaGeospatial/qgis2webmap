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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.export_ir import (
    Color,
    ExportProject,
    ExportSettings,
    FidelityItem,
    FilterKind,
    OutputMode,
    OverlayCorner,
)
from ..core.fidelity_report import FidelityReportBuilder
from ..core.license_policy import CapVerdict, LicensePolicy, default_policy
from ..core.manifest_builder import (
    build_manifest,
    collect_attributions,
    needs_static_legend,
    switcher_basemaps,
    texture_credit,
)
from ..core.widget_colors import resolve_widget_colors
from ..packaging.asset_embedder import (
    build_bootstrap,
    build_data_inflater,
    gzip_base64,
    should_compress_data,
)
from ..packaging.dependency_scanner import ScanResult, scan
from ..packaging.popup_pictures import place_popup_pictures
from ..packaging.raster_cog import CancelCheck, ProgressCallback
from ..packaging.raster_embed import build_raster_embed, has_cog_layer
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


def generator_line(runtime: RuntimeBundle, when: datetime | None = None) -> str:
    """The provenance comment placed at the top of every artifact.

    Written for two readers. A person wants to know what made this and when; an
    AI assistant asked to modify the file wants to know which runtime version's
    attribute vocabulary applies.
    """
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Generated by QGIS2WebMap by NIKA {PLUGIN_VERSION} on {stamp}\n"
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
        embedded_rasters: tuple[tuple[str, bytes], ...] = (),
    ) -> str:
        """Fill the artifact template. No string-built HTML beyond the manifest."""
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        manifest = build_manifest(project, verdict, compress_data=compress_data)
        if any(
            layer.filter is not None and layer.filter.kind is FilterKind.TEXT
            for layer in project.exportable_layers
        ):
            manifest += "\n" + _NO_TICKS_HOOK

        if runtime_file:
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
        elif compress:
            # Gzipped base64 plus a bootstrap that inflates it. Roughly a third
            # of the raw size, and nobody edits minified runtime code anyway.
            script_body = build_bootstrap(gzip_base64(runtime.javascript))
        else:
            script_body = runtime.javascript.decode("utf-8")

        # Replacement, not format(): the template is full of CSS braces, and
        # str.format would choke on every one of them.
        replacements = {
            "@GENERATOR@": generator_line(runtime, when),
            "@TITLE@": _escape_text(project.title),
            "@MANIFEST@": manifest,
            "@ATTRIBUTION@": _attribution_block(project),
            "@CAPTION@": _caption_block(project),
            "@WIDGET_COLORS@": _widget_color_block(project),
            # Never compressed: the fallback's CSS gate must work without JS.
            "@RUNTIME_CSS@": runtime.css.decode("utf-8"),
            "@RUNTIME_JS@": script_body,
            "@PREVIEW_HOOK@": preview_hook,
            "@RASTER_EMBED@": build_raster_embed(
                embedded_rasters, cog_layer=has_cog_layer(manifest)
            ),
        }

        # One pass, not a loop of replaces. Substituting sequentially would let
        # earlier-inserted content be scanned again: a layer named
        # "@RUNTIME_JS@" would have five megabytes of runtime pasted into its
        # label. A single regex pass can only ever match the template's own
        # tokens.
        pattern = re.compile("|".join(re.escape(t) for t in replacements))
        return pattern.sub(lambda m: replacements[m.group(0)], template)

    def render_map_element(
        self,
        project: ExportProject,
        verdict: CapVerdict,
        compress_data: bool = False,
    ) -> str:
        """The `<om-map>` element alone, for a cartograph's live frame.

        The same `build_manifest` call `render_html` makes, so a live frame
        draws exactly what the exported web map draws - not a second renderer
        that could drift from it. Only the element: the cartograph page loads
        the runtime itself, lazily, through its own core loader, so a boot
        script here would load it a second time.

        With `compress_data`, layer data arrives as gzip blocks the host page
        must inflate before the runtime loads - the same order rule as
        `DATA_INFLATE_TEMPLATE`, applied wherever the host keeps the element.
        Rasters must already be embedded in `project`: an element living in
        somebody else's document has no directory to stage siblings into.
        """
        return build_manifest(project, verdict, compress_data=compress_data)

    def _stage_rasters(
        self,
        project: ExportProject,
        destination: Path,
        mode: OutputMode,
        report: FidelityReportBuilder,
        on_progress: ProgressCallback | None,
        should_cancel: CancelCheck | None,
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
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> ArtifactResult:
        """Write the artifact and describe what was produced.

        `destination` is a directory; the entry file is always `index.html` so a
        recipient never has to guess which file to open.

        `unbundle` writes the runtime beside the page instead of inside it. That
        is the whole point of the folder tier: a served map gets a small HTML
        file and a runtime the browser caches across every map on the site,
        rather than the same two megabytes re-downloaded each time. It is
        **only** valid over HTTP - the runtime loads as a module, and a module
        cannot be fetched from `file://` - so the other tiers stay inlined.

        Scanning happens first: an export that cannot work for its recipient
        should fail before anything is written, not after.

        `on_progress` and `should_cancel` exist for the raster pass and only for
        it. Everything else here is milliseconds; converting an orthophoto to a
        Cloud-Optimized GeoTIFF is the one part of an export that can run for
        minutes, so it is the one part with a progress line and a way out. They
        take the shape the rest of the plugin already uses -- see
        `raster_cog.ProgressCallback` -- so an existing progress widget can be
        handed straight in.
        """
        runtime = self.runtime_provider.load()
        verdict = self.license_policy.evaluate(project)

        scan_report = FidelityReportBuilder()
        scan_result: ScanResult = scan(project, scan_report, mode)
        if not scan_result.can_export:
            raise ExportBlockedError(scan_result.blocking_reasons)

        # Small maps keep readable data so a person or an agent can edit them.
        compress_data = compress and should_compress_data(scan_result.data_bytes)

        destination.mkdir(parents=True, exist_ok=True)
        written: list[ArtifactFile] = []

        # Rasters before the page, because the page has to name them. Staging
        # is what turns `RasterSpec.path` -- a file on this machine -- into a
        # `src` the recipient can fetch, and it is also the last point at which
        # a raster that cannot be carried can stop the export instead of
        # producing a map with a blank layer in it. `staging.project` carries
        # the filled-in specs; nothing downstream may use `project` again.
        staging = self._stage_rasters(
            project, destination, mode, scan_report, on_progress, should_cancel
        )
        if not staging.can_export:
            raise ExportBlockedError(staging.blocking_reasons)
        project = staging.project
        written.extend(
            ArtifactFile(path, path.stat().st_size) for path in staging.files
        )
        # Popup pictures beside the page or inside it, as the tier carries
        # everything else; the reader already chose them and said so.
        project, picture_files = place_popup_pictures(project, destination, mode)
        written.extend(
            ArtifactFile(path, path.stat().st_size) for path in picture_files
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
                staging.embedded,
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
    # A colour left on Default is resolved against its partner, so a black
    # background never ends up under the runtime's own dark text.
    colors = resolve_widget_colors(
        settings.widget_background, settings.widget_foreground
    )

    if colors.background is not None:
        background = _hex(colors.background)
        declarations.append(f"        --om-widget-bg: {background};")
        declarations.append(f"        --om-widget-hover-bg: {background};")
    if colors.foreground is not None:
        foreground = _hex(colors.foreground)
        declarations.append(f"        --om-widget-fg: {foreground};")
        declarations.append(f"        --om-widget-muted: {foreground};")

    blocks: list[str] = []
    if declarations:
        blocks.append("\n".join(["      :root {", *declarations, "      }"]))

    # Our own static legend has no type, so the scale's transform never
    # reaches it (`_WIDGET_ORIGINS`), and the caps it sits under are not
    # divided by the scale either.
    legend_scaled = not needs_static_legend(project)
    scale_block = _chrome_scale_block(settings.chrome_scale, settings, legend_scaled)
    if scale_block:
        blocks.append(scale_block)
        stack_block = _top_left_stack_block(project)
        if stack_block:
            blocks.append(stack_block)

    size_block = _panel_size_block(settings, legend_scaled)
    if size_block:
        blocks.append(size_block)

    return "\n\n".join(blocks)


# The two panels whose size can be set, by the selector that reaches each and
# the slot it is in. The legend is the runtime's widget or, when a swatch needs
# a picture, our own static one: an `om-widget` with no type, in its corner
# (`manifest_builder.build_legend_widget`). Matched on the widget rather than
# the slot, so the rule follows it into the drawer a narrow map folds it into.
_SIZED_PANELS = {
    "legend": (
        ('om-widget[type="legend"]', 'om-widget[position="top-right"]:not([type])'),
        "top-end",
    ),
    "layer-switcher": (('om-widget[type="layer-switcher"]',), "top-start"),
}


def _panel_size_block(settings: ExportSettings, legend_scaled: bool = True) -> str:
    """The legend's and layer switcher's set sizes, never bigger than the map.

    Empty when neither has one, so an Auto map is byte-identical to one built
    before sizes could be set - and Auto is the runtime's own sizing: as wide
    as the contents, as tall as them up to half the map (`map.html`).

    A set size lifts that half-the-map cap on its corner's slot to the whole
    map less the 12px inset at each edge, and the panel yields to the slot, so
    whatever was typed the page never scrolls; on a map narrower than the
    width, the width yields the same way. Both caps are divided by
    `chrome_scale`, because its transform grows the panel after layout -
    except for a legend it does not grow (`legend_scaled` off). A
    height the contents do not fill is the panel's own colour, which the host
    wears, rather than a transparent gap under it. Print is left alone, as the
    half-the-map cap is.
    """
    sizes = {
        "legend": (
            settings.show_legend,
            settings.legend_width,
            settings.legend_height,
        ),
        "layer-switcher": (
            settings.show_layer_switcher,
            settings.layer_switcher_width,
            settings.layer_switcher_height,
        ),
    }
    scale = settings.chrome_scale
    rules: list[str] = []
    for panel, (shown, width, height) in sizes.items():
        if not shown or (width is None and height is None):
            continue
        scaled = panel != "legend" or legend_scaled
        cap = (
            f"calc((100% - 24px) / {round(scale, 3)})"
            if scaled and abs(scale - 1.0) >= 0.01
            else "calc(100% - 24px)"
        )
        selectors, slot = _SIZED_PANELS[panel]
        slot_rules = []
        panel_rules = []
        if width is not None:
            slot_rules.append(f"          max-width: {cap};")
            panel_rules.extend(
                [f"          width: {width}px;", "          max-width: 100%;"]
            )
        if height is not None:
            slot_rules.append(f"          max-height: {cap};")
            panel_rules.extend(
                [
                    f"          height: {height}px;",
                    "          background: var(--om-widget-bg, #fff);",
                ]
            )
        rules.extend(
            [
                f'        [data-om-widget-slot="{slot}"] {{',
                *slot_rules,
                "        }",
                ",\n".join(f"        {selector}" for selector in selectors) + " {",
                *panel_rules,
                "        }",
            ]
        )
    if not rules:
        return ""
    return "\n".join(["      @media not print {", *rules, "      }"])


# A filter panel's tick boxes all cleared ask the runtime for no value at all,
# and OnlyMap 0.13.3 reads an empty list as no filter: every feature came back,
# and the panel lost the ticks it had. A value no feature has asks for none, as
# no category ticked does in QGIS, and the panel shows every box cleared. After
# the layers, so it finds them; a plain script, so file:// is the same.
_NO_TICKS_HOOK = """    <script>
      (function () {
        var none = JSON.stringify(["\\u0000"]);
        var layers = document.querySelectorAll("om-layer[filter-category]");
        layers.forEach(function (layer) {
          new MutationObserver(function () {
            if (layer.getAttribute("filter-categories") === "[]") {
              layer.setAttribute("filter-categories", none);
            }
          }).observe(layer, {
            attributes: true,
            attributeFilter: ["filter-categories"],
          });
        });
      })();
    </script>"""


# Which corner each widget is pinned to, so a scale transform grows it *into*
# the map rather than off the edge. Mirrors `WIDGET_POSITIONS` in the manifest
# builder; kept here because it is a CSS concern, not a manifest one.
_WIDGET_ORIGINS = {
    "legend": "top right",
    "layer-switcher": "top left",
    "basemap-switcher": "top left",
    "filter": "top left",
    "zoom-controls": "bottom left",
    "scale-bar": "bottom left",
    "measure": "bottom left",
}


def _chrome_scale_block(
    scale: float, settings: ExportSettings, legend_scaled: bool = True
) -> str:
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

    # The measure tool sits above those two in the same corner, and nothing
    # moves it out of their way: a transform leaves the layout as it was, so
    # the zoom controls, lifted and grown by the rules above, reached up into
    # its readout - measured at scale 2. It is lifted by their lift plus their
    # growth, so the gap between them scales like the others. The zoom
    # controls are two `--om-widget-size` buttons (30px unless the page raises
    # it for touch), and 8px is the runtime's gap between stacked widgets.
    lift = _measure_lift(scale, settings)
    if lift:
        rules.extend(
            [
                '      om-widget[type="measure"] {',
                f"        bottom: {lift} !important;",
                "      }",
            ]
        )

    # `map.html` caps each top corner at half the map, and the transform
    # grows the panel in it after layout: at 2x a crowded legend reached past
    # the map's foot by the corner's inset and the page scrolled. The cap is
    # divided by the scale, so the panel is at most half the map at any size.
    # Not the legend's corner when it holds our static legend, which the
    # transform does not grow: divided, it stopped at a quarter of the map.
    slots = ['[data-om-widget-slot="top-start"]']
    if legend_scaled:
        slots.append('[data-om-widget-slot="top-end"]')
    rules.extend(
        [
            "      @media not print {",
            ",\n".join(f"        {slot}" for slot in slots) + " {",
            f"          max-height: calc(50% / {round(scale, 3)});",
            "        }",
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


def _top_left_stack_block(project: ExportProject) -> str:
    """Scale the top-left corner as one, when it stacks more than one panel.

    A transform moves nothing out of the way, so each panel scaled on its own
    grew down over the one under it. The layer switcher was alone up there
    until the basemap switcher and the filter panels joined it; with company,
    the corner's slot is scaled from its corner instead, and the panels in it
    are not scaled again.
    A narrow map that folds them into a drawer takes them out of the slot,
    where each keeps its own scale. Empty for a corner with the switcher alone,
    which keeps the rules it always had.
    """
    if not switcher_basemaps(project.settings) and not any(
        layer.filter is not None for layer in project.exportable_layers
    ):
        return ""
    scale = project.settings.chrome_scale
    return "\n".join(
        [
            '      [data-om-widget-slot="top-start"] {',
            f"        transform: scale({scale});",
            "        transform-origin: top left;",
            "      }",
            '      [data-om-widget-slot="top-start"] > om-widget {',
            "        transform: none;",
            "      }",
        ]
    )


def _measure_lift(scale: float, settings: ExportSettings) -> str:
    """How far a scaled measure tool rises to clear what is stacked under it.

    Empty when it is not on the map, or when nothing is under it to grow into.
    """
    if not settings.show_measure:
        return ""
    growth = round(scale - 1.0, 3)
    if settings.show_zoom_controls:
        return (
            f"calc({round(58.0 * scale, 1)}px + {growth} * "
            "(var(--om-widget-size, 30px) * 2 + 8px))"
        )
    if settings.show_scale_bar:
        # The scale bar measured 27px tall, its padding included.
        return f"{round(30.0 * scale + growth * (27.0 + 8.0), 1)}px"
    return ""


def _attribution_block(project: ExportProject) -> str:
    """The data-credit line for the artifact's credit component.

    Empty when no layer carries a credit -- an export of unattributed data
    should not grow a "Data: " label with nothing after it.

    These strings come from `QgsMapLayer` metadata, which is author-controlled
    text, so they are escaped rather than trusted as markup.

    A relief map also owes its drape's provider a credit, and that one is ours
    rather than the author's: the imagery is chosen by `RASTER_TEXTURE_TEMPLATES`
    from the basemap, so nobody but this exporter is in a position to name it.
    It is appended to the same line rather than given its own, because the chip
    is already competing with the runtime's licence notice for the corner. Note
    the emptiness rule now turns on *both* credits - a map with no layer
    metadata still shows the imagery credit, where before an empty list was
    enough to skip the whole element.
    """
    segments: list[str] = []
    credits = collect_attributions(project)
    if credits:
        joined = "; ".join(_escape_text(credit) for credit in credits)
        segments.append(f"Data: {joined}")
    imagery = texture_credit(project.settings.terrain, project.settings.basemap)
    if imagery is not None:
        segments.append(_escape_text(imagery))
    if not segments:
        return ""
    return f'      <span class="om-credit-data">{" &middot; ".join(segments)}</span>'
