#!/usr/bin/env python3
"""Report whether a newer OnlyMap runtime has been published since we pinned.

    python scripts/check_runtime_updates.py [--json]

Compares `nika_onlymap_exporter/runtime/runtime-lock.json` against the npm
registry's `latest` dist-tag and says whether the pin is behind.

**This never changes the pin and never fails the build.** Bumping the runtime is
a deliberate act with a test matrix behind it - the pinned build is the one every
tier is green against, and an export that silently moved to an untested runtime
is precisely what `runtime-lock.json` exists to prevent. So this only tells a
human that a decision is available.

It is called at the end of packaging, where it is most useful and least
intrusive: a zip is exactly the moment someone is about to hand the plugin to
someone else. Network failure, an air-gapped machine, or a registry outage all
degrade to a quiet note - packaging must work offline.

Set `ONLYMAP_SKIP_UPDATE_CHECK=1` to skip the request entirely.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = REPO_ROOT / "nika_onlymap_exporter" / "runtime" / "runtime-lock.json"

REGISTRY = "https://registry.npmjs.org/{package}"

# Short: this is a courtesy check at the end of a build, not a dependency of it.
TIMEOUT_SECONDS = 5

SKIP_ENV = "ONLYMAP_SKIP_UPDATE_CHECK"


def parse_version(version: str) -> tuple[int, ...]:
    """A dotted version as comparable integers.

    Pre-release suffixes are cut rather than ordered: `0.6.0-rc.1` compares as
    `0.6.0`, which is deliberate for a "should a human look at this?" prompt -
    being told about a release candidate is useful, and getting its precedence
    subtly wrong is not worth a full semver implementation here.
    """
    core = version.split("-", 1)[0].split("+", 1)[0]
    parts = []
    for piece in core.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def read_pin() -> tuple[str, str] | None:
    """The pinned `(package, version)`, or `None` when the lock is unreadable."""
    try:
        lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    package = lock.get("package")
    version = lock.get("version")
    if not package or not version:
        return None
    return str(package), str(version)


def fetch_registry(package: str) -> dict[str, object] | None:
    """The registry document, or `None` on any network or parse failure.

    Every failure is the same outcome - we do not know, so say nothing
    alarming - so they are deliberately not distinguished here.
    """
    url = REGISTRY.format(package=package)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def check() -> dict[str, object]:
    """Compare the pin against the registry. Never raises."""
    pin = read_pin()
    if pin is None:
        return {"status": "unknown", "reason": "runtime-lock.json is unreadable"}
    package, pinned = pin

    if os.environ.get(SKIP_ENV):
        return {"status": "skipped", "package": package, "pinned": pinned}

    document = fetch_registry(package)
    if document is None:
        return {
            "status": "unreachable",
            "package": package,
            "pinned": pinned,
            "reason": "the npm registry could not be reached",
        }

    tags = document.get("dist-tags")
    latest = tags.get("latest") if isinstance(tags, dict) else None
    if not latest:
        return {"status": "unknown", "package": package, "pinned": pinned}

    versions = document.get("versions")
    known = list(versions) if isinstance(versions, dict) else []
    behind = [
        v
        for v in known
        if parse_version(pinned) < parse_version(v) <= parse_version(str(latest))
    ]

    return {
        "status": "behind"
        if parse_version(str(latest)) > parse_version(pinned)
        else "current",
        "package": package,
        "pinned": pinned,
        "latest": str(latest),
        "releasesBehind": len(behind),
    }


def report(result: dict[str, object]) -> None:
    """One or two lines on stdout. Deliberately quiet when there is nothing to do."""
    status = result.get("status")
    if status == "current":
        print(f"runtime pin is current ({result.get('pinned')})")
        return
    if status == "behind":
        count = result.get("releasesBehind")
        print(
            f"runtime UPDATE AVAILABLE: pinned {result.get('pinned')}, "
            f"latest {result.get('latest')} ({count} release"
            f"{'' if count == 1 else 's'} behind)"
        )
        print(
            "  the pin is what every test tier is green against -- to move, "
            "re-lock with scripts/lock_runtime.py and re-run all tiers"
        )
        return
    if status in ("unreachable", "skipped"):
        print(f"runtime update check {status} (pin {result.get('pinned')})")
        return
    print(f"runtime update check inconclusive: {result.get('reason', 'unknown')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check for a newer OnlyMap runtime.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    result = check()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        report(result)
    # Always zero: being behind is information, not a build failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
