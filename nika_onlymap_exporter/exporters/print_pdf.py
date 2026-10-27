"""Print PDF through QGIS (issue #5 S2, Path B): CMYK and ICC output.

A browser only ever prints RGB, so a print shop's CMYK or ICC-profiled PDF
comes from QGIS's own exporter, run on the layout the cartograph was made
from. QGIS 3.40 added a project colour model and colour space, which its PDF
export turns into CMYK colours and a PDF/X-4 output intent -- but only on
builds against Qt 6.8 or newer, and QGIS has no bleed or crop marks at all.
So this module:

- copies the source layout (`QgsLayout.clone`) and works only on the copy;
  the user's layout is never touched, even on failure;
- keeps page 1 only, like the cartograph itself (`core/layout_reader.py`
  exports page 1), so the print matches the sheet the user saw;
- grows the page by the bleed and moves every item by it, so items keep
  their place from the trim corner -- the runtime's own rule for `bleed`;
- draws the crop marks as eight `QgsLayoutItemPolyline` strokes in the
  bleed, with the runtime's geometry (`core.print_settings.crop_mark_segments`);
- applies the Cartograph tab's page title to the copy (labels reading
  `[% @project_title %]` and the PDF's own title), as the browser output does;
  the page attribution and theme have no QGIS equivalent and are reported;
- sets the project's colour model / colour space for the export and puts
  back exactly what was there afterwards (and the project's dirty flag),
  since those are project-wide settings that a print must not leave behind.

**GUI thread only.** Layout rendering paints map items, which must happen on
the thread that owns the layout's items; `export_print_pdf` is synchronous
and is meant to be called straight from a menu action.

The safe-zone guide is ignored here on purpose: the runtime never prints it,
and neither does this path.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsExpressionContextUtils,
    QgsLayout,
    QgsLayoutExporter,
    QgsLayoutItem,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPolyline,
    QgsLayoutMeasurement,
    QgsLayoutSize,
    QgsLineSymbol,
    QgsProject,
    QgsProjectStyleSettings,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_VERSION_STR, QPointF
from qgis.PyQt.QtGui import QPainter, QPolygonF

from ..core.atomic_write import staged_beside
from ..core.page_settings import attribution_in_layout
from ..core.print_settings import (
    PROFILE_CMYK,
    PROFILE_ICC,
    PrintSettings,
    crop_mark_segments,
    qt_supports_colour_management,
)
from ..core.settings import PageChoice
from ..ui.i18n import tr

#: Crop-mark stroke, matching the runtime's `stroke-width="0.15"` in mm.
CROP_MARK_WIDTH_MM = 0.15

#: Above everything the layout itself could plausibly stack.
_CROP_MARK_Z = 1_000_000


class PrintPdfError(Exception):
    """The print PDF could not be written; the message is user-facing."""


@dataclass(frozen=True)
class PrintPdfResult:
    """Where the PDF went, and anything the user should know about it."""

    path: Path
    warnings: tuple[str, ...] = field(default_factory=tuple)


def colour_management_support() -> tuple[bool, str]:
    """Whether this QGIS can write CMYK / ICC PDFs, and if not, why not.

    Two separate requirements: QGIS 3.40's colour-model API, and a Qt of
    6.8 or newer underneath it (QGIS compiles the PDF side out otherwise,
    while still exposing the API -- so the API alone proves nothing).
    """
    if not hasattr(QgsProjectStyleSettings, "setColorModel") or not hasattr(
        Qgis, "ColorModel"
    ):
        return False, tr("CMYK and ICC PDFs need QGIS 3.40 or newer.")
    if not qt_supports_colour_management(QT_VERSION_STR):
        return False, tr(
            "CMYK and ICC PDFs need QGIS built on Qt 6.8 or newer; this QGIS "
            "uses Qt {version}."
        ).format(version=QT_VERSION_STR)
    return True, ""


def export_print_pdf(
    layout: QgsLayout,
    settings: PrintSettings,
    path: Path,
    page: PageChoice | None = None,
) -> PrintPdfResult:
    """Write `layout` page 1 as a print PDF at `path`. GUI thread only.

    An RGB `settings.profile` is accepted too (bleed and marks with no
    colour management), which is what lets the geometry be tested on any
    build. Raises `PrintPdfError` with a user-facing message on failure.
    """
    project = layout.project()
    if project is None:
        raise PrintPdfError(tr("The layout does not belong to a project."))

    # Copying and exporting a layout both mark the project modified, and so
    # do the colour settings; a print is not an edit, so the flag goes back.
    was_dirty = project.isDirty()
    try:
        return _export(project, layout, settings, path, page)
    finally:
        project.setDirty(was_dirty)


def _export(
    project: QgsProject,
    layout: QgsLayout,
    settings: PrintSettings,
    path: Path,
    page: PageChoice | None,
) -> PrintPdfResult:
    warnings: list[str] = []
    colour_space = None
    if settings.uses_qgis_exporter:
        supported, reason = colour_management_support()
        if not supported:
            raise PrintPdfError(reason)
        if settings.profile == PROFILE_ICC:
            colour_space = _load_icc(Path(settings.icc_path))

    copy = build_print_layout(layout, settings, warnings, page)

    export_settings = QgsLayoutExporter.PdfExportSettings()
    export_settings.dpi = settings.dpi
    export_settings.appendGeoreference = False
    export_settings.writeGeoPdf = False
    export_settings.exportMetadata = True
    # Forcing vector output draws a semi-transparent or blended layer opaque,
    # so it is only forced when nothing in the layout needs rasterising;
    # otherwise QGIS rasterises just those items, at the saved dpi.
    rasterised = _needs_rasterising(copy)
    export_settings.forceVectorOutput = not settings.flatten and not rasterised
    export_settings.rasterizeWholeImage = settings.flatten
    if rasterised and not settings.flatten:
        warnings.append(
            tr(
                "Transparent or blended layers are rasterised in the PDF, "
                "at {dpi} dpi; the rest stays vector."
            ).format(dpi=settings.dpi)
        )
    # No fonts embedded means text as outlines: the print still looks right
    # on a RIP that lacks the font, and nothing is embedded to license.
    export_settings.textRenderFormat = (
        Qgis.TextRenderFormat.AlwaysText
        if settings.embed_fonts
        else Qgis.TextRenderFormat.AlwaysOutlines
    )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PrintPdfError(
            tr("Could not create the folder for {path}: {error}").format(
                path=path, error=exc
            )
        ) from exc

    title = page.title if page is not None else ""
    # Written beside the destination and moved over it only on success, so a
    # failed export never leaves a torn PDF where a previous one was.
    try:
        with (
            _ProjectTitle(project, title),
            _ProjectColour(project, settings.profile, colour_space, warnings),
            staged_beside(path) as staged,
        ):
            exporter = QgsLayoutExporter(copy)
            result = exporter.exportToPdf(str(staged), export_settings)
            if result != QgsLayoutExporter.ExportResult.Success:
                detail = (
                    exporter.errorMessage() if hasattr(exporter, "errorMessage") else ""
                )
                raise PrintPdfError(
                    tr("QGIS could not write {path}: {error}").format(
                        path=path, error=detail or _result_name(result)
                    )
                )
    except OSError as exc:
        # The final move onto `path` fails when `path` is a folder, say.
        raise PrintPdfError(
            tr("QGIS could not write {path}: {error}").format(
                path=path, error=exc.strerror or exc
            )
        ) from exc
    return PrintPdfResult(path=path, warnings=tuple(warnings))


def _needs_rasterising(layout: QgsLayout) -> bool:
    """True when some item or map layer draws with transparency, a blend
    mode or an effect, which a PDF cannot hold as plain vectors."""
    normal = QPainter.CompositionMode.CompositionMode_SourceOver
    for item in layout.items():
        if not isinstance(item, QgsLayoutItem):
            continue
        if (
            item.requiresRasterization()
            or item.containsAdvancedEffects()
            or item.itemOpacity() < 1
            or item.blendMode() != normal
        ):
            return True
        if isinstance(item, QgsLayoutItemMap):
            for layer in item.layersToRender():
                if layer.opacity() < 1 or layer.blendMode() != normal:
                    return True
                if isinstance(layer, QgsVectorLayer):
                    renderer = layer.renderer()
                    effect = renderer.paintEffect() if renderer is not None else None
                    if layer.featureBlendMode() != normal or (
                        effect is not None and effect.enabled()
                    ):
                        return True
    return False


def build_print_layout(
    layout: QgsLayout,
    settings: PrintSettings,
    warnings: list[str] | None = None,
    page_choice: PageChoice | None = None,
) -> QgsLayout:
    """A throwaway copy of `layout` laid out for print: page 1 only, the
    page grown by the bleed with every item moved in by it, crop marks in
    the bleed. `layout` itself is not modified. `page_choice` is the
    Cartograph tab's page fields, applied to the copy as far as QGIS can."""
    warnings = warnings if warnings is not None else []
    copy = layout.clone()
    if copy is None:
        raise PrintPdfError(tr("QGIS could not copy the layout for printing."))
    if page_choice is not None:
        _apply_page_choice(copy, page_choice, warnings)
    pages = copy.pageCollection()
    if pages.pageCount() == 0:
        raise PrintPdfError(tr("The layout has no pages."))
    if pages.pageCount() > 1:
        warnings.append(
            tr(
                "Only page 1 is printed, the page the cartograph shows; "
                "{count} other pages were left out."
            ).format(count=pages.pageCount() - 1)
        )
        # deletePage also removes the items on that page.
        for index in range(pages.pageCount() - 1, 0, -1):
            pages.deletePage(index)

    page = pages.page(0)
    converter = copy.renderContext().measurementConverter()
    current = converter.convert(page.pageSize(), QgsUnitTypes.LayoutMillimeters)
    trim_w, trim_h = settings.trim_size_mm((current.width(), current.height()))
    sheet_w, sheet_h = settings.sheet_size_mm((current.width(), current.height()))
    bleed_mm = settings.bleed_mm

    pages.beginPageSizeChange()
    page.setPageSize(QgsLayoutSize(sheet_w, sheet_h, QgsUnitTypes.LayoutMillimeters))
    pages.endPageSizeChange()
    pages.reflow()

    if bleed_mm > 0:
        offset = copy.convertToLayoutUnits(
            QgsLayoutMeasurement(bleed_mm, QgsUnitTypes.LayoutMillimeters)
        )
        for item in _movable_items(copy):
            item.attemptMoveBy(offset, offset)

    if settings.crop_marks_effective:
        _add_crop_marks(copy, trim_w, trim_h, bleed_mm)
    return copy


