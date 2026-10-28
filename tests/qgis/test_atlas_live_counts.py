"""Live counts in atlas outputs: every page carries its own numbers.

The fixture has four sites and two atlas areas (A holds one site, B three), an
overview map that shows all four and a detail map that follows the atlas. What
is asserted is the text of the exported PDF, page by page, and that the user's
own layout is as it was.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("qgis.core")

from atlas_count_fixtures import build_atlas_counts
from pdf_text import pdf_pages_text

from nika_onlymap_exporter.core import atlas_live_counts
from nika_onlymap_exporter.core.print_settings import PrintSettings
from nika_onlymap_exporter.exporters.print_pdf import PrintPdfError, export_print_pdf


def _read(page_text: str, caption: str) -> int:
    found = re.search(rf"{caption}\s+(\S+)", page_text)
    assert found is not None, f"{caption!r} is not on the page:\n{page_text}"
    return int(found.group(1))


def _state(built) -> tuple:
    """What the user's layout looks like: map views, counter numbers, and the
    atlas page the preview shows."""
    atlas = built.layout.atlas()
    return (
        built.overview.extent().toString(6),
        built.detail.extent().toString(6),
        built.overview_total.text(),
        built.detail_total.text(),
        built.detail_kind_y.text(),
        atlas.currentFeatureNumber(),
    )


@pytest.fixture
def counts(project):
    return build_atlas_counts(project)


class TestAtlasPdfCountsPerPage:
    def test_each_pdf_page_shows_the_count_of_its_own_area(
        self, counts, tmp_path
    ) -> None:
        out = tmp_path / "atlas.pdf"
        export_print_pdf(counts.layout, PrintSettings(), out, atlas=True)

        pages = pdf_pages_text(out)

        assert len(pages) == 2  # area A, then area B
        area_a, area_b = pages
        # A fixed overview keeps counting everything; the detail map follows
        # the page; a counter of one kind follows the page too.
        assert [_read(p, "OVERVIEW") for p in pages] == [4, 4]
        assert [_read(p, "DETAIL") for p in pages] == [1, 3]
        assert [_read(area_a, "KINDY"), _read(area_b, "KINDY")] == [0, 2]

    def test_a_stale_number_on_the_layout_does_not_reach_the_pdf(
        self, counts, tmp_path
    ) -> None:
        # The layout as the user last left it shows the whole-map numbers.
        counts.detail_total.setText("4")
        out = tmp_path / "atlas.pdf"
        export_print_pdf(counts.layout, PrintSettings(), out, atlas=True)
        assert [_read(p, "DETAIL") for p in pdf_pages_text(out)] == [1, 3]

    def test_every_sheet_of_a_feature_carries_that_features_numbers(
        self, project, tmp_path
    ) -> None:
        built = build_atlas_counts(project, second_sheet=True)
        out = tmp_path / "atlas.pdf"
        export_print_pdf(built.layout, PrintSettings(), out, atlas=True)

        pages = pdf_pages_text(out)

        assert len(pages) == 4  # two sheets for each of the two areas, in order
        assert [_read(pages[0], "DETAIL"), _read(pages[2], "DETAIL")] == [1, 3]
        assert [_read(pages[1], "SECONDSHEET"), _read(pages[3], "SECONDSHEET")] == [
            1,
            3,
        ]

    def test_the_users_layout_and_preview_are_as_they_were(
        self, counts, tmp_path
    ) -> None:
        atlas = counts.layout.atlas()
        atlas.beginRender()
        atlas.seekTo(1)  # the preview is showing area B
        before = _state(counts)

        export_print_pdf(counts.layout, PrintSettings(), tmp_path / "a.pdf", atlas=True)

        assert _state(counts) == before
        assert atlas.currentFeatureNumber() == 1

    def test_a_failed_export_leaves_no_file_and_the_layout_untouched(
        self, counts, tmp_path, monkeypatch
    ) -> None:
        atlas = counts.layout.atlas()
        atlas.beginRender()
        atlas.seekTo(1)
        before = _state(counts)

        def fail(layout):
            raise RuntimeError("injected")

        monkeypatch.setattr(atlas_live_counts, "refresh_live_counts", fail)
        out = tmp_path / "atlas.pdf"
        with pytest.raises(PrintPdfError, match="Live counts"):
            export_print_pdf(counts.layout, PrintSettings(), out, atlas=True)

        assert not out.exists()  # no file claiming numbers that were not updated
        assert _state(counts) == before
