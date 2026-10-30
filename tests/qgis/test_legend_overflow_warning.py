"""A template legend that leaves entries out says so beside the page, with the
ways out that already exist, and the warning follows the layout.

Built from COPIES of the demo projects (never the originals).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from test_cartograph_panel_widgets import _settle
from test_cartograph_panel_widgets import pane as pane

qgis_core = pytest.importorskip("qgis.core")

# The demo projects are not in the repository: point Q2W_DEMO_MAPS at the folder
# that holds them (see CONTRIBUTING); the module is skipped without it.
DEMOS = Path(os.environ.get("Q2W_DEMO_MAPS", ""))
CASES = [
    # project folder, project file, template, orientation
    ("carto-grand-canyon", "carto-grand-canyon-dense.qgz", "dashboard", "landscape"),
    ("carto-grand-canyon", "carto-grand-canyon-standard.qgz", "dashboard", "landscape"),
    ("volcanoes", "volcanoes.qgz", "locator", "portrait"),
]

pytestmark = pytest.mark.skipif(
    not os.environ.get("Q2W_DEMO_MAPS") or not DEMOS.exists(),
    reason="set Q2W_DEMO_MAPS to the demo-maps folder to run these cases",
)


def _build(project, tmp_path, folder, name, template, orientation):
    from qgis.core import QgsRectangle

    from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view

    copy = tmp_path / folder
    shutil.copytree(DEMOS / folder, copy, ignore=shutil.ignore_patterns("*.png"))
    assert project.read(str(copy / name))
    layers = [n.layer() for n in project.layerTreeRoot().findLayers() if n.layer()]
    view = project.viewSettings().defaultViewExtent()
    return cartograph_from_view(
        QgsRectangle(view),
        view.crs(),
        layers,
        project,
        template_id=template,
        page_size="A4",
        orientation=orientation,
    )


def _pane(qgis_app, layout):
    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    widget = PagePane()
    widget.resize(900, 700)
    widget.show()
    widget.show_layout(layout)
    _settle(qgis_app)
    return widget


def _legend(layout):
    from qgis.core import QgsLayoutItemLegend

    return next(i for i in layout.items() if isinstance(i, QgsLayoutItemLegend))


@pytest.mark.parametrize(("folder", "name", "template", "orientation"), CASES)
def test_the_warning_names_how_many_entries_do_not_fit(
    qgis_app, project, tmp_path, folder, name, template, orientation
) -> None:
    built = _build(project, tmp_path, folder, name, template, orientation)
    pane = _pane(qgis_app, built.layout)
    try:
        if built.legend_left_out == 0:
            assert pane.legend_warning.isHidden()
            return
        assert not pane.legend_warning.isHidden()
        text = pane.legend_warning_label.text()
        assert text == (
            "1 legend entry does not fit."
            if built.legend_left_out == 1
            else f"{built.legend_left_out} legend entries do not fit."
        )
    finally:
        pane.close()


def test_the_dense_landscape_page_is_the_crowded_case(
    qgis_app, project, tmp_path
) -> None:
    """The user's run: dense Signal landscape leaves 9, standard 2."""
    dense = _build(project, tmp_path, *CASES[0])
    assert dense.legend_left_out == 9
    standard = _build(project, tmp_path / "s", *CASES[1])
    assert standard.legend_left_out == 2


def test_a_warning_follows_the_legend_and_goes_when_entries_are_back(
    qgis_app, project, tmp_path
) -> None:
    from nika_onlymap_exporter.core import legend_edit as le

    built = _build(project, tmp_path, *CASES[0])
    pane = _pane(qgis_app, built.layout)
    try:
        assert not pane.legend_warning.isHidden()
        legend = _legend(built.layout)
        before = built.legend_left_out
        # Putting entries back is the user's act; the count then shrinks.
        le.restore_removed(built.layout, legend, project)
        _settle(qgis_app, 0.5)
        assert pane.legend_warning.isHidden()
        assert before > 0
    finally:
        pane.close()


def test_a_layout_with_a_fitting_legend_shows_no_warning(
    qgis_app,
    project,
    pane,
) -> None:
    assert pane.legend_warning.isHidden()


def test_the_actions_reach_the_existing_controls(qgis_app, project, tmp_path) -> None:
    built = _build(project, tmp_path, *CASES[0])
    pane = _pane(qgis_app, built.layout)
    try:
        asked = []
        pane.edit_requested.connect(lambda: asked.append("edit"))
        pane.new_layout_requested.connect(lambda: asked.append("new"))
        pane.adjust_legend_button.click()
        assert asked == ["edit"]
        assert built.layout.selectedLayoutItems() == [_legend(built.layout)]
        pane.other_layout_button.click()
        assert asked == ["edit", "new"]
    finally:
        pane.close()


def test_the_warning_and_its_buttons_keep_the_page_visible(
    qgis_app, project, tmp_path
) -> None:
    built = _build(project, tmp_path, *CASES[0])
    pane = _pane(qgis_app, built.layout)
    try:
        assert pane.preview.rect().height() > 200
        box = pane.preview.view.mapFromScene(pane.preview._current_page_rect())
        assert pane.preview.view.viewport().rect().contains(box.boundingRect())
    finally:
        pane.close()
