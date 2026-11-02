"""A print layout's atlas, read into an `AtlasSpec`: which frame it drives,
the pages it makes, and the QGIS state it borrows while counting them.

Split out of `core/layout_reader.py`, which reads the rest of the layout and
calls `_read_atlas` for this part.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

from qgis.core import (
    Qgis,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsLayoutItemMap,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsRectangle,
    QgsVectorLayer,
)

from .cartograph_ir import (
    ATLAS_ORDER_KEY,
    SMALLEST_ATLAS_MARGIN,
    AtlasSpec,
    CartographItem,
    TextSpec,
)
from .expression_tokens import atlas_id_filter, translate_atlas_pattern
from .fidelity_report import FidelityReportBuilder
from .frame_layers import rendered_layers
from .layout_reading import AtlasRequirement, MapFrameReading, _name

#: The Fidelity subject of every row about the layout's atlas, so the Atlas
#: tab can find why an atlas will not page.
ATLAS_SUBJECT = "Layout item 'Atlas'"


@dataclass(frozen=True)
class _AtlasPlan:
    frames: tuple[MapFrameReading, ...]
    spec: AtlasSpec | None = None
    requirement: AtlasRequirement | None = None


def _draws_layer(frame: MapFrameReading, layer_id: str) -> bool:
    return any(layer.id() == layer_id for layer in rendered_layers(frame.map_item))


def _without_atlas_fit(frames: Sequence[MapFrameReading]) -> list[MapFrameReading]:
    """`frames` with no atlas refit: with no `<om-atlas>` nothing would refit
    them, so the attributes go rather than leave the validator a dangling
    atlas-fit."""
    return [
        replace(mf, atlas_fit=None, atlas_margin=None, atlas_cameras_from_qgis=False)
        for mf in frames
    ]


def _read_atlas(
    layout: QgsPrintLayout,
    frames: Sequence[MapFrameReading],
    report: FidelityReportBuilder,
) -> _AtlasPlan:
    """The layout's atlas -> an `AtlasSpec`, and each atlas-driven frame's
    refit, per the runtime's three hard rules (`docs/cartograph.md`
    "Atlas"):

    - pages are turned by a **live** frame (`for=`) -- a static frame is a
      picture, with no features to page and a camera that cannot move;
    - the coverage layer must be **in that frame's map** with local rows
      (every vector layer the plugin exports is inline, so "in the map" is
      the condition left to check here; whether it actually survived the
      export is checked by `finish_cartograph` against `AtlasRequirement`);
    - the `filter` is the runtime's own expression language.

    When the first two cannot be met, nothing is written: an `<om-atlas>`
    that pages nothing would be worse than none. The reason and the remedy
    go in a Blocked row instead.
    """
    atlas = layout.atlas()
    if atlas is None or not atlas.enabled():
        return _AtlasPlan(frames=tuple(frames))
    coverage = atlas.coverageLayer()
    if coverage is None or not coverage.isValid():
        report.blocked(
            ATLAS_SUBJECT,
            "The atlas is turned on but has no coverage layer, so no atlas "
            "pages were exported. Choose a coverage layer in the layout's "
            "Atlas panel, or turn the atlas off.",
        )
        return _AtlasPlan(frames=tuple(frames))
    coverage_id = coverage.id()
    coverage_name = coverage.name()

    updated = list(frames)
    driven_live: list[MapFrameReading] = []
    driven_static: list[MapFrameReading] = []
    for index, mf in enumerate(frames):
        if not mf.map_item.atlasDriven():
            continue
        if mf.mode == "static":
            driven_static.append(mf)
            report.blocked(
                f"Map frame {_name(mf.map_item)}",
                "This map follows the atlas, but it is an image frame: its "
                "picture is rendered once, so it would show the same place on "
                "every page. Set the atlas map to Interactive in Map frames.",
                item_id=mf.map_item.uuid(),
            )
            continue
        fit, margin = _atlas_fit(mf.map_item, coverage, report)
        updated[index] = replace(
            mf,
            atlas_fit=fit,
            atlas_margin=margin,
            atlas_cameras_from_qgis=fit == "fixed",
        )
        driven_live.append(updated[index])

    driver = next(
        (mf for mf in driven_live if _draws_layer(mf, coverage_id)), None
    ) or next(
        (mf for mf in updated if mf.mode == "live" and _draws_layer(mf, coverage_id)),
        None,
    )
    if (
        driver is None
        and driven_live
        and layout.project().mapLayer(coverage_id) is not None
    ):
        # QGIS pages an atlas through a coverage layer its map does not draw,
        # and so does the cartograph: the layer travels with the atlas frame,
        # hidden, and the frame's own layer list is left alone.
        first = driven_live[0]
        position = next(
            i for i, mf in enumerate(updated) if mf.frame_id == first.frame_id
        )
        updated[position] = replace(
            first, hidden_layer_ids=(*first.hidden_layer_ids, coverage_id)
        )
        driver = updated[position]
        report.preserved(
            ATLAS_SUBJECT,
            f"The atlas still pages through '{coverage_name}'; it is just not "
            f"drawn on map {_name(first.map_item)}.",
        )
    if driver is None:
        if driven_live:
            remedy = (
                f"Add the coverage layer '{coverage_name}' to map "
                f"{_name(driven_live[0].map_item)}'s layers (tick \"Hide "
                'coverage layer" in the Atlas panel if it should not be '
                "drawn)."
            )
        elif driven_static:
            remedy = "Set the atlas map to Interactive in Map frames."
        else:
            remedy = (
                f"Show the coverage layer '{coverage_name}' in a map that is "
                "Interactive in Map frames."
            )
        report.blocked(
            ATLAS_SUBJECT,
            "No atlas pages were exported: the cartograph turns atlas pages "
            "with an interactive map that carries the coverage layer "
            f"'{coverage_name}', and this layout has none. {remedy}",
        )
        return _AtlasPlan(frames=tuple(_without_atlas_fit(updated)))

    pages = _AtlasPages.of(layout, atlas, coverage)
    if pages.blocked is not None:
        # An atlas whose filter or sort cannot be applied must not page
        # every feature instead: nothing is written, and the rows say why.
        report.blocked(ATLAS_SUBJECT, pages.blocked)
        return _AtlasPlan(frames=tuple(_without_atlas_fit(updated)))
    for detail in pages.preserved:
        report.preserved(ATLAS_SUBJECT, detail)

    if atlas.hideCoverage():
        for index, mf in enumerate(updated):
            if not _draws_layer(mf, coverage_id):
                continue
            if mf.mode == "live":
                updated[index] = replace(
                    mf, hidden_layer_ids=(*mf.hidden_layer_ids, coverage_id)
                )
            else:
                report.approximated(
                    f"Map frame {_name(mf.map_item)}",
                    f"QGIS hides the atlas coverage layer '{coverage_name}' on "
                    "atlas pages, but this image frame's picture is rendered "
                    "outside an atlas page, so it shows the layer.",
                    item_id=mf.map_item.uuid(),
                )
    limit_render = getattr(atlas, "limitCoverageLayerRenderToCurrentFeature", None)
    if limit_render is not None and limit_render():
        report.approximated(
            ATLAS_SUBJECT,
            "QGIS draws only the current feature of the coverage layer on each "
            "page; the cartograph draws all of them.",
        )

    spec = AtlasSpec(
        for_=driver.frame_id,
        layer=coverage_id,  # swapped for its <om-layer id> by read_layout
        filter=pages.filter,
        sort=ATLAS_ORDER_KEY if pages.order else None,
        filename=_atlas_filename(atlas, report),
        page_name=_atlas_page_name(atlas, coverage, report),
        filter_source=pages.filter_source,
        sort_source=pages.sort_source,
        sort_ascending=atlas.sortAscending(),
    )
    report.preserved(
        ATLAS_SUBJECT,
        f"One page per feature of '{coverage_name}', turned with the arrow "
        "keys or the on-sheet pager, and exported page by page.",
    )
    return _AtlasPlan(
        frames=tuple(updated),
        spec=spec,
        requirement=AtlasRequirement(
            frame_id=driver.frame_id,
            layer_id=coverage_id,
            layer_name=coverage_name,
            page_order=pages.order,
            page_ids=pages.ids,
        ),
    )


#: `QgsLayoutItemMap.AtlasScalingMode` values (stable across QGIS 3 and 4).
_ATLAS_PREDEFINED = 1
_ATLAS_AUTO = 2


def _atlas_fit(
    map_item: QgsLayoutItemMap,
    coverage: QgsVectorLayer,
    report: FidelityReportBuilder,
) -> tuple[str, float | None]:
    """An atlas-driven live map -> `atlas-fit`, `atlas-margin`.

    QGIS Auto over lines or polygons is the runtime's own `feature` fit. The
    margin is halved, not copied. Both are fractions, but QGIS scales the
    fitted extent by `1 + margin` about its centre (a 10% margin pads each
    side by 5% -- verified headlessly: a 1000 x 500 m feature at margin 0.10
    gives a 550 m tall map), whereas the runtime pads *each side* by
    `margin` (`padBounds`). So QGIS's `m` is the runtime's `m / 2`.

    QGIS Fixed scale keeps the scale and recentres, Predefined scales picks
    one of the project's scales, and over points even Auto keeps the scale
    and recentres; the runtime can do none of these. Those frames become
    `fixed`, which the runtime never moves, and the export pages QGIS's own
    atlas for where each page puts the map (`AtlasCamera`).
    """
    label = f"Map frame {_name(map_item)}"
    mode = int(map_item.atlasScalingMode())
    over_points = coverage.geometryType() == Qgis.GeometryType.Point
    if mode == _ATLAS_AUTO and not over_points:
        report.preserved(
            label,
            "Refits to each atlas feature, with QGIS's margin.",
            item_id=map_item.uuid(),
        )
        margin = max(map_item.atlasMargin() / 2.0, SMALLEST_ATLAS_MARGIN)
        return "feature", round(margin, 6)
    if mode == _ATLAS_PREDEFINED:
        detail = (
            "Shows each atlas feature at the predefined scale QGIS picks for "
            "it, centred as in QGIS."
        )
    else:
        detail = "Keeps the map's scale and centres each atlas feature, as QGIS does."
    report.preserved(label, detail, item_id=map_item.uuid())
    return "fixed", None


def _count_features(layer: QgsVectorLayer, expression: str | None) -> int:
    """Features of `layer` matching `expression` (all of them when `None`),
    respecting the layer's own subset filter -- what QGIS's atlas pages."""
    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setNoAttributes()
    if expression:
        request.setFilterExpression(expression)
        context = QgsExpressionContext()
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        request.setExpressionContext(context)
    return sum(1 for _ in layer.getFeatures(request))


def atlas_page_count(layout: QgsPrintLayout) -> int | None:
    """How many pages the layout's atlas has in QGIS, or `None` when the
    atlas is off, has no coverage layer or has a filter QGIS cannot evaluate.
    For the Atlas tab's page count.

    Counted without touching the atlas itself (`QgsLayoutAtlas.updateFeatures`
    would move the layout designer's atlas preview): the coverage layer's
    features, through its subset filter and the atlas filter when that is
    on. The cartograph can page fewer: the runtime also skips features the
    exported map does not draw (a switched-off category), per its "the map's
    own filtering applies" rule.
    """
    atlas = layout.atlas()
    if atlas is None or not atlas.enabled():
        return None
    coverage = atlas.coverageLayer()
    if coverage is None or not coverage.isValid():
        return None
    expression = atlas.filterExpression() if atlas.filterFeatures() else None
    if expression and expression_error(expression, coverage) is not None:
        # An expression QGIS cannot evaluate gives no count, not every feature.
        return None
    return _count_features(coverage, expression or None)


@contextlib.contextmanager
def layout_state_restored(layout: QgsPrintLayout) -> Iterator[None]:
    """Leave the layout as it was found, whatever the body does or raises.

    Paging the atlas moves *every* atlas-driven map -- including the ones
    that are not being read, such as a static frame's -- and can resize them
    to the new extent's aspect; it also leaves the atlas on its last page
    with the report context pointing at that feature. The authored maps,
    the atlas's preview page and the report context all go back.
    """
    atlas = layout.atlas()
    context = layout.reportContext()
    enabled = atlas.enabled()
    previewed = atlas.currentFeatureNumber()
    feature = QgsFeature(context.feature())
    context_layer = context.layer()
    coverage = atlas.coverageLayer()
    subset = coverage.subsetString() if coverage is not None else ""
    maps = [
        (map_item, _MapState.of(map_item))
        for map_item in layout.items()
        if isinstance(map_item, QgsLayoutItemMap)
    ]
    try:
        yield
    finally:
        if atlas.enabled() != enabled:
            atlas.setEnabled(enabled)
        # `seekTo` is what moves the atlas's page; -1 (no page yet) is
        # reached by asking for it, which QGIS answers with False.
        atlas.seekTo(previewed)
        context.setFeature(feature)
        context.setLayer(context_layer)
        if coverage is not None and coverage.subsetString() != subset:
            coverage.setSubsetString(subset)
        # Last: seeking a page moves the atlas's maps again.
        for map_item, state in maps:
            state.restore(map_item)


@dataclass(frozen=True)
class _MapState:
    """What paging an atlas can change about a map item."""

    extent: QgsRectangle
    size: QgsLayoutSize
    rotation: float
    atlas_driven: bool
    scaling_mode: QgsLayoutItemMap.AtlasScalingMode
    margin: float

    @classmethod
    def of(cls, map_item: QgsLayoutItemMap) -> _MapState:
        return cls(
            extent=QgsRectangle(map_item.extent()),
            size=QgsLayoutSize(map_item.sizeWithUnits()),
            rotation=map_item.mapRotation(),
            atlas_driven=map_item.atlasDriven(),
            scaling_mode=map_item.atlasScalingMode(),
            margin=map_item.atlasMargin(),
        )

    def restore(self, map_item: QgsLayoutItemMap) -> None:
        map_item.setAtlasDriven(self.atlas_driven)
        map_item.setAtlasScalingMode(self.scaling_mode)
        map_item.setAtlasMargin(self.margin)
        self.restore_view(map_item)

    def restore_view(self, map_item: QgsLayoutItemMap) -> None:
        """Only what the map shows (extent, size, rotation), not how the atlas
        drives it: for putting a map back after an atlas preview while the
        atlas settings may have been edited since."""
        map_item.setMapRotation(self.rotation)
        # `setExtent` resizes the item to the extent's aspect, so the size
        # goes back afterwards, and the extent is fitted to it once more.
        map_item.setExtent(self.extent)
        if map_item.sizeWithUnits() != self.size:
            map_item.attemptResize(self.size)
            map_item.zoomToExtent(self.extent)


@dataclass(frozen=True)
class _AtlasPages:
    """Which features QGIS's atlas pages, and in what order -- asked of QGIS
    itself, so the cartograph pages exactly those.

    The runtime's own filter and sort cannot be trusted to agree with QGIS's:
    a comparison with NULL is NULL in QGIS (so a filter drops the feature)
    but `true` or `false` in JavaScript, QGIS puts NULL first in an
    ascending sort where the runtime puts it last, and text sorts by
    different rules. So the filter is written as a test on the ids QGIS keeps
    and the order as each feature's place in QGIS's own (`ATLAS_ORDER_KEY`).
    """

    filter: str | None = None
    filter_source: str | None = None
    order: tuple[int, ...] = ()
    sort_source: str | None = None
    # The ids that get a page, in page order.
    ids: tuple[int, ...] = ()
    # Set when the filter or sort cannot be applied: why, and so why no atlas
    # is written. Paging every feature instead would be a different atlas.
    blocked: str | None = None
    preserved: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        layout: QgsPrintLayout,
        atlas,
        coverage: QgsVectorLayer,
    ) -> _AtlasPages:
        filter_expression = (
            atlas.filterExpression().strip() if atlas.filterFeatures() else ""
        )
        sort_expression = atlas.sortExpression().strip() if atlas.sortFeatures() else ""
        for expression, what in (
            (filter_expression, "filter"),
            (sort_expression, "sort"),
        ):
            error = expression_error(expression, coverage) if expression else None
            if error is not None:
                return cls(
                    blocked=(
                        f"No atlas pages were exported: the atlas {what} "
                        f"`{expression}` is not a valid QGIS expression "
                        f"({error}), and paging every feature of "
                        f"'{coverage.name()}' instead would not be the atlas "
                        f"you asked for. Correct the {what} in the Atlas "
                        "panel, or clear it."
                    )
                )
        every = _feature_ids(coverage)
        if not filter_expression and not sort_expression:
            return cls(ids=tuple(every))
        pages = _qgis_page_ids(layout)
        if pages is None or len(set(every)) != len(every):
            return cls(
                blocked=(
                    "No atlas pages were exported: the atlas filter or sort of "
                    f"'{coverage.name()}' could not be carried over (QGIS could "
                    "not page the layer, or its features have no ids of their "
                    "own to name them by), and paging every feature instead "
                    "would not be the atlas you asked for."
                )
            )
        filter_js: str | None = None
        preserved: list[str] = []
        if filter_expression:
            filter_js = atlas_id_filter(pages, every)
            preserved.append(
                f"Atlas filter `{filter_expression}` kept: {len(pages)} of "
                f"{len(every)} features get a page, as QGIS evaluates it."
            )
        if sort_expression:
            preserved.append(
                f"Pages are sorted by `{sort_expression}`, in the order QGIS puts them."
            )
        return cls(
            filter=filter_js,
            filter_source=filter_expression or None,
            order=tuple(pages) if sort_expression else (),
            sort_source=sort_expression or None,
            ids=tuple(pages),
            preserved=tuple(preserved),
        )


def expression_error(expression: str, layer: QgsVectorLayer | None) -> str | None:
    """Why QGIS cannot evaluate `expression` over `layer`'s features (a syntax
    error, an unknown field or function), or `None` when it can."""
    parsed = QgsExpression(expression)
    if parsed.hasParserError():
        return parsed.parserErrorString().strip() or "syntax error"
    context = QgsExpressionContext()
    if layer is not None:
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        context.setFields(layer.fields())
    if not parsed.prepare(context):
        return parsed.evalErrorString().strip() or "it cannot be evaluated"
    return None


def _feature_ids(layer: QgsVectorLayer) -> list[int]:
    """The ids of every feature of `layer`, through the layer's own subset
    filter."""
    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setNoAttributes()
    return [feature.id() for feature in layer.getFeatures(request)]


def _qgis_page_ids(layout: QgsPrintLayout) -> list[int] | None:
    """The ids of the features QGIS's own atlas makes pages of, in page
    order: the atlas is asked, not re-implemented, because its filter, its
    NULL handling and its text order are QGIS's. `None` if it cannot start."""
    atlas = layout.atlas()
    ids: list[int] = []
    with layout_state_restored(layout):
        if not atlas.beginRender():
            # An atlas with no pages at all does not begin either.
            return [] if atlas.count() == 0 else None
        try:
            for index in range(atlas.count()):
                if atlas.seekTo(index):
                    ids.append(int(layout.reportContext().feature().id()))
        finally:
            atlas.endRender()
    return ids


