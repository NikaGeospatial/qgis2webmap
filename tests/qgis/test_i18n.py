"""Languages: which one is shown, downloading packs, and the dialog in them.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Nothing here touches the network. Downloads are of the language packs zip the
release packaging step builds from the repository's own compiled packs, served
by a fake fetcher, or by a local server answering the way GitHub does through
QGIS's own network stack. What the zip may hold, and the hosts the download may
be sent to, are checked in tests/unit/test_language_download.py.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")

REPO_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = REPO_ROOT / "nika_onlymap_exporter" / "i18n"


@pytest.fixture
def portuguese(qgis_app):
    from nika_onlymap_exporter.ui.i18n import use_language

    assert use_language("pt_BR") == "pt_BR"
    yield
    use_language("en")


@pytest.fixture
def installed_plugin(monkeypatch, tmp_path):
    """A plugin as a user has it: no packs beside the code, an empty cache.

    The repository carries every compiled pack in `i18n/`, which is what
    lets a contributor see each language without downloading - so to test
    the download path, that folder is swapped for an empty one.
    """
    from nika_onlymap_exporter.ui import languages

    empty = tmp_path / "plugin-i18n"
    empty.mkdir()
    monkeypatch.setattr(languages, "I18N_DIR", empty)
    monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path / "cache" / "runtime"))
    return languages


@pytest.fixture(scope="module")
def release_zip(tmp_path_factory) -> bytes:
    """The language packs asset, as `scripts/package_plugin.py` builds it."""
    spec = importlib.util.spec_from_file_location(
        "_package_plugin_for_i18n", REPO_ROOT / "scripts" / "package_plugin.py"
    )
    assert spec is not None and spec.loader is not None
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    return packager.build_language_packs(tmp_path_factory.mktemp("dist")).read_bytes()


def serve_release(body: bytes, calls: list[str]):
    """A fetcher that answers the release asset's URL with `body`."""
    from nika_onlymap_exporter.ui import languages

    def fetch(url: str) -> languages.Reply:
        calls.append(url)
        assert url == languages.asset_url()
        return languages.Reply(200, body)

    return fetch


class TestWhichLanguage:
    def test_the_six_packs_and_english_are_offered(self, qgis_app):
        from nika_onlymap_exporter.ui import languages

        assert languages.available() == ["en", "pt_BR", "zh_CN", "hi", "es", "ar", "fr"]

    def test_a_qgis_locale_finds_its_pack_by_language(self, qgis_app):
        from nika_onlymap_exporter.ui.languages import match_locale

        assert match_locale("pt_BR") == "pt_BR"
        assert match_locale("pt_PT") == "pt_BR"
        assert match_locale("es_MX") == "es"
        assert match_locale("zh_CN") == "zh_CN"
        assert match_locale("en_US") is None
        assert match_locale("de_DE") is None
        assert match_locale("") is None

    def test_english_when_qgis_has_no_language_set(self, installed_plugin):
        assert installed_plugin.effective("auto", "") == "en"

    def test_following_qgis_needs_the_pack_installed(
        self, installed_plugin, release_zip
    ):
        assert installed_plugin.effective("auto", "pt_BR") == "en"
        installed_plugin.download("pt_BR", serve_release(release_zip, []))
        assert installed_plugin.effective("auto", "pt_BR") == "pt_BR"

    def test_english_chosen_beats_qgis(self, installed_plugin, release_zip):
        installed_plugin.download("pt_BR", serve_release(release_zip, []))
        assert installed_plugin.effective("en", "pt_BR") == "en"

    def test_a_chosen_language_whose_pack_is_gone_falls_back_to_english(
        self, installed_plugin
    ):
        assert installed_plugin.effective("es", "pt_BR") == "en"