def attribution_warning() -> str:
    return tr(
        "The page attribution is not in the QGIS PDF; add it to the layout as a label."
    )


def _apply_page_choice(
    copy: QgsLayout, page_choice: PageChoice, warnings: list[str]
) -> None:
    """The page title as the layout's `@project_title` (a layout variable
    outranks the project's), and a warning when the attribution has no text
    item in the layout to carry it. The theme and a plain attribution are part
    of the layout itself, so the copy has them already."""
    if page_choice.title:
        QgsExpressionContextUtils.setLayoutVariable(
            copy, "project_title", page_choice.title
        )
    if page_choice.attribution and not attribution_in_layout(copy):
        warnings.append(attribution_warning())


def _movable_items(layout: QgsLayout) -> list[QgsLayoutItem]:
    """Every placed item, but not pages, and not a group's members: moving
    a group already moves them, so moving them again would double it."""
    return [
        item
        for item in layout.items()
        if isinstance(item, QgsLayoutItem)
        and not isinstance(item, QgsLayoutItemPage)
        and item.parentGroup() is None
    ]


def _add_crop_marks(
    layout: QgsLayout, trim_w: float, trim_h: float, bleed_mm: float
) -> None:
    to_layout = layout.convertToLayoutUnits
    for x1, y1, x2, y2 in crop_mark_segments(trim_w, trim_h, bleed_mm):
        points = [
            QPointF(
                to_layout(
                    QgsLayoutMeasurement(x + bleed_mm, QgsUnitTypes.LayoutMillimeters)
                ),
                to_layout(
                    QgsLayoutMeasurement(y + bleed_mm, QgsUnitTypes.LayoutMillimeters)
                ),
            )
            for x, y in ((x1, y1), (x2, y2))
        ]
        mark = QgsLayoutItemPolyline(QPolygonF(points), layout)
        mark.setSymbol(
            QgsLineSymbol.createSimple(
                {
                    "line_color": "0,0,0,255",
                    "line_width": str(CROP_MARK_WIDTH_MM),
                    "line_width_unit": "MM",
                    "capstyle": "flat",
                }
            )
        )
        mark.setId("crop-mark")
        mark.setZValue(_CROP_MARK_Z)
        layout.addLayoutItem(mark)


