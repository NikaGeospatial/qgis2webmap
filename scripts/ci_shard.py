#!/usr/bin/env python3
"""Print the test files one CI shard of the primary suite runs.

    python3 scripts/ci_shard.py --shards 3 --index 1

The primary suite is `tests/unit`, `tests/qgis` and `tests/fixtures`. It is split
the way `scripts/run_tests.py` splits it for a local run - the same function,
`test_selection.balance`, over the recorded per-file seconds in
`tests/durations.json` - so a shard's estimate here is the one `run_tests.py`
prints. The split is a partition: every file is in exactly one shard.

Needs nothing beyond the standard library. Prints one path per line; the shard's
estimated seconds go to stderr.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "q2w_test_selection", REPO / "scripts" / "test_selection.py"
)
if _spec is None or _spec.loader is None:
    raise SystemExit("error: cannot load scripts/test_selection.py")
selection = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = selection
_spec.loader.exec_module(selection)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shards", type=int, required=True)
    ap.add_argument("--index", type=int, required=True, help="1-based shard number")
    args = ap.parse_args()
    if not 1 <= args.index <= args.shards:
        print(f"error: --index must be 1..{args.shards}", file=sys.stderr)
        return 2

    targets = selection.all_primary_tests(REPO)
    durations = selection.load_durations(REPO)
    files, estimate = selection.balance(targets, args.shards, durations)[args.index - 1]
    print(
        f"shard {args.index}/{args.shards}: {len(files)} of {len(targets)} files, "
        f"~{estimate / 60:.0f} min estimated",
        file=sys.stderr,
    )
    print("\n".join(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
