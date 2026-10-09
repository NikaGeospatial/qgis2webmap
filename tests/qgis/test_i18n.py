"""The plugin follows QGIS's language: catalogues, fallbacks, and the dialog.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

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
    from qgis.PyQt.QtCore import QCoreApplication

    from nika_onlymap_exporter.ui.i18n import install_translator

    translator = install_translator("pt_BR")
    assert translator is not None
    yield
    QCoreApplication.removeTranslator(translator)


class TestCatalogueLookup:
    def test_brazilian_portuguese_is_found(self, qgis_app):
        from nika_onlymap_exporter.ui.i18n import catalogue_for

        assert catalogue_for("pt_BR").name == "qgis2webmap_pt_BR.qm"

    def test_any_portuguese_reads_the_brazilian_text(self, qgis_app):
        from nika_onlymap_exporter.ui.i18n import catalogue_for

        assert catalogue_for("pt_PT").name == "qgis2webmap_pt_BR.qm"
        assert catalogue_for("pt").name == "qgis2webmap_pt_BR.qm"

    def test_a_language_without_a_catalogue_stays_english(self, qgis_app):
        from nika_onlymap_exporter.ui.i18n import catalogue_for, install_translator

        assert catalogue_for("de_DE") is None
        assert catalogue_for("") is None
        assert install_translator("de_DE") is None


class TestTranslating:
    def test_without_a_translator_the_text_is_english(self, qgis_app):
        from nika_onlymap_exporter.ui.i18n import plural, tr

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

    def test_the_dialog_opens_in_portuguese(
        self, portuguese, project, make_memory_layer
    ):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        dialog = MainDialog(FakeIface(), None)
        try:
            titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
            assert titles == ["Mapa", "Camadas", "Aparência", "Fidelidade", "Ajuda"]
            # Found by the page, not the title, so it still works translated.
            dialog._show_fidelity_tab()
            assert dialog.tabs.currentWidget() is dialog._fidelity_page
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
