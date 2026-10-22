"""Keep a QGIS process that drives the plugin inside one scratch directory.

The dialog saves on its own: a cartograph autosaves the moment a layout is
shown, into Documents/QGIS2WebMap unless told otherwise. A test or a one-off
probe that forgets that writes into the developer's real Documents, and one
did (four atlas files, 2026-10-22). Everything a run could leave behind is
pointed at `scratch` here, before the plugin can make a folder:

- the default save root (`QGIS2WEBMAP_SAVE_ROOT`, read by the plugin itself),
- the QGIS profile `QgsApplication` keeps its own settings and databases in,
- `QSettings`, including the user's chosen save folder.

HOME is left alone; nothing is redirected by changing it.

The tiers use it through `tests/conftest.py` and `tests/qgis/conftest.py`. A
probe outside the suite uses it the same way, either as a script::

    from hermetic import start_qgis
    app = start_qgis(Path(scratch))

or as a pytest plugin, which also gives every test fresh settings::

    PYTHONPATH=$REPO:$REPO/tests:$REPO/tests/qgis python -m pytest -p hermetic ...

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Mirrors `nika_onlymap_exporter.core.save_folders.SAVE_ROOT_ENV`, written
#: out so this module can run before the plugin is importable.
SAVE_ROOT_ENV = "QGIS2WEBMAP_SAVE_ROOT"


def prepare(scratch: Path) -> None:
    """Point the plugin's default save root into `scratch`. Call before the
    plugin saves anything; it is read on every use, so before QGIS is fine."""
    os.environ[SAVE_ROOT_ENV] = str(Path(scratch) / "Documents" / "QGIS2WebMap")


def start_qgis(scratch: Path, gui: bool = True):
    """A `QgsApplication` whose profile, settings and saves live in `scratch`.

    One per process: a second `QgsApplication` crashes the interpreter.
    """
    from qgis.core import QgsApplication

    scratch = Path(scratch)
    prepare(scratch)
    profile = scratch / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    # The variable, not the constructor's profile argument: PyQGIS 4.2
    # ignores the argument and keeps ~/.local/share/profiles/default.
    os.environ["QGIS_CUSTOM_CONFIG_PATH"] = str(profile)
    app = QgsApplication([], gui)
    QgsApplication.initQgis()
    # After `initQgis`, which points QSettings at the profile it was given.
    isolate_settings(scratch / "qsettings")
    return app


def isolate_settings(directory: Path) -> None:
    """Fresh `QSettings` in `directory`, with the save folder inside it.

    Ini format because it is the only one `setPath` redirects; the native
    backend on each platform ignores it.
    """
    from qgis.PyQt.QtCore import QSettings

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(directory)
    )
    from nika_onlymap_exporter.core.save_folders import SAVE_FOLDER_KEY

    QSettings().setValue(SAVE_FOLDER_KEY, str(directory / "maps"))


# ---- As a pytest plugin (`-p hermetic`) --------------------------------------


@pytest.fixture(scope="session")
def qgis_app(tmp_path_factory):
    """A probe's QGIS, started inside the run's temporary directory."""
    pytest.importorskip("qgis.core")
    return start_qgis(tmp_path_factory.mktemp("qgis"))


@pytest.fixture(autouse=True)
def hermetic_settings(tmp_path_factory):
    """Fresh settings for every probe test, saves included."""
    from qgis.PyQt.QtCore import QSettings

    previous = QSettings.defaultFormat()
    isolate_settings(tmp_path_factory.mktemp("qsettings"))
    yield
    QSettings.setDefaultFormat(previous)
