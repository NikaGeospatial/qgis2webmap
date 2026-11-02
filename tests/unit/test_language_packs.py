"""The language packs a release ships are the ones `packs.json` pins.

The zip carries no `.qm`: the plugin downloads a pack from the tagged repository
and installs it only when its SHA-256 matches `i18n/packs.json`. A pack that was
rebuilt without refreshing the pin, or a `.ts` with no pack, would be refused (or
absent) on every user's machine after release, and nothing else would notice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
