"""Which tests a change needs, and how to split them across worker processes.

Pure logic, no QGIS and no subprocess except `git`: `scripts/run_tests.py` uses it
to plan a run and `tests/unit/test_run_tests_selection.py` pins its rules.

Selection (profile `patch`)
- The component groups in `tests/test_groups.json` map a changed source file (and,
  for a few files, the changed function) to the groups it can affect. A group names
  its regression tests, the integration checks it requires and, for data-loss
  paths, the shared data-loss regression list.
- Changed test files are always included; a changed shared helper selects its
  dependants (by import) or, for conftest/hermetic, the whole tier.
- A path with no rule is unknown: the whole primary suite is selected instead.
  Narrow selection must never be the result of not knowing.
- Every selected test carries the reasons it was selected; nothing is hand-picked
  outside the groups file.

Sharding balances files by recorded duration (greedy longest-first).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GROUPS_FILE = "tests/test_groups.json"
DURATIONS_FILE = "tests/durations.json"
PLUGIN = "nika_onlymap_exporter"
TIERS = ("unit", "qgis", "fixtures", "browser")
#: Test-side files that make every test of their scope suspect.
TIER_WIDE_SUPPORT = {
    "tests/conftest.py": ("unit", "qgis", "fixtures"),
    "tests/hermetic.py": ("unit", "qgis", "fixtures"),
    "tests/q2w_report.py": ("unit", "qgis", "fixtures"),
    "tests/qgis/conftest.py": ("qgis",),
    "tests/fixtures/conftest.py": ("fixtures",),
}


@dataclass(frozen=True)
class Change:
    """One changed path. `symbols` None = the whole file (or not tied to a function)."""

    path: str
    status: str = "M"
    symbols: frozenset[str] | None = None


@dataclass
class Selection:
    """What to run, each target with the reasons that put it there."""

    tests: dict[str, list[str]] = field(default_factory=dict)
    compat: dict[str, list[str]] = field(default_factory=dict)
    browser: dict[str, list[str]] = field(default_factory=dict)
    zip_smoke: list[str] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)
    broad: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    def add_test(self, target: str, reason: str) -> None:
        self.tests.setdefault(target, [])
        if reason not in self.tests[target]:
            self.tests[target].append(reason)

    def add_compat(self, target: str, reason: str) -> None:
        self.compat.setdefault(target, [])
        if reason not in self.compat[target]:
            self.compat[target].append(reason)

    def add_browser(self, target: str, reason: str) -> None:
        self.browser.setdefault(target, [])
        if reason not in self.browser[target]:
            self.browser[target].append(reason)

    def describe(self) -> list[str]:
        lines = []
        if self.broad:
            lines.append("BROAD FALLBACK (full primary suite) because:")
            lines.extend(f"  - {reason}" for reason in self.broad)
        if self.groups:
            lines.append("component groups touched:")
            for name, reasons in sorted(self.groups.items()):
                lines.append(f"  {name}: {'; '.join(reasons[:3])}")
        for title, mapping in (
            ("QGIS 4.2 tests", self.tests),
            ("QGIS 3.44 compatibility", self.compat),
            ("browser", self.browser),
        ):
            lines.append(f"{title}: {len(mapping)} target(s)")
            for target, reasons in sorted(mapping.items()):
                lines.append(f"  {target}")
                lines.extend(f"      why: {reason}" for reason in reasons[:4])
                if len(reasons) > 4:
                    lines.append(f"      why: ... and {len(reasons) - 4} more")
        if self.zip_smoke:
            lines.append(f"packaged-ZIP smoke: yes ({'; '.join(self.zip_smoke[:3])})")
        if self.ignored:
            lines.append("no extra tests needed beyond the unit tier for:")
            lines.extend(f"  - {item}" for item in self.ignored)
        return lines


# ---- groups file ----------------------------------------------------------------


def load_groups(repo: Path) -> dict:
    return json.loads((repo / GROUPS_FILE).read_text(encoding="utf-8"))


def load_durations(repo: Path) -> dict:
    path = repo / DURATIONS_FILE
    if not path.exists():
        return {"default_file_seconds": 10.0, "qgis42_files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def list_test_files(repo: Path, tier: str) -> list[str]:
    """Repo-relative paths of a tier's `test_*.py` files, sorted."""
    return sorted(
        p.relative_to(repo).as_posix()
        for p in (repo / "tests" / tier).glob("test_*.py")
    )


def file_of(target: str) -> str:
    return target.split("::")[0]


def tier_of(target: str) -> str | None:
    parts = file_of(target).split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "tests" else None


