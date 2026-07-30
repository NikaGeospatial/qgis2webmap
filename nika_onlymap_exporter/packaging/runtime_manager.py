"""Where the OnlyMap runtime bytes come from.

This is the seam agreed while planning: **whether the runtime is vendored in the
plugin or fetched on first export is an open legal question**, so nothing above
this module needs to know. Tasks 3-5 only need bytes, a version and a hash.

Two implementations:

* `LocalRuntime` reads from a path on disk - a vendored copy inside the plugin,
  or a developer's `node_modules`. This is what runs today.
* A fetching provider will land here if the runtime ships out-of-band. It has to
  verify a SHA-256 against `runtime/runtime-lock.json`, show the licence, and
  cache in the QGIS profile so later exports work offline.

Pure Python: no PyQGIS, no Qt.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PACKAGE_ROOT / "runtime"
LOCK_FILE = RUNTIME_DIR / "runtime-lock.json"

# Filenames inside the OnlyMap package.
RUNTIME_JS = "onlymap.standalone.js"
RUNTIME_CSS = "onlymapjs.css"


class RuntimeUnavailableError(RuntimeError):
    """The runtime could not be obtained.

    Raised rather than returning a placeholder: an artifact missing its runtime
    is a blank page for the recipient, and that is precisely the silent failure
    this project refuses to ship.
    """


@dataclass(frozen=True)
class RuntimeBundle:
    """Everything an artifact needs from the runtime."""

    javascript: bytes
    css: bytes
    version: str
    sha256: str

    @property
    def total_bytes(self) -> int:
        return len(self.javascript) + len(self.css)


class RuntimeProvider(Protocol):
    def load(self) -> RuntimeBundle: ...


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover_runtime_dir() -> Path | None:
    """Find a directory containing the runtime's `dist` files.

    Checked in order of authority: an explicit override, the plugin's own
    vendored copy, then a developer checkout. The env var exists so a contributor
    can point at any build without editing code.
    """
    override = os.environ.get("ONLYMAP_RUNTIME_DIR")
    if override:
        path = Path(override)
        return path if (path / RUNTIME_JS).exists() else None

    candidates = [
        RUNTIME_DIR,
        Path.home() / "Nika/nika-agent/node_modules/@nika-js/onlymap/dist",
        Path("node_modules/@nika-js/onlymap/dist"),
    ]
    return next((c for c in candidates if (c / RUNTIME_JS).exists()), None)


class LocalRuntime:
    """Reads the runtime from a directory on disk."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory

    def load(self) -> RuntimeBundle:
        directory = self._directory or discover_runtime_dir()
        if directory is None:
            raise RuntimeUnavailableError(
                "The OnlyMap runtime was not found. Set ONLYMAP_RUNTIME_DIR to a "
                "directory containing "
                f"{RUNTIME_JS}, or install the runtime with the plugin."
            )

        js_path = directory / RUNTIME_JS
        css_path = directory / RUNTIME_CSS

        if not js_path.exists():
            raise RuntimeUnavailableError(f"{js_path} is missing.")
        if not css_path.exists():
            raise RuntimeUnavailableError(
                f"{css_path} is missing. The stylesheet is required, not optional: "
                "the no-JavaScript fallback is gated by a pure-CSS rule, so without "
                "it a mail preview shows a blank frame instead of an explanation."
            )

        javascript = js_path.read_bytes()
        return RuntimeBundle(
            javascript=javascript,
            css=css_path.read_bytes(),
            version=_read_version(directory),
            sha256=sha256_of(javascript),
        )


def _read_version(directory: Path) -> str:
    """Read the runtime version from the lock file or the package manifest."""
    if LOCK_FILE.exists():
        try:
            return str(json.loads(LOCK_FILE.read_text()).get("version", "unknown"))
        except (OSError, ValueError):  # pragma: no cover - corrupt lock file
            pass

    package_json = directory.parent / "package.json"
    if package_json.exists():
        try:
            return str(json.loads(package_json.read_text()).get("version", "unknown"))
        except (OSError, ValueError):  # pragma: no cover
            pass

    return "unknown"


def default_provider() -> RuntimeProvider:
    """The provider in use. One place to change when the source is decided."""
    return LocalRuntime()