class TestTheOffer:
    def test_offered_when_qgis_speaks_it_and_it_is_missing(self, installed_plugin):
        assert installed_plugin.offer("auto", "pt_BR") == "pt_BR"

    def test_not_offered_once_installed(self, installed_plugin, release_zip):
        installed_plugin.download("pt_BR", serve_release(release_zip, []))
        assert installed_plugin.offer("auto", "pt_BR") is None

    def test_not_offered_to_someone_who_chose_english(self, installed_plugin):
        assert installed_plugin.offer("en", "pt_BR") is None

    def test_not_offered_again_once_dismissed(self, installed_plugin):
        assert installed_plugin.offer("auto", "pt_BR", dismissed="pt_BR") is None

    def test_never_for_english_or_an_unknown_language(self, installed_plugin):
        assert installed_plugin.offer("auto", "en_GB") is None
        assert installed_plugin.offer("auto", "de_DE") is None


class GitHubLike:
    """A local server answering a release download the way GitHub does.

    The release URL redirects (302, absolute address) to a file host, which
    serves the asset. `tests/fixtures/github-release-asset-redirect.json` holds
    what GitHub answered. `redirect_to` may point the redirect at another
    server; `endless` makes the file host send far more than any release.
    """

    FILE_PATH = "/github-production-release-asset/1316831615/asset"
    ENDLESS = 64 * 1024 * 1024

    def __init__(self, body: bytes = b"", redirect_to: str | None = None) -> None:
        self.body = body
        self.endless = False
        self.hits: list[str] = []
        self.sent = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.hits.append(self.path)
                if self.path.startswith("/NikaGeospatial/qgis2webmap/releases/"):
                    target = redirect_to or server.origin
                    self.send_response(302)
                    self.send_header("Location", target + server.FILE_PATH + "?sig=x")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                size = server.ENDLESS if server.endless else len(server.body)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                try:
                    if not server.endless:
                        self.wfile.write(server.body)
                        server.sent = size
                        return
                    chunk = b"\0" * 65536
                    while server.sent < size:
                        self.wfile.write(chunk)
                        server.sent += len(chunk)
                except OSError:
                    pass  # the client hung up, which is the point

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def github_like(installed_plugin, monkeypatch, release_zip):
    """The real download code, pointed at a local GitHubLike server.

    Only the release address and the trusted origin are moved; the redirect
    loop, the verification and QGIS's network stack are the real ones.
    """
    server = GitHubLike(release_zip)
    monkeypatch.setattr(
        installed_plugin,
        "RELEASES_URL",
        f"{server.origin}/NikaGeospatial/qgis2webmap/releases/download",
    )
    monkeypatch.setattr(
        installed_plugin, "_trusted", lambda url: url.startswith(server.origin + "/")
    )
    yield server
    server.close()


class TestDownloading:
    def test_one_download_through_qgis_installs_every_language(
        self, qgis_app, installed_plugin, github_like
    ):
        path = installed_plugin.download("es", installed_plugin.qgis_fetcher(10_000))
        assert github_like.hits == [
            "/NikaGeospatial/qgis2webmap/releases/download/"
            f"v{installed_plugin.plugin_version()}/{installed_plugin.asset_name()}",
            GitHubLike.FILE_PATH + "?sig=x",
        ]
        assert installed_plugin.installed_pack("es") == path
        for code in installed_plugin.pinned_packs():
            assert installed_plugin.installed_pack(code) is not None, code

    def test_qgis_does_not_follow_a_redirect_by_itself(
        self, qgis_app, installed_plugin, monkeypatch, release_zip
    ):
        """The redirect goes through the trust check, so Qt must not take it."""
        elsewhere = GitHubLike(release_zip)
        redirecting = GitHubLike(release_zip, redirect_to=elsewhere.origin)
        monkeypatch.setattr(
            installed_plugin,
            "RELEASES_URL",
            f"{redirecting.origin}/NikaGeospatial/qgis2webmap/releases/download",
        )
        monkeypatch.setattr(
            installed_plugin,
            "_trusted",
            lambda url: url.startswith(redirecting.origin + "/"),
        )
        try:
            with pytest.raises(installed_plugin.LanguageDownloadError):
                installed_plugin.download("es", installed_plugin.qgis_fetcher(10_000))
            assert len(redirecting.hits) == 1
            assert elsewhere.hits == []
        finally:
            redirecting.close()
            elsewhere.close()
        assert installed_plugin.installed_pack("es") is None

    def test_an_endless_download_is_cut_off(
        self, qgis_app, installed_plugin, github_like, monkeypatch
    ):
        monkeypatch.setattr(installed_plugin, "MAX_DOWNLOAD_BYTES", 256 * 1024)
        github_like.endless = True
        with pytest.raises(installed_plugin.LanguageDownloadError, match="too large"):
            installed_plugin.download("es", installed_plugin.qgis_fetcher(10_000))
        assert github_like.sent < GitHubLike.ENDLESS
        assert installed_plugin.installed_pack("es") is None


