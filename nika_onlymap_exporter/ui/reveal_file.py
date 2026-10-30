"""Show a saved file in the platform's file manager.

`QDesktopServices.openUrl(folder)` hands the folder to whatever the desktop
lists as the default opener of `inode/directory`. On a machine where that is a
code editor (VS Code registers itself so) the folder opens in the editor. A
reveal must go to a file manager, and select the file where the platform can:

- Linux: the `org.freedesktop.FileManager1` D-Bus service (`ShowItems`, or
  `ShowFolders` for a folder), which Nautilus, Dolphin, Nemo, Thunar, PCManFM
  and others implement. Asked over QtDBus; nothing is launched by us.
- Windows: `explorer /select,<file>`.
- macOS: `open -R <file>`.

When Linux has no such service, the folder is opened with the default opener
only if that opener is itself a file manager (its desktop entry is in the
`FileManager` category). Otherwise nothing is opened and the outcome says why,
so the caller can offer the path instead of surprising the user with an editor.
No MIME association is changed and no file manager is named in this code.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import configparser
import importlib
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from qgis.PyQt.QtCore import QProcess, QUrl
from qgis.PyQt.QtGui import QDesktopServices

from .i18n import tr

FILE_MANAGER_SERVICE = "org.freedesktop.FileManager1"
FILE_MANAGER_PATH = "/org/freedesktop/FileManager1"
FILE_MANAGER_INTERFACE = "org.freedesktop.FileManager1"
SHOW_ITEMS = "ShowItems"
SHOW_FOLDERS = "ShowFolders"
#: A file manager may need to start before it answers.
DBUS_TIMEOUT_MS = 10_000
DIRECTORY_MIME = "inode/directory"
FILE_MANAGER_CATEGORY = "FileManager"


@dataclass(frozen=True)
class RevealOutcome:
    """What a reveal did. `selected`: the file itself was highlighted in a file
    manager; otherwise only `folder` was opened. When `ok` is False nothing was
    opened and `reason` says why."""

    ok: bool
    selected: bool
    folder: Path
    reason: str = ""


# ---- which application opens folders ------------------------------------


def _config_homes(env: Mapping[str, str]) -> list[Path]:
    home = Path(env.get("HOME") or Path.home())
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    data = Path(env.get("XDG_DATA_HOME") or home / ".local/share")
    config_dirs = [
        Path(part)
        for part in (env.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
        if part
    ]
    data_dirs = [
        Path(part)
        for part in (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(
            ":"
        )
        if part
    ]
    return [
        config,
        data / "applications",
        *config_dirs,
        *[d / "applications" for d in data_dirs],
    ]


def _ini(path: Path) -> configparser.ConfigParser | None:
    parser = configparser.ConfigParser(
        interpolation=None, strict=False, delimiters=("=",)
    )
    parser.optionxform = str  # type: ignore[assignment, method-assign]  # keys are case-sensitive
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, configparser.Error):
        return None
    return parser


def _handler_ids(env: Mapping[str, str]) -> list[str]:
    """Desktop entry ids that open folders, most preferred first."""
    homes = _config_homes(env)
    for folder in homes:
        parser = _ini(folder / "mimeapps.list")
        if parser is not None and parser.has_option(
            "Default Applications", DIRECTORY_MIME
        ):
            ids = [
                part
                for part in parser.get("Default Applications", DIRECTORY_MIME).split(
                    ";"
                )
                if part
            ]
            if ids:
                return ids
    for folder in homes:
        parser = _ini(folder / "mimeinfo.cache")
        if parser is not None and parser.has_option("MIME Cache", DIRECTORY_MIME):
            ids = [
                part
                for part in parser.get("MIME Cache", DIRECTORY_MIME).split(";")
                if part
            ]
            if ids:
                return ids
    return []


def _categories(desktop_id: str, env: Mapping[str, str]) -> list[str] | None:
    for folder in _config_homes(env):
        entry = folder / desktop_id
        if not entry.is_file():
            continue
        parser = _ini(entry)
        if parser is None or not parser.has_option("Desktop Entry", "Categories"):
            return []
        return [
            part
            for part in parser.get("Desktop Entry", "Categories").split(";")
            if part
        ]
    return None


def default_folder_opener_is_file_manager(
    env: Mapping[str, str] | None = None,
) -> bool | None:
    """Whether the application the desktop opens folders with is a file manager
    (its entry is in the `FileManager` category). None when it cannot be told."""
    environment = os.environ if env is None else env
    for desktop_id in _handler_ids(environment):
        categories = _categories(desktop_id, environment)
        if categories is not None:
            return FILE_MANAGER_CATEGORY in categories
    return None


# ---- the platform requests (each one a seam for the tests) ---------------

_pending_calls: set[object] = set()


def _platform() -> str:
    return sys.platform


def dbus_module():  # type: ignore[no-untyped-def]  # a Qt module, absent on some builds
    """QtDBus from whichever binding QGIS runs on: `qgis.PyQt` re-exports it
    only for PyQt5, so PyQt6 is imported by its own name."""
    for name in ("qgis.PyQt.QtDBus", "PyQt6.QtDBus", "PyQt5.QtDBus"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise ImportError("QtDBus")


def _string_array(qt_dbus, value: str):  # type: ignore[no-untyped-def]
    """`[value]` as a D-Bus `as`. A plain Python list would go out as `av`, which
    the file-manager service rejects."""
    from qgis.PyQt.QtCore import QMetaType

    try:  # PyQt6
        string_id = QMetaType.Type.QString.value
        element = QMetaType(string_id)
    except (AttributeError, TypeError):  # PyQt5
        string_id = int(QMetaType.Type.QString)
        element = string_id
    argument = qt_dbus.QDBusArgument()
    argument.beginArray(element)
    argument.add(value, string_id)
    argument.endArray()
    return argument


def _session_bus():  # type: ignore[no-untyped-def]  # a QDBusConnection
    return dbus_module().QDBusConnection.sessionBus()


def _ask_file_manager(
    method: str, uri: str, done: Callable[[str | None], None]
) -> None:
    """Call FileManager1 `method` on `uri`; `done(None)` once the service
    accepted it, else `done(error text)`. Never blocks the interface."""
    try:
        qt_dbus = dbus_module()
        bus = _session_bus()
    except ImportError:
        done(tr("this QGIS has no D-Bus support"))
        return
    if not bus.isConnected():
        done(tr("there is no desktop session bus"))
        return
    message = qt_dbus.QDBusMessage.createMethodCall(
        FILE_MANAGER_SERVICE, FILE_MANAGER_PATH, FILE_MANAGER_INTERFACE, method
    )
    # (as uris, s startup_id)
    message.setArguments([_string_array(qt_dbus, uri), ""])
    watcher = qt_dbus.QDBusPendingCallWatcher(bus.asyncCall(message, DBUS_TIMEOUT_MS))
    _pending_calls.add(watcher)

    def finished() -> None:
        # An exception escaping a Qt slot aborts QGIS: nothing here may raise.
        _pending_calls.discard(watcher)
        error: str | None = None
        try:
            reply = qt_dbus.QDBusPendingReply(watcher)
            if reply.isError():
                failure = reply.error()
                error = f"{failure.name()}: {failure.message()}".rstrip(": ")
        except (RuntimeError, AttributeError) as exc:
            error = str(exc) or tr("no answer")
        watcher.deleteLater()
        done(error)

    watcher.finished.connect(finished)


def _start_detached(program: str, arguments: list[str]) -> bool:
    started = QProcess.startDetached(program, arguments)
    # PyQt5 returns a bool; PyQt6 returns (bool, pid).
    return bool(started[0] if isinstance(started, tuple) else started)


def _open_folder(folder: Path) -> bool:
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))


# ---- the public entry ------------------------------------------------------


def _nearest_folder(path: Path) -> Path:
    """The folder holding `path`, or the closest parent that exists (a file's
    folder is not made until the first save)."""
    folder = path.parent
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return folder


def reveal_file(path: Path, done: Callable[[RevealOutcome], None]) -> None:
    """Show `path` in the file manager, then call `done` once with the result.

    Success is reported only after the platform accepted the request. A file
    that does not exist yet reveals its nearest existing folder instead.
    """
    exists = path.exists()
    folder = _nearest_folder(path)
    platform = _platform()

    if platform.startswith("win") or platform == "darwin":
        if exists:
            native = os.path.normpath(str(path))
            request = (
                ("explorer", ["/select," + native])
                if platform.startswith("win")
                else ("open", ["-R", str(path)])
            )
            if _start_detached(*request):
                done(RevealOutcome(True, True, folder))
                return
        # The default opener of a folder is the file manager on these systems.
        if _open_folder(folder):
            done(RevealOutcome(True, False, folder))
        else:
            done(
                RevealOutcome(False, False, folder, tr("the system refused to open it"))
            )
        return

    method = SHOW_ITEMS if exists else SHOW_FOLDERS
    target = path if exists else folder

    def answered(error: str | None) -> None:
        if error is None:
            done(RevealOutcome(True, exists, folder))
            return
        if default_folder_opener_is_file_manager() and _open_folder(folder):
            done(RevealOutcome(True, False, folder))
            return
        done(
            RevealOutcome(
                False,
                False,
                folder,
                tr(
                    "no file manager answered ({error}), and the program that opens "
                    "folders by default is not a file manager"
                ).format(error=error),
            )
        )

    _ask_file_manager(method, QUrl.fromLocalFile(str(target)).toString(), answered)
