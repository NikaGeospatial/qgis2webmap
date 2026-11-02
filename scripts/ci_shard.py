#!/usr/bin/env python3
"""Print the test files one CI shard of the primary suite runs.

    python3 scripts/ci_shard.py --shards 3 --index 1
    python3 scripts/ci_shard.py --compat344

The primary suite is `tests/unit`, `tests/qgis` and `tests/fixtures`. It is split
the way `scripts/run_tests.py` splits it for a local run - the same function,
`test_selection.balance`, over the recorded per-file seconds in
`tests/durations.json` - so a shard's estimate here is the one `run_tests.py`
prints. The split is a partition: every file is in exactly one shard.

`--compat344` prints instead the focused QGIS 3.44 suite (`compat344` in
`tests/test_groups.json`), which is not sharded.

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
    ap.add_argument("--shards", type=int)
    ap.add_argument("--index", type=int, help="1-based shard number")
    ap.add_argument(
        "--compat344", action="store_true", help="print the focused 3.44 suite"
    )
    args = ap.parse_args()
    if args.compat344:
        entries = selection.load_groups(REPO)["compat344"]
        print("\n".join(entry["target"] for entry in entries))
        return 0
    if args.shards is None or args.index is None:
        ap.error("--shards and --index are required unless --compat344 is given")
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
