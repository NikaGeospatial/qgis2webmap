"""Print PDF through QGIS (issue #5 Path B), against the real exporter.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from cartograph_fixtures import build_survey_layout
from print_fixtures import (
    TEST_PROFILE_NAME,
    count_anywhere,
    media_boxes_mm,
    write_cmyk_icc,
)
from qgis.core import (
    Qgis,
    QgsLayoutItemPage,
    QgsLayoutItemPolyline,
    QgsUnitTypes,
)

from nika_onlymap_exporter.core.print_settings import (
    PROFILE_CMYK,
    PROFILE_ICC,
    PrintSettings,
)
from nika_onlymap_exporter.core.settings import PageChoice
from nika_onlymap_exporter.exporters import print_pdf
from nika_onlymap_exporter.exporters.print_pdf import (
    PrintPdfError,
    build_print_layout,
    colour_management_support,
    export_print_pdf,
)

needs_colour_management = pytest.mark.skipif(
    not colour_management_support()[0],
    reason=f"this QGIS cannot write CMYK PDFs: {colour_management_support()[1]}",
)


@pytest.fixture
def survey(project, tmp_path):
    """The survey layout, with every item given an id: `QgsLayout.clone`
    gives the copy's items new uuids, so ids are what match them up."""
    built = build_survey_layout(project, tmp_path)
    for index, item in enumerate(_placed_items(built.layout).values()):
        item.setId(f"item-{index}")
    yield built
    # `QgsProject.clear()` does not reset the style settings, so a colour
    # space one test sets would otherwise leak into the next.
    from qgis.PyQt.QtGui import QColorSpace

    project.styleSettings().setColorSpace(QColorSpace())
    project.styleSettings().setColorModel(Qgis.ColorModel.Rgb)


def _snapshot(layout):
    """What must not change on the source layout: pages, items, positions."""
    pages = layout.pageCollection()
    converter = layout.renderContext().measurementConverter()
    page_sizes = []
    for index in range(pages.pageCount()):
        size = converter.convert(
            pages.page(index).pageSize(), QgsUnitTypes.LayoutMillimeters
        )
        page_sizes.append((round(size.width(), 3), round(size.height(), 3)))
    items = sorted(
        (item.id(), round(item.pos().x(), 3), round(item.pos().y(), 3))
        for item in layout.items()
        if hasattr(item, "uuid") and not isinstance(item, QgsLayoutItemPage)
    )
    return page_sizes, items


def _placed_items(layout):
    return {
        item.id() or item.uuid(): item
        for item in layout.items()
        if hasattr(item, "uuid")
        and not isinstance(item, (QgsLayoutItemPage, QgsLayoutItemPolyline))
    }


