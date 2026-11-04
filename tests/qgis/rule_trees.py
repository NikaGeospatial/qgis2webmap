"""Rule-based renderer rules built in a test, made safely.

A `QgsRuleBasedRenderer.Rule` constructed in Python is sip's own subclass of
the rule. Its parent, and in the end the
renderer, deletes it through a destructor that is not virtual, so sip never
hears that it went; the Python wrapper, which the parent's wrapper keeps alive,
then writes into the freed rule when it is collected. On QGIS 3.44 that heap
corruption crashed a later `QgsProject.clear()`, tests after the one that built
the rules, with nothing in the trace pointing back to them.

A clone is QGIS's own object, whose wrapper never writes into it, so every rule
a tree is built from goes in as `qgis_made(Rule(...))`, before any child is
appended to it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations


def qgis_made(rule):
    """`rule`'s clone, to append or hand to a renderer in its place; `rule`
    itself, with no children yet, is deleted with its own wrapper."""
    return rule.clone()