class TestTranslating:
    def test_without_a_pack_the_text_is_english(self, qgis_app):
        from nika_onlymap_exporter.ui.i18n import plural, tr, use_language

        use_language("en")
        assert tr("Map") == "Map"
        assert plural(
            1, "{n} layer will be exported.", "{n} layers will be exported."
        ) == ("1 layer will be exported.")

    def test_portuguese_text_and_plurals(self, portuguese):
        from nika_onlymap_exporter.ui.i18n import plural, tr

        assert tr("Map") == "Mapa"
        assert plural(
            3, "{n} layer will be exported.", "{n} layers will be exported."
        ) == ("3 camadas serão exportadas.")

    @pytest.mark.parametrize(
        ("code", "map_tab"),
        [
            ("zh_CN", "地图"),
            ("hi", "मानचित्र"),
            ("es", "Mapa"),
            ("ar", "الخريطة"),
            ("fr", "Carte"),
        ],
    )
    def test_every_pack_loads(self, qgis_app, code, map_tab):
        from nika_onlymap_exporter.ui.i18n import tr, use_language

        try:
            assert use_language(code) == code
            assert tr("Map") == map_tab
        finally:
            use_language("en")

    def test_switching_back_to_english_removes_the_pack(self, portuguese):
        from nika_onlymap_exporter.ui.i18n import active_language, tr, use_language

        use_language("en")
        assert active_language() == "en"
        assert tr("Map") == "Map"


def _report_figure():
    from nika_onlymap_exporter.core.template_registry import TEMPLATES
    from nika_onlymap_exporter.core.translatable import MarkedText

    report = next(t for t in TEMPLATES if t.id == "report-figure")
    assert isinstance(report.name, MarkedText)
    return report


