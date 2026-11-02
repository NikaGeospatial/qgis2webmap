"""Plural text reads exactly as in 0.3.0, in every language.

0.3.1 changes nothing a user sees. Each plural is still two plain catalogue
entries, the singular chosen for exactly one, so the counts that a numerus
rule would word differently (Arabic's dual, French and Brazilian Portuguese
zero, Chinese one, a negative count) keep 0.3.0's text.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

I18N_DIR = Path(__file__).resolve().parents[2] / "nika_onlymap_exporter" / "i18n"

LAYERS = ("{n} layer will be exported.", "{n} layers will be exported.")
ZIP = (
    "Export makes a zip to email or upload, carrying {n} layer.",
    "Export makes a zip to email or upload, carrying {n} layers.",
)
DAYS = ("{n} day ago", "{n} days ago")
HOURS = ("{n} hour ago", "{n} hours ago")
LEFT_OUT = ("Left out 1 file outside {folder}.", "Left out {n} files outside {folder}.")
SUPPORT = (
    "{names} is left out as a support layer. Tick it to show it.",
    "{names} are left out as support layers. Tick one to show it.",
)

# Read off 0.3.0's own catalogues and `plural()`.
AS_030 = [
    ("ar", 2, DAYS, {}, "\u0642\u0628\u0644 2 \u0623\u064a\u0627\u0645"),
    ("ar", -1, DAYS, {}, "\u0642\u0628\u0644 -1 \u0623\u064a\u0627\u0645"),
    ("ar", 11, HOURS, {}, "\u0642\u0628\u0644 11 \u0633\u0627\u0639\u0627\u062a"),
    (
        "fr",
        0,
        ZIP,
        {},
        "Exporter cr\u00e9e un ZIP \u00e0 envoyer par e-mail ou t\u00e9l\u00e9verser, "
        "avec 0 couches.",
    ),
    ("pt_BR", 0, LAYERS, {}, "0 camadas ser\u00e3o exportadas."),
    (
        "hi",
        0,
        LAYERS,
        {},
        "0 \u092a\u0930\u0924\u0947\u0902 \u0928\u093f\u0930\u094d\u092f\u093e\u0924 "
        "\u0939\u094b\u0902\u0917\u0940\u0964",
    ),
    (
        "zh_CN",
        1,
        LEFT_OUT,
        {"folder": "F"},
        "\u5df2\u6392\u9664 F \u4e4b\u5916\u7684 1 \u4e2a\u6587\u4ef6\u3002",
    ),
    (
        "zh_CN",
        2,
        LEFT_OUT,
        {"folder": "F"},
        "\u5df2\u6392\u9664 F \u4e4b\u5916\u7684 2 \u4e2a\u6587\u4ef6\u3002",
    ),
    (
        "zh_CN",
        1,
        SUPPORT,
        {"names": "A"},
        "A \u4f5c\u4e3a\u8f85\u52a9\u56fe\u5c42\u88ab\u7701\u7565\u3002"
        "\u52fe\u9009\u5373\u53ef\u663e\u793a\u3002",
    ),
    ("es", 1, LAYERS, {}, "Se exportar\u00e1 1 capa."),
    ("en", -1, LAYERS, {}, "-1 layers will be exported."),
]


@pytest.mark.parametrize(("code", "count", "pair", "fields", "expected"), AS_030)
def test_plural_text_is_030s(qgis_app, code, count, pair, fields, expected) -> None:
    from nika_onlymap_exporter.ui.i18n import plural, use_language

    try:
        assert use_language(code) == code
        assert plural(count, *pair, **fields) == expected
    finally:
        use_language("en")


def test_no_catalogue_holds_a_numerus_entry() -> None:
    catalogues = sorted(I18N_DIR.glob("qgis2webmap_*.ts"))
    assert catalogues
    for path in catalogues:
        assert 'numerus="yes"' not in path.read_text(encoding="utf-8"), path.name
