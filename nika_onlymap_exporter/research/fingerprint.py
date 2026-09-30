"""Local fingerprints: has this map been reported, and has it changed since?

A map report is sent once per map, and again only when the map's structure
changes. Deciding that needs a stable name for "this map" that is not the map's
name, path or anything else identifying - so it is a salted SHA-256 over the
project's identity plus its sorted structure.

**Neither the salt nor any hash is ever sent.** The salt is random per install
and exists so that the hashes, which sit in a local file, cannot be matched
against a dictionary of likely project paths by anyone who reads that file.

Two hashes, for two questions:

* `structure_fingerprint` - project identity plus every layer's name, kind and
  sorted field names. It changes when a layer is added, removed or renamed or
  its fields change, which is exactly the spec's "resend" condition.
* `map_key` - project identity alone. It is what the weekly tally counts as one
  map: a monitoring map that gains a layer is still the same map, and counting
  it as new would hide the recurrence the tally exists to measure.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import secrets

from ..core.export_ir import ExportProject

SALT_BYTES = 32


def new_salt() -> str:
    return secrets.token_hex(SALT_BYTES)


def _digest(salt: str, material: object) -> str:
    payload = json.dumps(material, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((salt + "\x00" + payload).encode("utf-8")).hexdigest()


def structure(export: ExportProject) -> list[list[object]]:
    """Every exported layer's name, kind and sorted field names, sorted."""
    rows: list[list[object]] = []
    for layer in export.exportable_layers:
        fields = sorted({spec.name for spec in layer.popup.fields})
        rows.append([layer.name, layer.geometry_kind.value, fields])
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False))
    return rows


def structure_fingerprint(salt: str, identity: str, export: ExportProject) -> str:
    return _digest(salt, ["structure", identity, structure(export)])


def map_key(salt: str, identity: str) -> str:
    return _digest(salt, ["map", identity])