def _atlas_filename(atlas, report: FidelityReportBuilder) -> str | None:
    expression = atlas.filenameExpression().strip()
    if not expression:
        return None
    translated = translate_atlas_pattern(expression)
    if translated is None:
        report.approximated(
            ATLAS_SUBJECT,
            f"The page file names `{expression}` could not be translated; "
            "exported pages are named page-1, page-2, ... instead.",
        )
    return translated


def _atlas_page_name(
    atlas, coverage: QgsVectorLayer, report: FidelityReportBuilder
) -> str | None:
    expression = atlas.pageNameExpression().strip()
    if not expression:
        return None
    # The Atlas panel stores a plain field choice as the bare field name,
    # which may need quoting to read as an expression (spaces, keywords).
    if coverage.fields().indexOf(expression) >= 0:
        expression = '"' + expression.replace('"', '""') + '"'
    translated = translate_atlas_pattern(expression)
    if translated is None:
        report.approximated(
            ATLAS_SUBJECT,
            f"The page names `{atlas.pageNameExpression()}` could not be "
            "translated; the pager shows each page's number instead.",
        )
    return translated


_ATLAS_TOKEN_RE = re.compile(r"\{\{\s*atlas\.([A-Za-z0-9_]+)\s*\}\}")


def _atlas_fields_used(
    atlas: AtlasSpec, furniture: Sequence[CartographItem]
) -> tuple[str, ...]:
    """Every coverage-layer field the written atlas reads, in first-use
    order: `{{atlas.<field>}}` in the filename, page name and text. (The
    filter and sort read feature ids and `ATLAS_ORDER_KEY`, not fields.)"""
    names: list[str] = []
    texts = [atlas.filename or "", atlas.page_name or ""]
    texts += [item.body for item in furniture if isinstance(item, TextSpec)]
    for text in texts:
        names += _ATLAS_TOKEN_RE.findall(text)
    reserved = {"index", "count"}
    return tuple(name for name in dict.fromkeys(names) if name and name not in reserved)


def _reads_only_atlas(expression: QgsExpression) -> bool:
    """Whether `expression` reads atlas variables and nothing else: no
    fields, no other variables."""
    variables = {name.lower() for name in expression.referencedVariables()}
    return (
        bool(variables)
        and all(name.startswith("atlas_") for name in variables)
        and not expression.referencedColumns()
    )


def _reads_atlas_feature(expression: QgsExpression, layout: QgsPrintLayout) -> bool:
    """Whether a frozen expression would change from page to page in QGIS:
    it reads an `@atlas_*` variable or a field of the coverage layer."""
    variables = {name.lower() for name in expression.referencedVariables()}
    if any(name.startswith("atlas_") for name in variables):
        return True
    coverage = layout.atlas().coverageLayer()
    if coverage is None:
        return False
    fields = {field.name() for field in coverage.fields()}
    return bool(set(expression.referencedColumns()) & fields)
