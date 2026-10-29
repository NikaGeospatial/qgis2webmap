"""Map frames > Go to location...: the dialog, driven from the real ⋯ menu.

Every dialog test opens the dialog the way a user does - the Cartograph panel's
frame card, its ⋯ menu, the "Go to location…" row - and then types, clicks and
presses keys in the real widgets. What is asserted is what the user ends up
with: where the frame looks, what the undo stack and the document did, what the
dialog said.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from go_to_location_fixtures import (
    GO_TO_LOCATION,
    build_world,
    go_action,
    open_dialog,
)
from test_cartograph_dialog import (
    _choose_cartograph,
    _dialog,
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)


@pytest.fixture(autouse=True)
def fresh_session():
    from nika_onlymap_exporter.ui.cartograph import go_to_location_dialog

    go_to_location_dialog.reset_session()
    yield
    go_to_location_dialog.reset_session()


class Setup:
    """The Cartograph panel on the Canyon-like world."""

    def __init__(self, project, rotation: float = 0.0) -> None:
        self.world = build_world(project, rotation)
        self.dialog = _dialog(project)
        self.dialog.show()
        _choose_cartograph(self.dialog)
        self.cartograph = self.dialog._cartograph
        self.cartograph._autosave_timer.stop()
        self.cartograph.open_section("frames")
        self.project = project

    @property
    def stack(self):
        return self.world.layout.undoStack().stack()

    def card(self, item):
        return self.cartograph.frames_list.card(item.uuid())

    def action(self, item):
        return go_action(self.card(item))

    def open(self, script, item=None):
        return open_dialog(self.action(item or self.world.main), script)

    def close(self) -> None:
        self.dialog.close()


@pytest.fixture
def canyon(qgis_app, project):
    made = Setup(project)
    yield made
    made.close()


def _geometry_state(item):
    return (
        item.pos().x(),
        item.pos().y(),
        item.rect().width(),
        item.rect().height(),
        item.rotation(),
    )


class TestTheMenuRow:
    def test_the_row_replaces_locate_on_page(self, canyon) -> None:
        menu = canyon.card(canyon.world.main).actions_button.menu()
        texts = [a.text() for a in menu.actions() if a.isVisible()]
        assert GO_TO_LOCATION in texts
        assert "Locate on page" not in texts
        # In the place the old row held: second, after Use canvas extent.
        assert texts[:2] == ["Use canvas extent", GO_TO_LOCATION]
        assert go_action(canyon.card(canyon.world.main)).toolTip() == (
            "Center this map on coordinates or find a feature."
        )

    def test_the_dialog_names_the_map_of_the_card_it_came_from(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            seen["title"] = dialog.windowTitle()
            seen["context"] = dialog.context_label.text()
            seen["tabs"] = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]

        canyon.open(script, canyon.world.inset)
        assert seen == {
            "title": "Go to location",
            "context": "Map: Locator",
            "tabs": ["Coordinates", "Feature"],
        }
