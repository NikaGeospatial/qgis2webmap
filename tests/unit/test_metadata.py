"""Metadata and repository-shape tests that need no QGIS.

These run on any machine and in CI. They guard the things that silently break a
plugin-repository submission: version drift between files, `experimental` flipping
to True, a declared icon that isn't there, and the licence header rules.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import configparser
import re
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "nika_onlymap_exporter"
METADATA = PACKAGE_DIR / "metadata.txt"


@pytest.fixture(scope="module")
def general() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(METADATA, encoding="utf-8")
    return parser["general"]


def test_metadata_exists() -> None:
    assert METADATA.is_file()


def test_version_is_semver(general: configparser.SectionProxy) -> None:
    assert re.match(r"^\d+\.\d+\.\d+([-+].+)?$", general["version"].strip())


def test_version_matches_pyproject(general: configparser.SectionProxy) -> None:
    """metadata.txt is the source of truth; pyproject must not drift from it."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    assert match, "pyproject.toml has no version"
    assert match.group(1) == general["version"].strip()


def test_ships_as_stable_not_experimental(general: configparser.SectionProxy) -> None:
    """Issue #29: ship 0.1.0 stable. Experimental plugins are hidden by default."""
    assert general["experimental"].strip().lower() == "false"


def test_license_is_gpl2_or_later(general: configparser.SectionProxy) -> None:
    assert general["license"].strip() == "GPL-2.0-or-later"


def test_declared_icon_exists(general: configparser.SectionProxy) -> None:
    icon = general["icon"].strip()
    assert icon
    assert (PACKAGE_DIR / icon).is_file(), f"icon {icon} declared but missing"


def test_required_metadata_keys_present(general: configparser.SectionProxy) -> None:
    for key in (
        "name",
        "qgisMinimumVersion",
        "description",
        "about",
        "version",
        "author",
        "email",
        "tracker",
        "repository",
        "homepage",
        "category",
    ):
        assert key in general, f"metadata.txt missing {key}"


def test_licence_file_is_gpl2() -> None:
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Version 2, June 1991" in text


def _code_text(path: Path) -> str:
    """Return only executable tokens -- comments and string literals removed.

    A plain text search over the source cannot distinguish a forbidden call from
    a docstring explaining why it is forbidden. `plugin.py` does exactly that, so
    the guards below tokenize first. Names and operators are rejoined with spaces
    stripped so `os . _exit` still matches `os._exit`.
    """
    pieces: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.NAME, tokenize.OP):
                pieces.append(token.string)
    return "".join(pieces)


def _offenders(pattern: re.Pattern[str]) -> list[Path]:
    return [
        path.relative_to(REPO_ROOT)
        for path in PACKAGE_DIR.rglob("*.py")
        if pattern.search(_code_text(path))
    ]


def test_no_process_terminating_calls() -> None:
    """CONTRIBUTING.md rule 1 -- the plugin must never close QGIS.

    qgis2web's worst defect was `subprocess(["sudo", "apt-get", ...])` followed by
    `os._exit(0)`, which made QGIS vanish with no explanation on every non-Debian
    system. This guard makes that class of bug impossible to merge.
    """
    forbidden = re.compile(r"os\._exit|sys\.exit|QApplication\.quit|qApp\.quit")
    offenders = _offenders(forbidden)
    assert not offenders, f"process-terminating call in: {offenders}"


def test_no_package_manager_invocations() -> None:
    """CONTRIBUTING.md rule 2 -- no shelling out to a package manager.

    Checks call syntax, not string contents: a package-manager name can only
    reach a subprocess through a string literal, and those are stripped here, so
    this guards the import/call side (`subprocess`, `os.system`).
    """
    forbidden = re.compile(r"os\.system|subprocess\.(run|call|check_call|Popen)")
    offenders = _offenders(forbidden)
    assert not offenders, f"subprocess invocation in: {offenders}"


def test_every_package_has_init() -> None:
    for sub in ("core", "writers", "exporters", "packaging", "processing", "ui"):
        assert (PACKAGE_DIR / sub / "__init__.py").is_file(), (
            f"{sub} missing __init__.py"
        )


def test_class_factory_is_importable_without_qgis() -> None:
    """`classFactory` must not import PyQGIS at module import time.

    QGIS imports the package before calling classFactory; keeping the heavy
    imports inside the function is what lets this test run without QGIS present.
    """
    source = (PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "def classFactory" in source
    assert "from .plugin import" in source, (
        "plugin import should be lazy, inside classFactory"
    )
