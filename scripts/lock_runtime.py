#!/usr/bin/env python3
"""Regenerate `nika_onlymap_exporter/runtime/runtime-lock.json`.

The lock file records which OnlyMap build the plugin is tested against, so a
different runtime is noticed rather than silently shipped. Run this after
deliberately moving to a new build:

    python scripts/lock_runtime.py /path/to/@nika-js/onlymap

The argument is the package root (the directory holding `package.json` and
`dist/`), not `dist/` itself -- the version comes from the manifest.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO_ROOT / "nika_onlymap_exporter" / "runtime"
LOCK_FILE = RUNTIME_DIR / "runtime-lock.json"

# The runtime's own licence, shipped with the plugin so its terms can be read
# and accepted *before* the runtime is downloaded. Text is reviewable, which is
# exactly what the 5.7 MB minified bundle is not - so shipping the licence
# raises none of the objections that shipping the software would.
LICENCE_FILE = RUNTIME_DIR / "ONLYMAP-LICENSE.md"

RUNTIME_FILES = ("onlymap.standalone.js", "onlymapjs.css")

COMMENT = [
    "The OnlyMap runtime build this plugin is developed and tested against.",
    "The bytes themselves are NOT in this directory: whether they are vendored",
    "here or fetched on first export is an open licensing question (see",
    "docs/architecture.md). This file pins what the answer must produce, so a",
    "runtime that is not the expected build is caught rather than shipped.",
    "Regenerate with scripts/lock_runtime.py after changing the pinned build.",
]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    package_root = Path(argv[1]).expanduser().resolve()
    manifest = package_root / "package.json"
    if not manifest.is_file():
        print(f"error: {manifest} not found -- pass the package root, not dist/")
        return 1

    package = json.loads(manifest.read_text(encoding="utf-8"))

    files = {}
    for name in RUNTIME_FILES:
        path = package_root / "dist" / name
        if not path.is_file():
            print(f"error: {path} is missing")
            return 1
        data = path.read_bytes()
        files[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    licence = package_root / "LICENSE.md"
    if not licence.is_file():
        print(f"error: {licence} is missing; the licence must ship with the plugin")
        return 1
    LICENCE_FILE.write_text(licence.read_text(encoding="utf-8"), encoding="utf-8")

    LOCK_FILE.write_text(
        json.dumps(
            {
                "_comment": COMMENT,
                "package": package.get("name", "@nika-js/onlymap"),
                "version": package.get("version", "unknown"),
                "license": package.get("license", "unknown"),
                "files": files,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"locked {package.get('name')}@{package.get('version')} -> {LOCK_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