class TestOneTextTwoMeanings:
    """ "Caption" is the web map's box for a title over the map, and the
    template whose figure has a caption under it: two entries, told apart by
    Qt's disambiguation, looked up through a real compiled pack."""

    @pytest.mark.parametrize(
        ("code", "dialog_box", "template_name", "cue"),
        [
            ("fr", "En-tête", "Figure commentée", "commentaire"),
            ("es", "Encabezado", "Pie de figura", "pie de figura"),
            ("pt_BR", "Cabeçalho", "Figura comentada", "texto explicativo"),
            ("ar", "الترويسة", "شكل مع تعليق", "تعليق توضيحي"),
            ("hi", "शीर्ष भाग", "कैप्शन सहित आकृति", "कैप्शन"),
            ("zh_CN", "页眉", "图注", "图注"),
        ],
    )
    def test_each_meaning_finds_its_own_translation(
        self, qgis_app, code, dialog_box, template_name, cue
    ):
        from nika_onlymap_exporter.ui.i18n import N_, tr, use_language

        report = _report_figure()
        meaning = report.name.disambiguation
        try:
            assert use_language(code) == code
            assert tr("Caption") == dialog_box
            # Marked in core, translated later by the picker: the value
            # carries its disambiguation.
            assert tr(report.name) == template_name
            assert tr(N_("Caption", meaning)) == template_name
            assert tr("Caption", meaning) == template_name
            assert tr(report.includes[0]) == cue
        finally:
            use_language("en")

    def test_english_shows_the_source_for_both(self, qgis_app):
        from nika_onlymap_exporter.ui.i18n import tr, use_language

        use_language("en")
        assert tr("Caption") == "Caption"
        assert tr(_report_figure().name) == "Caption"

    def test_the_picker_names_the_card_in_the_template_s_sense(
        self, qgis_app, tmp_path
    ):
        """The card's name, its spoken name and the "Selected" line."""
        from qgis.PyQt.QtCore import QSettings, Qt
        from qgis.PyQt.QtGui import QColor, QImage

        from nika_onlymap_exporter.ui.cartograph.template_picker import (
            TemplatePicker,
        )
        from nika_onlymap_exporter.ui.i18n import tr, use_language

        def flat_page(
            template_id, page_size, orientation, width_px, figures=(), sizes=None
        ):
            image = QImage(
                width_px, round(width_px / 1.414), QImage.Format.Format_RGB32
            )
            image.fill(QColor("#336699"))
            return image

        settings = QSettings(str(tmp_path / "picker.ini"), QSettings.Format.IniFormat)
        assert use_language("fr") == "fr"
        picker = TemplatePicker(flat_page, qsettings=settings)
        try:
            row = picker.rows["report-figure"]
            assert row.name.text() == "Figure commentée"
            assert "commentaire" in row.includes.text() + row.supports.text()
            item = picker._items["report-figure"]
            spoken = item.data(Qt.ItemDataRole.AccessibleTextRole)
            assert spoken.startswith("Figure commentée")
            assert "En-tête" not in spoken
            picker.template_list.setCurrentItem(item)
            assert picker.selected_label.text() == tr("Selected: {name}").format(
                name="Figure commentée"
            )
        finally:
            picker.reject()
            use_language("en")


def _dialog(project, make_memory_layer):
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    class FakeIface:
        def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
            return None

    project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
    return MainDialog(FakeIface(), None)


