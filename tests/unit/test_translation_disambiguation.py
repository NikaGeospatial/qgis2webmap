"""One English text, two meanings: the catalogue keeps them apart.

"Caption" is the web map dialog's box for a title drawn over the map, and
also the name of the layout template whose figure has a caption under it.
`N_(text, disambiguation)` marks the second; `scripts/update_translations.py`
writes it as its own entry with Qt's `<comment>`, and the marked value
carries the disambiguation to the `tr()` that shows it. The Qt lookup itself
is `tests/qgis/test_i18n.py`'s business.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import pickle
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

from nika_onlymap_exporter.core.layout_templates import TEMPLATES
from nika_onlymap_exporter.core.translatable import N_, MarkedText

REPO_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = REPO_ROOT / "nika_onlymap_exporter" / "i18n"


def _generator() -> ModuleType:
    path = REPO_ROOT / "scripts" / "update_translations.py"
    spec = importlib.util.spec_from_file_location("update_translations", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The real script, collecting from a scratch package instead of the plugin."""
    module = _generator()
    monkeypatch.setattr(module, "PACKAGE_DIR", tmp_path)
    return module


def _messages(text: str) -> list[tuple[str, str | None, str | None, str | None]]:
    """(source, comment, translation, translation type) for each message."""
    return [
        (
            m.findtext("source") or "",
            m.findtext("comment"),
            m.findtext("translation") or None,
            m.find("translation").get("type"),  # type: ignore[union-attr]
        )
        for m in ET.fromstring(text.split("\n", 2)[2]).iter("message")
    ]


class TestTheMarker:
    def test_without_a_disambiguation_the_text_comes_back_as_it_was(self) -> None:
        text = "Caption"
        assert N_(text) is text
        assert type(N_(text)) is str

    def test_a_marked_text_is_still_the_text(self) -> None:
        marked = N_("Caption", "a template")
        assert isinstance(marked, MarkedText)
        assert marked.disambiguation == "a template"
        assert marked == "Caption"
        assert hash(marked) == hash("Caption")
        assert {marked: 1}["Caption"] == 1
        assert f"{marked}!" == "Caption!"

    def test_it_survives_copies_and_frozen_dataclasses(self) -> None:
        @dataclasses.dataclass(frozen=True)
        class Named:
            name: str

        held = Named(N_("Caption", "a template"))
        for copied in (
            copy.copy(held.name),
            copy.deepcopy(held).name,
            pickle.loads(pickle.dumps(held.name)),
        ):
            assert isinstance(copied, MarkedText)
            assert copied == "Caption"
            assert copied.disambiguation == "a template"

    def test_the_caption_template_carries_its_meaning(self) -> None:
        report = next(t for t in TEMPLATES if t.id == "report-figure")
        assert report.name == "Caption"
        assert isinstance(report.name, MarkedText)
        assert "template" in report.name.disambiguation


