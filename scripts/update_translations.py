#!/usr/bin/env python3
"""Collect the plugin's translatable text into `i18n/*.ts`, then compile them.

    python scripts/update_translations.py            # refresh every .ts, build .qm
    python scripts/update_translations.py pt_BR      # also start a new language
    python scripts/update_translations.py --check    # fail if anything is stale

**Why not `pylupdate6`.** It finds `self.tr()` on QObject subclasses and works
out a context per class. This plugin uses one context and module-level
`tr()` / `N_()` / `plural()` helpers (see `translation.py`), which it does not
recognise. Reading the literals out of the syntax tree is a few lines and
finds exactly what the helpers will look up - adjacent string literals are
already joined by the parser, so a sentence split across lines is one entry.

Existing translations are kept for every source string that still exists.
A string that disappears from the code is dropped; one that changes wording
is a new, untranslated entry, which is correct - the old translation said the
old thing.

**Disambiguation.** `tr(text, disambiguation)` and `N_(text, disambiguation)`
(positional or by keyword, a string literal) make the same English text a
separate entry, written with its `<comment>` - Qt's disambiguation, which
`lrelease` keys the compiled entry by. An entry is the pair, so adding a
disambiguation to one use of a text leaves the others' translation alone
and starts the new use untranslated.

Compiling needs Qt's `lrelease` (set `LRELEASE` or put it on PATH). Without
it the `.ts` files are still refreshed and the command says what it skipped.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "nika_onlymap_exporter"
I18N_DIR = PACKAGE_DIR / "i18n"
CONTEXT = "qgis2webmap"
# Marker -> (positions of its text arguments, position of its disambiguation
# or None). A disambiguation may also be passed as `disambiguation=`.
MARKERS: dict[str, tuple[tuple[int, ...], int | None]] = {
    "tr": ((0,), 1),
    "N_": ((0,), 1),
    "plural": ((1, 2), None),
}

# A catalogue entry: its source text and its disambiguation ("" for none).
Entry = tuple[str, str]


def _disambiguation(call: ast.Call, position: int | None, where: str) -> str:
    """The literal disambiguation of a marker call, or "" if it has none."""
    if position is None:
        return ""
    if position < len(call.args):
        arg: ast.expr | None = call.args[position]
    else:
        arg = next((k.value for k in call.keywords if k.arg == "disambiguation"), None)
    if arg is None:
        return ""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    # A computed one would be looked up at runtime under a key the
    # catalogue never had, and silently fall back to the plain entry.
    raise SystemExit(f"{where}: the disambiguation must be a string literal")


def collect() -> dict[Entry, list[str]]:
    """Every marked literal, in first-seen order, with where it appears."""
    found: dict[Entry, list[str]] = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in MARKERS:
                continue
            indexes, disambiguation_at = MARKERS[node.func.id]
            where = f"{path.relative_to(PACKAGE_DIR).as_posix()}:{node.lineno}"
            for index in indexes:
                if index < len(node.args):
                    arg = node.args[index]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        disambiguation = _disambiguation(node, disambiguation_at, where)
                        found.setdefault((arg.value, disambiguation), []).append(where)
    return found


def _entry(message: ET.Element) -> Entry:
    return (message.findtext("source") or "", message.findtext("comment") or "")


def read_existing(path: Path) -> dict[Entry, str]:
    if not path.is_file():
        return {}
    translations: dict[Entry, str] = {}
    for message in ET.parse(path).getroot().iter("message"):
        translation = message.find("translation")
        if (
            translation is not None
            and translation.text
            and (translation.get("type") not in ("unfinished", "obsolete", "vanished"))
        ):
            translations[_entry(message)] = translation.text
    return translations


def render(
    language: str, sources: dict[Entry, list[str]], known: dict[Entry, str]
) -> str:
    root = ET.Element("TS", version="2.1", language=language)
    context = ET.SubElement(root, "context")
    ET.SubElement(context, "name").text = CONTEXT
    for entry, places in sources.items():
        source, disambiguation = entry
        message = ET.SubElement(context, "message")
        for place in places:
            filename, line = place.rsplit(":", 1)
            ET.SubElement(message, "location", filename=f"../{filename}", line=line)
        ET.SubElement(message, "source").text = source
        if disambiguation:
            ET.SubElement(message, "comment").text = disambiguation
        translation = ET.SubElement(message, "translation")
        if entry in known:
            translation.text = known[entry]
        else:
            translation.set("type", "unfinished")
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n' + (
        ET.tostring(root, encoding="unicode") + "\n"
    )


PACKS_FILE = I18N_DIR / "packs.json"


def pack_hashes(ts_files: list[Path]) -> dict[str, str]:
    """Language code -> SHA-256 of its compiled pack.

    Shipped in the plugin as `i18n/packs.json`: a downloaded pack is installed
    only if it matches, so this is the list of exactly what was released.
    """
    hashes = {}
    for ts in ts_files:
        qm = ts.with_suffix(".qm")
        if qm.is_file():
            hashes[ts.stem.split("_", 1)[1]] = hashlib.sha256(
                qm.read_bytes()
            ).hexdigest()
    return dict(sorted(hashes.items()))


# Secret scanners cannot tell a checksum from a key by looking, and the
# plugin repository's scan flags every digest here. Like runtime-lock.json, each
# digest line carries detect-secrets' own allowlist pragma, on the same line -
# the only place it is read.
CHECKSUM_NOTE = "checksum of a released pack # pragma: allowlist secret"


def write_packs(ts_files: list[Path]) -> None:
    lines = [
        f'    "{code}": {{"sha256": "{digest}", "_": "{CHECKSUM_NOTE}"}}'
        for code, digest in pack_hashes(ts_files).items()
    ]
    PACKS_FILE.write_text(
        '{\n  "packs": {\n' + ",\n".join(lines) + "\n  }\n}\n", encoding="utf-8"
    )


def compile_qm(ts_files: list[Path]) -> bool:
    lrelease = os.environ.get("LRELEASE") or shutil.which("lrelease")
    if not lrelease:
        print("lrelease not found: .ts refreshed, .qm NOT rebuilt (set LRELEASE)")
        return False
    for ts in ts_files:
        subprocess.run(
            [lrelease, "-silent", str(ts), "-qm", str(ts.with_suffix(".qm"))],
            check=True,
        )
    return True


def main(argv: list[str]) -> int:
    check = "--check" in argv
    new_languages = [a for a in argv if not a.startswith("-")]
    sources = collect()
    I18N_DIR.mkdir(exist_ok=True)
    ts_files = sorted(I18N_DIR.glob("qgis2webmap_*.ts"))
    ts_files += [
        I18N_DIR / f"qgis2webmap_{lang}.ts"
        for lang in new_languages
        if I18N_DIR / f"qgis2webmap_{lang}.ts" not in ts_files
    ]

    stale = []
    for ts in ts_files:
        language = ts.stem.split("_", 1)[1]
        known = read_existing(ts)
        text = render(language, sources, known)
        missing = sum(1 for s in sources if s not in known)
        if check:
            # The strings, not the file: locations move with every edit, and a
            # check that failed on a shifted line number would train people to
            # regenerate without looking.
            listed = (
                {_entry(m) for m in ET.parse(ts).getroot().iter("message")}
                if ts.is_file()
                else set()
            )
            if listed != set(sources) or missing:
                stale.append(
                    f"{ts.name}: {missing} untranslated, "
                    f"{len(set(sources) - listed)} not in the catalogue, "
                    f"{len(listed - set(sources))} no longer in the code"
                )
            continue
        ts.write_text(text, encoding="utf-8")
        print(f"{ts.name}: {len(sources)} strings, {missing} untranslated")

    if check:
        entries = (
            json.loads(PACKS_FILE.read_text()).get("packs")
            if PACKS_FILE.is_file()
            else {}
        )
        pinned = {code: entry.get("sha256") for code, entry in entries.items()}
        if pinned != pack_hashes(ts_files):
            stale.append("packs.json does not match the compiled packs")
        for line in stale:
            print(line)
        return 1 if stale else 0
    if compile_qm(ts_files):
        write_packs(ts_files)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
