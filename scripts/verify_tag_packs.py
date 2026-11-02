#!/usr/bin/env python3
"""Assert a git ref carries every language pack that its `packs.json` pins.

    python3 scripts/verify_tag_packs.py v0.3.1

The release zip holds no `.qm`: the plugin downloads each pack from this
repository at the release tag (`ui/languages.py`, `pack_url`) and installs it
only if its SHA-256 matches the pin. So the tag, not the zip, must carry the
packs. This reads them from git objects at REF, not from the working tree, and
fails when one is missing or differs from its pin.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

I18N = "nika_onlymap_exporter/i18n"


def show(ref: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    ref = sys.argv[1]
    pins = show(ref, f"{I18N}/packs.json")
    if pins is None:
        print(f"error: {ref} has no {I18N}/packs.json", file=sys.stderr)
        return 1
    packs = json.loads(pins)["packs"]
    problems = []
    for code, entry in sorted(packs.items()):
        data = show(ref, f"{I18N}/qgis2webmap_{code}.qm")
        if data is None:
            problems.append(f"{code}: qgis2webmap_{code}.qm is not in {ref}")
        elif hashlib.sha256(data).hexdigest() != entry["sha256"]:
            problems.append(f"{code}: the .qm in {ref} does not match its pin")
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    if problems:
        print(f"FAILED -- {len(problems)} of {len(packs)} packs", file=sys.stderr)
        return 1
    print(f"all {len(packs)} language packs are in {ref} and match packs.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