class TestTheDialog:
    def test_it_opens_in_portuguese(self, portuguese, project, make_memory_layer):
        dialog = _dialog(project, make_memory_layer)
        try:
            titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
            assert titles == ["Mapa", "Camadas", "Aparência", "Fidelidade"]
            # Settings and Help are header icons: named for a screen reader.
            assert dialog.settings_button.accessibleName() == "Configurações"
            assert dialog.settings_button.toolTip() == "Configurações"
            assert dialog.help_button.accessibleName() == "Ajuda"
            # Found by the page, not the title, so it still works translated.
            dialog._show_tab("Settings")
            assert dialog.aside_heading.text() == "Configurações"
            dialog._show_tab("Help")
            assert dialog.aside_heading.text() == "Ajuda"
            dialog._show_fidelity_tab()
            assert dialog.tabs.currentWidget() is dialog._fidelity_page
            assert dialog.language_picker.currentData() == "auto"
        finally:
            dialog.close()

    def test_text_built_after_the_tabs_is_translated_too(
        self, portuguese, project, make_memory_layer
    ) -> None:
        """The layer rows, the sign-in chip and the problems warning were
        written as plain or f-strings, which the catalogue never saw."""
        from qgis.PyQt.QtWidgets import QComboBox

        from nika_onlymap_exporter.core.fidelity_report import (
            FidelityItem,
            FidelityStatus,
        )
        from nika_onlymap_exporter.ui.fidelity_panel import problems_box

        dialog = _dialog(project, make_memory_layer)
        try:
            texts = {
                combo.itemText(i)
                for combo in dialog.layer_tree.findChildren(QComboBox)
                for i in range(combo.count())
            }
            assert "Pop-ups: ao passar o mouse" in texts
            assert "Precisão: 3 casas decimais" in texts
            assert dialog.account_button.text() == "Entrar na NIKA"
        finally:
            dialog.close()

        item = FidelityItem("Camada 'x'", FidelityStatus.BLOCKED, "Ilegível.", "x")
        box, choices = problems_box(None, [item, item], "Export")
        assert box.text().startswith("2 itens")
        assert "Exportar mesmo assim" in {button.text() for button in choices}

    def test_arabic_is_laid_out_right_to_left(
        self, qgis_app, project, make_memory_layer
    ):
        from qgis.PyQt.QtCore import Qt

        from nika_onlymap_exporter.ui.i18n import use_language

        use_language("ar")
        dialog = _dialog(project, make_memory_layer)
        try:
            assert dialog.layoutDirection() == Qt.LayoutDirection.RightToLeft
        finally:
            dialog.close()
            use_language("en")

    def test_the_list_marks_what_needs_downloading(
        self, installed_plugin, project, make_memory_layer
    ):
        dialog = _dialog(project, make_memory_layer)
        try:
            picker = dialog.language_picker
            items = {
                picker.itemData(i): picker.itemText(i) for i in range(picker.count())
            }
            assert list(items) == [
                "auto",
                "en",
                "pt_BR",
                "zh_CN",
                "hi",
                "es",
                "ar",
                "fr",
            ]
            assert items["es"] == "Español - download"
            assert items["en"] == "English"
            assert picker.currentData() == "auto"
        finally:
            dialog.close()

    def test_the_header_puts_settings_before_help(
        self, qgis_app, project, make_memory_layer
    ):
        """Last in the header, Settings then Help, where people look for them."""
        dialog = _dialog(project, make_memory_layer)
        try:
            row = dialog.settings_button.parentWidget().layout()
            settings = row.indexOf(dialog.settings_button)
            assert row.indexOf(dialog.help_button) == settings + 1 == row.count() - 1
        finally:
            dialog.close()

    def test_choosing_a_language_downloads_it_and_asks_to_reopen(
        self, installed_plugin, project, make_memory_layer, release_zip
    ):
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.ui.language_picker import LanguagePicker

        calls: list[str] = []
        button = LanguagePicker(None, fetch=serve_release(release_zip, calls))
        fired: list[bool] = []
        button.changed.connect(lambda: fired.append(True))
        button.choose("es")
        assert len(calls) == 1
        assert installed_plugin.installed_pack("es") is not None
        assert QSettings().value(installed_plugin.SETTING_KEY) == "es"
        assert fired == [True]
        # One download brought every language: none is marked any more.
        names = [button.itemText(i) for i in range(button.count())]
        assert not [name for name in names if name.endswith(" - download")]

    def test_choosing_english_while_in_english_changes_nothing(
        self, installed_plugin, project, make_memory_layer
    ):
        from nika_onlymap_exporter.ui.language_picker import LanguagePicker

        calls: list[str] = []
        button = LanguagePicker(None, fetch=serve_release(b"", calls))
        fired: list[bool] = []
        button.changed.connect(lambda: fired.append(True))
        button.choose("en")
        assert calls == [] and fired == []

    def test_the_offer_appears_for_a_portuguese_qgis(
        self, installed_plugin, project, make_memory_layer, monkeypatch
    ):
        from nika_onlymap_exporter.ui import language_picker

        monkeypatch.setattr(language_picker, "qgis_locale", lambda: "pt_BR")
        dialog = _dialog(project, make_memory_layer)
        try:
            assert dialog.language_offer is not None
            assert dialog.language_offer.code == "pt_BR"
        finally:
            dialog.close()

    def test_no_offer_for_an_english_qgis(
        self, installed_plugin, project, make_memory_layer
    ):
        dialog = _dialog(project, make_memory_layer)
        try:
            assert dialog.language_offer is None
        finally:
            dialog.close()


