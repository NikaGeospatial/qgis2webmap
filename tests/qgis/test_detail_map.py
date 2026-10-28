"""Add item > Detail map: another real map frame, closer on an area of the
page's map. Driven through the page pane's Add menu and checked by what ends up
on the page: the new frame, what it draws, its ground, the export.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from test_cartograph_panel_widgets import _a4_layout, _settle
from test_locator_context import _region
from test_preview_item_editing import _choose, _items_of, _map

qgis_core = pytest.importorskip("qgis.core")
from qgis.core import (  # noqa: E402
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QRectF, QSize  # noqa: E402
from qgis.PyQt.QtWidgets import QMessageBox  # noqa: E402

from nika_onlymap_exporter.core.canvas_cartograph import (  # noqa: E402
    cartograph_from_view,
)
from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane  # noqa: E402

DETAIL = "Detail map"


def _near_far_pane(qgis_app, project, template_id):
    project.setTitle("Survey")
    parcels = build_parcels_layer()
    project.addMapLayer(parcels)
    region = _region(project, 3000)
    grown = QgsRectangle(parcels.extent())
    grown.scale(1.1)
    result = cartograph_from_view(
        grown,
        UTM,
        [parcels, region],
        project,
        template_id=template_id,
        page_size="A4",
        orientation="landscape",
    )
    pane = PagePane()
    pane.resize(1000, 760)
    pane.show()
    pane.show_layout(result.layout)
    pane.controls.resize(420, 640)
    pane.controls.show()
    _settle(qgis_app)
    return pane, result.layout


@pytest.fixture(params=["locator", "single"])
def page(request, qgis_app, project):
    """A shown pane on the Near & Far template, and on a layout whose only map
    frame is one single map."""
    if request.param == "locator":
        pane, layout = _near_far_pane(qgis_app, project, "locator")
    else:
        project.setTitle("Survey")
        parcels = build_parcels_layer()
        project.addMapLayer(parcels)
        layout = _a4_layout(project)
        frame = _map(layout, x=15, y=15, w=200, h=140, name="Map 1")
        frame.setCrs(UTM)
        frame.setLayers([parcels])
        frame.setKeepLayerSet(True)
        extent = QgsRectangle(parcels.extent())
        extent.scale(1.2)
        frame.zoomToExtent(extent)
        pane = PagePane()
        pane.resize(1000, 760)
        pane.show()
        pane.show_layout(layout)
        pane.controls.resize(420, 640)
        pane.controls.show()
        _settle(qgis_app)
    yield pane, layout
    _settle(qgis_app, 0.4)
    pane.close()
    pane.deleteLater()


def _detail_maps(layout):
    return [m for m in _items_of(layout, QgsLayoutItemMap) if m.id().startswith(DETAIL)]


def _add_detail(qgis_app, pane, layout):
    before = {m.uuid() for m in _items_of(layout, QgsLayoutItemMap)}
    _choose(pane, DETAIL)
    _settle(qgis_app, 0.5)
    new = [m for m in _items_of(layout, QgsLayoutItemMap) if m.uuid() not in before]
    assert len(new) == 1
    return new[0]


def _picture(layout, item, px=240):
    exporter = QgsLayoutExporter(layout)
    rect = item.sceneBoundingRect()
    return exporter.renderRegionToImage(
        QRectF(rect), QSize(px, round(px * rect.height() / rect.width())), 100
    )


def _colours(image) -> int:
    seen = set()
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            seen.add(image.pixel(x, y))
    return len(seen)


class TestAddingADetailMap:
    def test_the_entry_is_offered_and_enabled(self, page) -> None:
        pane, _layout = page
        pane._fill_add_menu()
        action = next(a for a in pane.add_menu.actions() if a.text() == DETAIL)
        assert action.isEnabled()

    def test_a_new_selected_map_frame_with_real_content(self, qgis_app, page) -> None:
        pane, layout = page
        new = _add_detail(qgis_app, pane, layout)
        assert layout.selectedLayoutItems() == [new]
        assert new.id() == DETAIL
        assert pane.controls.selected_item() is new
        assert _colours(_picture(layout, new)) > 3

    def test_it_shows_a_closer_part_of_the_source_inside_it(
        self, qgis_app, page
    ) -> None:
        pane, layout = page
        source_before = {
            m.uuid(): QgsRectangle(m.extent())
            for m in _items_of(layout, QgsLayoutItemMap)
        }
        new = _add_detail(qgis_app, pane, layout)
        ground = new.extent()
        source = max(
            (m for m in _items_of(layout, QgsLayoutItemMap) if m is not new),
            key=lambda m: m.rect().width(),
        )
        whole = source.extent()
        assert ground.width() < whole.width() and ground.height() < whole.height()
        assert whole.contains(ground)
        assert ground.width() / ground.height() == pytest.approx(
            new.rect().width() / new.rect().height(), rel=0.02
        )
        assert new.scale() < source.scale()
        for uuid, extent in source_before.items():
            item = layout.itemByUuid(uuid)
            assert item.extent() == extent

    def test_changing_its_ground_leaves_the_source_alone(self, qgis_app, page) -> None:
        pane, layout = page
        new = _add_detail(qgis_app, pane, layout)
        sources = [m for m in _items_of(layout, QgsLayoutItemMap) if m is not new]
        before = {m.uuid(): QgsRectangle(m.extent()) for m in sources}
        moved = QgsRectangle(new.extent())
        moved.scale(0.5)
        new.zoomToExtent(moved)
        _settle(qgis_app)
        assert new.extent().width() == pytest.approx(moved.width(), rel=0.02)
        for m in sources:
            assert m.extent() == before[m.uuid()]

    def test_the_frame_moves_and_resizes_like_any_map(self, qgis_app, page) -> None:
        pane, layout = page
        new = _add_detail(qgis_app, pane, layout)
        rect = new.sceneBoundingRect()
        new.attemptSetSceneRect(QRectF(rect.x() - 10, rect.y() - 5, 60, 45))
        _settle(qgis_app)
        assert new.sceneBoundingRect().width() == pytest.approx(60, abs=0.5)
        assert new.extent().width() > 0

    def test_a_second_one_has_its_own_name_and_the_menu_stays_usable(
        self, qgis_app, page
    ) -> None:
        pane, layout = page
        _add_detail(qgis_app, pane, layout)
        second = _add_detail(qgis_app, pane, layout)
        assert second.id() == f"{DETAIL} 2"

    def test_clearing_the_selection_returns_the_empty_inspector(
        self, qgis_app, page
    ) -> None:
        pane, layout = page
        _add_detail(qgis_app, pane, layout)
        layout.deselectAll()
        pane.controls.refresh()
        assert pane.controls.selected_item() is None
        assert pane.controls.heading.text().startswith("Select an item")

    def test_the_inspector_names_it_and_says_how_to_choose_the_area(
        self, qgis_app, page
    ) -> None:
        pane, layout = page
        _add_detail(qgis_app, pane, layout)
        controls = pane.controls
        assert controls.heading.text() == "Selected: Detail map"
        assert controls.edit_view_button.isVisibleTo(controls)
        assert controls.canvas_button.isVisibleTo(controls)
        assert controls.context_note.text().startswith(
            "Choose the area to show in this detail map."
        )

    def test_the_selected_map_is_the_source_when_one_is_selected(
        self, qgis_app, page
    ) -> None:
        pane, layout = page
        maps = [m for m in _items_of(layout, QgsLayoutItemMap)]
        smallest = min(maps, key=lambda m: m.rect().width())
        layout.deselectAll()
        smallest.setSelected(True)
        whole = QgsRectangle(smallest.extent())
        new = _add_detail(qgis_app, pane, layout)
        assert whole.contains(new.extent())


class TestWithoutAMap:
    def test_it_says_what_it_needs(self, qgis_app, project, monkeypatch) -> None:
        said: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            lambda *args, **kwargs: said.append(" ".join(map(str, args[1:]))),
        )
        layout = _a4_layout(project)
        pane = PagePane()
        pane.show()
        pane.show_layout(layout)
        _choose(pane, DETAIL)
        pane.close()
        assert said and "no map" in said[0]
        assert _detail_maps(layout) == []


class TestSavedAndExported:
    def test_it_survives_save_and_reopen(self, qgis_app, page, tmp_path) -> None:
        pane, layout = page
        new = _add_detail(qgis_app, pane, layout)
        extent = QgsRectangle(new.extent())
        path = tmp_path / "saved.qgz"
        project = QgsProject.instance()
        assert project.write(str(path))
        _settle(qgis_app, 0.3)
        project.clear()
        assert project.read(str(path))
        reopened = project.layoutManager().layouts()[0]
        again = [m for m in _items_of(reopened, QgsLayoutItemMap) if m.id() == DETAIL]
        assert len(again) == 1
        assert again[0].extent().width() == pytest.approx(extent.width(), rel=1e-6)
        assert again[0].extent().center().x() == pytest.approx(extent.center().x())
        pane.show_layout(None)

    def test_the_html_export_has_the_new_frame(self, qgis_app, page) -> None:
        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

        pane, layout = page
        runtime = RuntimeBundle(
            javascript=b"// js",
            css=b"/* css */",
            version="0.12.0",
            sha256="0" * 64,
            cartograph_javascript=b"// cartograph js",
            cartograph_css=b"/* cartograph css */",
        )
        count = len(_items_of(layout, QgsLayoutItemMap))

        def frames_in(html: str) -> int:
            return html.count("<om-map") + html.count("<om-static")

        base = frames_in(
            export_layout_to_cartograph(
                layout, QgsProject.instance(), runtime, FidelityReportBuilder()
            )
        )
        _add_detail(qgis_app, pane, layout)
        html = export_layout_to_cartograph(
            layout, QgsProject.instance(), runtime, FidelityReportBuilder()
        )
        assert count >= 1
        assert frames_in(html) == base + 1

    def test_the_pdf_export_draws_it(self, qgis_app, page, tmp_path) -> None:
        pane, layout = page
        new = _add_detail(qgis_app, pane, layout)
        target = tmp_path / "detail.pdf"
        exporter = QgsLayoutExporter(layout)
        result = exporter.exportToPdf(
            str(target), QgsLayoutExporter.PdfExportSettings()
        )
        assert result == QgsLayoutExporter.Success
        assert target.stat().st_size > 1000
        # What the PDF paints for the frame is what QGIS draws for it here.
        assert _colours(_picture(layout, new)) > 3
