"""What the preview injects, and what the export must never carry.

The hook slot is shared: `artifact_builder` passes `terrain_zoom_clamp()`
through the same `preview_hook` parameter on the real export path. So "it is
in the preview hook" is not the property that keeps preview-only chrome out of
a shipped map - composing it in `ui.preview` and nowhere else is. These tests
hold that line at the source; `tests/fixtures/test_release_gates.py` holds it
again on the rendered artifact.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

from nika_onlymap_exporter.core.export_ir import ExportProject, ExportSettings
from nika_onlymap_exporter.ui.links import FEATURE_REQUEST_URL
from nika_onlymap_exporter.ui.preview import compose_preview_hook

# The two patterns `test_release_gates` uses to prove an artifact is offline,
# applied to the hook itself so a network dependency is caught where it is
# written rather than inferred from a rendered file.
_REMOTE_SRC = re.compile(r'src="https?://')
_REMOTE_HREF = re.compile(r'href="https?://[^"]*\.(?:js|css)"')


def project(**settings) -> ExportProject:
    return ExportProject(title="t", layers=(), settings=ExportSettings(**settings))


class TestTheHostCallToAction:
    def test_a_preview_carries_it(self) -> None:
        hook = compose_preview_hook(project())
        assert "om-preview-cta" in hook
        assert FEATURE_REQUEST_URL in hook

    def test_it_appears_exactly_once(self) -> None:
        """Composition is additive; a duplicated concatenation is the bug."""
        assert (
            compose_preview_hook(project(), live=True).count('class="om-preview-cta"')
            == 1
        )

    def test_a_final_preview_omits_it(self) -> None:
        """`final` promises byte-for-byte what ships."""
        hook = compose_preview_hook(project(), final=True)
        assert "om-preview-cta" not in hook
        assert "qgis2webmap.camera" in hook

    def test_it_leaves_for_a_browser_safely(self) -> None:
        """The map is the page, and on file:// the referer is a disk path."""
        hook = compose_preview_hook(project())
        assert 'target="_blank" rel="noopener noreferrer"' in hook

    def test_it_is_dismissible_without_touching_the_fragment(self) -> None:
        """CAMERA_SCRIPT owns location.hash and would clobber a flag there."""
        hook = compose_preview_hook(project())
        assert "qgis2webmap.hostcta" in hook
        assert "localStorage" in hook


class TestTheHookStaysOffline:
    def test_no_remote_script_or_stylesheet(self) -> None:
        hook = compose_preview_hook(project(terrain="terrarium"), live=True)
        assert not _REMOTE_SRC.search(hook)
        assert not _REMOTE_HREF.search(hook)


class TestTheRestOfTheHook:
    def test_the_camera_script_is_always_there(self) -> None:
        for kwargs in ({}, {"live": True}, {"final": True}):
            assert "om-view-changed" in compose_preview_hook(project(), **kwargs)

    def test_the_reload_listener_is_live_only(self) -> None:
        assert "EventSource" not in compose_preview_hook(project())
        assert "EventSource" in compose_preview_hook(project(), live=True)

    def test_the_relief_clamp_rides_along(self) -> None:
        assert "setViewInternal" in compose_preview_hook(project(terrain="terrarium"))
        assert "setViewInternal" not in compose_preview_hook(project())
