"""Print a layout's atlas series through QGIS, to any printer Qt knows.

`QgsLayoutExporter.print` draws the series itself, one page per atlas feature
(and every sheet of each), on a `QPrinter` -- a real printer, or "Print to
file (PDF)" in the print dialog. It needs no Interactive map: the pages look as
QGIS draws them. This module owns what surrounds that call:

- the layout is copied (`QgsLayout.clone`) and only the copy is paged, so the
  user's layout, the atlas page the preview shows and the map views stay as
  they were, on success, cancellation and failure alike;
- each page's Live counts are calculated on the copy as the atlas reaches the
  page, before it is drawn (`core.atlas_live_counts`);
- a series whose counts were not all updated is reported as a failure.

The print dialog itself, the progress and the status text belong to the
caller; this function takes the configured `QPrinter`. **GUI thread only.**

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field

from qgis.core import Qgis, QgsFeature, QgsFeedback, QgsLayout, QgsLayoutExporter
from qgis.PyQt.QtPrintSupport import QPrinter

from ..core.atlas_live_counts import AtlasPageCounts, follow_maps_in_copy
from ..translation import tr
from .print_pdf import PrintPdfError, _require_atlas, _result_name

#: Why a print could not start or finish; the message is user-facing.
PrintSeriesError = PrintPdfError


@dataclass(frozen=True)
class PrintSeriesResult:
    """What was sent to the printer."""

    cancelled: bool = False
    pages: int = 0
    sheets: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)


def print_series(
    layout: QgsLayout,
    printer: QPrinter,
    *,
    dpi: int = 300,
    embed_fonts: bool = True,
    feedback: QgsFeedback | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> PrintSeriesResult:
    """Print every page of `layout`'s atlas, in order, on `printer`.

    Raises `PrintSeriesError` with a user-facing message when there is no
    series, a page's counts could not be updated, or QGIS fails. A cancel
    through `feedback` returns a result with `cancelled` set.
    """
    project = layout.project()
    if project is None:
        raise PrintSeriesError(tr("The layout does not belong to a project."))
    # A copy and a print are not edits of the project, but making the copy
    # marks it modified. Its state is taken before the copy and put back once
    # the copy is ready, failed or not; nothing here runs the event loop, so
    # no edit of the user's can land in between. The print itself changes
    # only the copy and leaves the project's state alone, keeping any edit
    # made while it runs.
    was_dirty = project.isDirty()
    try:
        copy = layout.clone()
        if copy is None:
            raise PrintSeriesError(tr("QGIS could not copy the layout for printing."))
        follow_maps_in_copy(layout, copy)
        _require_atlas(copy)
    finally:
        if not was_dirty:
            project.setDirty(False)
    settings = QgsLayoutExporter.PrintExportSettings()
    settings.dpi = dpi
    # Text stays text (searchable, sharp at any size) unless the print
    # settings ask for outlines, as the PDF export does.
    copy.renderContext().setTextRenderFormat(
        Qgis.TextRenderFormat.AlwaysText
        if embed_fonts
        else Qgis.TextRenderFormat.AlwaysOutlines
    )
    feedback = feedback or QgsFeedback()
    atlas = copy.atlas()
    callback_errors: list[str] = []

    def on_page(feature: QgsFeature) -> None:
        if progress is None or not feature.isValid():
            return
        try:  # an exception inside a Qt slot would abort QGIS
            progress(atlas.currentFeatureNumber() + 1, atlas.count())
        except Exception as exc:
            callback_errors.append(str(exc))
            feedback.cancel()

    # Connected after the counts' own hook, so a page's counts are ready
    # by the time anything reports it.
    with AtlasPageCounts(copy, feedback) as page_counts:
        atlas.featureChanged.connect(on_page)
        try:
            result, detail = QgsLayoutExporter.print(atlas, printer, settings, feedback)
        finally:
            with contextlib.suppress(TypeError, RuntimeError):
                atlas.featureChanged.disconnect(on_page)
    pages = copy.atlas().count()
    sheets = pages * max(1, int(copy.pageCollection().pageCount()))
    if callback_errors:
        printer.abort()
        raise PrintSeriesError(
            tr("QGIS could not print the series: {error}").format(
                error=callback_errors[0]
            )
        )
    if page_counts.error:
        printer.abort()  # nothing of a series with wrong numbers is kept
        raise PrintSeriesError(page_counts.problem(pages))
    if result == QgsLayoutExporter.ExportResult.Canceled:
        printer.abort()
        return PrintSeriesResult(cancelled=True)
    if result != QgsLayoutExporter.ExportResult.Success:
        raise PrintSeriesError(
            tr("QGIS could not print the series: {error}").format(
                error=detail or _result_name(result)
            )
        )
    problem = page_counts.problem(pages)
    if problem:
        raise PrintSeriesError(problem)
    return PrintSeriesResult(
        pages=pages, sheets=sheets, warnings=page_counts.warnings()
    )
