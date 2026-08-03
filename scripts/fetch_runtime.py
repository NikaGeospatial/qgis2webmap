#!/usr/bin/env python3
"""Fetch the pinned OnlyMap runtime into a directory, for CI.

    python scripts/fetch_runtime.py [cache-dir]

Prints the directory holding the runtime to stdout, so a workflow can do:

    echo "ONLYMAP_RUNTIME_DIR=$(python scripts/fetch_runtime.py)" >> $GITHUB_ENV

**This deliberately goes through `FetchingRuntime.fetch`, the same code path a
user's first export takes.** A `curl | tar` step in the workflow would put the
right bytes on disk while testing none of the download, SHA-256 verification or
atomic-extract logic - so the one code path that reaches the network on a user's
machine would be the only one CI never ran.

The licence gate is not bypassed so much as not reached: consent is enforced in
`FetchingRuntime.load`, which is what an export calls. `fetch` is the mechanical
download beneath it. CI is not a user and cannot consent on anyone's behalf, so
it may fetch for testing but must never be taken as acceptance.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nika_onlymap_exporter.packaging.runtime_manager import (  # noqa: E402
    FetchingRuntime,
    RuntimeUnavailableError,
)


def _progress(received: int, total: int | None) -> None:
    """Coarse progress on stderr, so stdout stays a single parseable path."""
    if not total:
        return
    percent = int(received * 100 / total)
    # Tenths only: a CI log is a file, not a terminal, and carriage returns
    # would make it unreadable.
    if percent % 10 == 0:
        print(f"  {percent}% ({received:,}/{total:,} bytes)", file=sys.stderr)


def main(argv: list[str]) -> int:
    cache_dir = Path(argv[1]).expanduser().resolve() if len(argv) > 1 else None

    provider = FetchingRuntime(cache_dir=cache_dir)
    version = provider.version
    if not version:
        print(
            "No runtime version is pinned; runtime-lock.json is missing or unreadable.",
            file=sys.stderr,
        )
        return 1

    if provider.is_cached():
        print(f"runtime {version} already cached", file=sys.stderr)
        print(provider.cached_dir())
        return 0

    print(f"fetching OnlyMap runtime {version}", file=sys.stderr)
    try:
        destination = provider.fetch(on_progress=_progress)
    except RuntimeUnavailableError as error:
        # Includes the SHA-256 mismatch case, which must fail the build loudly:
        # it means the registry served something other than the tested build.
        print(f"runtime fetch failed: {error}", file=sys.stderr)
        return 1

    print("verified against runtime-lock.json and cached", file=sys.stderr)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
