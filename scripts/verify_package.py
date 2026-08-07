#!/usr/bin/env python3
"""Assert the built zip is shaped the way the QGIS plugin repository requires.

Written to be independent of `package_plugin.py` -- it inspects the zip as an
opaque artifact, so a bug in the builder cannot hide itself here. Run in CI on
every push.

Checks:
  1. Exactly one top-level directory, named `nika_onlymap_exporter`.
  2. `metadata.txt` and `__init__.py` directly inside it.
  3. Required `metadata.txt` keys present, `experimental=False`, version is semver.
  4. The declared `icon=` path actually exists in the zip.
  5. No development detritus (`__pycache__`, `.pyc`, `.gitkeep`).
  6. No absolute or parent-escaping paths.
  7. The licence text ships with the plugin.

Usage:
    python scripts/verify_package.py dist/qgis2webmap-0.1.2.zip

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import configparser
import io
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

PACKAGE_NAME = "nika_onlymap_exporter"
REQUIRED_KEYS = (
    "name",
    "qgisMinimumVersion",
    "description",
    "about",
    "version",
    "author",
    "email",
    "tracker",
    "repository",
    "license",
)
SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+].+)?$")

failures: list[str] = []
checks_run = 0


def check(condition: bool, message: str) -> None:
    global checks_run
    checks_run += 1
    if not condition:
        failures.append(message)


def verify(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

        # 6. path safety first -- everything else trusts these paths
        for name in names:
            p = PurePosixPath(name)
            check(not p.is_absolute(), f"absolute path in zip: {name}")
            check(".." not in p.parts, f"parent-escaping path in zip: {name}")

        # 1. exactly one top-level directory, correctly named
        tops = {PurePosixPath(n).parts[0] for n in names}
        check(len(tops) == 1, f"expected 1 top-level entry, found {sorted(tops)}")
        check(
            tops == {PACKAGE_NAME},
            f"top-level directory must be '{PACKAGE_NAME}', found {sorted(tops)}",
        )

        # 2. required files directly inside it
        for required in ("metadata.txt", "__init__.py"):
            check(
                f"{PACKAGE_NAME}/{required}" in names,
                f"missing {PACKAGE_NAME}/{required}",
            )

        # 5. no detritus
        for name in names:
            check("__pycache__" not in name, f"__pycache__ in zip: {name}")
            check(not name.endswith(".pyc"), f".pyc in zip: {name}")
            check(not name.endswith(".gitkeep"), f".gitkeep in zip: {name}")

        # 7. licence present
        check(f"{PACKAGE_NAME}/LICENSE" in names, "LICENSE missing from zip")

        # 3. metadata contents
        meta_name = f"{PACKAGE_NAME}/metadata.txt"
        if meta_name in names:
            parser = configparser.ConfigParser()
            parser.read_file(io.StringIO(zf.read(meta_name).decode("utf-8")))
            check(
                parser.has_section("general"), "metadata.txt has no [general] section"
            )
            if parser.has_section("general"):
                general = parser["general"]
                for key in REQUIRED_KEYS:
                    check(key in general, f"metadata.txt missing required key: {key}")
                version = general.get("version", "").strip()
                check(
                    bool(SEMVER.match(version)),
                    f"version '{version}' is not semver",
                )
                check(
                    general.get("experimental", "").strip().lower() == "false",
                    "metadata.txt must set experimental=False "
                    "(issue #29: ship 0.1.0 stable)",
                )
                check(
                    general.get("license", "").strip() == "GPL-2.0-or-later",
                    "metadata.txt license must be GPL-2.0-or-later",
                )

                # 4. declared icon exists
                icon = general.get("icon", "").strip()
                if icon:
                    check(
                        f"{PACKAGE_NAME}/{icon}" in names,
                        f"icon declared as '{icon}' but not present in zip",
                    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("zip", type=Path, help="path to the built plugin zip")
    args = ap.parse_args()

    if not args.zip.exists():
        print(f"error: {args.zip} does not exist", file=sys.stderr)
        return 2

    verify(args.zip)

    if failures:
        print(f"FAILED -- {len(failures)} of {checks_run} checks:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"package verification passed ({checks_run} checks) -- {args.zip.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
