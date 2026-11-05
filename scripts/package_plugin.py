#!/usr/bin/env python3
"""Build the QGIS plugin zip, and the language packs zip released beside it.

The QGIS plugin repository requires a zip containing exactly one top-level
directory whose name is the Python package, with `metadata.txt` and `__init__.py`
directly inside it. Getting that shape wrong is the most common submission
rejection, so `verify_package.py` asserts it independently of this script.

The second zip, `qgis2webmap-language-packs-<version>.zip`, holds every
compiled language pack and nothing else. It is attached to the GitHub release
(the release workflow attaches all of `dist/*.zip`), and the plugin downloads
it when someone asks for a language (see `ui/languages.py`).

Usage:
    python scripts/package_plugin.py [--outdir dist]

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
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
# The release asset the plugin downloads its languages from. `ui/languages.py`
# builds the same name; tests/unit/test_language_packs.py holds the two together.
LANGUAGE_PACKS_BASENAME = f"{ZIP_BASENAME}-language-packs"
I18N_DIR = PACKAGE_DIR / "i18n"

# Anything matching these is development detritus, not plugin payload.
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
# Translations are not shipped: `.ts` is the translators' source, and the
# compiled `.qm` packs go in the language packs zip, downloaded on request (see
# `ui/languages.py`). Only `i18n/packs.json`, the pinned checksum of each, goes
# in the plugin zip.
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej", ".ts", ".qm"}
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
    "cartograph-first-export.md",
    "cartograph-dialog.md",
    "live-or-static-frames.md",
    "cartograph-atlas.md",
    "printing-a-cartograph.md",
    "colour-vision.md",
    "cartograph-supported-features.md",
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


# The earliest date a zip entry can carry. Used when neither SOURCE_DATE_EPOCH nor
# a git commit says when the source was made (an exported tree without .git).
ZIP_EPOCH_FLOOR = 315532800  # 1980-01-01T00:00:00Z
#: Permission bits stored for every entry: nothing in the plugin is executable.
ENTRY_MODE = 0o644

ZipStamp = tuple[int, int, int, int, int, int]


def source_timestamp() -> ZipStamp:
    """The fixed modification time stamped on every zip entry.

    A zip that records file mtimes and filesystem modes is different bytes on
    every build of the same commit, so its checksum proves nothing. The
    reproducible-builds convention is SOURCE_DATE_EPOCH; without it the commit
    time is the next best statement of "when this source was made".
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if not epoch:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            epoch = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%ct"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
    seconds = max(int(epoch), ZIP_EPOCH_FLOOR) if epoch.isdigit() else ZIP_EPOCH_FLOOR
    t = time.gmtime(seconds)
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def add_entry(zf: zipfile.ZipFile, source: Path, arcname: str, stamp: ZipStamp) -> None:
    """Write `source` as `arcname` with a fixed date and a normalised mode."""
    info = zipfile.ZipInfo(arcname, date_time=stamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix, whatever platform builds it
    info.external_attr = ENTRY_MODE << 16
    zf.writestr(info, source.read_bytes())


def build(outdir: Path) -> Path:
    version = read_version()
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{ZIP_BASENAME}-{version}.zip"

    files = sorted(
        p for p in PACKAGE_DIR.rglob("*") if p.is_file() and should_include(p)
    )
    if not files:
        raise SystemExit(f"error: no files found under {PACKAGE_DIR}")

    stamp = source_timestamp()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            # arcname keeps the required single top-level directory.
            add_entry(zf, path, path.relative_to(REPO_ROOT).as_posix(), stamp)
        # Ship the licence inside the zip -- the plugin is redistributed on its own.
        add_entry(zf, REPO_ROOT / "LICENSE", f"{PACKAGE_NAME}/LICENSE", stamp)

        for name in HELP_DOCS:
            source = REPO_ROOT / "docs" / name
            if not source.exists():
                raise SystemExit(f"error: missing help document docs/{name}")
            add_entry(zf, source, f"{PACKAGE_NAME}/help/{name}", stamp)

    size_kb = target.stat().st_size / 1024
    total = len(files) + 1 + len(HELP_DOCS)
    # Relative inside the repo; an --outdir elsewhere has no relative form.
    shown = target.resolve()
    if shown.is_relative_to(REPO_ROOT):
        shown = shown.relative_to(REPO_ROOT)
    print(f"built {shown}  ({total} files, {size_kb:.1f} KB)")
    return target


def build_language_packs(outdir: Path) -> Path:
    """Zip every pack `i18n/packs.json` pins, at the zip's root.

    Refuses to build when a pack is missing or differs from its pin: the plugin
    rejects the whole download then, so such a zip would leave every language
    undownloadable for everyone on this version.
    """
    version = read_version()
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{LANGUAGE_PACKS_BASENAME}-{version}.zip"

    pins = json.loads((I18N_DIR / "packs.json").read_text(encoding="utf-8"))["packs"]
    stamp = source_timestamp()
    packs = []
    for code in sorted(pins):
        source = I18N_DIR / f"qgis2webmap_{code}.qm"
        if not source.is_file():
            raise SystemExit(f"error: missing language pack {source.name}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != pins[code]["sha256"]:
            raise SystemExit(
                f"error: {source.name} is not the pack packs.json pins; "
                "run scripts/update_translations.py"
            )
        packs.append(source)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for source in packs:
            add_entry(zf, source, source.name, stamp)

    shown = target.resolve()
    if shown.is_relative_to(REPO_ROOT):
        shown = shown.relative_to(REPO_ROOT)
    size_kb = target.stat().st_size / 1024
    print(f"built {shown}  ({len(packs)} language packs, {size_kb:.1f} KB)")
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
    outdir = REPO_ROOT / args.outdir if not args.outdir.is_absolute() else args.outdir
    build(outdir)
    build_language_packs(outdir)
    if not args.no_update_check:
        announce_runtime_updates()
    return 0


if __name__ == "__main__":
    sys.exit(main())
