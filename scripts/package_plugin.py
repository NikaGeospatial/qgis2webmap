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
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "nika_onlymap_exporter"
PACKAGE_DIR = REPO_ROOT / PACKAGE_NAME

# Anything matching these is development detritus, not plugin payload.
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej"}
EXCLUDE_NAMES = {".DS_Store", ".gitkeep"}


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
    target = outdir / f"{PACKAGE_NAME}-{version}.zip"

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

    size_kb = target.stat().st_size / 1024
    print(
        f"built {target.relative_to(REPO_ROOT)}  "
        f"({len(files) + 1} files, {size_kb:.1f} KB)"
    )
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="dist", type=Path)
    args = ap.parse_args()
    build(REPO_ROOT / args.outdir if not args.outdir.is_absolute() else args.outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
