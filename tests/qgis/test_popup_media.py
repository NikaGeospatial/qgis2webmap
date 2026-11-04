"""Popup links and pictures, decided from QGIS's own field configuration.

What a field becomes follows its editor widget, read the way QGIS 4.2 writes
it: an Attachment (External Resource) widget whose document viewer is Image
shows pictures, its storage setting says where a relative path is resolved,
and "display resource path as link" without "display full path" shows a link
by its file name. Local pictures are read from disk here, made smaller when
large, and carried; nothing else on disk is read.

Every case runs through the two real read paths - the single-file web map
(`read_project`) and a cartograph's live frame - like the editor-widget tests
in `test_attribute_representation.py`, so a frame's popups cannot drift from
a web map's.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph_full,
    prepare_cartograph,
    prepare_live_reads,
)
from nika_onlymap_exporter.core.export_ir import (
    ExportSettings,
    FidelityStatus,
    OutputMode,
    PopupAttachment,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.popup_media import MAX_PICTURE_EDGE
from nika_onlymap_exporter.core.project_reader import read_project
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

IMAGE_VIEWER = 1
RELATIVE_TO_PROJECT = 1
RELATIVE_TO_ROOT = 2


def _picture(path: Path, width: int, height: int, fmt: str = "JPEG") -> Path:
    from qgis.PyQt.QtGui import QColor, QImage

    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(30, 90, 200))
    assert image.save(str(path), fmt)
    return path


def _size_of(data: bytes) -> tuple[int, int]:
    from qgis.PyQt.QtGui import QImage

    image = QImage()
    assert image.loadFromData(data)
    return image.width(), image.height()


def _attachment(layer, field: str, **config: Any) -> None:
    base = {"FileWidget": True, "FileWidgetButton": True, "StorageMode": 0}
    layer.setEditorWidgetSetup(
        layer.fields().indexOf(field),
        qgis_core.QgsEditorWidgetSetup("ExternalResource", {**base, **config}),
    )


def _layer(project, rows: list[tuple[Any, ...]], fields: str) -> Any:
    layer = qgis_core.QgsVectorLayer(f"Point?crs=EPSG:3857&{fields}", "sites", "memory")
    assert layer.isValid()
    features = []
    for number, row in enumerate(rows):
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setAttributes(list(row))
        feature.setGeometry(
            qgis_core.QgsGeometry.fromPointXY(qgis_core.QgsPointXY(1000.0 * number, 0))
        )
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
    project.addMapLayer(layer)
    return layer


@pytest.fixture
def saved(project, tmp_path):
    """The project, saved in a folder of its own: relative paths need one."""
    folder = tmp_path / "project"
    folder.mkdir()
    project.setFileName(str(folder / "pictures.qgz"))
    return folder


@dataclass
class Exported:
    properties: list[dict[str, Any]]
    pictures: dict[str, bytes]
    report: FidelityReportBuilder
    markup: str
    attachments: dict[str, PopupAttachment | None]

    def rows(self, status: FidelityStatus) -> list[str]:
        return [
            item.detail
            for item in self.report.items
            if item.status is status and item.subject == "Popups of 'sites'"
        ]


@pytest.fixture
def fake_runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )


def _web_map(project) -> Exported:
    from nika_onlymap_exporter.core.manifest_builder import build_manifest

    report = FidelityReportBuilder()
    export = read_project(project, report)
    layer = next(c for c in export.exportable_layers if c.name == "sites")
    return Exported(
        [dict(f["properties"]) for f in layer.geojson["features"]],
        {p.name: p.data for p in layer.popup_pictures},
        report,
        build_manifest(export),
        {f.alias or f.name: f.attachment for f in layer.popup.fields},
    )


def _frame(project, fake_runtime) -> Exported:
    """The same layer as a cartograph's live frame carries it: pictures
    inside the page, as `data:` URIs."""
    from atlas_fixtures import add_map

    layer = project.mapLayersByName("sites")[0]
    layout = qgis_core.QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("popup-media")
    add_map(layout, [layer])
    report = FidelityReportBuilder()
    prepared = prepare_cartograph(layout, project, fake_runtime, report)
    prepared = prepare_live_reads(prepared, project, report)
    finished = finish_cartograph_full(prepared, project, report)
    frame = next(c for c in finished.projects[0].exportable_layers if c.name == "sites")
    properties = [dict(f["properties"]) for f in frame.geojson["features"]]
    pictures = {}
    for row in properties:
        for value in row.values():
            for encoded in re.findall(
                r'src="data:image/(?:jpeg|png);base64,([A-Za-z0-9+/=]+)"', str(value)
            ):
                assert encoded in finished.html
                pictures[encoded] = base64.b64decode(encoded)
    return Exported(
        properties,
        pictures,
        report,
        finished.html,
        {f.alias or f.name: f.attachment for f in frame.popup.fields},
    )


@pytest.fixture(params=["web map", "cartograph frame"])
def export(request, project, fake_runtime, qgis_app):
    def run() -> Exported:
        if request.param == "web map":
            return _web_map(project)
        return _frame(project, fake_runtime)

    return run


class TestReadFromTheWidget:
    def test_the_attachment_settings_are_read_as_qgis_wrote_them(
        self, project, saved, tmp_path
    ) -> None:
        layer = _layer(
            project,
            [("a", "b", "c", "d", "e")],
            "field=photo:string&field=rooted:string&field=absolute:string"
            "&field=doc:string&field=note:string",
        )
        _attachment(
            layer,
            "photo",
            DocumentViewer=IMAGE_VIEWER,
            RelativeStorage=RELATIVE_TO_PROJECT,
        )
        _attachment(
            layer,
            "rooted",
            DocumentViewer=IMAGE_VIEWER,
            RelativeStorage=RELATIVE_TO_ROOT,
            DefaultRoot=str(tmp_path / "root"),
        )
        _attachment(layer, "absolute", DocumentViewer=IMAGE_VIEWER, RelativeStorage=0)
        _attachment(layer, "doc", UseLink=True, FullUrl=False)

        attachments = _web_map(project).attachments

        assert attachments["photo"] == PopupAttachment(
            picture=True, base_dir=str(saved)
        )
        assert attachments["rooted"] == PopupAttachment(
            picture=True, base_dir=str(tmp_path / "root")
        )
        assert attachments["absolute"] == PopupAttachment(picture=True)
        assert attachments["doc"] == PopupAttachment(file_name_label=True)
        assert attachments["note"] is None


class TestPictures:
    @pytest.fixture
    def pictured(self, project, saved, tmp_path):
        """One small PNG beside the project, one large photo, one missing."""
        small = _picture(saved / "photos" / "small.png", 200, 100, "PNG")
        _picture(saved / "photos" / "large.jpg", 2400, 1600)
        layer = _layer(
            project,
            [("photos/small.png",), ("photos/large.jpg",), ("photos/gone.jpg",)],
            "field=photo:string",
        )
        _attachment(
            layer,
            "photo",
            DocumentViewer=IMAGE_VIEWER,
            RelativeStorage=RELATIVE_TO_PROJECT,
        )
        return small

    def test_a_small_picture_is_carried_as_it_is(self, pictured, export) -> None:
        out = export()
        assert pictured.read_bytes() in out.pictures.values()

    def test_a_large_photo_is_scaled_down_to_fit(self, pictured, export) -> None:
        out = export()
        sizes = sorted(_size_of(data) for data in out.pictures.values())
        assert sizes == [(200, 100), (MAX_PICTURE_EDGE, MAX_PICTURE_EDGE * 2 // 3)]

    def test_each_popup_shows_its_own_picture(self, pictured, export) -> None:
        out = export()
        small, large, gone = (p["photo_html"] for p in out.properties)
        assert small.startswith('<img class="om-popup-picture"')
        assert large.startswith('<img class="om-popup-picture"')
        assert small != large
        # Not found: the path, as text, as before pictures existed.
        assert gone == "photos/gone.jpg"

    def test_the_fidelity_tab_says_what_was_carried_reduced_and_missing(
        self, pictured, export
    ) -> None:
        out = export()
        [carried] = out.rows(FidelityStatus.PRESERVED)
        assert carried.startswith("2 pictures are carried in the map from 'photo'")
        [reduced] = out.rows(FidelityStatus.APPROXIMATED)
        assert reduced.startswith("1 picture was made smaller")
        [missing] = out.rows(FidelityStatus.UNSUPPORTED)
        assert "could not be found" in missing and "photos/gone.jpg" in missing

    def test_a_web_picture_is_left_live_and_said_to_need_a_connection(
        self, project, saved, export
    ) -> None:
        layer = _layer(project, [("https://example.org/a.jpg",)], "field=photo:string")
        _attachment(layer, "photo", DocumentViewer=IMAGE_VIEWER)
        out = export()
        assert out.pictures == {}
        assert 'src="https://example.org/a.jpg"' in out.properties[0]["photo_html"]
        [live] = out.rows(FidelityStatus.APPROXIMATED)
        assert "internet connection" in live


class TestNothingElseIsRead:
    """Only files named by features the map carries, in fields its popups show."""

    def test_a_filtered_out_feature_carries_no_picture(
        self, project, saved, export
    ) -> None:
        _picture(saved / "kept.jpg", 50, 50)
        _picture(saved / "filtered.jpg", 60, 60)
        layer = _layer(
            project,
            [("kept.jpg", 1), ("filtered.jpg", 2)],
            "field=photo:string&field=n:integer",
        )
        _attachment(
            layer,
            "photo",
            DocumentViewer=IMAGE_VIEWER,
            RelativeStorage=RELATIVE_TO_PROJECT,
        )
        layer.setSubsetString('"n" = 1')
        out = export()
        assert [_size_of(d) for d in out.pictures.values()] == [(50, 50)]

    def test_a_field_hidden_in_the_attribute_table_carries_no_picture(
        self, project, saved, export
    ) -> None:
        _picture(saved / "secret.jpg", 50, 50)
        layer = _layer(
            project, [("secret.jpg", "x")], "field=photo:string&field=note:string"
        )
        _attachment(
            layer,
            "photo",
            DocumentViewer=IMAGE_VIEWER,
            RelativeStorage=RELATIVE_TO_PROJECT,
        )
        table = layer.attributeTableConfig()
        columns = table.columns()
        for column in columns:
            if column.name == "photo":
                column.hidden = True
        table.setColumns(columns)
        layer.setAttributeTableConfig(table)
        out = export()
        assert out.pictures == {}
        assert "secret.jpg" not in out.markup

    def test_a_plain_text_path_is_left_as_text(self, project, saved, export) -> None:
        """Only the Attachment widget says a value is a picture."""
        _picture(saved / "plain.jpg", 50, 50)
        _layer(project, [("plain.jpg",)], "field=photo:string")
        out = export()
        assert out.pictures == {}
        assert "photo_html" not in out.properties[0]


class TestLinks:
    def test_an_address_is_a_link_and_an_attachment_link_shows_its_file_name(
        self, project, saved, export
    ) -> None:
        layer = _layer(
            project,
            [("https://example.org/a?b=1&c=2", "https://example.org/docs/report.pdf")],
            "field=site:string&field=doc:string",
        )
        _attachment(layer, "doc", UseLink=True, FullUrl=False)
        out = export()
        [properties] = out.properties
        assert properties["site_html"] == (
            '<a href="https://example.org/a?b=1&amp;c=2" target="_blank" '
            'rel="noopener noreferrer">https://example.org/a?b=1&amp;c=2</a>'
        )
        assert properties["doc_html"].endswith(">report.pdf</a>")
        [links] = out.rows(FidelityStatus.PRESERVED)
        assert "'doc', 'site'" in links and "internet connection" in links


class TestCarriedByTier:
    """The pictures travel the way each tier carries everything else."""

    @pytest.fixture
    def picture_project(self, project, saved):
        picture = _picture(saved / "photos" / "p.png", 40, 30, "PNG")
        layer = _layer(project, [("photos/p.png",)], "field=photo:string")
        _attachment(
            layer,
            "photo",
            DocumentViewer=IMAGE_VIEWER,
            RelativeStorage=RELATIVE_TO_PROJECT,
        )
        return picture

    def _build(self, project, mode, destination):
        from nika_onlymap_exporter.packaging.artifact_builder import build_artifact

        export = read_project(
            project, FidelityReportBuilder(), settings=ExportSettings(output_mode=mode)
        )
        return build_artifact(export, destination, mode=mode)

    def test_folder_writes_the_picture_beside_the_page(
        self, project, picture_project, tmp_path, runtime_required
    ) -> None:
        _result, outcome = self._build(project, OutputMode.FOLDER, tmp_path / "out")
        [name] = [p.name for p in outcome.path.iterdir() if p.name.startswith("popup-")]
        assert (outcome.path / name).read_bytes() == picture_project.read_bytes()
        assert f'src=\\"{name}\\"' in (outcome.path / "index.html").read_text()

    def test_share_zip_carries_it_in_the_archive(
        self, project, picture_project, tmp_path, runtime_required
    ) -> None:
        _result, outcome = self._build(
            project, OutputMode.SHARE_ZIP, tmp_path / "m.zip"
        )
        with zipfile.ZipFile(outcome.path) as archive:
            pictures = [n for n in archive.namelist() if "/popup-picture-" in n]
            assert len(pictures) == 1
            assert archive.read(pictures[0]) == picture_project.read_bytes()

    def test_a_single_file_carries_it_inside(
        self, project, picture_project, tmp_path, runtime_required
    ) -> None:
        result, outcome = self._build(
            project, OutputMode.STANDALONE_HTML, tmp_path / "m.html"
        )
        assert [f.path.name for f in result.files] == ["index.html"]
        encoded = base64.b64encode(picture_project.read_bytes()).decode("ascii")
        assert f"data:image/png;base64,{encoded}" in outcome.path.read_text()


class TestTheExportedProjectsOwnFolder:
    """A relative path is found beside the project being exported, which need
    not be the one open in QGIS: Processing exports the project it is given,
    and a cartograph the project its layout belongs to."""

    @staticmethod
    def _other(project, tmp_path):
        """A second project, saved in a folder of its own, beside the open one
        in another; each has a different `photos/site.png`.

        Made in the test, never held by a fixture: a second project that
        outlives the test into the fixtures' teardown crashes QGIS."""
        opened = tmp_path / "opened"
        opened.mkdir()
        project.setFileName(str(opened / "opened.qgz"))
        _picture(opened / "photos" / "site.png", 30, 20, "PNG")
        folder = tmp_path / "other"
        folder.mkdir()
        other = qgis_core.QgsProject()
        other.setFileName(str(folder / "other.qgz"))
        mine = _picture(folder / "photos" / "site.png", 50, 10, "PNG")
        layer = _layer(other, [("photos/site.png",)], "field=photo:string")
        _attachment(
            layer,
            "photo",
            DocumentViewer=IMAGE_VIEWER,
            RelativeStorage=RELATIVE_TO_PROJECT,
        )
        return other, mine

    @pytest.mark.parametrize("route", ["web map", "cartograph frame"])
    def test_its_picture_is_carried(
        self, project, tmp_path, route, fake_runtime
    ) -> None:
        from nika_onlymap_exporter.core import cartograph_export

        other, mine = self._other(project, tmp_path)
        try:
            out = _web_map(other) if route == "web map" else _frame(other, fake_runtime)
        finally:
            # The shared live-frame cache follows the project it read: let go
            # of it, or it outlives QGIS and crashes the process on exit.
            cartograph_export._DEFAULT_LIVE_CACHE.clear()
        assert list(out.pictures.values()) == [mine.read_bytes()]

    def test_processing_carries_its_picture(
        self, project, tmp_path, runtime_required
    ) -> None:
        from nika_onlymap_exporter.processing.export_project import (
            ExportProjectAlgorithm,
        )

        other, mine = self._other(project, tmp_path)
        context = qgis_core.QgsProcessingContext()
        context.setProject(other)
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()
        results = algorithm.processAlgorithm(
            {"OUTPUT": str(tmp_path / "map.html"), "MODE": 0},
            context,
            qgis_core.QgsProcessingFeedback(),
        )
        page = Path(results["OUTPUT"]).read_text(encoding="utf-8")
        opened = tmp_path / "opened" / "photos" / "site.png"
        assert base64.b64encode(mine.read_bytes()).decode("ascii") in page
        assert base64.b64encode(opened.read_bytes()).decode("ascii") not in page
