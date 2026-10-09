"""The plugin's own text in the language QGIS is running in.

**Follows QGIS, with no picker of its own.** QGIS already has a language
setting (Settings > Options > General > Override system locale), and a second
one inside a plugin would let the two disagree. Whatever QGIS shows its menus
in, this dialog shows itself in, if a translation exists; otherwise English.

**Qt's own mechanism.** Strings go through `QCoreApplication.translate`, and the
catalogues are Qt `.ts` files compiled to `.qm` - the format QGIS's translators
already work in. One context, `qgis2webmap`, for the whole plugin: the same
sentence means the same thing wherever it appears, so it is translated once.

`tr()` translates now. `N_()` only marks a string for the catalogue, for tables
built at import time (combo labels, status names) that are translated where
they are shown - a module can be imported before the translator is installed.

`scripts/update_translations.py` collects every `tr()` and `N_()` literal into
`i18n/*.ts` and compiles them.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QTranslator

CONTEXT = "qgis2webmap"
I18N_DIR = Path(__file__).resolve().parent.parent / "i18n"


def tr(text: str) -> str:
    """`text` in the current language, or unchanged if there is no translation."""
    return QCoreApplication.translate(CONTEXT, text)


def N_(text: str) -> str:  # noqa: N802 - the conventional gettext marker name
    """Mark `text` for the catalogue without translating it yet."""
    return text


def plural(count: int, singular: str, many: str, **fields: object) -> str:
    """`singular` or `many` by `count`, translated, with `{n}` and `fields` filled in.

    Both forms go into the catalogue as separate entries. English and
    Portuguese both use the singular for exactly one, which covers every
    language this plugin ships; a language with more forms would move this to
    Qt's numerus entries.
    """
    return tr(singular if count == 1 else many).format(n=count, **fields)


def catalogue_for(locale: str) -> Path | None:
    """The compiled catalogue for a QGIS locale such as `pt_BR`, if we have one.

    Exact match first, then any catalogue for the same language: a QGIS set to
    Portuguese (Portugal) still reads the Brazilian text rather than English.
    """
    if not locale:
        return None
    exact = I18N_DIR / f"qgis2webmap_{locale}.qm"
    if exact.is_file():
        return exact
    language = locale.split("_", 1)[0].lower()
    return next(iter(sorted(I18N_DIR.glob(f"qgis2webmap_{language}*.qm"))), None)


def install_translator(locale: str) -> QTranslator | None:
    """Load the catalogue for `locale` into the application.

    Returns the translator, which the caller must keep a reference to and
    remove on unload: Qt does not own it, and a collected translator silently
    stops translating.
    """
    path = catalogue_for(locale)
    if path is None:
        return None
    translator = QTranslator()
    if not translator.load(str(path)):
        return None
    QCoreApplication.installTranslator(translator)
    return translator


def qgis_locale() -> str:
    """The language QGIS itself is showing, as `pt_BR`, `en_US` and so on."""
    from qgis.core import QgsApplication

    try:
        return str(QgsApplication.locale() or "")
    except Exception:  # an old QGIS without it means English
        return ""
