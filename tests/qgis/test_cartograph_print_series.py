"""Print series in the Cartograph dialog: the QGIS print path end to end.

The system print dialog is replaced by a stand-in that "chooses" a PDF file as
the printer, so the printed pages can be read back; no real print dialog or
printer is involved. The layout is the four-site / two-area one, in Image mode.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

pytest.importorskip("qgis.core")

from atlas_count_fixtures import build_atlas_counts
from pdf_text import pdf_pages_text
from test_cartograph_dialog import (
    fake_cartograph_export,  # noqa: F401 - fixture
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)
from test_cartograph_series import _Series

from nika_onlymap_exporter.exporters import print_native


@dataclass
class _Built:
    layout: object
    maps: list


@pytest.fixture
def printing(qgis_app, project, tmp_path, fake_cartograph_export, monkeypatch):  # noqa: F811
    counts = build_atlas_counts(project)
    series = _Series(
        qgis_app,
        project,
        tmp_path,
        _Built(counts.layout, [counts.overview, counts.detail]),
    )
    # Every map is an Image: printing through QGIS must not need Interactive.
    for item_id in (counts.overview.uuid(), counts.detail.uuid()):
        series.cartograph._frame_rows[item_id]["mode"].setCurrentIndex(
            series.cartograph._frame_rows[item_id]["mode"].findData("static")
        )
    series.settle()
    yield series, counts, tmp_path
    series.close()


def _printer_to(path, accept: bool = True, names=("Stand-in printer",)):
    """Replace the system print dialog and printer list for one test."""
    from qgis.PyQt.QtPrintSupport import QPrinter

    class _Dialog:
        def __init__(self, printer, _parent=None) -> None:
            self.printer = printer

        def setWindowTitle(self, _title: str) -> None:  # noqa: N802 - Qt name
            pass

        def exec(self) -> int:
            if not accept:
                return 0
            self.printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            self.printer.setOutputFileName(str(path))
            return 1

    return _Dialog, list(names)


def _read(page_text: str, caption: str) -> int:
    found = re.search(rf"{caption}\s+(\S+)", page_text)
    assert found is not None, f"{caption!r} is not on the page:\n{page_text}"
    return int(found.group(1))


def _use(monkeypatch, dialog, names) -> None:
    monkeypatch.setattr("qgis.PyQt.QtPrintSupport.QPrintDialog", dialog)
    monkeypatch.setattr(
        "qgis.PyQt.QtPrintSupport.QPrinterInfo.availablePrinterNames",
        staticmethod(lambda: names),
    )


class TestPrintSeriesButton:
    def test_it_is_ready_for_an_image_mode_layout(self, printing) -> None:
        series, _counts, _tmp = printing
        assert series.tab.print_button.isEnabled()
        assert (
            not series.tab.status().print.ok
        )  # web printing is the one needing Interactive
        assert series.tab.status().native_print.ok

    def test_the_whole_series_is_printed_in_order_with_each_pages_counts(
        self, printing, monkeypatch
    ) -> None:
        series, _counts, tmp = printing
        out = tmp / "printed.pdf"
        dialog, names = _printer_to(out)
        _use(monkeypatch, dialog, names)

        series.cartograph.on_print_series()

        pages = pdf_pages_text(out)
        assert len(pages) == 2
        assert [_read(p, "OVERVIEW") for p in pages] == [4, 4]
        assert [_read(p, "DETAIL") for p in pages] == [1, 3]
        status = series.dialog.status_label.text()
        assert "2 sheets" in status and "printed.pdf" in status
        assert not series.cartograph._pdf_running

    def test_cancelling_the_print_dialog_prints_nothing_and_says_so(
        self, printing, monkeypatch
    ) -> None:
        series, _counts, tmp = printing
        out = tmp / "printed.pdf"
        dialog, names = _printer_to(out, accept=False)
        _use(monkeypatch, dialog, names)

        series.cartograph.on_print_series()

        assert not out.exists()
        assert "Nothing was sent" in series.dialog.status_label.text()

    def test_a_failure_is_shown_and_nothing_is_reported_as_printed(
        self, printing, monkeypatch
    ) -> None:
        series, _counts, tmp = printing
        dialog, names = _printer_to(tmp / "printed.pdf")
        _use(monkeypatch, dialog, names)
        warned: list[str] = []
        monkeypatch.setattr(
            "qgis.PyQt.QtWidgets.QMessageBox.warning",
            staticmethod(lambda _p, _t, text: warned.append(text)),
        )

        def fail(*_args, **_kwargs):
            raise print_native.PrintSeriesError("the printer is on fire")

        monkeypatch.setattr(print_native, "print_series", fail)
        series.cartograph.on_print_series()

        status = series.dialog.status_label.text()
        assert "Could not print the series" in status and "on fire" in status
        assert warned == ["the printer is on fire"]
        assert "Sent" not in status

    def test_without_a_printer_the_series_becomes_a_pdf_and_says_so(
        self, printing, monkeypatch
    ) -> None:
        series, _counts, tmp = printing
        dialog, _names = _printer_to(tmp / "unused.pdf")
        _use(monkeypatch, dialog, [])
        target = tmp / "to-print.pdf"
        monkeypatch.setattr(
            "qgis.PyQt.QtWidgets.QFileDialog.getSaveFileName",
            staticmethod(lambda *_a, **_k: (str(target), "")),
        )

        series.cartograph.on_print_series()

        pages = pdf_pages_text(target)
        assert [_read(p, "DETAIL") for p in pages] == [1, 3]
        status = series.dialog.status_label.text()
        assert "nothing was printed" in status
        assert "Sent" not in status
