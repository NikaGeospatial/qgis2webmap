"""What the plugin repository's security scan checks, kept passing here.

plugins.qgis.org runs Bandit and detect-secrets over every upload. Both flagged
0.2.0: `assert` statements (Bandit B101: stripped under `python -O`, so a check
that silently vanishes) and the language packs' SHA-256 pins (detect-secrets
cannot tell a checksum from a key). These tests catch both before an upload.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "nika_onlymap_exporter"
HEX_DIGEST = re.compile(r'"[0-9a-f]{32,}"')
PRAGMA = "pragma: allowlist secret"


def test_the_plugin_has_no_assert_statements() -> None:
    found = [
        f"{path.relative_to(PACKAGE)}:{node.lineno}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assert)
    ]
    assert found == [], "Bandit B101 - use an explicit check and raise instead"


def test_every_shipped_digest_is_marked_as_a_checksum() -> None:
    unmarked = [
        f"{path.relative_to(PACKAGE)}:{number}"
        for path in sorted(PACKAGE.rglob("*.json"))
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if HEX_DIGEST.search(line) and PRAGMA not in line
    ]
    assert unmarked == [], "detect-secrets flags these; add the allowlist pragma"
