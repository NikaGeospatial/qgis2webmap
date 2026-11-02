"""JSON-shaped data that cannot be changed in place.

A layer's GeoJSON is serialised once and the text kept beside it
(`ExportLayer.geojson_text`), and a cartograph's live-frame cache hands the
same layer to build after build. Both are right only while the data never
changes under them, so a built layer holds its GeoJSON as `ReadOnlyDict` and
`ReadOnlyList` all the way down: every in-place change raises `TypeError`
instead of leaving the text, or a later build, behind.

They are `dict` and `list` subclasses, so everything that reads the data -
`json.dumps`, equality with plain dicts and lists, `isinstance`, `{**d}`,
iteration, indexing, slicing - reads it as before and gets the same answer.
A copy (`dict(d)`, `list(l)`, `copy.copy`, `copy.deepcopy`, `{**d}`, a slice)
is a plain one, free to change.

Pure Python, like `export_ir`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import copy
import gc
import threading
from collections.abc import Iterator
from typing import Any, NoReturn

_SCALARS = frozenset({str, int, float, bool, type(None)})


def _refuse(self: object, *_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError(
        f"{type(self).__name__} is read-only: change a copy, and build a new "
        "layer from it"
    )


class ReadOnlyDict(dict[Any, Any]):
    """A `dict` whose items cannot be changed once it is built."""

    __slots__ = ()

    __setitem__ = __delitem__ = __ior__ = _refuse
    clear = pop = popitem = setdefault = update = _refuse

    def __copy__(self) -> dict[Any, Any]:
        return dict(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[Any, Any]:
        return copy.deepcopy(dict(self), memo)

    def __reduce__(self) -> tuple[type, tuple[dict[Any, Any]]]:
        return dict, (dict(self),)


class ReadOnlyList(list[Any]):
    """A `list` whose items cannot be changed once it is built."""

    __slots__ = ()

    __setitem__ = __delitem__ = __iadd__ = __imul__ = _refuse
    append = extend = insert = pop = remove = clear = sort = reverse = _refuse

    def __copy__(self) -> list[Any]:
        return list(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        return copy.deepcopy(list(self), memo)

    def __reduce__(self) -> tuple[type, tuple[list[Any]]]:
        return list, (list(self),)


def read_only(value: Any) -> Any:
    """`value` with every dict and list in it made read-only.

    Read-only parts are kept as they are, not copied again, so re-building a
    layer from another's data (`dataclasses.replace`) costs nothing. Scalars
    and anything that is not a dict, list or tuple are kept as they are.
    """
    if type(value) in _SCALARS:
        return value
    with _collection_paused():
        return _read_only(value)


def _read_only(value: Any) -> Any:
    kind = type(value)
    if kind is ReadOnlyDict or kind is ReadOnlyList:
        return value
    if isinstance(value, dict):
        return ReadOnlyDict(
            {
                key: item if type(item) in _SCALARS else _read_only(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list):
        # Most lists are a position, numbers only: copied in one call.
        for item in value:
            if type(item) not in _SCALARS:
                break
        else:
            return ReadOnlyList(value)
        return ReadOnlyList(
            [item if type(item) in _SCALARS else _read_only(item) for item in value]
        )
    if kind is tuple:
        return tuple(
            item if type(item) in _SCALARS else _read_only(item) for item in value
        )
    return value


# Copying a large layer makes a million small lists at once, every one of
# them alive, and the cycle collector rescans the growing heap over and over
# while it happens: with it paused the copy takes about a third of the time.
# Paused once however many threads copy at the same time, and resumed only if
# it was running before the first of them.
_pause_lock = threading.Lock()
_pauses = 0
_resume = False


@contextlib.contextmanager
def _collection_paused() -> Iterator[None]:
    global _pauses, _resume
    with _pause_lock:
        if _pauses == 0:
            _resume = gc.isenabled()
            gc.disable()
        _pauses += 1
    try:
        yield
    finally:
        with _pause_lock:
            _pauses -= 1
            if _pauses == 0 and _resume:
                gc.enable()
