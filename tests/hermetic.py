"""Keep a QGIS process that drives the plugin inside one scratch directory.

The dialog saves on its own: a cartograph autosaves the moment a layout is
shown, into Documents/QGIS2WebMap unless told otherwise. A test or a one-off
probe that forgets that writes into the developer's real Documents, and one
did (four atlas files, 2026-10-22). Everything a run could leave behind is
pointed at `scratch` here, before the plugin can make a folder:

- the default save root (`QGIS2WEBMAP_SAVE_ROOT`, read by the plugin itself),
- the QGIS profile `QgsApplication` keeps its own settings and databases in,
- `QSettings`, including the user's chosen save folder,
- QGIS's network cache, which every QGIS on the machine shares: a test that
  puts a recorded answer in it under a real service's address would hand that
  answer to the user's own QGIS for as long as it stays fresh (it did, for a
  day, 2026-11-06).

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
    isolate_network_cache(scratch / "cache")
    profile = scratch / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    # The variable, not the constructor's profile argument: PyQGIS 4.2
    # ignores the argument and keeps ~/.local/share/profiles/default.
    os.environ["QGIS_CUSTOM_CONFIG_PATH"] = str(profile)
    app = QgsApplication([], gui)
    QgsApplication.initQgis()
    # After `initQgis`, which points QSettings at the profile it was given.
    isolate_settings(scratch / "qsettings")
    pin_application_font()
    return app


def isolate_network_cache(directory: Path) -> None:
    """Point QGIS's network cache into `directory`. Call before the
    `QgsApplication`: Qt places the cache under `XDG_CACHE_HOME`, read once,
    and QGIS shares one disk cache per process."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CACHE_HOME"] = str(directory)


def pin_application_font(point_size: float = 9.0) -> None:
    """Give every Qt the same application font size.

    With no desktop theme to ask, Qt 5's fallback application font is 12 pt
    and Qt 6's is 9 pt, so the same layout assertions measured different text
    on QGIS 3 and 4. A real desktop supplies the user's own font size either
    way; the tests pin the one the layouts are specified at.
    """
    from qgis.PyQt.QtWidgets import QApplication

    font = QApplication.font()
    if font.pointSizeF() != point_size:
        font.setPointSizeF(point_size)
        QApplication.setFont(font)


def stop_qgis() -> None:
    """Close every widget, then QGIS: the order `tests/conftest.py` explains.
    Widgets freed after `exitQgis` segfault the process on its way out."""
    from qgis.core import QgsApplication, QgsProject
    from qgis.PyQt.QtWidgets import QApplication

    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, 0)
    QApplication.processEvents()
    QgsProject.instance().clear()
    QgsApplication.exitQgis()


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
    app = start_qgis(tmp_path_factory.mktemp("qgis"))
    yield app
    stop_qgis()


@pytest.fixture
def project(qgis_app):
    """An empty project, cleared before and after each probe test."""
    from qgis.core import QgsProject

    instance = QgsProject.instance()
    instance.clear()
    yield instance
    instance.clear()


@pytest.fixture(autouse=True)
def hermetic_settings(tmp_path_factory):
    """Fresh settings for every probe test, saves included."""
    from qgis.PyQt.QtCore import QSettings

    previous = QSettings.defaultFormat()
    isolate_settings(tmp_path_factory.mktemp("qsettings"))
    yield
    QSettings.setDefaultFormat(previous)
