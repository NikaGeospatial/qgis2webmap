"""Plural wording follows each language's own rule, not English's `count == 1`.

Qt numerus entries: the compiled pack carries the language's plural rule and
`plural()` shows the form it picks. These tests load the REAL shipped packs.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

I18N_DIR = Path(__file__).resolve().parents[2] / "nika_onlymap_exporter" / "i18n"

ONE = "{n} layer will be exported."
MANY = "{n} layers will be exported."
KEY = "%n layer will be exported."


def _arabic(n: int) -> int:
    if n == 0:
        return 0
    if n == 1:
        return 1
    if n == 2:
        return 2
    if 3 <= n % 100 <= 10:
        return 3
    if 11 <= n % 100 <= 99:
        return 4
    return 5


# Form index per count, as the languages define it (CLDR; and Qt's own
# choices where they differ: Spanish and Hindi count only 1 as singular).
RULES: dict[str, tuple[int, Callable[[int], int]]] = {
    "ar": (6, _arabic),
    "zh_CN": (1, lambda n: 0),
    "es": (2, lambda n: 0 if n == 1 else 1),
    "hi": (2, lambda n: 0 if n == 1 else 1),
    "fr": (2, lambda n: 0 if n in (0, 1) else 1),
    "pt_BR": (2, lambda n: 0 if n in (0, 1) else 1),
}
COUNTS = (0, 1, 2, 3, 5, 11, 25, 100, 101, 102, 103)


def _forms(code: str, key: str) -> list[str]:
    root = ET.parse(I18N_DIR / f"qgis2webmap_{code}.ts").getroot()
    for message in root.iter("message"):
        if message.findtext("source") == key:
            return [f.text or "" for f in message.iter("numerusform")]
    raise AssertionError(f"{key!r} not in the {code} catalogue")


@pytest.fixture
def language(qgis_app):
    from nika_onlymap_exporter.ui.i18n import use_language

    yield use_language
    use_language("en")


@pytest.mark.parametrize("code", sorted(RULES))
def test_each_count_shows_the_form_its_language_picks(language, code: str) -> None:
    from nika_onlymap_exporter.ui.i18n import plural

    count_forms, rule = RULES[code]
    forms = _forms(code, KEY)
    assert len(forms) == count_forms
    assert language(code) == code
    for n in COUNTS:
        assert plural(n, ONE, MANY) == forms[rule(n)].replace("%n", str(n)), n


def test_english_is_unchanged(language) -> None:
    from nika_onlymap_exporter.ui.i18n import plural

    language("en")
    assert plural(1, ONE, MANY) == "1 layer will be exported."
    assert plural(0, ONE, MANY) == "0 layers will be exported."
    assert plural(2, ONE, MANY) == "2 layers will be exported."
    assert plural(11, ONE, MANY) == "11 layers will be exported."


def test_english_picks_a_pair_without_a_number_in_it(language) -> None:
    from nika_onlymap_exporter.ui.i18n import plural

    one, many = "{names} is left out.", "{names} are left out."
    assert plural(1, one, many, names="A") == "A is left out."
    assert plural(2, one, many, names="A, B") == "A, B are left out."


def test_a_text_a_pack_lacks_falls_back_to_english_rule(language) -> None:
    from nika_onlymap_exporter.ui.i18n import plural

    language("fr")
    assert plural(1, "{n} gizmo", "{n} gizmos") == "1 gizmo"
    assert plural(5, "{n} gizmo", "{n} gizmos") == "5 gizmos"


@pytest.mark.parametrize("code", sorted(RULES))
def test_every_plural_in_every_catalogue_has_all_its_forms(code: str) -> None:
    wanted = RULES[code][0]
    root = ET.parse(I18N_DIR / f"qgis2webmap_{code}.ts").getroot()
    plurals = [m for m in root.iter("message") if m.get("numerus") == "yes"]
    assert len(plurals) > 40
    for message in plurals:
        forms = [f.text or "" for f in message.iter("numerusform")]
        assert len(forms) == wanted, message.findtext("source")
        assert all(forms), message.findtext("source")
        assert message.find("translation").get("type") is None  # type: ignore[union-attr]
