"""Research sharing against real QGIS: the read path, the sender, the dialogs.

The pure rules are covered in `tests/unit/test_research*.py`. This tier proves
the parts only QGIS can: that a report built from a project read through the
real export path leaks no path, URL, value or long number; that the request the
sender builds carries no credentials; that 204 and 410 are handled through the
real session object; and that both dialogs build under the installed PyQt.

No test here reaches a network: the conftest replaces the default sender with
one that fails the test, and every test that sends passes its own.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")

SECRET_DIR = "alice-private-9876543210"
SECRET_VALUE = "SECRET-VALUE-4242"


def _gpkg_layer(tmp_path: Path):
    """A file-backed layer whose path, name and values are all things to hide."""
    from qgis.core import (
        QgsCoordinateTransformContext,
        QgsFeature,
        QgsGeometry,
        QgsPointXY,
        QgsVectorFileWriter,
        QgsVectorLayer,
    )

    memory = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=owner:string&field=surveyed:date"
        "&field=parcel_ref:string",
        "tmp",
        "memory",
    )
    feature = QgsFeature(memory.fields())
    feature.setAttributes([SECRET_VALUE, None, "PR-0012345678"])
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(103.8123, 1.3521)))
    memory.dataProvider().addFeatures([feature])

    directory = tmp_path / SECRET_DIR
    directory.mkdir()
    target = directory / "parcels.gpkg"
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    error = QgsVectorFileWriter.writeAsVectorFormatV3(
        memory, str(target), QgsCoordinateTransformContext(), options
    )
    assert error[0] == QgsVectorFileWriter.WriterError.NoError, error
    layer = QgsVectorLayer(str(target), str(target), "ogr")
    assert layer.isValid()
    return layer, target


@pytest.fixture
def read_export(qgis_app, project, tmp_path, make_memory_layer):
    """A project read through `read_project`, exactly as Export reads it."""
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.core.project_reader import read_project

    layer, target = _gpkg_layer(tmp_path)
    project.addMapLayer(layer)
    project.addMapLayer(
        make_memory_layer("Wells https://intranet.example/wells", features=[])
    )
    project.setTitle(f"Survey for bob@example.com see {target}")
    export = read_project(project, FidelityReportBuilder())
    return export, target


class TestMapReportFromARealRead:
    def test_nothing_identifying_leaks(self, read_export, project) -> None:
        from nika_onlymap_exporter.research.envelope import Environment
        from nika_onlymap_exporter.research.map_report import build_map_report
        from nika_onlymap_exporter.research.profile import Profile
        from nika_onlymap_exporter.ui.research_session import facts_for

        export, target = read_export
        facts = facts_for(project, export)
        report = build_map_report(
            export,
            Environment.build("0.1.5", "3.44", "linux"),
            Profile(),
            map_id="AbCdEfGhIjKlMnOpQrSt-_",
            utc_offset_hours=8,
            facts=facts,
        )
        body = json.dumps(report)

        assert SECRET_DIR not in body
        assert str(target.parent) not in body
        assert "alice" not in body
        assert SECRET_VALUE not in body
        assert "0012345678" not in body
        assert "bob@example.com" not in body
        assert "https://" not in body and "intranet" not in body
        # No run of 6+ digits anywhere, and no coordinate finer than a degree.
        assert not re.search(r"\d{6,}", body)
        assert "103.8" not in body and "1.35" not in body

    def test_what_it_does_say(self, read_export, project) -> None:
        from nika_onlymap_exporter.research.envelope import Environment
        from nika_onlymap_exporter.research.map_report import build_map_report
        from nika_onlymap_exporter.research.profile import Profile
        from nika_onlymap_exporter.ui.research_session import facts_for

        export, _ = read_export
        report = build_map_report(
            export,
            Environment.build("0.1.5", "3.44", "linux"),
            Profile(),
            map_id="AbCdEfGhIjKlMnOpQrSt-_",
            utc_offset_hours=8,
            facts=facts_for(project, export),
        )
        assert report["title"] == "Survey for [email] see parcels"
        by_name = {layer["name"]: layer for layer in report["layers"]}
        parcels = by_name["parcels"]
        assert parcels["format"] == "gpkg"
        assert parcels["source"] == "file"
        assert parcels["kind"] == "point"
        assert parcels["features"] == "1-100"
        assert parcels["has_time_field"] is True
        assert set(parcels["fields"]) >= {"owner", "surveyed", "parcel_ref"}
        assert "Wells" in by_name
        assert by_name["Wells"]["format"] == "memory"
        assert report["cell"] is not None
        assert report["crs"] is None or re.match(r"^[A-Z]+:\w+$", report["crs"])


class FakePoster:
    def __init__(self, statuses: list[int | None]) -> None:
        self.statuses = statuses
        self.sent: list[tuple[str, dict]] = []

    def __call__(self, url, body, done) -> None:
        self.sent.append((url, json.loads(body)))
        done(self.statuses.pop(0) if self.statuses else 204)


def _share_and_export(session, read_export, project) -> None:
    export, _ = read_export
    service = session.service()
    service.choose(True)
    session.record_output(export, "/project.qgz", project)


class TestSender:
    def test_nothing_is_sent_without_share(
        self, qgis_app, tmp_path, read_export, project
    ) -> None:
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        poster = FakePoster([])
        session = ResearchSession(path=tmp_path / "r.json", poster=poster)
        export, _ = read_export
        session.record_output(export, "/project.qgz", project)
        session.record_preview()
        session.flush()
        assert poster.sent == []
        assert session.service().state.queue == []

    def test_204_dequeues(self, qgis_app, tmp_path, read_export, project) -> None:
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        poster = FakePoster([204])
        session = ResearchSession(path=tmp_path / "r.json", poster=poster)
        _share_and_export(session, read_export, project)
        assert len(poster.sent) == 1
        url, body = poster.sent[0]
        assert url.endswith("/research/reports")
        assert body["kind"] == "map"
        assert session.service().state.queue == []

    def test_failure_keeps_it_for_next_time(
        self, qgis_app, tmp_path, read_export, project
    ) -> None:
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        poster = FakePoster([None])
        session = ResearchSession(path=tmp_path / "r.json", poster=poster)
        _share_and_export(session, read_export, project)
        assert len(session.service().state.queue) == 1
        session.flush()  # the next dialog open or export
        assert len(poster.sent) == 2
        assert session.service().state.queue == []

    def test_a_report_turned_away_too_often_stops_blocking_the_queue(
        self, qgis_app, tmp_path, read_export, project
    ) -> None:
        from nika_onlymap_exporter.research.service import MAX_REPORT_ATTEMPTS
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        # The first report is always answered 403; the one behind it would be
        # accepted, but is never tried while the first holds the head.
        poster = FakePoster([403] * MAX_REPORT_ATTEMPTS)
        path = tmp_path / "r.json"
        session = ResearchSession(path=path, poster=poster)
        _share_and_export(session, read_export, project)
        export, _ = read_export
        session.service().record_output(export, "/other.qgz")
        for _ in range(MAX_REPORT_ATTEMPTS - 2):
            session = ResearchSession(path=path, poster=poster)  # a later open
            session.flush()
        assert [body["map_id"] for _, body in poster.sent] == [
            poster.sent[0][1]["map_id"]
        ] * (MAX_REPORT_ATTEMPTS - 1)
        assert len(session.service().state.queue) == 2
        session = ResearchSession(path=path, poster=poster)
        session.flush()
        # The fifth 403 drops the first; the second goes straight after.
        assert len(poster.sent) == MAX_REPORT_ATTEMPTS + 1
        assert session.service().state.queue == []

    def test_410_ends_it_for_good(
        self, qgis_app, tmp_path, read_export, project
    ) -> None:
        from nika_onlymap_exporter.research import consent
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        poster = FakePoster([410])
        path = tmp_path / "r.json"
        session = ResearchSession(path=path, poster=poster)
        _share_and_export(session, read_export, project)
        assert session.service().consent == consent.ENDED
        assert session.service().state.queue == []
        export, _ = read_export
        session.record_output(export, "/other.qgz", project)
        session.flush()
        assert len(poster.sent) == 1
        reopened = ResearchSession(path=path, poster=poster)
        assert reopened.service().consent == consent.ENDED

    def test_a_broken_service_never_raises(self, qgis_app, tmp_path, project) -> None:
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        session = ResearchSession(path=tmp_path / "r.json", poster=FakePoster([]))
        service = session.service()
        service.choose(True)

        def explode(*_args, **_kwargs):
            raise OSError("disk full")

        service.record_output = explode  # type: ignore[method-assign]
        service.record_preview = explode  # type: ignore[method-assign]
        service.prepare_flush = explode  # type: ignore[method-assign]
        from nika_onlymap_exporter.core.export_ir import ExportProject

        session.record_output(ExportProject(title="x"), "/p.qgz", project)
        session.record_preview()
        session.flush()


class TestQuietFailures:
    """A live preview failing on every edit is one failure, not hundreds."""

    def _session(self, tmp_path):
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        session = ResearchSession(path=tmp_path / "r.json", poster=FakePoster([]))
        session.service().choose(True)
        return session

    @staticmethod
    def _failures(session) -> dict[str, int]:
        (week,) = session.service().state.weeks.values()
        return dict(week.failures)

    def test_once_per_class_and_project_per_session(self, qgis_app, tmp_path) -> None:
        session = self._session(tmp_path)
        for _ in range(20):
            session.record_quiet_failure("write_error", "/a.qgz")
            session.record_quiet_failure("read_error", "/a.qgz")
            session.record_quiet_failure("write_error", "/b.qgz")
        assert self._failures(session) == {"write_error": 2, "read_error": 1}
        session.start_preview_session()
        session.record_quiet_failure("write_error", "/a.qgz")
        assert self._failures(session)["write_error"] == 3

    def test_explicit_failures_still_count_every_time(self, qgis_app, tmp_path) -> None:
        session = self._session(tmp_path)
        session.record_quiet_failure("write_error", "/a.qgz")
        for _ in range(3):
            session.record_failure("write_error")
        assert self._failures(session) == {"write_error": 4}

    def test_a_failure_before_share_still_counts_once_after(
        self, qgis_app, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        session = ResearchSession(path=tmp_path / "r.json", poster=FakePoster([]))
        session.record_quiet_failure("write_error", "/a.qgz")
        session.service().choose(True)
        session.record_quiet_failure("write_error", "/a.qgz")
        session.record_quiet_failure("write_error", "/a.qgz")
        assert self._failures(session) == {"write_error": 1}


class TestRequest:
    def test_no_credentials_and_the_plugin_agent(self, qgis_app) -> None:
        from qgis.PyQt.QtNetwork import QNetworkRequest

        from nika_onlymap_exporter.ui.research_session import build_request

        request = build_request("https://api.example/research/reports")
        assert not request.hasRawHeader(b"Authorization")
        assert bytes(request.rawHeader(b"User-Agent")) == b"QGIS2WebMap-by-NIKA"
        manual = QNetworkRequest.LoadControl.Manual
        for attribute in (
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
            QNetworkRequest.Attribute.AuthenticationReuseAttribute,
        ):
            assert request.attribute(attribute) == manual

    def test_url_follows_the_hosting_api_base(self, monkeypatch) -> None:
        from nika_onlymap_exporter.ui import research_session

        monkeypatch.setenv("NIKA_API_BASE", "https://api-dev.example/")
        assert (
            research_session.research_url()
            == "https://api-dev.example/research/reports"
        )


class TestDialogs:
    def test_about_you_round_trip(self, qgis_app) -> None:
        from nika_onlymap_exporter.research.profile import Profile
        from nika_onlymap_exporter.ui.about_you_dialog import AboutYouDialog

        start = Profile.build("team", "forestry", ["monitoring", "other"], "carbon")
        dialog = AboutYouDialog(start)
        assert dialog.profile() == start
        assert not hasattr(dialog, "email_edit")
        dialog.audience_buttons["public"].setChecked(True)
        dialog.use_case_boxes["monitoring"].setChecked(False)
        assert dialog.profile().audience == "public"
        assert dialog.profile().use_cases == ("other",)
        dialog.deleteLater()

    def test_about_you_starts_empty(self, qgis_app) -> None:
        from nika_onlymap_exporter.research.profile import Profile
        from nika_onlymap_exporter.ui.about_you_dialog import AboutYouDialog

        dialog = AboutYouDialog()
        assert dialog.profile() == Profile()
        dialog.deleteLater()

    def test_share_and_dont_share_have_equal_weight(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.whats_new_dialog import ResearchChoiceDialog

        dialog = ResearchChoiceDialog(lambda: "")
        for button in (dialog.share_button, dialog.dont_share_button):
            assert not button.isDefault()
            assert not button.autoDefault()
        assert dialog.share_button.minimumWidth() == (
            dialog.dont_share_button.minimumWidth()
        )
        assert dialog.choice is None
        dialog.dont_share_button.click()
        assert dialog.choice is False
        dialog.deleteLater()

    def test_share_click(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.whats_new_dialog import ResearchChoiceDialog

        dialog = ResearchChoiceDialog(lambda: "")
        dialog.share_button.click()
        assert dialog.choice is True
        dialog.deleteLater()

    def test_whats_new_never_asks_and_scrolls_as_one(self, qgis_app) -> None:
        """The changelog is one view; the question lives in its own window."""
        from qgis.PyQt.QtWidgets import QTextBrowser

        from nika_onlymap_exporter.ui.whats_new_dialog import WhatsNewDialog

        dialog = WhatsNewDialog("0.1.5", ("- new", "- fixed"), choice_line="Off.")
        assert not hasattr(dialog, "share_button")
        assert len(dialog.findChildren(QTextBrowser)) == 1
        text = dialog.notes.toPlainText()
        assert text.index("new") < text.index("fixed")
        dialog.deleteLater()


class TestFirstOpen:
    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_order_and_once_per_version(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QDialog

        from nika_onlymap_exporter.research import consent
        from nika_onlymap_exporter.ui import main_dialog

        shown: list[str] = []

        def about_exec(self):
            shown.append("about")
            return QDialog.DialogCode.Rejected

        def whats_new_exec(self):
            shown.append("whats_new")
            return QDialog.DialogCode.Accepted

        def question_exec(self):
            shown.append("question")
            self.choice = True
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(main_dialog.AboutYouDialog, "exec", about_exec)
        monkeypatch.setattr(main_dialog.WhatsNewDialog, "exec", whats_new_exec)
        monkeypatch.setattr(main_dialog.ResearchChoiceDialog, "exec", question_exec)
        monkeypatch.setattr(
            main_dialog, "discover_runtime_dir", lambda: None, raising=True
        )
        monkeypatch.setattr(
            main_dialog.FetchingRuntime, "is_cached", lambda self: False
        )
        monkeypatch.setattr(
            main_dialog,
            "ensure_runtime",
            lambda parent=None: shown.append("runtime"),
        )
        sent: list[str] = []
        dialog = self._dialog(project, make_memory_layer)
        dialog._research._poster = lambda url, body, done: sent.append(url)

        dialog._run_first_open()
        assert shown == ["about", "runtime", "whats_new", "question"]
        service = dialog._research.service()
        assert service.consent == consent.SHARE
        assert service.state.profile_answered

        shown.clear()
        dialog._run_first_open()
        assert shown == []
        assert "Research sharing is on" in dialog.research_status.text()
        dialog.close()

    def test_quiet_preview_failures_count_once_until_the_session_restarts(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui.research_session import ResearchSession

        dialog = self._dialog(project, make_memory_layer)
        dialog._research = ResearchSession(
            dialog, path=tmp_path / "r.json", poster=FakePoster([])
        )
        service = dialog._research.service()
        service.choose(True)

        def failures() -> dict[str, int]:
            (week,) = service.state.weeks.values()
            return dict(week.failures)

        dialog._job_failure_class = "write_error"
        for _ in range(10):  # an hour of edits on a broken project
            dialog._on_job_failed_quietly("broken", "details")
        assert failures() == {"write_error": 1}
        dialog._on_project_switched()
        dialog._on_job_failed_quietly("broken", "details")
        assert failures() == {"write_error": 2}
        dialog._on_job_failed_quietly("broken", "details")
        assert failures() == {"write_error": 2}
        dialog.close()

    def test_runtime_prompt_skipped_when_installed(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QDialog

        from nika_onlymap_exporter.ui import main_dialog

        shown: list[str] = []
        monkeypatch.setattr(
            main_dialog.AboutYouDialog,
            "exec",
            lambda self: shown.append("about") or QDialog.DialogCode.Rejected,
        )
        monkeypatch.setattr(
            main_dialog.WhatsNewDialog,
            "exec",
            lambda self: shown.append("whats_new") or QDialog.DialogCode.Rejected,
        )
        monkeypatch.setattr(
            main_dialog.ResearchChoiceDialog,
            "exec",
            lambda self: shown.append("question") or QDialog.DialogCode.Rejected,
        )
        monkeypatch.setattr(main_dialog, "discover_runtime_dir", lambda: Path("/x"))
        monkeypatch.setattr(
            main_dialog, "ensure_runtime", lambda parent=None: shown.append("runtime")
        )
        dialog = self._dialog(project, make_memory_layer)
        dialog._run_first_open()
        assert shown == ["about", "whats_new", "question"]
        # Closed without choosing: research stays off.
        assert dialog._research.service().consent == "unset"
        dialog.close()
