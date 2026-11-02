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

When one English text has two meanings, both take a disambiguation:
`tr(text, disambiguation)`, or `N_(text, disambiguation)`, whose value
carries it to the `tr()` that later shows it (see `core/translatable.py`).

`scripts/update_translations.py` collects every `tr()` and `N_()` literal into
`i18n/*.ts` and compiles them.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QTranslator

from ..core.translatable import N_ as N_
from ..translation import CONTEXT as CONTEXT
from ..translation import plural as plural
from ..translation import tr as tr

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
