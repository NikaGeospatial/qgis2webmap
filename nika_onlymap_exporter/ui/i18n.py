"""The plugin's own text in the language QGIS is running in.

**English is built in; other languages are downloaded packs.** Which one is
shown, and how a pack is fetched and verified, is `languages`' business. This
module only translates, and swaps the active catalogue.

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

from qgis.PyQt.QtCore import QCoreApplication, QTranslator

CONTEXT = "qgis2webmap"


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


_active: QTranslator | None = None
_active_code = "en"


def active_language() -> str:
    """The language `tr()` is answering in right now."""
    return _active_code


def use_language(code: str) -> str:
    """Make `code` the language `tr()` answers in. Returns what was applied.

    Anything without an installed pack - English included - removes the
    current catalogue, which leaves the source text: English. Only this
    plugin's catalogue is touched; QGIS's own translators are left alone.
    """
    global _active, _active_code
    from . import languages

    if _active is not None:
        QCoreApplication.removeTranslator(_active)
        _active = None

    _active_code = languages.ENGLISH
    path = languages.installed_pack(code) if code != languages.ENGLISH else None
    if path is None:
        return _active_code
    translator = QTranslator()
    if not translator.load(str(path)):
        return _active_code
    QCoreApplication.installTranslator(translator)
    _active = translator
    _active_code = code
    return code


def qgis_locale() -> str:
    """The language QGIS itself is showing, as `pt_BR`, `en_US` and so on."""
    from qgis.core import QgsApplication

    try:
        return str(QgsApplication.locale() or "")
    except Exception:  # an old QGIS without it means English
        return ""