class TestCatalogueFiles:
    def _messages(self):
        for ts in sorted(I18N_DIR.glob("*.ts")):
            for message in ET.parse(ts).getroot().iter("message"):
                yield (
                    ts.name,
                    message.findtext("source"),
                    message.findtext("translation"),
                )

    def test_every_placeholder_survives_translation(self):
        """A dropped `{n}` would print the brace; a renamed one would raise."""
        broken = [
            (name, source)
            for name, source, translation in self._messages()
            if translation
            and sorted(re.findall(r"\{\w+\}", source))
            != sorted(re.findall(r"\{\w+\}", translation))
        ]
        assert not broken

    def test_the_catalogues_match_the_code(self):
        """Every `tr()` in the code is in every catalogue, and translated."""
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "update_translations.py"),
                "--check",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout

    def test_every_catalogue_is_compiled(self):
        for ts in I18N_DIR.glob("*.ts"):
            qm = ts.with_suffix(".qm")
            assert qm.is_file(), (
                f"{qm.name} missing; run scripts/update_translations.py"
            )
            assert qm.stat().st_mtime >= ts.stat().st_mtime - 1, f"{qm.name} is stale"


def test_plural_fills_every_field(qgis_app):
    """A second placeholder beside `{n}` once raised KeyError inside a Qt slot,
    which aborts QGIS rather than raising."""
    from nika_onlymap_exporter.ui.i18n import plural

    assert plural(2, "{size} MB - {n} layer", "{size} MB - {n} layers", size="1.5") == (
        "1.5 MB - 2 layers"
    )


def test_every_call_supplies_its_placeholders():
    """`tr("... {x}").format()` without `x` raises - and inside a Qt slot that
    aborts QGIS. Checked over the source, so no call site can be missed."""
    import ast

    bad = []
    for path in (REPO_ROOT / "nika_onlymap_exporter").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            given = {k.arg for k in node.keywords}
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "format"
                and isinstance(func.value, ast.Call)
                and isinstance(func.value.func, ast.Name)
                and func.value.func.id == "tr"
                and isinstance(func.value.args[0], ast.Constant)
            ):
                texts = [func.value.args[0].value]
            elif isinstance(func, ast.Name) and func.id == "plural":
                texts = [a.value for a in node.args[1:3] if isinstance(a, ast.Constant)]
                given |= {"n"}
            else:
                continue
            for text in texts:
                if not set(re.findall(r"\{(\w+)", text)) <= given:
                    bad.append(f"{path.name}:{node.lineno}")
    assert not bad


def test_the_plugin_reopens_the_dialog_in_the_new_language(
    qgis_app, project, make_memory_layer
):
    """Labels are set when the dialog is built, so a new language is a new dialog."""
    from nika_onlymap_exporter.plugin import Qgis2WebMapPlugin

    class FakeIface:
        def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
            return None

    project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
    plugin = Qgis2WebMapPlugin(FakeIface())
    try:
        plugin.run()
        first = plugin.dialog
        assert first is not None
        first.languageChanged.emit()
        for _ in range(5):
            qgis_app.processEvents()
        assert plugin.dialog is not None
        assert plugin.dialog is not first
        assert plugin.dialog.isVisible()
    finally:
        if plugin.dialog is not None:
            plugin.dialog.close()


