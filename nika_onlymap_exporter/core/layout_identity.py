"""A stable identity for each print layout.

QGIS print layouts have no id, only a name, and the name is the one thing a
user changes freely. Everything the plugin keeps per layout (its save file,
frame modes, print and page settings) is therefore filed under an id the
plugin writes into the layout itself, as a custom property that travels with
the `.qgz`. A rename, in the dialog or in QGIS, cannot detach any of it.

A project saved by an earlier version filed these under the layout's NAME.
`LayoutKeys.name_for` / `key_for` accept both forms, so those projects load
unchanged and are moved onto ids the next time they are saved.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsProject

#: The custom property on a `QgsPrintLayout` that holds its id.
LAYOUT_ID_PROPERTY = "qgis2webmap/layout_id"

#: What tells an id from a legacy layout name in a persisted key.
KEY_PREFIX = "lid:"


def _new_key() -> str:
    return f"{KEY_PREFIX}{uuid.uuid4().hex}"


class LayoutKeys:
    """The ids of one project's print layouts, as they are right now.

    Built once per save or load. A layout that is asked for a key and has none
    gets one written into it; a copy of a layout (QGIS "Duplicate layout"
    copies custom properties) that carries its original's id is given a new
    one, so two layouts never share a key.
    """

    def __init__(self, project: QgsProject) -> None:
        self._layouts = list(project.layoutManager().printLayouts())
        self._by_key: dict[str, object] = {}
        for layout in self._layouts:
            key = layout.customProperty(LAYOUT_ID_PROPERTY)
            if (
                isinstance(key, str)
                and key.startswith(KEY_PREFIX)
                and key not in self._by_key
            ):
                self._by_key[key] = layout

    def _ensure(self, layout: object) -> str:
        key = layout.customProperty(LAYOUT_ID_PROPERTY)  # type: ignore[attr-defined]
        if isinstance(key, str) and self._by_key.get(key) is layout:
            return key
        key = _new_key()
        layout.setCustomProperty(LAYOUT_ID_PROPERTY, key)  # type: ignore[attr-defined]
        self._by_key[key] = layout
        return key

    def key_for(self, name: str) -> str:
        """The persisted key of the layout called `name`: its id (written into
        it if it has none), or `name` itself when no layout has that name."""
        for layout in self._layouts:
            if layout.name() == name:
                return self._ensure(layout)
        return name

    def name_for(self, key: str) -> str:
        """The name the layout filed under `key` has now. A legacy name key,
        or an id whose layout is gone, comes back as it is."""
        layout = self._by_key.get(key) if key.startswith(KEY_PREFIX) else None
        return layout.name() if layout is not None else key  # type: ignore[attr-defined]

    def keys(self, names: Iterable[str]) -> dict[str, str]:
        return {name: self.key_for(name) for name in names}


def layout_id(project: QgsProject, name: str) -> str:
    """The id of the layout called `name` (written into it if it has none)."""
    return LayoutKeys(project).key_for(name)


def existing_key(layout: object) -> str | None:
    """The id already written into `layout`, or None - never creates one."""
    key = layout.customProperty(LAYOUT_ID_PROPERTY)  # type: ignore[attr-defined]
    return key if isinstance(key, str) and key.startswith(KEY_PREFIX) else None


def layout_key(project: QgsProject, layout: object) -> str:
    """The id of `layout` (written into it if it has none)."""
    return LayoutKeys(project).key_for(layout.name())  # type: ignore[attr-defined]