def _load_icc(path: Path) -> object:
    """The ICC file at `path` as a valid `QColorSpace`."""
    from qgis.PyQt.QtCore import QByteArray
    from qgis.PyQt.QtGui import QColorSpace

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PrintPdfError(
            tr("Could not read the ICC profile {path}: {error}").format(
                path=path, error=exc
            )
        ) from exc
    colour_space = QColorSpace.fromIccProfile(QByteArray(data))
    if not colour_space.isValid():
        raise PrintPdfError(
            tr(
                "{path} is not an ICC profile QGIS can use. Ask the print shop "
                "for an RGB or CMYK output profile (.icc)."
            ).format(path=path)
        )
    return colour_space


class _ProjectTitle:
    """Set the project's title for one export, then put it back: the PDF's
    own Title metadata is the project's, and a layout cannot override it."""

    def __init__(self, project: QgsProject, title: str) -> None:
        self.project = project
        self.title = title
        self.old_title = project.title()

    def __enter__(self) -> None:
        if self.title:
            self.project.setTitle(self.title)

    def __exit__(self, *_exc: object) -> None:
        if self.title:
            self.project.setTitle(self.old_title)


class _ProjectColour:
    """Set the project's colour model / space for one export, then restore.

    A context manager rather than inline try/finally so the restore is in
    one place and also runs when the export raises. `setColorSpace` attaches
    the profile to the project as a file and `setColorSpace(QColorSpace())`
    detaches it again.
    """

    def __init__(
        self,
        project: QgsProject,
        profile: str,
        colour_space: object,
        warnings: list[str],
    ) -> None:
        self.project = project
        self.profile = profile
        self.colour_space = colour_space
        self.warnings = warnings
        self.active = profile in (PROFILE_CMYK, PROFILE_ICC)

    def __enter__(self) -> None:
        if not self.active:
            return
        style = self.project.styleSettings()
        self.old_model = style.colorModel()
        self.old_space = style.colorSpace()
        if self.profile == PROFILE_ICC:
            # The colour space sets the model to match it (CMYK or RGB).
            style.setColorSpace(self.colour_space)
            return
        style.setColorModel(Qgis.ColorModel.Cmyk)
        if style.colorSpace().isValid():
            return  # the project's own CMYK profile becomes the output intent
        self.warnings.append(
            tr(
                "The PDF uses CMYK colours but is not PDF/X-4: there is no "
                "output profile. Choose Printer ICC profile..., or set a CMYK "
                "profile in Project Properties > Styles."
            )
        )

    def __exit__(self, *_exc: object) -> None:
        if not self.active:
            return
        style = self.project.styleSettings()
        from qgis.PyQt.QtGui import QColorSpace

        if self.old_space.isValid():
            style.setColorSpace(self.old_space)
        else:
            style.setColorSpace(QColorSpace())
        style.setColorModel(self.old_model)


def _result_name(result: object) -> str:
    name = getattr(result, "name", None)
    return str(name) if name else str(result)
