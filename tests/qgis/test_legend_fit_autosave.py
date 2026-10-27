"""The legend fit as the dialog runs it: on every autosave, in the layout the
preview shows, before the HTML is made, and without looping or fighting the
user's undo.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import types

import pytest
from legend_fit_fixtures import (
    FitWorld,
    build_fit_world,
    page_legend_box,
    rect_mm,
    runtime,
)
from test_cartograph_dialog import FakeIface, _choose_cartograph, _wait_for

from nika_onlymap_exporter.core import legend_edit as le


@pytest.fixture
def real_runtime_stub(monkeypatch):
    """The dialog's runtime check answers with a stub bundle; everything after
    it is the real export."""
    import nika_onlymap_exporter.ui.cartograph.controller as controller_module

    class _Provider:
        version = "0.12.0"

        def load(self):
            return runtime()

    monkeypatch.setattr(
        controller_module, "ensure_runtime", lambda parent=None: _Provider()
    )
    return types.SimpleNamespace()


@pytest.fixture
def world(qgis_app, project) -> FitWorld:
    """Four of the eight layers are listed; the tests list the others, one
    by one, as the user does."""
    built = build_fit_world(project, legend_rect=(10, 150, 190, 25))
    for layer in built.layers[4:]:
        le.set_layer_listed(None, built.legend, project, layer.id(), False)
    return built


@pytest.fixture
def shown(qgis_app, project, world, real_runtime_stub, tmp_path):
    """The dialog on Cartograph with the fit world's layout selected and its
    first autosave landed. Returns (dialog, controller, target)."""
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    dialog = MainDialog(FakeIface(), None)
    _choose_cartograph(dialog)
    cartograph = dialog._cartograph
    target = tmp_path / "fit.html"
    cartograph.file_edit.setText(str(target))
    cartograph._on_file_edited(str(target))
    assert _wait_for(qgis_app, lambda: target.exists() and cartograph._job is None)
    yield dialog, cartograph, target
    dialog.close()


def _settled(qgis_app, cartograph) -> int:
    """Wait out any debounce and build, and return the builds so far."""
    assert _wait_for(
        qgis_app,
        lambda: not cartograph._autosave_timer.isActive() and cartograph._job is None,
        timeout=20.0,
    )
    return cartograph._builds_started


class TestAutosaveFitsTheLayout:
    def test_the_first_save_has_already_fitted_the_layout_and_the_page(
        self, qgis_app, world, shown
    ) -> None:
        _dialog, cartograph, target = shown
        builds = _settled(qgis_app, cartograph)
        fitted = rect_mm(world.legend)
        assert fitted[3] > 25 + 5  # it was 25 mm tall
        assert page_legend_box(target.read_text(encoding="utf-8")) == pytest.approx(
            fitted, abs=0.01
        )
        # the fit is not an edit: no second build follows it
        qgis_app.processEvents()
        assert _settled(qgis_app, cartograph) == builds

    def test_adding_a_layer_to_the_legend_grows_the_box_and_the_saved_page(
        self, qgis_app, project, world, shown
    ) -> None:
        _dialog, cartograph, target = shown
        last = world.layers[-1].id()
        smaller = rect_mm(world.legend)
        stack = world.layout.undoStack().stack()
        le.set_layer_listed(world.layout, world.legend, project, last, True)
        steps = stack.count()
        builds = _settled(qgis_app, cartograph)
        grown = rect_mm(world.legend)
        assert grown[3] > smaller[3]
        assert page_legend_box(target.read_text(encoding="utf-8")) == pytest.approx(
            grown, abs=0.01
        )
        # the user's step is the only one; and the layout stays settled
        assert stack.count() == steps
        qgis_app.processEvents()
        assert _settled(qgis_app, cartograph) == builds

    def test_undo_and_redo_stay_the_users_through_the_autosaves(
        self, qgis_app, project, world, shown
    ) -> None:
        _dialog, cartograph, target = shown
        stack = world.layout.undoStack().stack()
        last = world.layers[-1].id()
        before = rect_mm(world.legend)
        le.set_layer_listed(world.layout, world.legend, project, last, True)
        _settled(qgis_app, cartograph)
        after = rect_mm(world.legend)
        steps = stack.count()

        stack.undo()
        _settled(qgis_app, cartograph)
        assert stack.count() == steps and stack.canRedo()
        assert rect_mm(world.legend) == pytest.approx(before, abs=0.01)
        assert page_legend_box(target.read_text(encoding="utf-8")) == pytest.approx(
            before, abs=0.01
        )
        assert stack.index() == steps - 1

        stack.redo()
        _settled(qgis_app, cartograph)
        assert rect_mm(world.legend) == pytest.approx(after, abs=0.01)
        assert stack.count() == steps
