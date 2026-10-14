"""Is NIKA desktop installed on this computer, and where?

**A path probe, nothing else.** Issue #3 section 4 step 1: no network request,
no process started. The plugin makes no network calls of its own beyond the
runtime download (CONTRIBUTING.md rule 5), and asking a server "is this app
installed?" would be both a new request and a worse answer than the disk gives.

**Every platform is probed through one injectable `Probe`.** Detection reads the
home directory, the environment, `which` and - on Windows - the uninstall keys
through that object, so `tests/unit/test_nika_desktop.py` runs the macOS,
Windows and Linux branches on any machine against a temporary directory.

**Two product names.** The issue calls the app "NIKA"; the Tauri build ships as
"Nika Agent" (`nika-agent/src-tauri/tauri.conf.json` productName), which is the
name its installers actually use for the bundle, the install folder and the
uninstall entry. Both are probed until the name settles.

**Launching is not here.** Opening the app needs Qt (`QDesktopServices` or
`QProcess`), so it lives in `ui/handoff_dialog.py`; this module only says what
to open. `DesktopApp.command` is set when the thing found is a program to run
(a Windows `.exe`, a Linux binary), and empty when it is a bundle the desktop
opens (a macOS `.app`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

PRODUCT_NAMES = ("NIKA", "Nika Agent")
LINUX_COMMANDS = ("nika", "nika-agent")
DOWNLOAD_URL = "https://www.nikaplanet.com/download"


@dataclass(frozen=True)
class DesktopApp:
    """What the probe found. `path` and `version` are None when not found."""

    found: bool
    path: Path | None = None
    version: str | None = None
    command: tuple[str, ...] = ()


NOT_FOUND = DesktopApp(found=False)


@dataclass(frozen=True)
class UninstallEntry:
    """The fields of one Windows uninstall key this module reads."""

    display_name: str
    display_version: str | None = None
    install_location: str | None = None
    display_icon: str | None = None


@dataclass(frozen=True)
class Probe:
    """Everything detection reads from the machine, so tests can replace it."""

    platform: str
    home: Path
    environ: Mapping[str, str]
    which: Callable[[str], str | None]
    uninstall_entries: Callable[[], list[UninstallEntry]] = field(default=lambda: [])
    # Where absolute system folders (/Applications) are looked up: "/" outside
    # tests, a temporary directory inside them.
    root: Path = Path("/")


def system_probe() -> Probe:
    """The probe for the machine QGIS is running on."""
    return Probe(
        platform=sys.platform,
        home=Path.home(),
        environ=dict(os.environ),
        which=shutil.which,
        uninstall_entries=_windows_uninstall_entries,
    )


def detect(probe: Probe | None = None) -> DesktopApp:
    """Find NIKA desktop. Never raises: an unreadable path is "not found"."""
    probe = probe or system_probe()
    try:
        if probe.platform == "darwin":
            return _detect_macos(probe)
        if probe.platform.startswith("win"):
            return _detect_windows(probe)
        return _detect_linux(probe)
    except OSError:
        return NOT_FOUND


# ---- macOS -----------------------------------------------------------------


def _detect_macos(probe: Probe) -> DesktopApp:
    for folder in (probe.root / "Applications", probe.home / "Applications"):
        for name in PRODUCT_NAMES:
            bundle = folder / f"{name}.app"
            if bundle.is_dir():
                return DesktopApp(True, bundle, _plist_version(bundle))
    return NOT_FOUND


def _plist_version(bundle: Path) -> str | None:
    """`CFBundleShortVersionString` - the version the Finder shows."""
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    version = info.get("CFBundleShortVersionString") if isinstance(info, dict) else None
    return (version.strip() or None) if isinstance(version, str) else None


# ---- Windows ---------------------------------------------------------------


def _detect_windows(probe: Probe) -> DesktopApp:
    # The uninstall key first: it is the one place that also has the version.
    for entry in probe.uninstall_entries():
        if not _is_ours(entry.display_name):
            continue
        exe = _exe_from_entry(entry)
        if exe is not None:
            return DesktopApp(True, exe, _clean(entry.display_version), (str(exe),))

    local = probe.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
        for name in PRODUCT_NAMES:
            # `Programs\NIKA` per the issue; `<productName>` directly under
            # LOCALAPPDATA is where Tauri's per-user NSIS installer puts it.
            for folder in (base / "Programs" / name, base / name):
                exe = _exe_in(folder)
                if exe is not None:
                    return DesktopApp(True, exe, None, (str(exe),))
    return NOT_FOUND


def _is_ours(display_name: str) -> bool:
    lowered = display_name.strip().lower()
    return any(
        lowered == name.lower() or lowered.startswith(name.lower() + " ")
        for name in PRODUCT_NAMES
    )


def _exe_from_entry(entry: UninstallEntry) -> Path | None:
    icon = (entry.display_icon or "").split(",")[0].strip().strip('"')
    if icon.lower().endswith(".exe") and Path(icon).is_file():
        return Path(icon)
    if entry.install_location:
        return _exe_in(Path(entry.install_location.strip().strip('"')))
    return None


def _exe_in(folder: Path) -> Path | None:
    """The app's executable in `folder`: a product-named one, else the only other."""
    if not folder.is_dir():
        return None
    for name in PRODUCT_NAMES:
        exe = folder / f"{name}.exe"
        if exe.is_file():
            return exe
    candidates = sorted(
        p
        for p in folder.glob("*.exe")
        if p.is_file() and not p.name.lower().startswith("uninstall")
    )
    return candidates[0] if len(candidates) == 1 else None


