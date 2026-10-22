"""Where maps save when the user has not said: Documents/QGIS2WebMap.

A new map used to have no file at all until the user browsed for one - and
a cartograph in an unsaved project wrote nothing until then. Now each kind
has its own folder under one root, so every web map sits together and every
cartograph sits together, and nobody has to open a file dialog to start:

    Documents/QGIS2WebMap/Web maps/parcels.html
    Documents/QGIS2WebMap/Cartographs/parcels-Survey.html

Documents rather than the home folder or Downloads: it is where people look
for files they made, and nothing cleans it out. The root is a per-user
setting (Settings > Saving). The folder names stay English in every language,
so a path does not move when the dialog's language changes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from pathlib import Path

from qgis.PyQt.QtCore import QSettings, QStandardPaths

#: The user's chosen root, when they have changed it in Settings.
SAVE_FOLDER_KEY = "qgis2webmap/saveFolder"
ROOT_NAME = "QGIS2WebMap"
WEB_MAPS = "Web maps"
CARTOGRAPHS = "Cartographs"
#: Replaces Documents/QGIS2WebMap as the default root for a whole process -
#: a managed machine's shared drive, or a test run that must not touch the
#: user's own Documents (see tests/hermetic.py).
SAVE_ROOT_ENV = "QGIS2WEBMAP_SAVE_ROOT"


def default_save_root() -> Path:
    """Documents/QGIS2WebMap. Qt knows where Documents really is - a
    translated name, or a folder OneDrive moved - so it is asked first.
    `QGIS2WEBMAP_SAVE_ROOT`, when set, wins over both."""
    override = os.environ.get(SAVE_ROOT_ENV)
    if override:
        return Path(override)
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    return (Path(documents) if documents else Path.home() / "Documents") / ROOT_NAME


def save_root(qsettings: QSettings | None = None) -> Path:
    """The chosen root, or the default when none is chosen."""
    stored = (qsettings or QSettings()).value(SAVE_FOLDER_KEY, "", type=str)
    return Path(stored) if stored else default_save_root()


def set_save_root(path: Path | None, qsettings: QSettings | None = None) -> None:
    """Choose a root; `None` goes back to the default."""
    settings = qsettings or QSettings()
    if path is None:
        settings.remove(SAVE_FOLDER_KEY)
    else:
        settings.setValue(SAVE_FOLDER_KEY, str(path))


def web_maps_folder(qsettings: QSettings | None = None) -> Path:
    return save_root(qsettings) / WEB_MAPS


def cartographs_folder(qsettings: QSettings | None = None) -> Path:
    return save_root(qsettings) / CARTOGRAPHS


def unused_path(path: Path) -> Path:
    """`path`, or `name (2).html`, `name (3).html`... - the first one not
    there yet. A default must never land on a file someone else's map owns,
    and a cartograph saves itself, so it would overwrite it unasked."""
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1
