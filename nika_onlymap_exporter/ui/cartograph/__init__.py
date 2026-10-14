"""The Cartograph (Beta) output's dialog-side code.

Kept apart from `ui/main_dialog.py` on purpose - see `specs/cartograph/plan.md`
("Minimal-diff approach"). `main_dialog.py` only owns the Make switch, which
tabs are visible, and dispatching the corner to whichever output is active; all
cartograph-specific chrome lives here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from .controller import CartographController

__all__ = ["CartographController"]
