"""Per-layout settings are filed under a stable layout id, not the name.

A rename (in the dialog, in QGIS, or while the dialog is closed) cannot detach
a layout's save file, frame modes, print or page settings; a project saved by
an earlier version, which filed them under the name, still loads.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsPrintLayout, QgsProject

from nika_onlymap_exporter.core.layout_identity import (
    KEY_PREFIX,
    LAYOUT_ID_PROPERTY,
    LayoutKeys,
)
from nika_onlymap_exporter.core.settings import (
    SCOPE,
    DialogState,
    FrameChoice,
    PageChoice,
    load_state,
    save_state,
)


def _layout(project: QgsProject, name: str) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    project.layoutManager().addLayout(layout)
    return layout


def _filled(name: str) -> DialogState:
    state = DialogState()
    state.cartograph_layout = name
    state.cartograph_files[name] = "/tmp/out.html"
    state.frame_choices(name)["map-1"] = FrameChoice(mode="static", dpi=300)
    state.cartograph_print[name] = '{"bleed": "3mm"}'
    state.page_choice(name).title = "Kept title"
    return state


class TestLayoutIdentity:
    def test_saved_entries_are_filed_under_the_layout_id(self, qgis_app) -> None:
        project = QgsProject()
        layout = _layout(project, "Survey")
        save_state(project, _filled("Survey"))
        key = layout.customProperty(LAYOUT_ID_PROPERTY)
        assert key.startswith(KEY_PREFIX)
        files = json.loads(project.readEntry(SCOPE, "cartographFiles")[0])
        assert files == {key: "/tmp/out.html"}
        assert project.readEntry(SCOPE, "cartographLayout")[0] == key

    def test_a_rename_while_the_dialog_is_closed_keeps_everything(
        self, qgis_app
    ) -> None:
        project = QgsProject()
        layout = _layout(project, "Survey")
        save_state(project, _filled("Survey"))
        layout.setName("Survey, final")
        state = load_state(project)
        assert state.cartograph_layout == "Survey, final"
        assert state.cartograph_files == {"Survey, final": "/tmp/out.html"}
        assert state.frame_choices("Survey, final")["map-1"].dpi == 300
        assert state.cartograph_print == {"Survey, final": '{"bleed": "3mm"}'}
        assert state.page_choice("Survey, final").title == "Kept title"

    def test_a_project_saved_under_names_still_loads(self, qgis_app) -> None:
        project = QgsProject()
        _layout(project, "Survey")
        project.writeEntry(SCOPE, "cartographLayout", "Survey")
        project.writeEntry(SCOPE, "cartographFiles", json.dumps({"Survey": "/a.html"}))
        project.writeEntry(
            SCOPE,
            "cartographPage",
            json.dumps({"Survey": PageChoice(title="Old").to_dict()}),
        )
        state = load_state(project)
        assert state.cartograph_layout == "Survey"
        assert state.cartograph_files == {"Survey": "/a.html"}
        assert state.page_choice("Survey").title == "Old"
        # And the next save moves it onto the id.
        save_state(project, state)
        files = json.loads(project.readEntry(SCOPE, "cartographFiles")[0])
        assert next(iter(files)).startswith(KEY_PREFIX)

    def test_an_id_entry_wins_over_a_stale_name_entry(self, qgis_app) -> None:
        project = QgsProject()
        layout = _layout(project, "Survey")
        key = LayoutKeys(project).key_for("Survey")
        project.writeEntry(
            SCOPE, "cartographFiles", json.dumps({"Survey": "/old", key: "/new"})
        )
        assert load_state(project).cartograph_files == {"Survey": "/new"}
        assert layout.customProperty(LAYOUT_ID_PROPERTY) == key

    def test_a_duplicated_layout_gets_its_own_id(self, qgis_app) -> None:
        project = QgsProject()
        first = _layout(project, "A")
        second = _layout(project, "B")
        key = LayoutKeys(project).key_for("A")
        second.setCustomProperty(LAYOUT_ID_PROPERTY, key)  # what a duplicate copies
        keys = LayoutKeys(project)
        assert keys.key_for("A") != keys.key_for("B")
        assert first.customProperty(LAYOUT_ID_PROPERTY) == key

    def test_a_layout_without_an_entry_is_not_marked(self, qgis_app) -> None:
        project = QgsProject()
        other = _layout(project, "Other")
        _layout(project, "Survey")
        save_state(project, _filled("Survey"))
        assert not other.customProperty(LAYOUT_ID_PROPERTY)

    def test_an_unknown_layout_name_is_kept_as_a_name(self, qgis_app) -> None:
        project = QgsProject()
        save_state(project, _filled("Gone"))
        assert json.loads(project.readEntry(SCOPE, "cartographFiles")[0]) == {
            "Gone": "/tmp/out.html"
        }
