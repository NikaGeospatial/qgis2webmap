"""Go to location on the real Grand Canyon demo project (a COPY of it).

The demo's frames are drawn in UTM 12N while its layers are stored in WGS 84.
The project is not part of the repository, so this runs only when
`Q2W_CANYON_COPY` names a copy of `demo-maps/carto-grand-canyon` (with its
`data` folder); the project is never opened from its original place.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from go_to_location_fixtures import (
    choose_layer,
    frame_centre,
    go_action,
    open_dialog,
    search,
    wgs_to_frame,
)
from test_cartograph_dialog import (
    _choose_cartograph,
    _dialog,
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)

COPY = os.environ.get("Q2W_CANYON_COPY", "")

pytestmark = pytest.mark.skipif(
    not COPY or not (Path(COPY) / "carto-grand-canyon.qgz").exists(),
    reason="Q2W_CANYON_COPY does not name a copy of the Grand Canyon demo project",
)


@pytest.fixture
def canyon_project(qgis_app, project):
    from qgis.core import QgsLayoutItemMap

    assert project.read(str(Path(COPY) / "carto-grand-canyon.qgz"))
    layout = project.layoutManager().layoutByName(
        "02 · South Rim detail + locator · A3"
    )
    assert layout is not None
    dialog = _dialog(project)
    dialog.show()
    _choose_cartograph(dialog)
    cartograph = dialog._cartograph
    cartograph._select_layout(layout.name())
    cartograph._autosave_timer.stop()
    cartograph.open_section("frames")
    maps = {i.id(): i for i in layout.items() if isinstance(i, QgsLayoutItemMap)}
    yield project, layout, cartograph, maps
    cartograph._autosave_timer.stop()
    dialog.close()


def _open(cartograph, item, script):
    return open_dialog(go_action(cartograph.frames_list.card(item.uuid())), script)


def test_latitude_longitude_into_the_projected_canyon_frame(canyon_project) -> None:
    _project, layout, cartograph, maps = canyon_project
    main, locator = maps["main"], maps["locator"]
    assert main.crs().authid() == "EPSG:32612"
    scale, locator_before = main.scale(), locator.extent().toString()
    index = layout.undoStack().stack().index()

    def script(dialog) -> None:
        from go_to_location_fixtures import fill_coordinates

        fill_coordinates(dialog, "36.10", "-112.10")
        dialog.go_button.click()

    _open(cartograph, main, script)
    expected = wgs_to_frame(-112.10, 36.10)
    centre = frame_centre(main)
    assert math.hypot(centre.x() - expected.x(), centre.y() - expected.y()) < 0.01
    assert 395_000 < centre.x() < 410_000 and 3_990_000 < centre.y() < 4_005_000
    assert math.isclose(main.scale(), scale, rel_tol=1e-9)
    assert locator.extent().toString() == locator_before
    assert layout.undoStack().stack().index() == index + 1


def test_a_named_viewpoint_is_found_and_gone_to(canyon_project) -> None:
    project, _layout, cartograph, maps = canyon_project
    main = maps["main"]
    viewpoints = project.mapLayersByName("Viewpoints")[0]
    first = next(iter(viewpoints.getFeatures()))
    name = first["name"]
    point = first.geometry().asPoint()
    seen = {}

    def script(dialog) -> None:
        choose_layer(dialog, viewpoints)
        search(dialog, name)
        seen["rows"] = dialog.results.count()
        dialog.results.setCurrentRow(0)
        dialog.go_button.click()

    _open(cartograph, main, script)
    assert seen["rows"] >= 1
    # Several viewpoints can share a name: the one gone to is one of them.
    names = [f for f in viewpoints.getFeatures() if f["name"] == name]
    spots = [
        wgs_to_frame(f.geometry().asPoint().x(), f.geometry().asPoint().y())
        for f in names
    ]
    centre = frame_centre(main)
    assert min(math.hypot(centre.x() - s.x(), centre.y() - s.y()) for s in spots) < 0.05
    assert point is not None
