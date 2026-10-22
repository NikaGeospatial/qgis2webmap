"""Evaluate an exported `<om-atlas filter>` the way the runtime does.

The runtime compiles the filter as a JavaScript expression over the feature
(`d`). With Node on the machine that is exactly what runs here; without it,
the filter's own grammar (`d.id` tests joined by `&&`, `||`, `!`) means the
same in Python.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import shutil
import subprocess


def evaluate_filter(expression: str, ids: list[int]) -> list[bool]:
    node = shutil.which("node")
    if node is not None:
        script = (
            f"const f = (d) => ({expression});"
            f"console.log(JSON.stringify({ids!r}.map((id) => !!f({{ id }}))));"
        )
        done = subprocess.run(
            [node, "-e", script], capture_output=True, text=True, check=True
        )
        return json.loads(done.stdout)
    python = expression.replace("&&", " and ").replace("||", " or ")
    python = python.replace("false", "False")
    python = python.replace("!(", "not (").replace("d.id", "feature_id")
    return [bool(eval(python, {"feature_id": feature_id})) for feature_id in ids]
