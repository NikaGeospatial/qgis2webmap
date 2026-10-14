"""NIKA desktop detection on macOS, Windows and Linux, from any machine.

Every branch reads the disk through an injected `Probe`, so each platform's
layout is built in a temporary directory here and the real probe never runs.
Install layouts follow the issue (NIKA.app, %LOCALAPPDATA%\\Programs\\NIKA,
nika*.desktop, `which nika`) and what the Tauri build actually installs
("Nika Agent").

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import plistlib
from pathlib import Path

from nika_onlymap_exporter.core.nika_desktop import (
    NOT_FOUND,
    Probe,
    UninstallEntry,
    detect,
    system_probe,
)


def _probe(
    tmp_path: Path,
    platform: str,
    environ: dict[str, str] | None = None,
    which: dict[str, str] | None = None,
    entries: list[UninstallEntry] | None = None,
) -> Probe:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    found = which or {}
    return Probe(
        platform=platform,
        home=home,
        # An empty XDG_DATA_DIRS would fall back to the real /usr/share.
        environ=environ
        if environ is not None
        else {"XDG_DATA_DIRS": str(root / "usr" / "share")},
        which=found.get,
        uninstall_entries=lambda: list(entries or []),
        root=root,
    )


def _bundle(folder: Path, name: str, version: str | None) -> Path:
    contents = folder / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    if version is not None:
        info = {"CFBundleName": name, "CFBundleShortVersionString": version}
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    return folder / f"{name}.app"


class TestMacOS:
    def test_found_in_applications_with_its_version(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "darwin")
        bundle = _bundle(probe.root / "Applications", "NIKA", "0.3.6")
        app = detect(probe)
        assert app.found
        assert app.path == bundle
        assert app.version == "0.3.6"
        # A bundle is opened by the desktop, not run as a program.
        assert app.command == ()

    def test_found_in_the_users_applications(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "darwin")
        bundle = _bundle(probe.home / "Applications", "Nika Agent", "0.2.5")
        app = detect(probe)
        assert (app.found, app.path, app.version) == (True, bundle, "0.2.5")

    def test_found_without_a_readable_plist_has_no_version(
        self, tmp_path: Path
    ) -> None:
        probe = _probe(tmp_path, "darwin")
        _bundle(probe.root / "Applications", "NIKA", None)
        app = detect(probe)
        assert app.found and app.version is None

    def test_a_corrupt_plist_is_not_a_crash(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "darwin")
        bundle = _bundle(probe.root / "Applications", "NIKA", None)
        (bundle / "Contents" / "Info.plist").write_bytes(b"not a plist")
        assert detect(probe).found
        assert detect(probe).version is None

    def test_absent(self, tmp_path: Path) -> None:
        assert detect(_probe(tmp_path, "darwin")) == NOT_FOUND


class TestWindows:
    def test_found_in_local_programs(self, tmp_path: Path) -> None:
        local = tmp_path / "Local"
        folder = local / "Programs" / "NIKA"
        folder.mkdir(parents=True)
        (folder / "uninstall.exe").write_bytes(b"")
        exe = folder / "NIKA.exe"
        exe.write_bytes(b"")
        app = detect(_probe(tmp_path, "win32", environ={"LOCALAPPDATA": str(local)}))
        assert (app.found, app.path, app.version) == (True, exe, None)
        assert app.command == (str(exe),)

    def test_found_where_tauri_installs_per_user(self, tmp_path: Path) -> None:
        local = tmp_path / "Local"
        folder = local / "Nika Agent"
        folder.mkdir(parents=True)
        exe = folder / "nika-agent.exe"
        exe.write_bytes(b"")
        app = detect(_probe(tmp_path, "win32", environ={"LOCALAPPDATA": str(local)}))
        assert app.path == exe

    def test_uninstall_key_gives_the_version(self, tmp_path: Path) -> None:
        folder = tmp_path / "Programs" / "Nika Agent"
        folder.mkdir(parents=True)
        exe = folder / "Nika Agent.exe"
        exe.write_bytes(b"")
        entries = [
            UninstallEntry("Some Other App", "9.9", str(tmp_path)),
            UninstallEntry("Nika Agent", "0.2.5", str(folder), f'"{exe}",0'),
        ]
        app = detect(_probe(tmp_path, "win32", environ={}, entries=entries))
        assert (app.found, app.path, app.version) == (True, exe, "0.2.5")

    def test_uninstall_key_without_icon_uses_install_location(
        self, tmp_path: Path
    ) -> None:
        folder = tmp_path / "NIKA"
        folder.mkdir()
        exe = folder / "NIKA.exe"
        exe.write_bytes(b"")
        entries = [UninstallEntry("NIKA", " 0.3.6 ", str(folder))]
        app = detect(_probe(tmp_path, "win32", environ={}, entries=entries))
        assert (app.path, app.version) == (exe, "0.3.6")

    def test_a_name_that_only_starts_with_nika_is_not_ours(
        self, tmp_path: Path
    ) -> None:
        folder = tmp_path / "Nikanor"
        folder.mkdir()
        (folder / "Nikanor.exe").write_bytes(b"")
        entries = [UninstallEntry("Nikanor Tools", "1.0", str(folder))]
        assert (
            detect(_probe(tmp_path, "win32", environ={}, entries=entries)) == NOT_FOUND
        )

    def test_absent(self, tmp_path: Path) -> None:
        local = tmp_path / "Local"
        local.mkdir()
        probe = _probe(tmp_path, "win32", environ={"LOCALAPPDATA": str(local)})
        assert detect(probe) == NOT_FOUND


class TestLinux:
    def _desktop(self, folder: Path, name: str, body: str) -> Path:
        applications = folder / "applications"
        applications.mkdir(parents=True, exist_ok=True)
        path = applications / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_user_desktop_file_with_app_version(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "linux", which={"nika-agent": "/opt/nika/nika-agent"})
        entry = self._desktop(
            probe.home / ".local" / "share",
            "nika.desktop",
            "[Desktop Entry]\nVersion=1.5\nType=Application\nName=NIKA\n"
            "Name[fr]=NIKA\nExec=nika-agent %U\nX-AppVersion=0.3.6\n"
            "[Desktop Action new]\nExec=other\n",
        )
        app = detect(probe)
        assert (app.found, app.path, app.version) == (True, entry, "0.3.6")
        # Field codes dropped, the program resolved on PATH.
        assert app.command == ("/opt/nika/nika-agent",)

    def test_spec_version_is_not_the_apps_version(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "linux")
        self._desktop(
            probe.home / ".local" / "share",
            "Nika Agent.desktop",
            '[Desktop Entry]\nVersion=1.0\nExec="/usr/bin/nika agent" --flag\n',
        )
        app = detect(probe)
        assert app.found and app.version is None
        assert app.command == ("/usr/bin/nika agent", "--flag")

    def test_system_applications_folder(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "linux")
        entry = self._desktop(
            probe.root / "usr" / "share", "Nika Agent.desktop", "[Desktop Entry]\n"
        )
        app = detect(probe)
        assert app.path == entry and app.command == ()

    def test_hidden_entry_is_ignored(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "linux")
        self._desktop(
            probe.home / ".local" / "share",
            "nika.desktop",
            "[Desktop Entry]\nHidden=true\nExec=nika\n",
        )
        assert detect(probe) == NOT_FOUND

    def test_which_nika(self, tmp_path: Path) -> None:
        probe = _probe(tmp_path, "linux", which={"nika": "/usr/local/bin/nika"})
        app = detect(probe)
        assert (app.found, app.path, app.version) == (
            True,
            Path("/usr/local/bin/nika"),
            None,
        )
        assert app.command == ("/usr/local/bin/nika",)

    def test_absent(self, tmp_path: Path) -> None:
        assert detect(_probe(tmp_path, "linux")) == NOT_FOUND


def test_the_real_probe_runs_without_raising() -> None:
    # Whatever this machine has installed, detection answers rather than raises.
    app = detect(system_probe())
    assert app.found in (True, False)
