"""A folder export replaces the previous one all-or-nothing.

Results on disk: after a failure at any stage the folder holds the previous
export byte for byte, the page still points only at files that exist, no
staging directory is left, and files the user put there are untouched.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import errno
import os
import re
import shutil
from pathlib import Path

import pytest
from test_packaging import FakeRuntime, make_layer, make_project

from nika_onlymap_exporter.core.export_ir import OutputMode
from nika_onlymap_exporter.exporters import folder as folder_module
from nika_onlymap_exporter.exporters.folder import STAGING_PREFIX
from nika_onlymap_exporter.packaging.artifact_builder import build_artifact
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

_REAL_COPY2 = shutil.copy2
_REAL_REPLACE = os.replace
_REFERENCE = re.compile(r"[\w.-]+\.(?:js|tif)")


def _export(destination: Path, *, layers: int = 1, raster: bytes | None = None) -> None:
    """The real pipeline. A raster is dropped into the staging directory the way
    the writer's raster pass does, so the exporter sees a real extra asset."""
    project = make_project(
        layers=[make_layer(layer_id=f"l{i}", name=f"Layer {i}") for i in range(layers)]
    )
    writer = OnlyMapWriter(runtime_provider=FakeRuntime())
    if raster is not None:
        real_write = writer.write

        def write_with_raster(proj, staging, *args, **kwargs):  # type: ignore[no-untyped-def]
            result = real_write(proj, staging, *args, **kwargs)
            index = Path(result.entry_path)
            (index.parent / "dem.tif").write_bytes(raster)
            index.write_text(
                index.read_text(encoding="utf-8") + "\n<!-- dem.tif -->",
                encoding="utf-8",
            )
            return result

        writer.write = write_with_raster  # type: ignore[method-assign]
    build_artifact(project, destination, mode=OutputMode.FOLDER, writer=writer)


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(folder)): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _assert_page_loads(folder: Path) -> None:
    page = (folder / "index.html").read_text(encoding="utf-8")
    for reference in set(_REFERENCE.findall(page)):
        assert (folder / reference).is_file(), f"index.html points at {reference}"


def _assert_no_staging(folder: Path) -> None:
    assert not list(folder.glob(f"{STAGING_PREFIX}*"))


def _disk_full(monkeypatch: pytest.MonkeyPatch, *, copying: str) -> None:
    def copy2(src, dst, **kwargs):  # type: ignore[no-untyped-def]
        if Path(src).name == copying:
            raise OSError(errno.ENOSPC, "No space left on device")
        return _REAL_COPY2(src, dst, **kwargs)

    monkeypatch.setattr(folder_module.shutil, "copy2", copy2)


@pytest.fixture
def previous(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    folder = tmp_path / "map"
    _export(folder)
    (folder / "notes-from-me.txt").write_text("mine")
    return folder, _snapshot(folder)


class TestFailureKeepsThePreviousExport:
    @pytest.mark.parametrize("failing", ["dem.tif", "onlymap.js", "index.html"])
    def test_disk_full_while_copying_one_file(
        self, monkeypatch, previous, failing: str
    ) -> None:
        folder, before = previous
        _disk_full(monkeypatch, copying=failing)
        with pytest.raises(OSError):
            _export(folder, layers=2, raster=b"RASTER" * 100)
        assert _snapshot(folder) == before
        _assert_page_loads(folder)
        _assert_no_staging(folder)

    def test_disk_full_at_every_step_of_the_swap(
        self, monkeypatch, previous, tmp_path: Path
    ) -> None:
        folder, before = previous
        failed_steps = 0
        for fail_at in range(1, 40):
            calls = {"n": 0}

            def replace(src, dst, _at=fail_at, _calls=calls):  # type: ignore[no-untyped-def]
                if str(folder) in str(dst) or str(folder) in str(src):
                    _calls["n"] += 1
                    if _calls["n"] == _at:
                        raise OSError(errno.ENOSPC, "No space left on device")
                return _REAL_REPLACE(src, dst)

            monkeypatch.setattr(os, "replace", replace)
            try:
                _export(folder, layers=2, raster=b"RASTER" * 100)
            except OSError:
                failed_steps += 1
                assert _snapshot(folder) == before, f"step {fail_at}"
                _assert_page_loads(folder)
                _assert_no_staging(folder)
                continue
            finally:
                monkeypatch.setattr(os, "replace", _REAL_REPLACE)
            break
        assert failed_steps >= 5, "the swap was not exercised"
        # The first step that did not fail is the success path.
        assert (folder / "dem.tif").is_file()
        _assert_page_loads(folder)


class TestSuccess:
    def test_new_export_is_complete_and_user_files_survive(self, previous) -> None:
        folder, _ = previous
        _export(folder, layers=2, raster=b"RASTER")
        assert (folder / "dem.tif").read_bytes() == b"RASTER"
        assert (folder / "notes-from-me.txt").read_text() == "mine"
        _assert_page_loads(folder)
        _assert_no_staging(folder)

    def test_removed_asset_is_dropped_after_a_good_export(self, tmp_path) -> None:
        folder = tmp_path / "map"
        _export(folder, raster=b"RASTER")
        _export(folder)
        assert not (folder / "dem.tif").exists()
        _assert_page_loads(folder)

    def test_first_export_into_a_new_folder(self, tmp_path) -> None:
        folder = tmp_path / "does" / "not" / "exist"
        _export(folder)
        assert {"index.html", "onlymap.js"} <= set(_snapshot(folder))
        _assert_no_staging(folder)

    def test_failed_first_export_leaves_no_page_and_no_staging(
        self, monkeypatch, tmp_path
    ) -> None:
        folder = tmp_path / "map"
        _disk_full(monkeypatch, copying="onlymap.js")
        with pytest.raises(OSError):
            _export(folder)
        assert not (folder / "index.html").exists()
        _assert_no_staging(folder)


class TestCrashRecovery:
    def test_stale_staging_from_a_crash_is_cleaned_and_unrelated_dirs_kept(
        self, previous
    ) -> None:
        folder, _ = previous
        stale = folder / f"{STAGING_PREFIX}dead"
        (stale / "new").mkdir(parents=True)
        (stale / "new" / "index.html").write_text("half built")
        user_dir = folder / "my-stuff"
        user_dir.mkdir()
        (user_dir / "a.txt").write_text("keep")
        _export(folder)
        assert not stale.exists()
        assert (user_dir / "a.txt").read_text() == "keep"
        _assert_page_loads(folder)

    def test_crash_during_swap_is_rolled_back_by_the_next_export(
        self, previous
    ) -> None:
        folder, before = previous
        # A process killed after moving the old index.html aside.
        stale = folder / f"{STAGING_PREFIX}dead"
        (stale / "old").mkdir(parents=True)
        os.replace(folder / "index.html", stale / "old" / "index.html")
        folder_module._recover_interrupted(folder)
        assert _snapshot(folder) == before
        _assert_no_staging(folder)


class TestPublishOrderMechanism:
    """Checks the mechanism (advisory): a page is only swapped in after the assets
    it points at, so a reader mid-swap never sees a page without its files."""

    def test_index_html_is_moved_into_place_last(self, monkeypatch, tmp_path) -> None:
        folder = tmp_path / "map"
        landed: list[str] = []

        def replace(src, dst):  # type: ignore[no-untyped-def]
            if Path(dst).parent == folder and not Path(dst).name.startswith("."):
                landed.append(Path(dst).name)
            return _REAL_REPLACE(src, dst)

        monkeypatch.setattr(os, "replace", replace)
        _export(folder, raster=b"RASTER")
        assert landed[-1] == "index.html"
