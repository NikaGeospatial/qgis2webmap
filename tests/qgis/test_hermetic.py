"""The test run stays in its temporary directory (`tests/hermetic.py`).

A cartograph autosaves as soon as the dialog shows a layout, so a run whose
save folder or profile leaked would write into the developer's own Documents
or QGIS profile - which a probe without this isolation once did.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qgis.core")


def _inside(path: Path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def test_every_default_save_location_is_inside_the_test_area(
    qgis_app, tmp_path_factory
) -> None:
    from qgis.core import QgsApplication

    from nika_onlymap_exporter.core.save_folders import (
        cartographs_folder,
        default_save_root,
        save_root,
        set_save_root,
        web_maps_folder,
    )
    from nika_onlymap_exporter.ui.cartograph.controller import (
        default_cartograph_path,
    )

    area = tmp_path_factory.getbasetemp()
    assert _inside(QgsApplication.qgisSettingsDirPath(), area)
    for path in (
        save_root(),
        web_maps_folder(),
        cartographs_folder(),
        default_cartograph_path("", "Survey"),
    ):
        assert _inside(path, area), path
    # Settings > Saving > "Use Documents" clears the choice; the default it
    # falls back to is the test area's, not the real Documents.
    set_save_root(None)
    assert _inside(default_save_root(), area)
    assert _inside(cartographs_folder(), area)
    assert not _inside(default_save_root(), Path.home() / "Documents")
