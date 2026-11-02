#!/usr/bin/env python3
"""Run the plugin repository's Qt6 compatibility check on the built zip.

    python3 scripts/check_qt6.py dist/qgis2webmap-0.3.0.zip

plugins.qgis.org unzips every upload and runs `pyqt5_to_pyqt6.py --dry_run` from
the `ghcr.io/qgis/pyqgis4-checker` image over it. `--dry_run` exits 0 whether or
not it found anything, so the site decides from the log: a line shaped
`/path:line:column - message` is an incompatibility. This does the same, and
exits 1 on such a line, so a CI job running it fails when the site would show red.

Run it with that image's Python (the script is on its PATH). The directory is
passed as an absolute path on purpose: the site's regex only matches a log line
that starts with `/`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Copied from the site (plugins/tasks/save_qt6_result.py): what counts as a finding.
FINDING = re.compile(r"^/[^:]+:\d+:\d+\s+-\s+.+$", re.MULTILINE)


def findings_in(log: str) -> list[str]:
    return [match.group(0) for match in FINDING.finditer(log)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("zip", type=Path, help="path to the built plugin zip")
    args = ap.parse_args()
    if not args.zip.is_file():
        print(f"error: {args.zip} does not exist", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="q2w-qt6-") as scratch:
        tree = Path(scratch) / "plugin"
        with zipfile.ZipFile(args.zip) as archive:
            archive.extractall(tree)
        log_file = Path(scratch) / "pyqt6_checker.log"
        result = subprocess.run(
            [
                "pyqt5_to_pyqt6.py",
                "--dry_run",
                "--logfile",
                str(log_file),
                f"{tree}/",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not log_file.is_file():
            print(f"the Qt6 checker did not run: {result.stderr[-500:]}")
            return 1
        found = findings_in(log_file.read_text(encoding="utf-8") + result.stdout)

    print(f"Qt6 check: {len(found)} finding(s)")
    for line in found:
        print(f"  - {line.replace(str(tree), '', 1)}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
