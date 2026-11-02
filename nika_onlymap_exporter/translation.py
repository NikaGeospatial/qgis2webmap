"""`tr()` and `plural()`: the plugin's text in the active language.

Package-level so core, exporters and processing can translate without
importing the UI. Which catalogue is active, and how a language pack is
installed, stays in `ui/i18n.py`, which re-exports these for its callers;
marking text without Qt is `core/translatable.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication

CONTEXT = "qgis2webmap"


def tr(text: str, disambiguation: str = "") -> str:
    """`text` in the current language, or unchanged if there is no translation.

    `disambiguation` picks one meaning of a text that has several; without
    one, a value marked by `N_(text, disambiguation)` brings its own.
    """
    disambiguation = disambiguation or getattr(text, "disambiguation", "")
    if disambiguation:
        return QCoreApplication.translate(CONTEXT, text, disambiguation)
    return QCoreApplication.translate(CONTEXT, text)


def plural(count: int, singular: str, many: str, **fields: object) -> str:
    """`singular` or `many` by `count`, translated, with `{n}` and `fields` filled in.

    Both forms go into the catalogue as separate entries, and the singular
    is chosen for exactly one. That is English's rule, not every shipped
    language's: French and Hindi also use the singular for zero, and Arabic
    has more forms. Moving to Qt's numerus entries would fix both.
    """
    return tr(singular if count == 1 else many).format(n=count, **fields)