class TestPageGeometry:
    def test_a3_with_3mm_bleed_is_a_303_by_426_pdf(self, survey, tmp_path) -> None:
        before = _snapshot(survey.layout)
        out = tmp_path / "print.pdf"
        settings = PrintSettings(page="A3", orientation="portrait", bleed="3mm")

        result = export_print_pdf(survey.layout, settings, out)

        assert result.path == out
        boxes = media_boxes_mm(out.read_bytes())
        assert len(boxes) == 1  # page 1 only, like the cartograph
        width, height = boxes[0]
        assert width == pytest.approx(303.0, abs=0.5)
        assert height == pytest.approx(426.0, abs=0.5)
        assert _snapshot(survey.layout) == before
        assert any("page 1" in warning for warning in result.warnings)

    def test_items_move_in_by_the_bleed(self, survey) -> None:
        settings = PrintSettings(bleed="5mm")
        copy = build_print_layout(survey.layout, settings)

        page = copy.pageCollection().page(0)
        size = (
            copy.renderContext()
            .measurementConverter()
            .convert(page.pageSize(), QgsUnitTypes.LayoutMillimeters)
        )
        # From layout: A4 landscape (297 x 210) plus 5 mm each side.
        assert size.width() == pytest.approx(307.0)
        assert size.height() == pytest.approx(220.0)
        assert copy.pageCollection().pageCount() == 1

        source_items = _placed_items(survey.layout)
        copied = _placed_items(copy)
        page_one = {key for key, item in source_items.items() if item.page() == 0}
        assert page_one and set(copied) == page_one
        for key in page_one:
            source_pos = source_items[key].pagePos()
            copy_pos = copied[key].pagePos()
            assert copy_pos.x() - source_pos.x() == pytest.approx(5.0)
            assert copy_pos.y() - source_pos.y() == pytest.approx(5.0)

    def test_crop_marks_sit_in_the_bleed(self, survey) -> None:
        copy = build_print_layout(survey.layout, PrintSettings(bleed="3mm"))
        marks = [
            item for item in copy.items() if isinstance(item, QgsLayoutItemPolyline)
        ]
        assert len(marks) == 8
        trim = (3.0, 3.0, 300.0, 213.0)  # x0, y0, x1, y1 on the 303 x 216 sheet
        for mark in marks:
            rect = mark.sceneBoundingRect()
            centre = rect.center()
            inside_trim = (
                trim[0] < centre.x() < trim[2] and trim[1] < centre.y() < trim[3]
            )
            assert not inside_trim
            assert rect.left() >= -0.5 and rect.right() <= 303.5
            assert rect.top() >= -0.5 and rect.bottom() <= 216.5

    def test_no_bleed_means_no_marks_and_no_move(self, survey) -> None:
        settings = PrintSettings(bleed="none", crop_marks=True)
        copy = build_print_layout(survey.layout, settings)
        assert not [
            item for item in copy.items() if isinstance(item, QgsLayoutItemPolyline)
        ]
        source_items = _placed_items(survey.layout)
        for key, item in _placed_items(copy).items():
            assert item.pagePos() == source_items[key].pagePos()

    def test_crop_marks_off(self, survey) -> None:
        copy = build_print_layout(
            survey.layout, PrintSettings(bleed="3mm", crop_marks=False)
        )
        assert not [
            item for item in copy.items() if isinstance(item, QgsLayoutItemPolyline)
        ]


