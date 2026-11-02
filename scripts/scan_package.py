#!/usr/bin/env python3
"""Run, on the built zip, the scans plugins.qgis.org runs on every upload.

    python scripts/scan_package.py dist/qgis2webmap-0.3.0.zip

The repository extracts the upload and scans the extracted tree, so this does the
same: it never reads the source tree, only what would actually ship. Each scan is
invoked the way the site invokes it (QGIS-Plugins-Website,
`qgis-app/plugins/security_scanner.py`), with the rule set the site has enabled
(`plugins/management/commands/data/*_rules.json`):

- Bandit, `-t` the enabled test ids and no severity filter, so LOW findings count.
- detect-secrets, run from inside the extracted tree on `.`, every detector but one.
- flake8, `--max-line-length=120` and `--select` the site's codes.

The file-level rules (executable bit, suspicious or binary types, dotfiles) are
asserted by `verify_package.py`. The Qt6 compatibility check is its own CI job.

Needs `bandit`, `detect-secrets` and `flake8` on PATH (`requirements-ci.txt`).
Exits 1 on any finding. A rule set that the site changes is a reason to refresh
the constants below, not to loosen them.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# The enabled Bandit tests: the site disables only B109, B404, B410 and B411.
BANDIT_TESTS = (
    "B101,B102,B103,B104,B105,B106,B107,B108,B110,B111,B112,B113,B201,B202,B301,"
    "B302,B303,B304,B305,B306,B307,B308,B310,B311,B312,B313,B314,B315,B316,B317,"
    "B318,B319,B320,B321,B323,B324,B401,B402,B403,B405,B406,B407,B408,B409,B412,"
    "B413,B501,B502,B503,B504,B505,B506,B507,B508,B509,B601,B602,B603,B604,B605,"
    "B606,B607,B608,B609,B610,B611,B612,B613,B614,B615,B701,B702,B703,B704"
)
# detect-secrets runs every detector except this one.
SECRETS_DISABLED_PLUGINS = ("IPPublicDetector",)
# The 26 flake8 codes the site selects (C901 does nothing without --max-complexity).
FLAKE8_CODES = (
    "E101,E711,E712,E713,E714,E721,E722,E731,E741,E742,E743,E901,E902,E999,W605,"
    "F402,F403,F404,F405,F811,F821,F822,F823,F831,F901,C901"
)
FLAKE8_MAX_LINE_LENGTH = "120"


def run_bandit(tree: Path) -> list[str]:
    result = subprocess.run(
        ["bandit", "-r", str(tree), "-f", "json", "--quiet", "-t", BANDIT_TESTS],
        capture_output=True,
        text=True,
        check=False,
    )
    if not result.stdout.strip():
        return [f"bandit produced no report: {result.stderr.strip()[:300]}"]
    report = json.loads(result.stdout)
    return [
        f"{Path(f['filename']).relative_to(tree)}:{f['line_number']} {f['test_id']} "
        f"({f['issue_severity']}/{f['issue_confidence']}) {f['issue_text']}"
        for f in report.get("results", [])
    ]


def run_detect_secrets(tree: Path) -> list[str]:
    command = [
        "detect-secrets",
        "scan",
        "--all-files",
        "--exclude-files",
        r"metadata\.txt",
        "--exclude-files",
        r"\.secrets\.baseline",
    ]
    if (tree / ".secrets.baseline").is_file():
        command += ["--baseline", ".secrets.baseline"]
    for plugin in SECRETS_DISABLED_PLUGINS:
        command += ["--disable-plugin", plugin]
    command.append(".")
    result = subprocess.run(
        command, cwd=tree, capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        return [f"detect-secrets failed: {result.stderr.strip()[:300]}"]
    report = json.loads(result.stdout)
    return [
        f"{path}:{secret['line_number']} {secret['type']}"
        for path, secrets in sorted(report.get("results", {}).items())
        for secret in secrets
    ]


def run_flake8(tree: Path) -> list[str]:
    python_files = sorted(str(p) for p in tree.rglob("*.py"))
    command = [
        "flake8",
        f"--max-line-length={FLAKE8_MAX_LINE_LENGTH}",
        "--select",
        FLAKE8_CODES,
        *python_files,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1) or result.stderr.strip():
        return [f"flake8 failed: {result.stderr.strip()[:300]}"]
    return [
        line.replace(f"{tree}/", "", 1)
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("zip", type=Path, help="path to the built plugin zip")
    args = ap.parse_args()
    if not args.zip.is_file():
        print(f"error: {args.zip} does not exist", file=sys.stderr)
        return 2

    scans = (
        ("Bandit", run_bandit),
        ("detect-secrets", run_detect_secrets),
        ("flake8", run_flake8),
    )
    failed = False
    with tempfile.TemporaryDirectory(prefix="q2w-scan-") as scratch:
        tree = Path(scratch)
        with zipfile.ZipFile(args.zip) as archive:
            archive.extractall(tree)
        for name, scan in scans:
            findings = scan(tree)
            print(f"{name}: {len(findings)} finding(s)")
            for finding in findings:
                print(f"  - {finding}")
            failed = failed or bool(findings)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
