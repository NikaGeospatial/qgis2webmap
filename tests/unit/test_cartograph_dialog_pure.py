"""Pure-Python pieces of the Cartograph (Beta) dialog: no PyQGIS, no Qt.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.settings import FrameChoice, default_frame_mode
from nika_onlymap_exporter.ui.cartograph.status import (
    format_bytes,
    format_elapsed,
    status_text,
)


class TestDefaultFrameMode:
    def test_web_mercator_defaults_to_live(self) -> None:
        assert default_frame_mode("EPSG:3857") == "live"

    def test_wgs84_defaults_to_live(self) -> None:
        assert default_frame_mode("EPSG:4326") == "live"

    def test_a_utm_zone_defaults_to_static(self) -> None:
        assert default_frame_mode("EPSG:32648") == "static"

    def test_an_unknown_or_missing_crs_defaults_to_static(self) -> None:
        assert default_frame_mode("") == "static"


class TestFrameChoiceRoundTrip:
    def test_round_trips_through_a_dict(self) -> None:
        choice = FrameChoice(mode="static", dpi=300)
        restored = FrameChoice.from_dict(choice.to_dict())
        assert restored == choice

    def test_an_unknown_mode_falls_back_to_live(self) -> None:
        assert FrameChoice.from_dict({"mode": "huh", "dpi": 150}).mode == "live"

    def test_a_bad_dpi_falls_back_to_the_default(self) -> None:
        assert FrameChoice.from_dict({"mode": "static", "dpi": "huh"}).dpi == 150


class TestStatusText:
    def test_not_saved_yet(self) -> None:
        text = status_text(
            saved_at=None, saved_bytes=None, error="", now=1000.0, translate=str
        )
        assert text == "Not saved yet"

    def test_an_error_wins_over_everything_else(self) -> None:
        text = status_text(
            saved_at=900.0,
            saved_bytes=2048,
            error="Could not save the cartograph: disk full",
            now=1000.0,
            translate=str,
        )
        assert text == "Could not save the cartograph: disk full"

    def test_a_recent_save_reads_in_seconds_and_megabytes(self) -> None:
        text = status_text(
            saved_at=998.0,
            saved_bytes=int(2.1 * 1024 * 1024),
            error="",
            now=1000.0,
            translate=lambda s: s,
        )
        assert text == "Saved 2 s ago · 2.1 MB"


class TestFormatHelpers:
    def test_elapsed_buckets(self) -> None:
        assert format_elapsed(0.4) == "just now"
        assert format_elapsed(2) == "2 s ago"
        assert format_elapsed(125) == "2 min ago"
        assert format_elapsed(7200) == "2 h ago"

    def test_bytes_buckets(self) -> None:
        assert format_bytes(12) == "12 B"
        assert format_bytes(2048) == "2 KB"
        assert format_bytes(int(2.1 * 1024 * 1024)) == "2.1 MB"


class TestPageLabel:
    def test_iso_sizes_are_named_with_their_orientation(self) -> None:
        from types import SimpleNamespace

        from nika_onlymap_exporter.ui.cartograph.status import page_label

        a3 = SimpleNamespace(
            width=420.0, height=297.0, size=None, orientation="landscape"
        )
        assert page_label(a3) == "A3 landscape"

    def test_a_custom_page_is_given_in_millimetres(self) -> None:
        from types import SimpleNamespace

        from nika_onlymap_exporter.ui.cartograph.status import page_label

        odd = SimpleNamespace(
            width=500.0, height=300.0, size=None, orientation="landscape"
        )
        assert page_label(odd) == "500 x 300 mm landscape"