class TestColour:
    @needs_colour_management
    def test_cmyk_without_a_profile_writes_cmyk_and_says_it_is_not_pdfx(
        self, survey, tmp_path
    ) -> None:
        project = survey.project
        project.setDirty(False)
        out = tmp_path / "cmyk.pdf"

        result = export_print_pdf(
            survey.layout, PrintSettings(profile=PROFILE_CMYK, dpi=150), out
        )

        pdf = out.read_bytes()
        assert count_anywhere(pdf, b"/CSpcmyk cs") > 0
        assert b"/GTS_PDFX" not in pdf
        assert any("PDF/X-4" in warning for warning in result.warnings)
        style = project.styleSettings()
        assert style.colorModel() == Qgis.ColorModel.Rgb
        assert not style.colorSpace().isValid()
        assert project.isDirty() is False

    @needs_colour_management
    def test_icc_profile_becomes_the_pdfx4_output_intent(
        self, survey, tmp_path
    ) -> None:
        project = survey.project
        attached_before = list(project.attachedFiles())
        icc = write_cmyk_icc(tmp_path / "press.icc")
        out = tmp_path / "press.pdf"

        result = export_print_pdf(
            survey.layout,
            PrintSettings(profile=PROFILE_ICC, icc_path=str(icc), dpi=150),
            out,
        )

        pdf = out.read_bytes()
        assert pdf.startswith(b"%PDF-1.6")
        assert b"/OutputIntents" in pdf
        assert b"/GTS_PDFX" in pdf
        assert count_anywhere(pdf, TEST_PROFILE_NAME.encode()) > 0
        assert count_anywhere(pdf, b"/CSpcmyk cs") > 0
        assert not result.warnings or all("PDF/X-4" not in w for w in result.warnings)
        style = project.styleSettings()
        assert style.colorModel() == Qgis.ColorModel.Rgb
        assert not style.colorSpace().isValid()
        assert list(project.attachedFiles()) == attached_before

    @needs_colour_management
    def test_the_projects_own_cmyk_profile_is_used_and_kept(
        self, survey, tmp_path
    ) -> None:
        from print_fixtures import cmyk_icc_profile
        from qgis.PyQt.QtCore import QByteArray
        from qgis.PyQt.QtGui import QColorSpace

        style = survey.project.styleSettings()
        style.setColorSpace(QColorSpace.fromIccProfile(QByteArray(cmyk_icc_profile())))
        assert style.colorModel() == Qgis.ColorModel.Cmyk
        out = tmp_path / "own.pdf"

        result = export_print_pdf(
            survey.layout, PrintSettings(profile=PROFILE_CMYK, dpi=150), out
        )

        assert b"/GTS_PDFX" in out.read_bytes()
        assert not result.warnings or all("PDF/X-4" not in w for w in result.warnings)
        assert style.colorModel() == Qgis.ColorModel.Cmyk
        assert style.colorSpace().isValid()
        assert style.colorSpace().description() == TEST_PROFILE_NAME

    @needs_colour_management
    def test_project_colour_is_restored_when_the_export_fails(
        self, survey, tmp_path
    ) -> None:
        icc = write_cmyk_icc(tmp_path / "press.icc")
        blocked = tmp_path / "is-a-folder.pdf"
        blocked.mkdir()

        with pytest.raises(PrintPdfError):
            export_print_pdf(
                survey.layout,
                PrintSettings(profile=PROFILE_ICC, icc_path=str(icc)),
                blocked,
            )

        style = survey.project.styleSettings()
        assert style.colorModel() == Qgis.ColorModel.Rgb
        assert not style.colorSpace().isValid()

    def test_a_file_that_is_not_an_icc_profile_is_refused(
        self, survey, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(print_pdf, "colour_management_support", lambda: (True, ""))
        bogus = tmp_path / "not.icc"
        bogus.write_bytes(b"definitely not a profile")
        with pytest.raises(PrintPdfError, match="not an ICC profile"):
            export_print_pdf(
                survey.layout,
                PrintSettings(profile=PROFILE_ICC, icc_path=str(bogus)),
                tmp_path / "x.pdf",
            )
        with pytest.raises(PrintPdfError, match="Could not read"):
            export_print_pdf(
                survey.layout,
                PrintSettings(profile=PROFILE_ICC, icc_path=str(tmp_path / "gone.icc")),
                tmp_path / "x.pdf",
            )
        assert not (tmp_path / "x.pdf").exists()

    def test_an_old_qt_is_refused_with_the_reason(
        self, survey, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(print_pdf, "QT_VERSION_STR", "5.15.13")
        supported, reason = colour_management_support()
        assert not supported and "6.8" in reason and "5.15.13" in reason
        with pytest.raises(PrintPdfError, match=r"6\.8"):
            export_print_pdf(
                survey.layout, PrintSettings(profile=PROFILE_CMYK), tmp_path / "x.pdf"
            )
        assert not (tmp_path / "x.pdf").exists()


class TestPageFields:
    """The Cartograph tab's page fields reach the QGIS PDF like the browser's."""

    def test_the_page_title_is_the_copys_project_title(self, survey) -> None:
        copy = build_print_layout(
            survey.layout,
            PrintSettings(bleed="none"),
            page_choice=PageChoice(title="NEW TITLE"),
        )
        texts = [i.currentText() for i in copy.items() if hasattr(i, "currentText")]
        assert any(text.startswith("NEW TITLE --") for text in texts)
        # The user's own layout keeps reading the project's title.
        assert not survey.title_label.currentText().startswith("NEW TITLE")

    def test_the_pdf_title_follows_and_the_project_title_comes_back(
        self, survey, project, tmp_path
    ) -> None:
        project.setTitle("SOURCE TITLE")
        out = tmp_path / "titled.pdf"
        result = export_print_pdf(
            survey.layout,
            PrintSettings(bleed="none"),
            out,
            PageChoice(title="NEW TITLE", attribution="Credit", theme="dark"),
        )
        data = out.read_bytes()
        assert "NEW TITLE".encode("utf-16-be") in data or b"NEW TITLE" in data
        assert "SOURCE TITLE".encode("utf-16-be") not in data
        assert project.title() == "SOURCE TITLE"
        assert any("attribution" in warning for warning in result.warnings)
        assert any("theme" in warning for warning in result.warnings)

    def test_blank_page_fields_change_nothing(self, survey, project, tmp_path) -> None:
        project.setTitle("SOURCE TITLE")
        result = export_print_pdf(
            survey.layout,
            PrintSettings(bleed="none"),
            tmp_path / "plain.pdf",
            PageChoice(),
        )
        assert not any("attribution" in w or "theme" in w for w in result.warnings)
        assert b"SOURCE TITLE" in (tmp_path / "plain.pdf").read_bytes() or (
            "SOURCE TITLE".encode("utf-16-be") in (tmp_path / "plain.pdf").read_bytes()
        )
