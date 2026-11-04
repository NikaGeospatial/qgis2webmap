"""The pieces the cartograph build's caches are made of.

Autosave rebuilds a cartograph after every edit, most of which change nothing
a frame draws, so `core/cartograph_export.py` keeps what is slow to make -- a
static frame's picture, a raster's conversion, a live frame's layer read --
and reuses it while it is still right. Three caches, one policy, so the
policy lives here:

- `BoundedCache` holds the entries: at most so many, and at most so many
  bytes, least recently used out first. A single result larger than the
  whole budget is not kept at all, since holding it would push out
  everything else and still not fit the next one.
- `LayerGenerations` counts each watched layer's changes from its own
  signals, for what a layer's values do not show (an edit that moves no
  extent and no count).
- `ProjectFollower` empties a cache when a project it holds entries of is
  cleared (closed, or another one opened) or deleted, and never keeps one
  alive itself.
- `value_digest` turns a plain-value snapshot into a key, and refuses
  (`None`) when part of it is something it cannot vouch for, so an unknown
  value means "do not cache", never "assume unchanged".

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import hashlib
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from pathlib import PurePath
from typing import Generic, TypeVar

from qgis.core import QgsMapLayer, QgsProject

V = TypeVar("V")


class BoundedCache(Generic[V]):
    """Entries by slot, each valid for one key, within an entry and byte limit.

    A slot is where a result belongs (a frame of a layout); the key is what
    it was made from. Storing a slot replaces what it held, so an edited
    frame costs one entry, not one per edit. `get` answers only for the key
    the slot holds and marks the entry as just used; eviction drops the least
    recently used first. Safe to call from any thread: a build reads and
    fills it on a worker while the GUI thread may clear it.
    """

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, tuple[Hashable, V, int]] = OrderedDict()
        self._bytes = 0

    def get(self, slot: Hashable, key: Hashable) -> V | None:
        with self._lock:
            held = self._entries.get(slot)
            if held is None or held[0] != key:
                return None
            self._entries.move_to_end(slot)
            return held[1]

    def put(self, slot: Hashable, key: Hashable, value: V, size: int) -> None:
        """Keep `value` (about `size` bytes) for `slot`, unless it alone is
        over the byte budget; the slot's old entry goes either way."""
        with self._lock:
            old = self._entries.pop(slot, None)
            if old is not None:
                self._bytes -= old[2]
            if size > self.max_bytes:
                return
            self._entries[slot] = (key, value, size)
            self._bytes += size
            while self._entries and (
                len(self._entries) > self.max_entries or self._bytes > self.max_bytes
            ):
                _slot, (_key, _value, dropped) = self._entries.popitem(last=False)
                self._bytes -= dropped

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    @property
    def total_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)


class LayerGenerations:
    """A change counter per layer, bumped by the layer's own signals.

    `signals_of` names the signals after which a layer may give a different
    result. A layer is watched from its first `watch`; a layer being deleted
    is bumped one last time and forgotten. GUI thread only (signals)."""

    def __init__(self, signals_of: Callable[[QgsMapLayer], list]) -> None:
        self._signals_of = signals_of
        self._generations: dict[str, int] = {}
        self._watched: dict[
            str, tuple[QgsMapLayer, Callable[..., None], Callable[..., None]]
        ] = {}

    @property
    def generations(self) -> Mapping[str, int]:
        return self._generations

    def get(self, layer_id: str) -> int:
        return self._generations.get(layer_id, 0)

    def watch(self, layer: QgsMapLayer) -> None:
        layer_id = layer.id()
        if layer_id in self._watched:
            return

        def bump(*_args: object) -> None:
            self._generations[layer_id] = self._generations.get(layer_id, 0) + 1

        def forget(*_args: object) -> None:
            bump()
            self._watched.pop(layer_id, None)

        for signal in self._signals_of(layer):
            signal.connect(bump)
        layer.willBeDeleted.connect(forget)
        self._watched[layer_id] = (layer, bump, forget)

    def clear(self) -> None:
        """Stop listening to every layer and forget every count."""
        for layer, bump, forget in self._watched.values():
            # A layer QGIS already deleted (a project closing deletes its
            # layers before `cleared`) raises on any attribute, signals
            # included; its connections went with it.
            with contextlib.suppress(TypeError, RuntimeError):
                connections = [(signal, bump) for signal in self._signals_of(layer)]
                connections.append((layer.willBeDeleted, forget))
                for signal, slot in connections:
                    with contextlib.suppress(TypeError, RuntimeError):
                        signal.disconnect(slot)
        self._watched.clear()
        self._generations.clear()


