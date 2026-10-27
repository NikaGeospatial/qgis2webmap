"""`core/page_theme.py`: every theme's text reads on its surface (4.5:1), and
`readable` moves a colour only as far as it must.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.cartograph_ir import CARTOGRAPH_THEMES
from nika_onlymap_exporter.core.page_theme import (
    MINIMUM_CONTRAST,
    THEMES,
    readable,
    surface_contrast,
    theme_colours,
)


def test_every_named_theme_but_default_has_colours() -> None:
    assert set(THEMES) == set(CARTOGRAPH_THEMES) - {"default"}
    assert theme_colours("default") is None
    assert theme_colours("no-such-theme") is None


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_body_text_reads_on_its_surface(theme_id: str) -> None:
    theme = THEMES[theme_id]
    for ink in (theme.ink, theme.title, theme.muted):
        assert surface_contrast(ink, theme.page) >= MINIMUM_CONTRAST, (theme_id, ink)
    assert surface_contrast(theme.panel_ink, theme.panel) >= MINIMUM_CONTRAST


def test_a_colour_that_already_reads_is_left_alone() -> None:
    assert readable("#222222", "#ffffff") == "#222222"
    assert readable("#f7ece6", "#261a29") == "#f7ece6"


def test_a_weak_grey_is_darkened_on_light_and_lightened_on_dark() -> None:
    assert surface_contrast("#999999", "#fafafa") < MINIMUM_CONTRAST
    fixed = readable("#999999", "#fafafa")
    assert surface_contrast(fixed, "#fafafa") >= MINIMUM_CONTRAST
    assert int(fixed[1:3], 16) < 0x99
    lifted = readable("#554455", "#111111")
    assert surface_contrast(lifted, "#111111") >= MINIMUM_CONTRAST
    assert int(lifted[1:3], 16) > 0x55
