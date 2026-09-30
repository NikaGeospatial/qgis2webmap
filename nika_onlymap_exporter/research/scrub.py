"""The scrubbing rules applied to every free-text field before it is queued.

Applied to the map title, layer names, field names and `use_case_other`. The
server re-applies the same rules (`nika-cf-workers`,
`apps/control-plane/src/research/scrub.ts`), and this is a line-for-line mirror
of it: both sides have to agree, or text the plugin considers clean would be
changed again when stored. `tests/unit/research_scrub_vectors.json` holds the
server's own test cases and this implementation is checked against them.

The spec lists the rules in this order:

1. Anything that looks like a path: keep the last component, drop the extension.
2. URLs: removed.
3. Email addresses: replaced by `[email]`.
4. Phone-like runs (7+ digits allowing spaces, dots, dashes, parentheses,
   leading +): `[number]`.
5. Any remaining run of 6+ digits: `[number]`.
6. Control characters removed, whitespace collapsed, trimmed, cut to 60 chars
   (140 for `use_case_other`).

Both sides run a stricter order whose output is never *less* scrubbed than the
literal one: control characters, URLs, emails, paths, phone runs, digit runs,
whitespace, cut.

* Control characters go first. Left to the end, `123<NUL>456` is two short
  runs when the digit rules look at it and one six-digit run once the NUL is
  gone.
* URLs go before paths. A URL looks like a path, and the path rule keeps the
  last component - `https://host/a/report-final.pdf` would keep
  `report-final`, a piece of the URL the rules say to remove entirely.
* Emails go before paths, so `x/jane@corp.com` is replaced whole rather than
  having `.com` taken for an extension and `jane@corp` left behind.

Idempotent by construction: `[email]` and `[number]` contain nothing any rule
matches, and no separator survives the path rule except as punctuation.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import unicodedata

MAX_TEXT_LENGTH = 60
MAX_OTHER_LENGTH = 140

EMAIL_PLACEHOLDER = "[email]"
NUMBER_PLACEHOLDER = "[number]"

# Tabs, newlines and other line breaks are whitespace a person typed; they
# become a space so the words either side do not fuse. Every other control
# character and every format character (zero-width spaces, bidi overrides) is
# dropped: none carry meaning in a name, and the bidi ones can make stored text
# read differently from what it is.
_WHITESPACE_CONTROLS = re.compile("[\t\n\v\f\r\u0085\u2028\u2029]")
_DROPPED_CATEGORIES = ("Cc", "Cf")

# A scheme followed by `//`, or a bare `www.` host. The look-behind is the
# server's ASCII `\b`, spelled out: Python's `\b` is Unicode-aware, so
# `éhttps://x` would otherwise keep its URL here and lose it there.
_URL = re.compile(
    r"(?:(?<![A-Za-z0-9_])[a-z][a-z0-9+.\-]*://|(?<![A-Za-z0-9_])www\.)\S*",
    re.IGNORECASE,
)

# `[^\W_]` is a Unicode letter or digit: the server's `[\p{L}\p{N}]`.
_EMAIL = re.compile(r"(?:[^\W_]|[._%+\-])+@(?:[^\W_]|-)+(?:\.(?:[^\W_]|-)+)+")

# Seven or more digits where any two neighbours may be separated by up to three
# of: space, dot, dash, parentheses. An optional leading `+` and `(` and a
# trailing `)` belong to the number. `\d` is any Unicode decimal digit, so a
# number in Arabic-Indic or Devanagari digits counts like an ASCII one.
_PHONE = re.compile(r"\+?\(?\d(?:[ .\-()]{0,3}\d){6,}\)?")
_LONG_DIGITS = re.compile(r"\d{6,}")

_WHITESPACE = re.compile(r"\s+")

# Where an ANCHORED path starts: a drive (`C:\`), a UNC share (`\\server`), a
# home-relative path (`~/`), or a separator opening a token (`/home`).
_ANCHORED_PATH_START = re.compile(
    r"(?:^|\s)((?:[A-Za-z]:[\\/]|\\\\|~[\\/]|[\\/](?=[^\s\\/])))"
)
_SEPARATOR = re.compile(r"[\\/]")
_NOT_SEPARATOR = re.compile(r"[^\\/]")
_TOKEN = re.compile(r"\S+")
_SPACE = re.compile(r"\s")
_EXTENSION = re.compile(r"\.[^\W_]{1,10}$")


def _without_extension(component: str) -> str:
    """`roads.shp` -> `roads`; `.gitignore` -> `` (all extension, no name)."""
    return _EXTENSION.sub("", component)


def _relative_path(match: re.Match[str]) -> str:
    """One token with a separator inside it: `data/roads.gpkg` -> `roads`.

    These carry no home folder, so they are handled per token and cannot eat
    the words around them. A token made only of separators - the ` / ` in
    `Roads / Rail` - is punctuation, not a path, and is left alone.
    """
    token = match.group(0)
    if not (_SEPARATOR.search(token) and _NOT_SEPARATOR.search(token)):
        return token
    parts = [part for part in _SEPARATOR.split(token) if part]
    return _without_extension(parts[-1] if parts else "")


def _scrub_paths(text: str) -> str:
    """Paths down to their last component, without the extension.

    Anchored paths are the dangerous ones - they carry a home folder - and they
    are the ones that contain spaces: `C:\\Users\\Jane Smith\\Maps\\roads.shp`.
    Split on whitespace that is three "paths", and keeping the last component
    of each keeps `Jane` and `Smith`. So an anchored path runs from its anchor
    to the end of the component after the LAST separator anywhere later in the
    text, spaces included, and the whole span becomes that final component.
    """
    anchor = _ANCHORED_PATH_START.search(text)
    if anchor is not None:
        start = anchor.start(1)
        last_separator = max(text.rfind("/"), text.rfind("\\"))
        tail = text[last_separator + 1 :]
        space = _SPACE.search(tail)
        end = len(text) if space is None else last_separator + 1 + space.start()
        component = _without_extension(text[last_separator + 1 : end])
        text = f"{text[:start]}{component}{text[end:]}"
    return _TOKEN.sub(_relative_path, text)


def _remove_control_characters(text: str) -> str:
    spaced = _WHITESPACE_CONTROLS.sub(" ", text)
    return "".join(
        char for char in spaced if unicodedata.category(char) not in _DROPPED_CATEGORIES
    )


def scrub(text: str | None, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Apply every rule to `text` and cut to `max_length` code points.

    `None` scrubs to the empty string. Python strings index by code point, so
    the cut never splits a character - the server cuts by code point for the
    same reason.
    """
    if not text:
        return ""
    cleaned = _remove_control_characters(text)
    cleaned = _URL.sub(" ", cleaned)
    cleaned = _EMAIL.sub(EMAIL_PLACEHOLDER, cleaned)
    cleaned = _scrub_paths(cleaned)
    cleaned = _PHONE.sub(NUMBER_PLACEHOLDER, cleaned)
    cleaned = _LONG_DIGITS.sub(NUMBER_PLACEHOLDER, cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if len(cleaned) > max_length:
        return cleaned[:max_length].rstrip()
    return cleaned


def scrub_other(text: str | None) -> str:
    """The rules for `use_case_other`, which is allowed a longer answer."""
    return scrub(text, MAX_OTHER_LENGTH)