class TestTheGenerator:
    SOURCE = (
        'tr("Caption")\n'
        'N_("Caption", "template name")\n'
        'N_("Note", disambiguation="by keyword")\n'
        'plural(n, "{n} map", "{n} maps")\n'
        "tr(name)\n"
        'tr("Caption")\n'
    )

    def test_a_disambiguated_use_is_its_own_entry(
        self, generator: ModuleType, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text(self.SOURCE, encoding="utf-8")
        assert generator.collect() == {
            ("Caption", ""): ["a.py:1", "a.py:6"],
            ("Caption", "template name"): ["a.py:2"],
            ("Note", "by keyword"): ["a.py:3"],
            ("%n map", ""): ["a.py:4"],
        }

    def test_the_disambiguation_is_written_as_qt_s_comment(
        self, generator: ModuleType, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text(self.SOURCE, encoding="utf-8")
        text = generator.render(
            "fr",
            generator.collect(),
            {("Caption", ""): "En-tête", ("Caption", "template name"): "Figure"},
        )
        assert _messages(text)[:3] == [
            ("Caption", None, "En-tête", None),
            ("Caption", "template name", "Figure", None),
            ("Note", "by keyword", None, "unfinished"),
        ]
        # Qt's element order: the comment follows the source.
        assert (
            "<source>Caption</source>\n      <comment>template name</comment>\n"
            "      <translation>Figure</translation>"
        ) in text

    def test_an_existing_translation_stays_with_the_use_it_was_for(
        self, generator: ModuleType, tmp_path: Path
    ) -> None:
        """Adding a disambiguation to one use must not hand it the other's
        translation: it starts untranslated, and the plain use keeps its own."""
        ts = tmp_path / "qgis2webmap_fr.ts"
        ts.write_text(
            generator.render("fr", {("Caption", ""): ["a.py:1"]}, {}).replace(
                '<translation type="unfinished" />',
                "<translation>En-tête</translation>",
            ),
            encoding="utf-8",
        )
        known = generator.read_existing(ts)
        assert known == {("Caption", ""): "En-tête"}
        both = {("Caption", ""): ["a.py:1"], ("Caption", "template name"): ["b.py:1"]}
        ts.write_text(generator.render("fr", both, known), encoding="utf-8")
        assert _messages(ts.read_text(encoding="utf-8")) == [
            ("Caption", None, "En-tête", None),
            ("Caption", "template name", None, "unfinished"),
        ]
        ts.write_text(
            ts.read_text(encoding="utf-8").replace(
                '<translation type="unfinished" />', "<translation>Figure</translation>"
            ),
            encoding="utf-8",
        )
        assert generator.read_existing(ts) == {
            ("Caption", ""): "En-tête",
            ("Caption", "template name"): "Figure",
        }

    def test_an_entry_without_one_is_written_exactly_as_before(
        self, generator: ModuleType
    ) -> None:
        text = generator.render(
            "fr",
            {("Map", ""): ["ui/a.py:3", "core/b.py:9"], ("Close", ""): ["ui/a.py:4"]},
            {("Map", ""): "Carte"},
        )
        assert text == (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE TS>\n"
            '<TS version="2.1" language="fr">\n'
            "  <context>\n"
            "    <name>qgis2webmap</name>\n"
            "    <message>\n"
            '      <location filename="../ui/a.py" line="3" />\n'
            '      <location filename="../core/b.py" line="9" />\n'
            "      <source>Map</source>\n"
            "      <translation>Carte</translation>\n"
            "    </message>\n"
            "    <message>\n"
            '      <location filename="../ui/a.py" line="4" />\n'
            "      <source>Close</source>\n"
            '      <translation type="unfinished" />\n'
            "    </message>\n"
            "  </context>\n"
            "</TS>\n"
        )

    def test_a_computed_disambiguation_is_refused(
        self, generator: ModuleType, tmp_path: Path
    ) -> None:
        """It would be looked up under a key the catalogue never had."""
        (tmp_path / "a.py").write_text('N_("Caption", meaning)\n', encoding="utf-8")
        with pytest.raises(SystemExit, match=r"a\.py:1"):
            generator.collect()


class TestTheShippedCatalogues:
    @pytest.mark.parametrize("ts", sorted(I18N_DIR.glob("qgis2webmap_*.ts")))
    def test_each_meaning_of_caption_is_translated_on_its_own(self, ts: Path) -> None:
        report = next(t for t in TEMPLATES if t.id == "report-figure")
        assert isinstance(report.name, MarkedText)
        captions = {
            (m.findtext("comment") or ""): m.findtext("translation")
            for m in ET.parse(ts).getroot().iter("message")
            if m.findtext("source") == "Caption"
        }
        assert set(captions) == {"", report.name.disambiguation}
        assert captions[""] and captions[report.name.disambiguation]
        assert captions[""] != captions[report.name.disambiguation]


class TestPluralEntries:
    """`plural()` and `N_plural()` pairs are Qt numerus entries."""

    SOURCE = (
        'plural(n, "{n} map", "{n} maps")\n'
        'WORDS = N_plural("It is gone.", "They are gone.")\n'
        'plural(n, "{n} map", "{n} maps")\n'
    )

    def test_one_numerus_entry_per_pair(
        self, generator: ModuleType, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text(self.SOURCE, encoding="utf-8")
        sources, plurals = generator.scan()
        assert list(sources) == [("%n map", ""), ("It is gone.", "")]
        assert plurals == {
            ("%n map", ""): "%n maps",
            ("It is gone.", ""): "They are gone.",
        }

    @pytest.mark.parametrize(
        ("language", "forms"),
        [("ar", 6), ("zh_CN", 1), ("fr", 2), ("es", 2), ("pt_BR", 2), ("hi", 2)],
    )
    def test_an_untranslated_plural_has_its_languages_form_count(
        self, generator: ModuleType, tmp_path: Path, language: str, forms: int
    ) -> None:
        (tmp_path / "a.py").write_text(self.SOURCE, encoding="utf-8")
        sources, plurals = generator.scan()
        root = ET.fromstring(
            generator.render(language, sources, {}, plurals).split("\n", 2)[2]
        )
        message = root.find(".//message")
        assert message is not None and message.get("numerus") == "yes"
        assert len(message.findall(".//numerusform")) == forms
        assert message.findtext("extracomment") == "English plural: %n maps"

    def test_translated_forms_are_read_back(
        self, generator: ModuleType, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text(self.SOURCE, encoding="utf-8")
        sources, plurals = generator.scan()
        ts = tmp_path / "qgis2webmap_fr.ts"
        known = {("%n map", ""): ["%n carte", "%n cartes"]}
        ts.write_text(generator.render("fr", sources, known, plurals), encoding="utf-8")
        assert generator.read_existing(ts) == known

    def test_a_text_both_plural_and_plain_is_refused(
        self, generator: ModuleType, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text(
            'plural(n, "{n} map", "{n} maps")\ntr("%n map")\n', encoding="utf-8"
        )
        with pytest.raises(SystemExit):
            generator.scan()
