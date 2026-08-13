"""Relief exports carry a camera clamp; flat exports carry nothing.

The public elevation tiles end at z14 and the runtime blanks the relief
rather than magnifying past it. The runtime has no camera-cap attribute yet,
so the artifact ships a page script built only on the element's public
surface. It must vanish entirely from flat exports - the hook is the only
script the artifact carries beyond the runtime itself.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.export_ir import ExportProject, ExportSettings
from nika_onlymap_exporter.packaging.artifact_builder import terrain_zoom_clamp


def project(**settings) -> ExportProject:
    return ExportProject(title="t", layers=(), settings=ExportSettings(**settings))


class TestTerrainZoomClamp:
    def test_relief_exports_carry_the_clamp(self) -> None:
        script = terrain_zoom_clamp(project(terrain="terrarium"))
        assert "om-view-changed" in script
        assert "zoom > 12" in script
        assert "setViewInternal({ zoom: 12 })" in script

    def test_flat_exports_carry_nothing(self) -> None:
        assert terrain_zoom_clamp(project()) == ""

    def test_an_unknown_preset_carries_nothing(self) -> None:
        assert terrain_zoom_clamp(project(terrain="whatever")) == ""
