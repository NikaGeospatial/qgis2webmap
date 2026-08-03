"""`DialogState.snapshot` - what the live preview watches for changes.

The value of this method is entirely in its completeness: if a field can change
the exported map but does not change the snapshot, the live preview goes stale
without saying so. The last test here is the one that matters, because it fails
when someone adds a field and forgets this method.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from nika_onlymap_exporter.core.export_ir import (
    ExtentSource,
    OutputMode,
    OverlayCorner,
)
from nika_onlymap_exporter.core.settings import DialogState, LayerSettings


class TestStability:
    def test_identical_state_gives_an_identical_snapshot(self) -> None:
        assert DialogState().snapshot() == DialogState().snapshot()

    def test_layer_order_does_not_affect_it(self) -> None:
        """Dict insertion order must not read as a settings change."""
        first = DialogState()
        first.layers = {
            "a": LayerSettings(include=True),
            "b": LayerSettings(include=False),
        }
        second = DialogState()
        second.layers = {
            "b": LayerSettings(include=False),
            "a": LayerSettings(include=True),
        }
        assert first.snapshot() == second.snapshot()

    def test_it_is_a_string_so_it_can_be_compared_cheaply(self) -> None:
        assert isinstance(DialogState().snapshot(), str)


class TestSensitivity:
    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("map_name", "Something else"),
            ("output_mode", OutputMode.SHARE_ZIP),
            ("show_legend", False),
            ("show_layer_switcher", False),
            ("show_zoom_controls", False),
            ("show_scale_bar", False),
            ("popup_on_hover", True),
            ("show_title", True),
            ("show_abstract", True),
            ("title_corner", OverlayCorner.BOTTOM_RIGHT),
            ("widget_background", "#123456"),
            ("widget_foreground", "#654321"),
            ("highlight_color", "#ff000055"),
            ("quantize_precision", 6),
            ("extent_source", ExtentSource.CANVAS),
        ],
    )
    def test_changing_a_field_changes_the_snapshot(self, field_name, value) -> None:
        state = DialogState()
        before = state.snapshot()
        setattr(state, field_name, value)
        assert state.snapshot() != before, f"{field_name} is not watched"

    def test_changing_a_layer_setting_changes_the_snapshot(self) -> None:
        state = DialogState()
        state.layers = {"a": LayerSettings()}
        before = state.snapshot()
        state.layers["a"].include = not state.layers["a"].include
        assert state.snapshot() != before

    def test_adding_a_layer_changes_the_snapshot(self) -> None:
        state = DialogState()
        before = state.snapshot()
        state.layers["new"] = LayerSettings()
        assert state.snapshot() != before


class TestCompleteness:
    def test_every_field_of_dialog_state_is_watched(self) -> None:
        """Fails when a field is added to DialogState but not to `snapshot`.

        Without this, a new setting silently stops the live preview from
        refreshing when it changes - which looks like the preview being broken
        rather than the snapshot being incomplete.
        """
        snapshot_keys = set(json.loads(DialogState().snapshot()).keys())
        declared = {f.name for f in dataclasses.fields(DialogState)}
        missing = declared - snapshot_keys
        assert not missing, (
            f"DialogState fields missing from snapshot(): {sorted(missing)}. "
            "Add them, or the live preview will not refresh when they change."
        )
