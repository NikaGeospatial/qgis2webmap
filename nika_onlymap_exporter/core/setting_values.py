"""Reading saved values defensively: one place for bool, number and enum text.

Saved settings come from a project file or a user's settings store, where a
value may be a real JSON type, text, or something hand-edited or written by a
different plugin version. Python's own `bool("false")` is True, so every
reader goes through these helpers instead. A value that cannot be understood
yields the caller's default, never an exception, so one bad value cannot stop
the rest of the settings loading.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})

EnumT = TypeVar("EnumT", bound=Enum)


def parse_optional_bool(raw: Any) -> bool | None:
    """A real bool, 0/1, or true/false/yes/no/on/off text; else None."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None


def parse_bool(raw: Any, default: bool) -> bool:
    """`parse_optional_bool`, with `default` for anything not understood."""
    parsed = parse_optional_bool(raw)
    return default if parsed is None else parsed


def parse_int(raw: Any, default: int) -> int:
    """A whole number (not a bool); numeric text and whole floats accepted."""
    if isinstance(raw, bool):
        return default
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    if number != int(number):
        return default
    return int(number)


def parse_float(raw: Any, default: float) -> float:
    """A finite number (not a bool); numeric text accepted."""
    if isinstance(raw, bool):
        return default
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def parse_enum(enum_type: type[EnumT], raw: Any, default: EnumT) -> EnumT:
    """The member whose value is `raw`; else `default`."""
    try:
        return enum_type(raw)
    except (ValueError, TypeError):
        return default