def test_picking_a_language_reopens_the_dialog_in_it(
    qgis_app, project, make_memory_layer
):
    """Reopening alone is not enough: the new dialog must be in the new language.

    The first version reopened the dialog but only applied a language when
    QGIS loaded the plugin, so a user who picked Portuguese saw English again.
    """
    from qgis.PyQt.QtCore import QSettings

    from nika_onlymap_exporter.plugin import Qgis2WebMapPlugin
    from nika_onlymap_exporter.ui import languages
    from nika_onlymap_exporter.ui.i18n import use_language

    class FakeIface:
        def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
            return None

    project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
    plugin = Qgis2WebMapPlugin(FakeIface())
    try:
        plugin.run()
        plugin.dialog.language_picker.choose("pt_BR")
        for _ in range(5):
            qgis_app.processEvents()
        assert plugin.dialog.tabs.tabText(0) == "Mapa"
        plugin.dialog.language_picker.choose("en")
        for _ in range(5):
            qgis_app.processEvents()
        assert plugin.dialog.tabs.tabText(0) == "Map"
    finally:
        QSettings().remove(languages.SETTING_KEY)
        use_language("en")
        if plugin.dialog is not None:
            plugin.dialog.close()


def test_the_fidelity_words_from_core_are_in_the_catalogue(qgis_app) -> None:
    """Core has no Qt, so its verdict and topic names are registered in the
    panel; a word added in core must be added there too."""
    from nika_onlymap_exporter.core.fidelity_summary import (
        VERDICT_LABELS,
        WHOLE_MAP,
        Topic,
    )
    from nika_onlymap_exporter.ui.fidelity_panel import _CORE_WORDS

    core = set(VERDICT_LABELS.values()) | {t.value for t in Topic} | {WHOLE_MAP}
    assert core <= set(_CORE_WORDS)


def test_the_osm_warning_is_in_the_catalogue(qgis_app) -> None:
    from nika_onlymap_exporter.core.manifest_builder import OSM_FILE_NOTE
    from nika_onlymap_exporter.ui.main_dialog import _OSM_FILE_NOTE_FOR_CATALOGUE

    assert OSM_FILE_NOTE == _OSM_FILE_NOTE_FOR_CATALOGUE


def test_the_processing_tool_speaks_the_language(portuguese) -> None:
    """Its options and help were plain strings the catalogue never saw."""
    from nika_onlymap_exporter.processing.export_project import (
        ExportProjectAlgorithm,
    )

    algorithm = ExportProjectAlgorithm()
    algorithm.initAlgorithm()
    assert algorithm.displayName() == "Exportar para mapa web do OnlyMap"
    blocked = algorithm.parameterDefinition(ExportProjectAlgorithm.EXPORT_BLOCKED)
    assert blocked.description() == "Exportar mesmo que alguns itens estejam bloqueados"
    assert "Exportar mesmo que alguns itens" in algorithm.shortHelpString()


def test_every_fidelity_cell_speaks_the_language(
    portuguese, project, make_memory_layer
) -> None:
    """The filters were translated but each row's verdict and the map-wide
    group title were not, leaving one tree in two languages."""
    from qgis.PyQt.QtWidgets import QTreeWidgetItemIterator

    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    dialog = _dialog(project, make_memory_layer)
    try:
        report = FidelityReportBuilder()
        report.approximated("Coordinate precision", "Rounded.")
        dialog._show_fidelity(report)
        texts = []
        it = QTreeWidgetItemIterator(dialog.fidelity_panel.tree)
        while it.value():
            texts += [it.value().text(0), it.value().text(1)]
            it += 1
        assert "Mapa inteiro" in " ".join(texts)
        assert "Alterado" in texts
        assert "Changed" not in texts
    finally:
        dialog.close()


def test_the_fidelity_strip_speaks_the_language(
    portuguese, project, make_memory_layer
) -> None:
    """The line under the tabs is on screen from every tab."""
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    dialog = _dialog(project, make_memory_layer)
    try:
        report = FidelityReportBuilder()
        report.approximated("Coordinate precision", "Rounded.")
        report.approximated("Basemap", "Tiles.")
        dialog._show_fidelity(report)
        strip = dialog.fidelity_strip
        assert strip.label.text() == "2 itens mudam na exportação."
        assert strip.button.text() == "Ver o que muda"
    finally:
        dialog.close()