# `winreg` exists only on Windows, and this module is imported everywhere. A
# module-level platform branch rather than a guard inside the function, so
# mypy checks the registry code against the Windows stubs and skips it on Linux.
if sys.platform == "win32":
    import winreg

    def _windows_uninstall_entries() -> list[UninstallEntry]:
        """Every uninstall entry in the per-user and per-machine keys.

        A key that cannot be read is skipped rather than failing the whole probe.
        """

        def read(key: winreg.HKEYType, name: str) -> str | None:
            try:
                data = winreg.QueryValueEx(key, name)[0]
            except OSError:
                return None
            return data if isinstance(data, str) else None

        entries: list[UninstallEntry] = []
        path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(root, path)
            except OSError:
                continue
            with key:
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(key, sub_name) as sub:
                            name = read(sub, "DisplayName")
                            if name:
                                entries.append(
                                    UninstallEntry(
                                        display_name=name,
                                        display_version=read(sub, "DisplayVersion"),
                                        install_location=read(sub, "InstallLocation"),
                                        display_icon=read(sub, "DisplayIcon"),
                                    )
                                )
                    except OSError:
                        continue
        return entries

else:

    def _windows_uninstall_entries() -> list[UninstallEntry]:
        return []


# ---- Linux -----------------------------------------------------------------


def _detect_linux(probe: Probe) -> DesktopApp:
    for entry in _desktop_files(probe):
        fields = _desktop_entry(entry)
        if fields is None:
            continue
        command = _exec_command(fields.get("Exec", ""), probe)
        return DesktopApp(True, entry, _clean(fields.get("X-AppVersion")), command)

    for name in LINUX_COMMANDS:
        found = probe.which(name)
        if found:
            return DesktopApp(True, Path(found), None, (found,))
    return NOT_FOUND


def _desktop_files(probe: Probe) -> list[Path]:
    """`nika*.desktop` in the user's and the system's applications folders.

    Matched case-insensitively: the Tauri .deb installs "Nika Agent.desktop".
    """
    data_home = probe.environ.get("XDG_DATA_HOME") or str(
        probe.home / ".local" / "share"
    )
    data_dirs = probe.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    folders = [Path(data_home)] + [Path(d) for d in data_dirs.split(":") if d]
    found: list[Path] = []
    for folder in folders:
        applications = folder / "applications"
        if not applications.is_dir():
            continue
        found.extend(
            sorted(
                p
                for p in applications.iterdir()
                if p.name.lower().startswith("nika") and p.suffix == ".desktop"
            )
        )
    return found


def _desktop_entry(path: Path) -> dict[str, str] | None:
    """The `[Desktop Entry]` group's keys, or None if unreadable or hidden.

    Hand-parsed: `configparser` rejects legal desktop files (repeated keys,
    `%` in Exec lines, localised `Name[fr]` keys), and only a handful of keys
    matter here.

    `Version` is deliberately not read. In a desktop file it is the version of
    the Desktop Entry *specification* ("1.0", "1.5"), not the app's, and
    showing "1.5 found" would be a confident wrong number. Only `X-AppVersion`
    names the app's version.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields: dict[str, str] = {}
    in_entry = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if in_entry and "=" in line:
            key, _, value = line.partition("=")
            fields.setdefault(key.strip(), value.strip())
    if fields.get("Hidden", "").lower() == "true":
        return None
    return fields


def _exec_command(exec_line: str, probe: Probe) -> tuple[str, ...]:
    """The `Exec=` line as a program and arguments, field codes removed."""
    try:
        parts = shlex.split(exec_line)
    except ValueError:
        return ()
    parts = [p for p in parts if not (len(p) == 2 and p.startswith("%"))]
    if not parts:
        return ()
    program = parts[0]
    if not os.path.isabs(program):
        program = probe.which(program) or program
    return (program, *parts[1:])


def _clean(version: str | None) -> str | None:
    return (version.strip() or None) if version else None
