#!/usr/bin/env python3
"""Build the QGIS plugin zip.

The QGIS plugin repository requires a zip containing exactly one top-level
directory whose name is the Python package, with `metadata.txt` and `__init__.py`
directly inside it. Getting that shape wrong is the most common submission
rejection, so `verify_package.py` asserts it independently of this script.

Usage:
    python scripts/package_plugin.py [--outdir dist]

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "nika_onlymap_exporter"
PACKAGE_DIR = REPO_ROOT / PACKAGE_NAME

# The zip is *named* after the product and *contains* a directory named after the
# Python package. They are different strings on purpose, and only one of them is
# ours to choose: QGIS requires the single top-level directory inside the zip to
# be the importable package name, and `plugins.qgis.org` derives the plugin's URL
# slug from it. The filename is free, and "nika_onlymap_exporter-0.1.2.zip" told
# a user downloading it nothing about what they had just downloaded.
ZIP_BASENAME = "qgis2webmap"

# Anything matching these is development detritus, not plugin payload.
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej"}
EXCLUDE_NAMES = {".DS_Store", ".gitkeep"}

# User guides live in docs/ so GitHub Pages can serve them, and are copied into
# the zip so the plugin's Help tab shows the same text offline. One source, two
# consumers - qgis2web's local-and-online docs pattern, which is worth copying.
HELP_DOCS = (
    "index.md",
    "installation.md",
    "first-export.md",
    "the-dialog.md",
    "sharing.md",
    "enhance-with-ai.md",
    "hosting.md",
    "supported-features.md",
    "troubleshooting.md",
    "privacy.md",
)


def read_version() -> str:
    """Read the single source of truth for the plugin version."""
    parser = configparser.ConfigParser()
    parser.read(PACKAGE_DIR / "metadata.txt", encoding="utf-8")
    return parser["general"]["version"].strip()


def should_include(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    return path.name not in EXCLUDE_NAMES


def build(outdir: Path) -> Path:
    version = read_version()
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{ZIP_BASENAME}-{version}.zip"

    files = sorted(
        p for p in PACKAGE_DIR.rglob("*") if p.is_file() and should_include(p)
    )
    if not files:
        raise SystemExit(f"error: no files found under {PACKAGE_DIR}")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            # arcname keeps the required single top-level directory.
            zf.write(path, path.relative_to(REPO_ROOT))
        # Ship the licence inside the zip -- the plugin is redistributed on its own.
        zf.write(REPO_ROOT / "LICENSE", f"{PACKAGE_NAME}/LICENSE")

        for name in HELP_DOCS:
            source = REPO_ROOT / "docs" / name
            if not source.exists():
                raise SystemExit(f"error: missing help document docs/{name}")
            zf.write(source, f"{PACKAGE_NAME}/help/{name}")

    size_kb = target.stat().st_size / 1024
    total = len(files) + 1 + len(HELP_DOCS)
    print(f"built {target.relative_to(REPO_ROOT)}  ({total} files, {size_kb:.1f} KB)")
    return target


def announce_runtime_updates() -> None:
    """Say whether a newer OnlyMap runtime exists, without ever failing the build.

    Packaging is the right moment: a zip is what gets handed to someone else, so
    it is when being 25 releases behind matters and when nobody is mid-thought
    about something else. It stays advisory - the pinned build is the one every
    test tier is green against, and moving it is a deliberate act. Import errors
    are swallowed along with network ones so an air-gapped or trimmed checkout
    still packages.
    """
    # Explicit rather than relying on `sys.path[0]`: this module is also loaded
    # by `spec_from_file_location` from the docs tests, where the script's own
    # directory is not on the path.
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from check_runtime_updates import check, report
    except ImportError:  # pragma: no cover - the checker is optional
        return
    with contextlib.suppress(Exception):  # advisory only, never fatal
        report(check())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="dist", type=Path)
    ap.add_argument(
        "--no-update-check",
        action="store_true",
        help="skip the check for a newer OnlyMap runtime",
    )
    args = ap.parse_args()
    build(REPO_ROOT / args.outdir if not args.outdir.is_absolute() else args.outdir)
    if not args.no_update_check:
        announce_runtime_updates()
    return 0


if __name__ == "__main__":
    sys.exit(main())
