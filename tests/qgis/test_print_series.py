"""Printing a series through QGIS: the whole series, in order, with each page's
own Live counts, on any printer Qt offers. The printer here writes a PDF file
(Qt's "Print to file"), so the printed pages are read back from that file;
no print dialog is opened.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("qgis.core")

from atlas_count_fixtures import build_atlas_counts
from pdf_text import pdf_pages_text
from qgis.core import QgsFeedback
from qgis.PyQt.QtPrintSupport import QPrinter

from nika_onlymap_exporter.core import atlas_live_counts
from nika_onlymap_exporter.core.atlas_series import SeriesState, series_status
from nika_onlymap_exporter.exporters.print_native import (
    PrintSeriesError,
    print_series,
)


def _printer(path) -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    return printer


def _read(page_text: str, caption: str) -> int:
    found = re.search(rf"{caption}\s+(\S+)", page_text)
    assert found is not None, f"{caption!r} is not on the page:\n{page_text}"
    return int(found.group(1))


def _state(built) -> tuple:
    return (
        built.detail.extent().toString(6),
        built.detail_total.text(),
        built.layout.atlas().currentFeatureNumber(),
    )


@pytest.fixture
def counts(project):
    return build_atlas_counts(project)


class TestPrintedSeries:
    def test_every_page_is_printed_in_order_with_its_own_counts(
        self, counts, tmp_path
    ) -> None:
        out = tmp_path / "printed.pdf"
        result = print_series(counts.layout, _printer(out))

        assert not result.cancelled and result.pages == 2
        pages = pdf_pages_text(out)
        assert len(pages) == 2
        assert [_read(p, "OVERVIEW") for p in pages] == [4, 4]
        assert [_read(p, "DETAIL") for p in pages] == [1, 3]
        assert [_read(p, "KINDY") for p in pages] == [0, 2]

    def test_every_sheet_of_every_feature_is_printed(self, project, tmp_path) -> None:
        built = build_atlas_counts(project, second_sheet=True)
        out = tmp_path / "printed.pdf"
        result = print_series(built.layout, _printer(out))

        pages = pdf_pages_text(out)
        assert result.sheets == len(pages) == 4
        assert [_read(pages[1], "SECONDSHEET"), _read(pages[3], "SECONDSHEET")] == [
            1,
            3,
        ]

    def test_the_users_layout_and_preview_are_as_they_were(
        self, counts, tmp_path
    ) -> None:
        atlas = counts.layout.atlas()
        atlas.beginRender()
        atlas.seekTo(1)
        before = _state(counts)

        print_series(counts.layout, _printer(tmp_path / "printed.pdf"))

        assert _state(counts) == before

    def test_a_cancel_stops_the_series_and_says_so(self, counts, tmp_path) -> None:
        feedback = QgsFeedback()
        seen: list[int] = []

        def progress(page: int, pages: int) -> None:
            seen.append(page)
            if page == 2:
                feedback.cancel()

        before = _state(counts)
        result = print_series(
            counts.layout,
            _printer(tmp_path / "printed.pdf"),
            feedback=feedback,
            progress=progress,
        )

        assert result.cancelled
        assert seen[0] == 1 and max(seen) == 2
        assert _state(counts) == before

    def test_progress_reports_each_page_before_it_is_printed(
        self, counts, tmp_path
    ) -> None:
        seen: list[tuple[int, int]] = []
        print_series(
            counts.layout,
            _printer(tmp_path / "printed.pdf"),
            progress=lambda page, pages: seen.append((page, pages)),
        )
        assert seen == [(1, 2), (2, 2)]

    def test_counts_that_cannot_be_updated_fail_visibly_and_stop(
        self, counts, tmp_path, monkeypatch
    ) -> None:
        before = _state(counts)

        def fail(layout):
            raise RuntimeError("injected")

        monkeypatch.setattr(atlas_live_counts, "refresh_live_counts", fail)
        with pytest.raises(PrintSeriesError, match="Live counts"):
            print_series(counts.layout, _printer(tmp_path / "printed.pdf"))
        assert _state(counts) == before

    def test_a_layout_without_a_series_is_refused(self, counts, tmp_path) -> None:
        counts.layout.atlas().setEnabled(False)
        with pytest.raises(PrintSeriesError, match="no atlas"):
            print_series(counts.layout, _printer(tmp_path / "printed.pdf"))


class TestPrintingIsReadyWithoutInteractiveMaps:
    def test_image_mode_maps_do_not_block_printing_through_qgis(self, counts) -> None:
        status = series_status(counts.layout, lambda _id: "static")
        assert status.state is SeriesState.READY
        assert status.native_print.ok  # the QGIS print needs no Interactive map
        assert status.pdf.ok
        # Each web-based output still has its own requirement.
        assert not status.print.ok and status.print.needs_interactive
        assert not status.html.ok and not status.images.ok

    def test_interactive_maps_make_every_print_option_ready(self, counts) -> None:
        status = series_status(counts.layout, lambda _id: "live")
        assert status.native_print.ok and status.print.ok

    def test_no_series_means_no_print_of_any_kind(self, counts) -> None:
        counts.layout.atlas().setEnabled(False)
        status = series_status(counts.layout, lambda _id: "static")
        assert not status.native_print.ok and not status.print.ok
