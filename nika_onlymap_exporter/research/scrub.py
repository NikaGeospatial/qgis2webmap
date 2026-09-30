"""The scrubbing rules applied to every free-text field before it is queued.

Applied to the map title, layer names, field names and `use_case_other`. The
server re-applies the same rules, so these only have to be at least as strict
as the spec, and applying them twice must change nothing (`scrub` is
idempotent - the tests hold it to that).

The spec lists the rules in this order:

1. Anything that looks like a path: keep the last component, drop the extension.
2. URLs: removed.
3. Email addresses: replaced by `[email]`.
4. Phone-like runs (7+ digits allowing spaces, dots, dashes, parentheses,
   leading +): `[number]`.
5. Any remaining run of 6+ digits: `[number]`.
6. Control characters removed, whitespace collapsed, trimmed, cut to 60 chars
   (140 for `use_case_other`).

Two deliberate deviations in *order*, both stricter than the literal reading:

* Control characters are removed first, so one hidden inside a path, an address
  or a number cannot split it into pieces that each slip past its rule.
* URLs are removed before paths are shortened. A URL contains slashes, so
  shortening it as a path first would keep its last segment - and a URL's last
  segment is where a token or a file name usually sits.

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

# A scheme (`https://`, `file://`, `ftp://`...) or a bare `www.` host, up to the
# next whitespace.
_URL = re.compile(r"(?:\b[A-Za-z][A-Za-z0-9+.\-]*://|\bwww\.)\S*", re.IGNORECASE)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")

# 7 or more digits, separated by at most two of space . - ( ) between any two
# of them, optionally led by `+` and an opening parenthesis.
_PHONE = re.compile(r"\+?\(?\d(?:[ .\-()]{0,2}\d){6,}")

_LONG_DIGITS = re.compile(r"\d{6,}")

_WHITESPACE = re.compile(r"\s+")

_SEPARATORS = re.compile(r"[\\/]")
_DRIVE = re.compile(r"^[A-Za-z]:")
# A final component that ends in a file extension: `roads.shp`, `dem.tif`.
_HAS_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")


def _looks_like_path(token: str) -> bool:
    """Whether a whitespace-free token is a filesystem path.

    A separator alone is not enough: `2020/2021` and `N/A` are ordinary
    text. A token counts when it is anchored like a path (leading separator,
    `~`, `.`, a drive letter), has two or more separators, or ends in a file
    name with an extension.
    """
    separators = len(_SEPARATORS.findall(token))
    if separators == 0:
        return False
    if token[0] in "/\\~." or _DRIVE.match(token):
        return True
    if separators >= 2:
        return True
    return bool(_HAS_EXTENSION.search(_last_component(token)))


def _last_component(token: str) -> str:
    # QGIS appends provider options after a pipe: `/data/x.gpkg|layername=roads`.
    component = _SEPARATORS.split(token.rstrip("/\\"))[-1]
    return component.split("|", 1)[0]


def _shorten_path(token: str) -> str:
    if not _looks_like_path(token):
        return token
    component = _last_component(token)
    if _DRIVE.fullmatch(component):
        return ""
    return _HAS_EXTENSION.sub("", component)


def _remove_control_characters(text: str) -> str:
    """Control and format characters out; whitespace-like ones become spaces.

    A tab or newline separates words, so it becomes a space rather than
    vanishing and gluing two words together. Format characters (`Cf`) go too:
    they include the bidirectional overrides that make text display in an
    order other than the one it is stored in.
    """
    kept: list[str] = []
    for char in text:
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char) in ("Cc", "Cf"):
            continue
        else:
            kept.append(char)
    return "".join(kept)


def scrub(text: str | None, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Apply every rule to `text`. `None` scrubs to the empty string.

    Repeated until nothing changes, because the cut can itself create a match:
    `a/b.abcdefghij` is not a path (that is too long for an extension), but cut
    to `a/b.abcd` it ends in one and is. The server re-applies these rules, so
    the result has to be a fixed point or the two sides would disagree about
    the same text.
    """
    current = text or ""
    for _ in range(8):
        cleaned = _scrub_once(current, max_length)
        if cleaned == current:
            break
        current = cleaned
    return current


def _scrub_once(text: str, max_length: int) -> str:
    if not text:
        return ""
    cleaned = _remove_control_characters(text)
    cleaned = _URL.sub(" ", cleaned)
    cleaned = " ".join(_shorten_path(token) for token in cleaned.split(" "))
    cleaned = _EMAIL.sub(EMAIL_PLACEHOLDER, cleaned)
    cleaned = _PHONE.sub(NUMBER_PLACEHOLDER, cleaned)
    cleaned = _LONG_DIGITS.sub(NUMBER_PLACEHOLDER, cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned[:max_length].rstrip()


def scrub_other(text: str | None) -> str:
    """The rules for `use_case_other`, which is allowed a longer answer."""
    return scrub(text, MAX_OTHER_LENGTH)
