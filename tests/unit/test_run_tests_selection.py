"""The rules that decide which tests a change runs (scripts/test_selection.py).

A narrow selection that silently drops an essential regression is worse than no
selection, so these pin the cases that matter: a change to the controller's save
path pulls in the destination, layout-switch, close and recovery regressions; a
path nobody mapped falls back to the whole suite; and the maintained groups file
stays consistent with the tests and sources it describes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTROLLER = "nika_onlymap_exporter/ui/cartograph/controller.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "test_selection_under_test", REPO / "scripts" / "test_selection.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


sel = _load()

DATA_LOSS = (
    "tests/qgis/test_cartograph_destination.py",
    "tests/qgis/test_switch_layout_saves.py",
    "tests/qgis/test_close_lifecycle.py",
    "tests/qgis/test_close_worker_lifetime.py",
    "tests/qgis/test_project_clear_under_dialog.py",
)


def _select(*changes):
    return sel.select(list(changes), REPO)


def _function_change(path, *names):
    return sel.Change(path, "M", frozenset(names))


class TestTheGroupsFileStaysConsistent:
    def test_nothing_it_lists_is_missing_and_every_test_and_source_is_covered(self):
        assert sel.check_groups(REPO) == []

    def test_a_test_file_in_no_group_is_reported(self, tmp_path):
        (tmp_path / "tests" / "qgis").mkdir(parents=True)
        (tmp_path / "tests" / "qgis" / "test_orphan.py").write_text("")
        (tmp_path / sel.PLUGIN).mkdir()
        (tmp_path / "scripts").mkdir()
        groups = {
            "groups": {"g": {"tests": [], "integration": []}},
            "sources": [],
            "compat344": [],
            "data_loss_regressions": [],
        }
        problems = sel.check_groups(tmp_path, groups)
        assert any("test_orphan.py is in no group" in p for p in problems)

    def test_every_data_loss_regression_exists_and_is_listed_in_a_group(self):
        groups = sel.load_groups(REPO)
        in_groups = {
            sel.file_of(t)
            for definition in groups["groups"].values()
            for key in ("tests", "integration")
            for t in definition.get(key, [])
        }
        for target in groups["data_loss_regressions"]:
            assert (REPO / target).exists()
            assert target in in_groups, f"{target} would never run for its own group"


class TestASavePathChangeKeepsTheDataLossRegressions:
    def test_a_change_to_the_controller_write_path_selects_them(self):
        picked = _select(
            _function_change(CONTROLLER, "CartographController._write_atomic")
        )
        for target in DATA_LOSS:
            assert target in picked.tests, target
        assert not picked.broad

    def test_the_destination_and_switch_and_close_and_recovery_groups_are_named(self):
        picked = _select(
            _function_change(CONTROLLER, "CartographController.on_switch_layout")
        )
        assert {"saving", "layout_session", "recovery"} <= set(picked.groups)
        reasons = " ".join(r for rs in picked.tests.values() for r in rs)
        assert "data-loss/crash regression" in reasons

    def test_a_whole_file_change_to_the_controller_selects_everything_it_owns(self):
        picked = _select(sel.Change(CONTROLLER))
        for target in DATA_LOSS:
            assert target in picked.tests
        assert "tests/qgis/test_cartograph_dialog.py" in picked.tests

    def test_an_unrelated_function_does_not_drag_in_the_save_regressions(self):
        picked = _select(
            _function_change(CONTROLLER, "CartographController._colour_vision_dialog")
        )
        assert "tests/qgis/test_close_worker_lifetime.py" not in picked.tests
        assert "tests/qgis/test_cartograph_dialog.py" in picked.tests

    def test_a_save_change_runs_the_save_as_checks_on_3_44_too(self):
        picked = _select(
            _function_change(CONTROLLER, "CartographController._write_atomic")
        )
        assert "tests/qgis/test_cartograph_destination.py" in picked.compat
        assert "tests/qgis/test_close_worker_lifetime.py" in picked.compat


class TestWhatIsNotUnderstoodFallsBackToEverything:
    def test_a_source_file_no_rule_covers_selects_the_whole_primary_suite(self):
        picked = _select(sel.Change("nika_onlymap_exporter/core/brand_new.py", "A"))
        assert picked.broad
        everything = set(sel.all_primary_tests(REPO))
        assert everything <= set(picked.tests)

    def test_the_broad_fallback_includes_the_whole_3_44_suite(self):
        picked = _select(sel.Change("nika_onlymap_exporter/core/brand_new.py", "A"))
        listed = {e["target"] for e in sel.load_groups(REPO)["compat344"]}
        assert listed <= set(picked.compat)

    def test_the_forced_flag_selects_everything_even_for_a_docs_change(self):
        picked = sel.select(
            [sel.Change("README.md")], REPO, force_broad="--full-suite requested"
        )
        assert set(sel.all_primary_tests(REPO)) <= set(picked.tests)


class TestChangedAndSharedTestFiles:
    def test_a_changed_test_file_is_always_included_with_that_reason(self):
        picked = _select(sel.Change("tests/qgis/test_wheel_guard.py"))
        assert picked.tests["tests/qgis/test_wheel_guard.py"] == ["changed test file"]

    def test_a_changed_browser_test_goes_to_the_browser_stage(self):
        picked = _select(sel.Change("tests/browser/test_cartograph_text.py"))
        assert "tests/browser/test_cartograph_text.py" in picked.browser

    def test_a_changed_tier_conftest_selects_the_whole_tier(self):
        picked = _select(sel.Change("tests/qgis/conftest.py"))
        assert set(sel.list_test_files(REPO, "qgis")) <= set(picked.tests)
        assert picked.broad

    def test_the_shared_hermetic_helper_selects_every_tier_that_uses_qgis(self):
        picked = _select(sel.Change("tests/hermetic.py"))
        assert set(sel.list_test_files(REPO, "fixtures")) <= set(picked.tests)
        assert set(sel.list_test_files(REPO, "qgis")) <= set(picked.tests)

    def test_a_support_module_selects_only_the_tests_that_import_it(self):
        # The old selector widened any helper change to the whole tier ("or True").
        picked = _select(sel.Change("tests/qgis/pdf_text.py"))
        qgis = [t for t in picked.tests if t.startswith("tests/qgis/")]
        assert qgis, "its importers must run"
        assert len(qgis) < len(sel.list_test_files(REPO, "qgis")) / 2
        assert not picked.broad

    def test_a_helper_nobody_imports_cannot_be_narrowed_and_goes_broad(self, tmp_path):
        (tmp_path / "tests" / "qgis").mkdir(parents=True)
        (tmp_path / "tests" / "qgis" / "stray_helper.py").write_text("X = 1\n")
        (tmp_path / "tests" / "qgis" / "test_a.py").write_text("def test_a(): pass\n")
        groups = {"groups": {}, "sources": [], "compat344": [], "unit_always": False}
        picked = sel.select(
            [sel.Change("tests/qgis/stray_helper.py")], tmp_path, groups
        )
        assert picked.broad
        assert "tests/qgis/test_a.py" in picked.tests

    def test_a_deleted_test_file_is_not_scheduled(self):
        picked = _select(sel.Change("tests/qgis/test_gone.py", "D"))
        assert "tests/qgis/test_gone.py" not in picked.tests


class TestEveryPatchRunHasTheUnitTierAndAReasonPerTest:
    def test_the_unit_tier_is_always_selected(self):
        picked = _select(sel.Change("README.md"))
        assert set(sel.list_test_files(REPO, "unit")) <= set(picked.tests)

    def test_a_documentation_change_needs_nothing_beyond_the_unit_tier(self):
        picked = _select(sel.Change("docs/the-dialog.md"), sel.Change("CHANGELOG.md"))
        assert not picked.broad
        assert all(t.startswith("tests/unit/") for t in picked.tests)
        assert not picked.compat

    def test_every_selected_target_names_why(self):
        picked = _select(
            _function_change(CONTROLLER, "CartographController._write_atomic"),
            sel.Change("nika_onlymap_exporter/core/go_to_location.py"),
        )
        assert picked.tests
        assert all(reasons for reasons in picked.tests.values())
        assert any(
            "group location_search" in r
            for r in picked.tests["tests/qgis/test_go_to_location.py"]
        )

    def test_a_class_target_is_dropped_when_its_whole_file_is_selected(self):
        picked = _select(
            sel.Change("tests/qgis/test_cartograph_dialog.py"),
            _function_change(CONTROLLER, "CartographController._write_atomic"),
        )
        assert "tests/qgis/test_cartograph_dialog.py" in picked.tests
        assert not [
            t
            for t in picked.tests
            if t.startswith("tests/qgis/test_cartograph_dialog.py::")
        ]


class TestFocusedCompatSuiteCoversTheSpec:
    @pytest.mark.parametrize(
        "needle",
        [
            "plugin loading",
            "UI construction",
            "Qt5",
            "Save As",
            "destination ownership",
            "overwrite cancellation",
            "recovery",
            "layout switching",
            "pending saves",
            "closing",
            "project clearing",
            "worker lifetime",
            "vector export",
            "raster export",
            "atlas",
            "printing",
            "Value Relation ordering",
        ],
    )
    def test_each_required_area_has_an_entry(self, needle):
        covers = " | ".join(e["covers"] for e in sel.load_groups(REPO)["compat344"])
        assert needle.lower() in covers.lower()

    def test_no_entry_appears_twice(self):
        targets = [e["target"] for e in sel.load_groups(REPO)["compat344"]]
        assert len(targets) == len(set(targets))


class TestChangedFunctionsComeFromTheDiff:
    @pytest.fixture
    def repo(self, tmp_path):
        if shutil.which("git") is None:
            pytest.skip("git is not available")

        def git(*args):
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(tmp_path),
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    *args,
                ],
                check=True,
                capture_output=True,
            )

        git("init", "-q")
        (tmp_path / "m.py").write_text(
            "import os\n\nclass A:\n    def one(self):\n        return 1\n\n"
            "    def two(self):\n        return 2\n\n\ndef free():\n    return 3\n"
        )
        git("add", ".")
        git("commit", "-q", "-m", "base")
        return tmp_path, git

    def _changes(self, repo, new_text):
        path, git = repo
        (path / "m.py").write_text(new_text)
        git("commit", "-qam", "edit")
        return {c.path: c for c in sel.changes_from_git(path, "HEAD~1")}

    def test_an_edit_inside_a_method_names_that_method(self, repo):
        text = (repo[0] / "m.py").read_text().replace("return 2", "return 22")
        change = self._changes(repo, text)["m.py"]
        assert change.symbols == frozenset({"A.two"})

    def test_an_edit_outside_every_function_is_the_whole_file(self, repo):
        text = (repo[0] / "m.py").read_text().replace("import os", "import os, sys")
        assert self._changes(repo, text)["m.py"].symbols is None

    def test_a_deleted_function_is_still_attributed(self, repo):
        text = (repo[0] / "m.py").read_text().replace("def free():\n    return 3\n", "")
        change = self._changes(repo, text)["m.py"]
        assert change.symbols is None or "free" in change.symbols

    def test_a_new_untracked_file_counts_in_working_tree_mode(self, repo):
        path, _ = repo
        (path / "new.py").write_text("x = 1\n")
        found = {
            c.path for c in sel.changes_from_git(path, "HEAD", include_worktree=True)
        }
        assert "new.py" in found


class TestShardsAreBalancedByRecordedTime:
    DURATIONS: ClassVar[dict] = {
        "default_file_seconds": 10.0,
        "qgis42_files": {
            f"tests/qgis/test_{n}.py": float(s)
            for n, s in enumerate(
                [300, 220, 190, 120, 100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 5, 5]
            )
        },
    }

    def test_every_target_lands_in_exactly_one_shard(self):
        targets = [*self.DURATIONS["qgis42_files"], "tests/qgis/test_new.py"]
        shards = sel.balance(targets, 2, self.DURATIONS)
        flat = [t for items, _ in shards for t in items]
        assert sorted(flat) == sorted(targets)

    def test_the_split_is_far_better_than_by_file_count(self):
        targets = list(self.DURATIONS["qgis42_files"])
        (_, a), (_, b) = sel.balance(targets, 2, self.DURATIONS)
        assert abs(a - b) <= 10  # longest-first gets within one small file

    def test_an_unrecorded_file_is_costed_at_the_default(self):
        assert sel.estimate_seconds("tests/qgis/test_new.py", self.DURATIONS) == 10.0

    def test_more_shards_than_targets_is_clamped(self):
        assert len(sel.balance(["tests/qgis/test_0.py"], 4, self.DURATIONS)) == 1

    def test_the_split_is_deterministic(self):
        targets = list(self.DURATIONS["qgis42_files"])
        assert sel.balance(targets, 2, self.DURATIONS) == sel.balance(
            list(reversed(targets)), 2, self.DURATIONS
        )


class TestFixtureReuseIsKeyedByEveryInput:
    BASE = ("tree-a", "builders-a", "runtime-a", "env-a")

    def test_the_same_inputs_give_the_same_key(self):
        assert sel.fixture_key(*self.BASE) == sel.fixture_key(*self.BASE)

    @pytest.mark.parametrize("position", range(4))
    def test_changing_any_one_input_changes_the_key(self, position):
        changed = list(self.BASE)
        changed[position] += "-changed"
        assert sel.fixture_key(*changed) != sel.fixture_key(*self.BASE)
