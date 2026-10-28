"""Show in folder reaches a file manager, never a program that opens folders.

Nothing here launches an application: the platform requests are replaced by
recorders, and the one test that sends a real D-Bus message sends it to a
private message bus that has no services at all.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import shutil
import subprocess
import time
import types
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QCoreApplication, QUrl

from nika_onlymap_exporter.ui import reveal_file as module
from nika_onlymap_exporter.ui.reveal_file import (
    RevealOutcome,
    default_folder_opener_is_file_manager,
    reveal_file,
)

MODULE = "nika_onlymap_exporter.ui.reveal_file"


class Platform:
    """Records every request instead of making it."""

    def __init__(self, monkeypatch, name: str, error: str | None = None) -> None:
        self.dbus: list[tuple[str, str]] = []
        self.processes: list[tuple[str, list[str]]] = []
        self.folders: list[Path] = []
        self.pending = None
        self.process_works = True
        self.hold_answer = False
        self._error = error
        monkeypatch.setattr(f"{MODULE}._platform", lambda: name)
        monkeypatch.setattr(f"{MODULE}._ask_file_manager", self._ask)
        monkeypatch.setattr(f"{MODULE}._start_detached", self._start)
        monkeypatch.setattr(f"{MODULE}._open_folder", self._open)

    def _ask(self, method, uri, done) -> None:
        self.dbus.append((method, uri))
        if self.hold_answer:
            self.pending = done
        else:
            done(self._error)

    def _start(self, program, arguments) -> bool:
        self.processes.append((program, arguments))
        return self.process_works

    def _open(self, folder) -> bool:
        self.folders.append(folder)
        return True


def _outcomes(path: Path) -> list[RevealOutcome]:
    seen: list[RevealOutcome] = []
    reveal_file(path, seen.append)
    return seen


@pytest.fixture
def saved(tmp_path) -> Path:
    path = tmp_path / "maps and more" / "a map.html"
    path.parent.mkdir()
    path.write_text("<html></html>")
    return path


class TestLinux:
    def test_the_file_is_selected_through_the_file_manager_service(
        self, monkeypatch, saved
    ) -> None:
        platform = Platform(monkeypatch, "linux")
        outcomes = _outcomes(saved)
        assert platform.dbus == [
            ("ShowItems", QUrl.fromLocalFile(str(saved)).toString())
        ]
        assert outcomes == [RevealOutcome(True, True, saved.parent)]
        # The default folder opener (which may be an editor) is never used.
        assert platform.folders == [] and platform.processes == []

    def test_success_is_reported_only_after_the_service_answers(
        self, monkeypatch, saved
    ) -> None:
        platform = Platform(monkeypatch, "linux")
        platform.hold_answer = True
        outcomes = _outcomes(saved)
        assert outcomes == []
        platform.pending(None)
        assert outcomes and outcomes[0].ok

    def test_a_file_not_saved_yet_shows_the_nearest_existing_folder(
        self, monkeypatch, tmp_path
    ) -> None:
        platform = Platform(monkeypatch, "linux")
        outcomes = _outcomes(tmp_path / "new" / "deeper" / "x.html")
        assert platform.dbus == [
            ("ShowFolders", QUrl.fromLocalFile(str(tmp_path)).toString())
        ]
        assert outcomes == [RevealOutcome(True, False, tmp_path)]

    def test_no_file_manager_and_an_editor_as_folder_opener_opens_nothing(
        self, monkeypatch, saved
    ) -> None:
        platform = Platform(monkeypatch, "linux", error="ServiceUnknown")
        monkeypatch.setattr(
            f"{MODULE}.default_folder_opener_is_file_manager", lambda env=None: False
        )
        outcomes = _outcomes(saved)
        assert platform.folders == []
        assert len(outcomes) == 1 and not outcomes[0].ok
        assert "ServiceUnknown" in outcomes[0].reason

    def test_an_undeterminable_folder_opener_is_not_trusted(
        self, monkeypatch, saved
    ) -> None:
        platform = Platform(monkeypatch, "linux", error="ServiceUnknown")
        monkeypatch.setattr(
            f"{MODULE}.default_folder_opener_is_file_manager", lambda env=None: None
        )
        assert not _outcomes(saved)[0].ok
        assert platform.folders == []

    def test_no_file_manager_but_a_file_manager_as_folder_opener_opens_the_folder(
        self, monkeypatch, saved
    ) -> None:
        platform = Platform(monkeypatch, "linux", error="ServiceUnknown")
        monkeypatch.setattr(
            f"{MODULE}.default_folder_opener_is_file_manager", lambda env=None: True
        )
        assert _outcomes(saved) == [RevealOutcome(True, False, saved.parent)]
        assert platform.folders == [saved.parent]


class TestOtherPlatforms:
    def test_windows_selects_the_file_in_explorer(self, monkeypatch, saved) -> None:
        platform = Platform(monkeypatch, "win32")
        outcomes = _outcomes(saved)
        assert len(platform.processes) == 1
        program, arguments = platform.processes[0]
        assert program == "explorer"
        assert len(arguments) == 1
        assert arguments[0].startswith("/select,")
        assert arguments[0].endswith(saved.name)
        assert outcomes[0].ok and outcomes[0].selected
        assert platform.dbus == []

    def test_macos_reveals_the_file_in_finder(self, monkeypatch, saved) -> None:
        platform = Platform(monkeypatch, "darwin")
        outcomes = _outcomes(saved)
        assert platform.processes == [("open", ["-R", str(saved)])]
        assert outcomes[0].ok and outcomes[0].selected

    @pytest.mark.parametrize("name", ["win32", "darwin"])
    def test_a_refused_select_falls_back_to_the_folder(
        self, monkeypatch, saved, name
    ) -> None:
        platform = Platform(monkeypatch, name)
        platform.process_works = False
        assert _outcomes(saved) == [RevealOutcome(True, False, saved.parent)]
        assert platform.folders == [saved.parent]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


EDITOR = (
    "[Desktop Entry]\nName=Editor\nCategories=Utility;TextEditor;Development;IDE;\n"
)
FILES = "[Desktop Entry]\nName=Files\nCategories=GNOME;GTK;Utility;Core;FileManager;\n"


@pytest.fixture
def desktop(tmp_path):
    """A fake desktop: home, system data dirs and the environment naming them."""
    home = tmp_path / "home"
    system = tmp_path / "usr-share"
    (system / "applications").mkdir(parents=True)
    (home / ".local/share/applications").mkdir(parents=True)
    _write(system / "applications/editor.desktop", EDITOR)
    _write(system / "applications/files.desktop", FILES)
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CONFIG_DIRS": str(tmp_path / "etc-xdg"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_DATA_DIRS": str(system),
    }
    return env, home, system


class TestFolderOpenerKind:
    def test_an_editor_registered_for_folders_is_not_a_file_manager(
        self, desktop
    ) -> None:
        env, home, _system = desktop
        _write(
            home / ".config/mimeapps.list",
            "[Default Applications]\ninode/directory=editor.desktop;\n",
        )
        assert default_folder_opener_is_file_manager(env) is False

    def test_a_file_manager_is_recognised_by_its_category(self, desktop) -> None:
        env, home, _system = desktop
        _write(
            home / ".config/mimeapps.list",
            "[Default Applications]\ninode/directory=files.desktop;\n",
        )
        assert default_folder_opener_is_file_manager(env) is True

    def test_the_users_choice_beats_the_systems(self, desktop) -> None:
        env, home, system = desktop
        _write(
            system / "applications/mimeinfo.cache",
            "[MIME Cache]\ninode/directory=files.desktop;\n",
        )
        _write(
            home / ".config/mimeapps.list",
            "[Default Applications]\ninode/directory=editor.desktop;\n",
        )
        assert default_folder_opener_is_file_manager(env) is False

    def test_the_systems_cache_is_used_when_the_user_chose_nothing(
        self, desktop
    ) -> None:
        env, _home, system = desktop
        _write(
            system / "applications/mimeinfo.cache",
            "[MIME Cache]\ninode/directory=files.desktop;editor.desktop;\n",
        )
        assert default_folder_opener_is_file_manager(env) is True

    def test_an_unknown_opener_is_unknown(self, desktop) -> None:
        env, home, _system = desktop
        assert default_folder_opener_is_file_manager(env) is None
        _write(
            home / ".config/mimeapps.list",
            "[Default Applications]\ninode/directory=missing.desktop;\n",
        )
        assert default_folder_opener_is_file_manager(env) is None


needs_dbus_tools = pytest.mark.skipif(
    shutil.which("dbus-daemon") is None or shutil.which("dbus-monitor") is None,
    reason="needs dbus-daemon and dbus-monitor",
)


@pytest.fixture
def private_bus(tmp_path):
    """A message bus of our own with no services at all (so no file manager can
    be started by a request), and a monitor reading everything sent on it."""
    socket = tmp_path / "bus"
    config = tmp_path / "bus.conf"
    config.write_text(
        "<busconfig><type>session</type>"
        f"<listen>unix:path={socket}</listen>"
        '<policy context="default"><allow send_destination="*" eavesdrop="true"/>'
        '<allow eavesdrop="true"/><allow own="*"/></policy></busconfig>'
    )
    daemon = subprocess.Popen(
        ["dbus-daemon", f"--config-file={config}", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    address = daemon.stdout.readline().strip()
    assert address.startswith("unix:")
    monitor = subprocess.Popen(
        ["dbus-monitor", "--address", address],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    time.sleep(0.5)
    yield types.SimpleNamespace(address=address, monitor=monitor)
    for process in (monitor, daemon):
        process.terminate()
        process.wait(timeout=5)


def _wait_for_outcome(outcomes: list[RevealOutcome]) -> RevealOutcome:
    deadline = time.monotonic() + 5
    while not outcomes and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert outcomes, "the bus never answered"
    return outcomes[0]


@needs_dbus_tools
class TestOverARealBus:
    def _client(self, monkeypatch, private_bus):
        qt_dbus = module.dbus_module()
        bus = qt_dbus.QDBusConnection.connectToBus(
            private_bus.address, f"client-{private_bus.address[-12:]}"
        )
        assert bus.isConnected()
        monkeypatch.setattr(f"{MODULE}._session_bus", lambda: bus)
        monkeypatch.setattr(f"{MODULE}._platform", lambda: "linux")

    def test_the_message_carries_the_file_uri_and_a_missing_service_fails_honestly(
        self, qgis_app, monkeypatch, private_bus, saved
    ) -> None:
        self._client(monkeypatch, private_bus)
        monkeypatch.setattr(
            f"{MODULE}.default_folder_opener_is_file_manager", lambda env=None: False
        )
        outcomes: list[RevealOutcome] = []
        reveal_file(saved, outcomes.append)
        outcome = _wait_for_outcome(outcomes)

        assert not outcome.ok
        assert "ServiceUnknown" in outcome.reason
        private_bus.monitor.terminate()
        seen = private_bus.monitor.communicate(timeout=5)[0]
        assert "member=ShowItems" in seen
        assert "interface=org.freedesktop.FileManager1" in seen
        assert QUrl.fromLocalFile(str(saved)).toString() in seen
        # The wire type the service expects is an array of strings, then a
        # string (a plain list would be sent as an array of variants).
        assert "array [\n" in seen and "variant" not in seen
        assert 'string ""' in seen

    def test_a_file_manager_that_accepts_the_request_makes_it_a_success(
        self, qgis_app, monkeypatch, private_bus, saved
    ) -> None:
        from qgis.PyQt.QtCore import QObject, pyqtSlot

        qt_dbus = module.dbus_module()
        received: list[tuple[list[str], str]] = []

        class FileManager(QObject):
            @pyqtSlot("QStringList", str)
            def ShowItems(self, uris, startup_id) -> None:  # noqa: N802 - D-Bus name
                received.append((list(uris), startup_id))

        service_bus = qt_dbus.QDBusConnection.connectToBus(
            private_bus.address, f"service-{private_bus.address[-12:]}"
        )
        manager = FileManager()
        assert service_bus.registerService(module.FILE_MANAGER_SERVICE)
        assert service_bus.registerObject(
            module.FILE_MANAGER_PATH,
            module.FILE_MANAGER_INTERFACE,
            manager,
            qt_dbus.QDBusConnection.RegisterOption.ExportAllSlots,
        )
        self._client(monkeypatch, private_bus)

        outcomes: list[RevealOutcome] = []
        reveal_file(saved, outcomes.append)
        outcome = _wait_for_outcome(outcomes)

        assert outcome == RevealOutcome(True, True, saved.parent), outcome.reason
        assert received == [([QUrl.fromLocalFile(str(saved)).toString()], "")]


def test_the_module_names_no_file_manager() -> None:
    source = Path(module.__file__).read_text().lower()
    for name in ("nautilus", "dolphin", "thunar", "nemo", "xdg-open"):
        assert name not in source.replace("nautilus, dolphin, nemo, thunar", "")
