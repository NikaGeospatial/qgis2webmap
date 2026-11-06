"""The language packs a release ships are the ones `packs.json` pins.

The plugin zip carries no `.qm`: the release carries every pack in one more
asset, `qgis2webmap-language-packs-<version>.zip`, which the plugin downloads
and installs only when each pack's SHA-256 matches `i18n/packs.json`. A pack
that was rebuilt without refreshing the pin, or a `.ts` with no pack, would be
refused (or absent) on every user's machine after release, and nothing else
would notice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

import pytest

I18N_DIR = Path(__file__).resolve().parents[2] / "nika_onlymap_exporter" / "i18n"
PREFIX = "qgis2webmap_"


def _pinned() -> dict[str, str]:
    entries = json.loads((I18N_DIR / "packs.json").read_text(encoding="utf-8"))
    return {code: entry["sha256"] for code, entry in entries["packs"].items()}


def _languages(suffix: str) -> set[str]:
    return {p.stem[len(PREFIX) :] for p in I18N_DIR.glob(f"{PREFIX}*{suffix}")}


def test_every_pinned_pack_is_the_compiled_file_on_disk() -> None:
    mismatched = [
        code
        for code, digest in sorted(_pinned().items())
        if hashlib.sha256((I18N_DIR / f"{PREFIX}{code}.qm").read_bytes()).hexdigest()
        != digest
    ]
    assert mismatched == [], (
        "packs.json pins a different file than the committed .qm for "
        f"{mismatched}; run scripts/update_translations.py"
    )


def test_the_pinned_packs_are_exactly_the_translation_sources() -> None:
    pinned = set(_pinned())
    sources = _languages(".ts")
    assert pinned == sources, (
        f"packs.json lacks {sorted(sources - pinned)}, "
        f"pins {sorted(pinned - sources)} that have no .ts"
    )


def test_every_compiled_pack_is_pinned() -> None:
    unpinned = _languages(".qm") - set(_pinned())
    assert unpinned == set(), f"a .qm that packs.json does not pin: {sorted(unpinned)}"


def _package_plugin():
    spec = importlib.util.spec_from_file_location(
        "_package_plugin_for_packs",
        I18N_DIR.parents[1] / "scripts" / "package_plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_release_asset_holds_exactly_the_pinned_packs(
    tmp_path, monkeypatch
) -> None:
    """Built twice, the same bytes; inside, each pinned pack and nothing else."""
    packager = _package_plugin()
    i18n = tmp_path / "i18n"
    shutil.copytree(
        I18N_DIR,
        i18n,
        ignore=shutil.ignore_patterns("*.ts"),
        copy_function=shutil.copyfile,  # writable, from a read-only snapshot too
    )
    monkeypatch.setattr(packager, "I18N_DIR", i18n)
    first = packager.build_language_packs(tmp_path / "a")
    for pack in i18n.iterdir():  # a fresh checkout has other file times
        os.utime(pack, (1_900_000_000, 1_900_000_000))
    again = packager.build_language_packs(tmp_path / "b")
    assert first.read_bytes() == again.read_bytes()
    with zipfile.ZipFile(first) as archive:
        members = {i.filename: archive.read(i) for i in archive.infolist()}
        stamps = {i.date_time for i in archive.infolist()}
    assert list(members) == sorted(f"{PREFIX}{code}.qm" for code in _pinned())
    assert len(members) == 6
    assert {
        name[len(PREFIX) : -len(".qm")]: hashlib.sha256(data).hexdigest()
        for name, data in members.items()
    } == _pinned()
    assert len(stamps) == 1


def test_the_packager_refuses_a_pack_that_is_not_pinned(tmp_path, monkeypatch) -> None:
    packager = _package_plugin()
    i18n = tmp_path / "i18n"
    shutil.copytree(
        I18N_DIR,
        i18n,
        ignore=shutil.ignore_patterns("*.ts"),
        copy_function=shutil.copyfile,  # writable, from a read-only snapshot too
    )
    (i18n / f"{PREFIX}fr.qm").write_bytes(b"rebuilt, pin not refreshed")
    monkeypatch.setattr(packager, "I18N_DIR", i18n)
    with pytest.raises(SystemExit, match="update_translations"):
        packager.build_language_packs(tmp_path / "dist")


def test_the_guides_state_the_asset_s_size(tmp_path) -> None:
    """The size the guides promise is the size of what is built, give or take."""
    built_kb = _package_plugin().build_language_packs(tmp_path).stat().st_size / 1024
    docs = I18N_DIR.parents[1] / "docs"
    stated = {
        name: [
            int(n)
            for n in re.findall(
                r"about\s+(\d+)\s+KB", (docs / name).read_text(encoding="utf-8")
            )
        ]
        for name in ("privacy.md", "the-dialog.md", "llms.txt")
    }
    assert all(stated.values()), f"a guide no longer states the size: {stated}"
    wrong = {
        name: sizes
        for name, sizes in stated.items()
        if any(abs(size - built_kb) > 0.1 * built_kb for size in sizes)
    }
    assert not wrong, f"the language packs zip is {built_kb:.0f} KB; {wrong}"