class ProjectFollower:
    """Calls `on_cleared` once a project followed is cleared or deleted: what
    a cache held for it is worth nothing to the next project, and may be
    large. `stop` disconnects, so a cache that was emptied follows again only
    the projects it takes new entries from. GUI thread only (signals).

    Projects are held weakly. A project a script made itself (`QgsProject()`,
    exported through Processing) is Python's to delete, and a strong reference
    here kept it alive until the interpreter shut down - after QGIS itself,
    which crashed on the way out."""

    def __init__(self, on_cleared: Callable[[], None]) -> None:
        self._on_cleared = on_cleared
        self._followed: dict[int, weakref.ref[QgsProject]] = {}

    def follow(self, project: QgsProject) -> None:
        known = self._followed.get(id(project))
        if known is not None and known() is project:
            return
        project.cleared.connect(self._cleared)
        project.destroyed.connect(self._cleared)
        self._followed[id(project)] = weakref.ref(project)

    def stop(self) -> None:
        for reference in self._followed.values():
            project = reference()
            if project is None:
                continue
            # A project already deleted took its connections with it.
            with contextlib.suppress(TypeError, RuntimeError):
                project.cleared.disconnect(self._cleared)
            with contextlib.suppress(TypeError, RuntimeError):
                project.destroyed.disconnect(self._cleared)
        self._followed.clear()

    def _cleared(self) -> None:
        self._on_cleared()


class UnvouchedValueError(Exception):
    """Raised inside `value_digest` for a value it cannot encode faithfully."""


#: `special(value, add)` encodes a value the plain rules do not know (a QGIS
#: value type) through `add`, and answers True; False leaves it to the plain
#: rules, which refuse anything unknown. It may raise `UnvouchedValueError` itself.
Special = Callable[[object, Callable[[object], None]], bool]


def value_digest(value: object, special: Special | None = None) -> str | None:
    """A digest of `value`'s content, or None when part of it cannot be
    vouched for.

    Every part is encoded with its type: scalars by `repr`, enums by class
    and name, dataclasses field by field, sequences and mappings in their
    own order (an order that changes the output must change the key), sets
    sorted. `special` sees every non-scalar part first, so it can encode a
    known type its own way; any other type makes the whole digest None, so
    a value whose content cannot be seen is never mistaken for an unchanged
    one.
    """
    digest = hashlib.sha256()

    def text(part: str) -> None:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")

    def add(part: object) -> None:
        if part is None or isinstance(part, (bool, int, float, str)):
            text(f"{type(part).__name__}:{part!r}")
        elif isinstance(part, bytes):
            text(f"bytes:{len(part)}")
            digest.update(part)
        elif special is not None and special(part, add):
            return
        elif isinstance(part, enum.Enum):
            text(f"enum:{type(part).__qualname__}.{part.name}")
        elif dataclasses.is_dataclass(part) and not isinstance(part, type):
            text(f"dataclass:{type(part).__module__}.{type(part).__qualname__}")
            for field in dataclasses.fields(part):
                text(field.name)
                add(getattr(part, field.name))
        elif isinstance(part, (tuple, list)):
            text(f"{type(part).__name__}:{len(part)}")
            for item in part:
                add(item)
        elif isinstance(part, dict):
            text(f"dict:{len(part)}")
            for item_key, item in part.items():
                add(item_key)
                add(item)
        elif isinstance(part, (set, frozenset)):
            text(f"set:{len(part)}")
            for item_digest in sorted(
                value_digest(item, special) or "" for item in part
            ):
                if not item_digest:
                    raise UnvouchedValueError(type(part).__name__)
                text(item_digest)
        elif isinstance(part, PurePath):
            text(f"path:{part}")
        else:
            raise UnvouchedValueError(type(part).__qualname__)

    try:
        add(value)
    except UnvouchedValueError:
        return None
    return digest.hexdigest()
