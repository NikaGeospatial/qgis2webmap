"""Marking text for the translation catalogue, without Qt.

`N_()` marks a literal for `scripts/update_translations.py` and hands it back
untranslated; the UI translates it with `ui/i18n.tr()` where it is shown.
It lives here because core modules must not import Qt, and `ui/i18n.py`
re-exports it, so the plugin has one marker.

**Disambiguation.** The catalogue has one context, so one English text gets
one translation - but the same word can mean two things: "Caption" is the
web map's box for a title drawn over the map, and also the name of a layout
template whose figure has a caption under it. Qt tells such uses apart by a
disambiguation, written to the `.ts` as `<comment>` and passed as the third
argument of `QCoreApplication.translate`.

A marked value is translated later, often far from where it was marked
(a template's name is marked in core and shown by the picker), so the
disambiguation travels with the value: `N_(text, disambiguation)` returns a
`MarkedText`, a `str` that remembers it, and `tr()` passes it on. Without a
disambiguation `N_()` returns exactly the string it was given.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations


class MarkedText(str):
    """A `str` that carries the disambiguation it was marked with.

    It compares, hashes and formats as its text, so it stands anywhere the
    plain string did: ids, dict keys, frozen dataclass fields. Operations
    that build a new string (`+`, `.format()`, `str()`) return a plain `str`
    without it, so translate the marked value itself.
    """

    disambiguation: str

    def __new__(cls, text: str, disambiguation: str = "") -> MarkedText:
        marked = super().__new__(cls, text)
        marked.disambiguation = disambiguation
        return marked


def N_(text: str, disambiguation: str = "") -> str:  # noqa: N802 - gettext's name
    """Mark `text` for the catalogue without translating it yet.

    `disambiguation` is a short note on which meaning this is, needed only
    when the same text appears elsewhere with another meaning. It must be
    a string literal, so the catalogue script can read it.
    """
    return MarkedText(text, disambiguation) if disambiguation else text
