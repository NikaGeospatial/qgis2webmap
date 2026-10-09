"""Languages: which one is shown, downloading packs, and the dialog in them.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Nothing here touches the network. Downloads go through a fake fetcher that
serves the repository's own compiled packs, which are the same bytes jsDelivr
serves from a release tag.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
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


def serve_repo_packs(calls: list[str]):
    """A fetcher that answers with the repository's own pack for the URL."""

    def fetch(url: str) -> bytes:
        calls.append(url)
        return (I18N_DIR / url.rsplit("/", 1)[1]).read_bytes()

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

    def test_following_qgis_needs_the_pack_installed(self, installed_plugin):
        assert installed_plugin.effective("auto", "pt_BR") == "en"
        installed_plugin.download("pt_BR", serve_repo_packs([]))
        assert installed_plugin.effective("auto", "pt_BR") == "pt_BR"

    def test_english_chosen_beats_qgis(self, installed_plugin):
        installed_plugin.download("pt_BR", serve_repo_packs([]))
        assert installed_plugin.effective("en", "pt_BR") == "en"

    def test_a_chosen_language_whose_pack_is_gone_falls_back_to_english(
        self, installed_plugin
    ):
        assert installed_plugin.effective("es", "pt_BR") == "en"


class TestTheOffer:
    def test_offered_when_qgis_speaks_it_and_it_is_missing(self, installed_plugin):
        assert installed_plugin.offer("auto", "pt_BR") == "pt_BR"

    def test_not_offered_once_installed(self, installed_plugin):
        installed_plugin.download("pt_BR", serve_repo_packs([]))
        assert installed_plugin.offer("auto", "pt_BR") is None

    def test_not_offered_to_someone_who_chose_english(self, installed_plugin):
        assert installed_plugin.offer("en", "pt_BR") is None

    def test_not_offered_again_once_dismissed(self, installed_plugin):
        assert installed_plugin.offer("auto", "pt_BR", dismissed="pt_BR") is None

    def test_never_for_english_or_an_unknown_language(self, installed_plugin):
        assert installed_plugin.offer("auto", "en_GB") is None
        assert installed_plugin.offer("auto", "de_DE") is None


class TestDownloading:
    def test_the_url_is_the_release_tag_on_jsdelivr(self, qgis_app, monkeypatch):
        from nika_onlymap_exporter.ui import languages

        monkeypatch.delenv(languages.BASE_URL_ENV, raising=False)
        assert languages.pack_url("pt_BR", "0.2.0") == (
            "https://cdn.jsdelivr.net/gh/NikaGeospatial/qgis2webmap@v0.2.0"
            "/nika_onlymap_exporter/i18n/qgis2webmap_pt_BR.qm"
        )

    def test_a_good_pack_is_installed(self, installed_plugin):
        calls: list[str] = []
        path = installed_plugin.download("es", serve_repo_packs(calls))
        assert path.is_file() and path.parent == installed_plugin.install_dir()
        assert len(calls) == 1
        assert installed_plugin.installed_pack("es") == path

    def test_a_pack_that_differs_from_the_release_is_refused(self, installed_plugin):
        with pytest.raises(installed_plugin.LanguageDownloadError):
            installed_plugin.download("es", lambda url: b"not the released pack")
        assert installed_plugin.installed_pack("es") is None
        folder = installed_plugin.install_dir()
        assert not folder.exists() or not any(folder.iterdir())

    def test_a_tampered_file_on_disk_is_not_trusted(self, installed_plugin):
        path = installed_plugin.download("fr", serve_repo_packs([]))
        path.write_bytes(path.read_bytes() + b"x")
        assert installed_plugin.installed_pack("fr") is None

    def test_plain_http_is_refused(self, installed_plugin, monkeypatch):
        monkeypatch.setenv(installed_plugin.BASE_URL_ENV, "http://example.org/i18n")
        with pytest.raises(installed_plugin.LanguageDownloadError):
            installed_plugin.download("es", serve_repo_packs([]))

    def test_an_unknown_language_is_refused_before_any_request(self, installed_plugin):
        calls: list[str] = []
        with pytest.raises(installed_plugin.LanguageDownloadError):
            installed_plugin.download("de", serve_repo_packs(calls))
        assert calls == []


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
            assert titles == [
                "Mapa",
                "Camadas",
                "Aparência",
                "Fidelidade",
                "Configurações",
                "Ajuda",
            ]
            # Found by the page, not the title, so it still works translated.
            dialog._show_fidelity_tab()
            assert dialog.tabs.currentWidget() is dialog._fidelity_page
            assert dialog.language_picker.currentData() == "auto"
        finally:
            dialog.close()

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

    def test_the_tabs_put_settings_before_help(
        self, qgis_app, project, make_memory_layer
    ):
        dialog = _dialog(project, make_memory_layer)
        try:
            titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
            assert titles[-2:] == ["Settings", "Help"]
        finally:
            dialog.close()

    def test_live_preview_is_one_switch_in_two_places(
        self, qgis_app, project, make_memory_layer
    ):
        dialog = _dialog(project, make_memory_layer)
        try:
            dialog.live_action.blockSignals(True)
            dialog.live_action.setChecked(False)
            dialog.live_action.blockSignals(False)
            dialog.live_check.setChecked(False)
            dialog.live_check.setChecked(True)
            assert dialog.live_action.isChecked()
            dialog.live_action.setChecked(False)
            assert not dialog.live_check.isChecked()
        finally:
            dialog.close()

    def test_choosing_a_language_downloads_it_and_asks_to_reopen(
        self, installed_plugin, project, make_memory_layer
    ):
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.ui.language_picker import LanguagePicker

        calls: list[str] = []
        button = LanguagePicker(None, fetch=serve_repo_packs(calls))
        fired: list[bool] = []
        button.changed.connect(lambda: fired.append(True))
        button.choose("es")
        assert len(calls) == 1
        assert installed_plugin.installed_pack("es") is not None
        assert QSettings().value(installed_plugin.SETTING_KEY) == "es"
        assert fired == [True]

    def test_choosing_english_while_in_english_changes_nothing(
        self, installed_plugin, project, make_memory_layer
    ):
        from nika_onlymap_exporter.ui.language_picker import LanguagePicker

        calls: list[str] = []
        button = LanguagePicker(None, fetch=serve_repo_packs(calls))
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