def matching_rule(groups: dict, path: str) -> dict | None:
    for rule in groups["sources"]:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule["match"]):
            return rule
    return None


# ---- changed files and the functions inside them -----------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _functions_by_line(source: str) -> list[tuple[int, int, str]]:
    """(first line, last line, dotted name) of every def, innermost last."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    found.append((child.lineno, child.end_lineno or child.lineno, name))
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _symbols_for(source: str, ranges: list[tuple[int, int]]) -> frozenset[str] | None:
    """Names of the defs the ranges fall in; None if a range is outside every def."""
    functions = _functions_by_line(source)
    names: set[str] = set()
    for first, last in ranges:
        hit = [f for f in functions if f[0] <= last and f[1] >= first]
        # A range that is not entirely inside one def touches module/class level.
        if not hit or not any(f[0] <= first and last <= f[1] for f in hit):
            return None
        names.update(name for _, _, name in hit)
    return frozenset(names)


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def changes_from_git(
    repo: Path, base: str, head: str | None = None, include_worktree: bool = False
) -> list[Change]:
    """Changed paths between `base` and `head` (HEAD), with changed-function names.

    `include_worktree` also counts uncommitted edits (a dev loop); release and
    full runs use committed snapshots and leave it off.
    """
    target = [] if include_worktree else [head or "HEAD"]
    spec = [base, *target] if target else [base]
    status_out = _git(repo, "diff", "--name-status", "--no-renames", *spec)
    changes = []
    for line in status_out.splitlines():
        status, _, path = line.partition("\t")
        symbols: frozenset[str] | None = None
        if status == "M" and path.endswith(".py"):
            patch = _git(repo, "diff", "-U0", "--no-renames", *spec, "--", path)
            old_ranges, new_ranges = [], []
            for match in _HUNK.finditer(patch):
                old_start, old_len = int(match[1]), int(match[2] or 1)
                new_start, new_len = int(match[3]), int(match[4] or 1)
                if old_len:
                    old_ranges.append((old_start, old_start + old_len - 1))
                if new_len:
                    new_ranges.append((new_start, new_start + new_len - 1))
                else:  # pure deletion: touches the spot between two lines
                    new_ranges.append((new_start, new_start + 1))
            new_source = (
                _git(repo, "show", f"{head or 'HEAD'}:{path}")
                if not include_worktree
                else (repo / path).read_text(encoding="utf-8")
            )
            old_source = _git(repo, "show", f"{base}:{path}")
            new_symbols = (
                _symbols_for(new_source, new_ranges) if new_ranges else frozenset()
            )
            old_symbols = (
                _symbols_for(old_source, old_ranges) if old_ranges else frozenset()
            )
            if new_symbols is not None and old_symbols is not None:
                symbols = new_symbols | old_symbols
        changes.append(Change(path, status, symbols))
    if include_worktree:
        for path in _git(repo, "ls-files", "-o", "--exclude-standard").splitlines():
            changes.append(Change(path, "A"))
    return changes


# ---- helper -> dependant test files -------------------------------------------------


def _imported_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def dependants_of_helper(repo: Path, helper: str) -> list[str]:
    """Test files that import the helper module, directly or through other helpers."""
    folder = repo / Path(helper).parent
    helpers = {p.stem: p for p in folder.glob("*.py") if not p.name.startswith("test_")}
    wanted = {Path(helper).stem}
    changed = True
    while changed:  # a helper importing a changed helper is itself changed
        changed = False
        for stem, path in helpers.items():
            if stem not in wanted and _imported_names(path) & wanted:
                wanted.add(stem)
                changed = True
    return sorted(
        p.relative_to(repo).as_posix()
        for p in folder.glob("test_*.py")
        if _imported_names(p) & wanted
    )


# ---- selection ----------------------------------------------------------------------


def all_primary_tests(repo: Path) -> list[str]:
    return [
        t for tier in ("unit", "qgis", "fixtures") for t in list_test_files(repo, tier)
    ]


def _prune_nodes(tests: dict[str, list[str]]) -> dict[str, list[str]]:
    """Drop `file::Class` targets whose whole file is also selected (merge reasons)."""
    whole = {t for t in tests if "::" not in t}
    out: dict[str, list[str]] = {}
    for target, reasons in tests.items():
        key = file_of(target) if "::" in target and file_of(target) in whole else target
        merged = out.setdefault(key, [])
        merged.extend(r for r in reasons if r not in merged)
    return out


def select(
    changes: list[Change],
    repo: Path,
    groups: dict | None = None,
    force_broad: str | None = None,
) -> Selection:
    """The patch-profile selection for `changes`. See the module docstring."""
    groups = groups or load_groups(repo)
    sel = Selection()
    group_defs = groups["groups"]

    def touch(group: str, reason: str) -> None:
        sel.groups.setdefault(group, [])
        if reason not in sel.groups[group]:
            sel.groups[group].append(reason)

    if force_broad:
        sel.broad.append(force_broad)

    if groups.get("unit_always", True):
        for target in list_test_files(repo, "unit"):
            sel.add_test(target, "unit tier always runs (about 40 s in total)")

    for change in changes:
        path = change.path
        label = path if change.symbols is None else f"{path} ({_brief(change.symbols)})"
        if change.status == "D" and path.startswith("tests/") and "test_" in path:
            sel.ignored.append(f"{path} (deleted test file)")
            continue

        if path in TIER_WIDE_SUPPORT:
            tiers = TIER_WIDE_SUPPORT[path]
            sel.broad.append(
                f"shared test support {path} changed (affects {', '.join(tiers)})"
            )
            for tier in tiers:
                for target in list_test_files(repo, tier):
                    sel.add_test(target, f"shared test support {path} changed")
            continue

        # a changed test file always runs
        name = Path(path).name
        if (
            path.startswith("tests/")
            and name.startswith("test_")
            and path.endswith(".py")
        ):
            tier = tier_of(path)
            if tier == "browser":
                sel.add_browser(path, "changed test file")
            elif tier in TIERS:
                sel.add_test(path, "changed test file")
            continue

        # a changed helper next to the tests: its importers
        if path.startswith("tests/") and path.endswith(".py"):
            tier = tier_of(path)
            if tier == "browser":
                for target in list_test_files(repo, "browser"):
                    sel.add_browser(target, f"browser support {path} changed")
                continue
            if tier in ("qgis", "fixtures", "unit"):
                found = dependants_of_helper(repo, path)
                if not found:
                    sel.broad.append(
                        f"helper {path} has no importing test: cannot narrow"
                    )
                    found = list_test_files(repo, tier)
                for target in found:
                    sel.add_test(target, f"helper {path} changed")
                continue

        rule = matching_rule(groups, path)
        if rule is None:
            sel.broad.append(f"no rule for {path}: unknown dependencies")
            continue
        if not rule["owner"] and not rule.get("also"):
            sel.ignored.append(path)
            continue

        chosen: list[str] = []
        unclaimed = change.symbols is None  # whole-file change: owner applies
        claimed_symbols = set()
        for extra in rule.get("also", []):
            matched = (
                {s for s in change.symbols if re.search(extra["symbols"], s)}
                if change.symbols is not None
                else None
            )
            if matched is None or matched:
                chosen.extend(g for g in extra["groups"] if g not in chosen)
                claimed_symbols |= matched or set()
        if change.symbols is not None:
            # A changed function no `also` pattern claims belongs to the owner.
            unclaimed = bool(change.symbols - claimed_symbols) or not change.symbols
        if unclaimed:
            chosen = list(rule["owner"]) + [g for g in chosen if g not in rule["owner"]]
        for group in chosen:
            touch(group, label)

    # Expand the touched groups.
    data_loss = False
    for group, reasons in sorted(sel.groups.items()):
        definition = group_defs[group]
        why = f"group {group} ({reasons[0]})"
        for target in definition["tests"]:
            sel.add_test(target, why)
        for target in definition.get("integration", []):
            sel.add_test(target, f"required integration check of {why}")
        for target in definition.get("browser", []):
            sel.add_browser(target, why)
        if definition.get("zip_smoke"):
            sel.zip_smoke.append(why)
        data_loss = data_loss or bool(definition.get("data_loss"))
        for entry in groups["compat344"]:
            if group in entry["groups"]:
                sel.add_compat(entry["target"], f"{why}; covers: {entry['covers']}")
    if data_loss:
        for target in groups["data_loss_regressions"]:
            sel.add_test(
                target, "data-loss/crash regression for a touched save/close path"
            )

    # a changed test that is also a compat entry runs there too
    for entry in groups["compat344"]:
        for target, reasons in list(sel.tests.items()):
            if (
                file_of(entry["target"]) == file_of(target)
                and "changed test file" in reasons
            ):
                sel.add_compat(
                    entry["target"], "changed test file is in the 3.44 suite"
                )

    if sel.broad:
        for target in all_primary_tests(repo):
            sel.add_test(target, "broad fallback: " + sel.broad[0])
        for entry in groups["compat344"]:
            sel.add_compat(entry["target"], "broad fallback: " + sel.broad[0])
    sel.tests = _prune_nodes(sel.tests)
    sel.compat = _prune_nodes(sel.compat)
    return sel


def _brief(symbols: frozenset[str]) -> str:
    names = sorted({s.rsplit(".", 1)[-1] for s in symbols})
    shown = ", ".join(names[:4])
    return shown + (f", +{len(names) - 4} more" if len(names) > 4 else "")


# ---- consistency of the groups file --------------------------------------------


def check_groups(repo: Path, groups: dict | None = None) -> list[str]:
    """Problems that would make selection silently narrow. Empty list = consistent."""
    groups = groups or load_groups(repo)
    problems: list[str] = []
    listed: set[str] = set()

    def exists(target: str) -> bool:
        return (repo / file_of(target)).exists()

    for name, definition in groups["groups"].items():
        for key in ("tests", "integration", "browser"):
            for target in definition.get(key, []):
                listed.add(file_of(target))
                if not exists(target):
                    problems.append(f"group {name}.{key}: {target} does not exist")
                elif "::" in target:
                    cls = target.split("::")[1]
                    if f"class {cls}" not in (repo / file_of(target)).read_text(
                        "utf-8"
                    ):
                        problems.append(
                            f"group {name}.{key}: {target} names no such class"
                        )
    for target in groups["data_loss_regressions"]:
        if not exists(target):
            problems.append(f"data_loss_regressions: {target} does not exist")
    for entry in groups["compat344"]:
        if not exists(entry["target"]):
            problems.append(f"compat344: {entry['target']} does not exist")
        elif "::" in entry["target"]:
            cls = entry["target"].split("::")[1]
            if f"class {cls}" not in (repo / file_of(entry["target"])).read_text(
                "utf-8"
            ):
                problems.append(f"compat344: {entry['target']} names no such class")
        for group in entry["groups"]:
            if group not in groups["groups"]:
                problems.append(f"compat344: unknown group {group}")
    for rule in groups["sources"]:
        for group in list(rule["owner"]) + [
            g for extra in rule.get("also", []) for g in extra["groups"]
        ]:
            if group not in groups["groups"]:
                problems.append(f"sources {rule['match'][0]}: unknown group {group}")
    for tier in ("qgis", "fixtures", "browser"):
        for path in list_test_files(repo, tier):
            if path not in listed:
                problems.append(f"{path} is in no group (patch could never select it)")
    tracked = sorted(
        p.relative_to(repo).as_posix()
        for folder in (PLUGIN, "scripts", "tests")
        for p in (repo / folder).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    for path in tracked:
        if path.endswith((".qm", ".ts", ".pyc")) or path.startswith("tests/unit/"):
            continue
        if path.startswith("tests/") and path.endswith(".py"):
            continue  # tests and helpers: resolved by name/import, not by a rule
        if matching_rule(groups, path) is None:
            problems.append(
                f"{path} matches no source rule (it would force the broad fallback)"
            )
    return problems


# ---- sharding -----------------------------------------------------------------------


def estimate_seconds(target: str, durations: dict) -> float:
    files = durations.get("qgis42_files", {})
    default = float(durations.get("default_file_seconds", 10.0))
    seconds = float(files.get(file_of(target), default))
    # a class inside a file: assume a quarter of the file (never recorded per class)
    return seconds if "::" not in target else max(1.0, seconds * 0.25)


def balance(
    targets: list[str], shards: int, durations: dict
) -> list[tuple[list[str], float]]:
    """Greedy longest-first split into `shards` lists, with each list's estimate.

    Deterministic: ties break on the target name.
    """
    shards = max(1, min(shards, len(targets) or 1))
    bins: list[tuple[list[str], float]] = [([], 0.0) for _ in range(shards)]
    for target in sorted(targets, key=lambda t: (-estimate_seconds(t, durations), t)):
        index = min(range(shards), key=lambda i: (bins[i][1], i))
        items, total = bins[index]
        items.append(target)
        bins[index] = (items, total + estimate_seconds(target, durations))
    return [(sorted(items), total) for items, total in bins]


# ---- fixture cache keys -----------------------------------------------------------


def file_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def fixture_key(
    source_tree: str, builder_hash: str, runtime_hash: str, env_id: str
) -> str:
    """Identity of a built browser-fixture set: change any input and it changes."""
    payload = json.dumps(
        {
            "source_tree": source_tree,
            "builder": builder_hash,
            "runtime": runtime_hash,
            "env": env_id,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]
