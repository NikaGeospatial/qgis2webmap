#!/usr/bin/env python3
"""Regenerate `nika_onlymap_exporter/runtime/runtime-lock.json`.

The lock file records which OnlyMap build the plugin is tested against, so a
different runtime is noticed rather than silently shipped. Run this after
deliberately moving to a new build:

    python scripts/lock_runtime.py /path/to/@nika-js/onlymap /path/to/@loaders.gl/mvt

Each argument is a package root (the directory holding `package.json` and
`dist/`), not `dist/` itself -- the version comes from the manifest. The second
is the package of the vector tile decoder worker exports carry beside the
runtime (`runtime_manager.MVT_WORKER`). It must be the loaders.gl version the
runtime bundles, which is read from the runtime itself: a worker of another
version is refused here rather than pinned.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nika_onlymap_exporter.packaging.runtime_manager import (  # noqa: E402
    MVT_WORKER,
    bundled_loaders_version,
)

RUNTIME_DIR = REPO_ROOT / "nika_onlymap_exporter" / "runtime"
LOCK_FILE = RUNTIME_DIR / "runtime-lock.json"

# The runtime's own licence, shipped with the plugin so its terms can be read
# and accepted *before* the runtime is downloaded. Text is reviewable, which is
# exactly what the 5.7 MB minified bundle is not - so shipping the licence
# raises none of the objections that shipping the software would.
LICENCE_FILE = RUNTIME_DIR / "ONLYMAP-LICENSE.md"

RUNTIME_FILES = (
    "onlymap.standalone.js",
    "onlymapjs.css",
    # Added for the cartograph feature. Same package, same pinned version - the
    # dist/ directory has carried these since 0.8.0 - so this is additive and
    # does not move the pin.
    "cartograph.standalone.js",
    "cartograph.css",
)

COMMENT = [
    "The OnlyMap runtime build this plugin is developed and tested against.",
    "The bytes themselves are NOT in this directory: whether they are vendored",
    "here or fetched on first export is an open licensing question (see",
    "docs/architecture.md). This file pins what the answer must produce, so a",
    "runtime that is not the expected build is caught rather than shipped.",
    "Regenerate with scripts/lock_runtime.py after changing the pinned build.",
    "Under workers: the vector tile decoder worker every export carries beside",
    "the runtime, from its own open-source package, which must be the",
    "loaders.gl version the runtime bundles.",
    "Each sha256 below is the digest of a published, public npm artifact. It is",
    "an integrity check, not a credential, and it is meant to be read: that is",
    "the whole point of publishing it. Secret scanners cannot tell a checksum",
    "from a key by looking, so each digest line carries detect-secrets' own",
    "allowlist pragma to say so.",
]

# A 64-character hex string is what an API key looks like to a secret scanner,
# and the QGIS plugin repository runs one over every upload. detect-secrets
# suppresses a line carrying this pragma, but only when it is on the *same*
# line as the digest -- so the note is appended after serialising rather than
# added as a sibling key, which json.dumps would put on its own line.
ALLOWLIST_NOTE = '"_": "checksum of a public artifact # pragma: allowlist secret"'


def _worker_entry(package_root: Path, runtime_js: bytes) -> dict | None:
    """The lock entry for the vector tile decoder worker, or `None` (said why)."""
    manifest = package_root / "package.json"
    if not manifest.is_file():
        print(f"error: {manifest} not found -- pass the package root, not dist/")
        return None
    package = json.loads(manifest.read_text(encoding="utf-8"))
    bundled = bundled_loaders_version(runtime_js)
    if bundled is None:
        print("error: the runtime names no single loaders.gl version")
        return None
    if package.get("version") != bundled:
        print(
            f"error: {package.get('name')} is {package.get('version')}, "
            f"but the runtime bundles loaders.gl {bundled}"
        )
        return None
    path = package_root / "dist" / MVT_WORKER
    if not path.is_file():
        print(f"error: {path} is missing")
        return None
    data = path.read_bytes()
    return {
        "package": package.get("name"),
        "version": package.get("version"),
        "license": package.get("license", "unknown"),
        "path": f"dist/{MVT_WORKER}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
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

    worker = _worker_entry(
        Path(argv[2]).expanduser().resolve(),
        (package_root / "dist" / RUNTIME_FILES[0]).read_bytes(),
    )
    if worker is None:
        return 1

    licence = package_root / "LICENSE.md"
    if not licence.is_file():
        print(f"error: {licence} is missing; the licence must ship with the plugin")
        return 1
    LICENCE_FILE.write_text(licence.read_text(encoding="utf-8"), encoding="utf-8")

    serialised = json.dumps(
        {
            "_comment": COMMENT,
            "package": package.get("name", "@nika-js/onlymap"),
            "version": package.get("version", "unknown"),
            "license": package.get("license", "unknown"),
            "files": files,
            "workers": {MVT_WORKER: worker},
        },
        indent=2,
    )
    lines = [
        f"{line.rstrip()[:-1]}, {ALLOWLIST_NOTE},"
        if line.lstrip().startswith('"sha256":')
        else line
        for line in serialised.splitlines()
    ]
    LOCK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"locked {package.get('name')}@{package.get('version')} -> {LOCK_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
