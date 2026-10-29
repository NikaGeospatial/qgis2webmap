"""A cartograph saves itself unasked, so the file it saves to must be its own.

The destination is stored in the project, which may be shared or stale: a
project naming an existing `.txt`, someone else's page, a link or a folder must
never lose that file when the cartograph opens. These drive the real dialog
and controller and look at the files: untouched ones byte for byte and by
modification time, and where the cartograph went instead.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from close_lifecycle_fixtures import (
    FakeIface,
    LabelExport,
    add_survey_layout,
    edit_label,
)

qgis_core = pytest.importorskip("qgis.core")
from qgis.PyQt.QtWidgets import QFileDialog  # noqa: E402

PRECIOUS = b"my only copy of something"
FOREIGN_PAGE = b"<!doctype html>\n<html><body>somebody else's page</body></html>\n"


def _wait_for(qgis_app, predicate, timeout=15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        qgis_app.processEvents()
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def export(monkeypatch) -> LabelExport:
    fake = LabelExport()
    fake.install(monkeypatch)
    return fake


def _settled(qgis_app, cartograph) -> bool:
    return _wait_for(
        qgis_app,
        lambda: cartograph._job is None and not cartograph._autosave_timer.isActive(),
    )


def _project_pointing_at(project, folder: Path, stored: Path, text="Old") -> Path:
    """A `.qgz` on disk whose settings say the Survey cartograph lives at
    `stored`, re-read as a user opening a shared project would."""
    from nika_onlymap_exporter.core.settings import (
        DialogOutput,
        DialogState,
        save_state,
    )

    project.clear()
    layout, _label = add_survey_layout(project, text=text)
    state = DialogState()
    state.output = DialogOutput.CARTOGRAPH
    state.cartograph_layout = layout.name()
    state.cartograph_files[layout.name()] = str(stored)
    save_state(project, state)
    qgz = folder / "shared.qgz"
    assert project.write(str(qgz))
    project.clear()
    assert project.read(str(qgz))
    return qgz


def _repoint(project, folder: Path, stored: Path) -> None:
    """The same project and layout, its settings now naming `stored` (a stale
    setting, or a crafted edit of the project), re-read from disk."""
    from nika_onlymap_exporter.core.settings import load_state, save_state

    state = load_state(project)
    name = project.layoutManager().printLayouts()[0].name()
    state.cartograph_files[name] = str(stored)
    save_state(project, state)
    qgz = folder / "repointed.qgz"
    assert project.write(str(qgz))
    project.clear()
    assert project.read(str(qgz))


def _open_cartograph(qgis_app, project):
    from nika_onlymap_exporter.core.settings import DialogOutput
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    dialog = MainDialog(FakeIface(), None)
    dialog.show()
    if dialog._cartograph.state.output is not DialogOutput.CARTOGRAPH:
        dialog.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
    cartograph = dialog._cartograph
    layout = project.layoutManager().printLayouts()[0]
    cartograph.show_layout_named(layout.name())
    assert _settled(qgis_app, cartograph)
    return dialog, cartograph, layout


def _fingerprint(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def _pin_old_mtime(path: Path) -> None:
    os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))


def _saved_elsewhere(cartograph, original: Path) -> Path:
    shown = Path(cartograph.file_edit.text())
    assert shown != original
    assert shown.suffix == ".html" and shown.is_file()
    assert "Old" in shown.read_text(encoding="utf-8")
    return shown


class TestForeignFilesAreNeverOverwritten:
    def test_a_project_naming_a_text_file_leaves_it_alone(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        victim = tmp_path / "notes.txt"
        victim.write_bytes(PRECIOUS)
        _pin_old_mtime(victim)
        before = _fingerprint(victim)
        _project_pointing_at(project, tmp_path, victim)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert _fingerprint(victim) == before
        _saved_elsewhere(cartograph, victim)
        dialog.close()

    def test_a_project_naming_someone_elses_html_leaves_it_alone(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        victim = tmp_path / "report.html"
        victim.write_bytes(FOREIGN_PAGE)
        _pin_old_mtime(victim)
        before = _fingerprint(victim)
        _project_pointing_at(project, tmp_path, victim)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert _fingerprint(victim) == before
        moved = _saved_elsewhere(cartograph, victim)
        assert moved.parent == victim.parent
        dialog.close()

    def test_the_user_is_told_where_it_went_and_the_project_remembers(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.settings import load_state

        victim = tmp_path / "report.html"
        victim.write_bytes(FOREIGN_PAGE)
        _project_pointing_at(project, tmp_path, victim)

        dialog, cartograph, layout = _open_cartograph(qgis_app, project)

        moved = _saved_elsewhere(cartograph, victim)
        message = dialog.status_label.text()
        assert str(victim) in message and str(moved) in message
        assert load_state(project).cartograph_file_for(layout.name()) == str(moved)
        dialog.close()

    def test_a_page_of_another_layout_is_not_this_ones(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.cartograph_destination import stamp_owner

        victim = tmp_path / "other.html"
        victim.write_text(
            stamp_owner(FOREIGN_PAGE.decode(), "lid:someoneelse"), encoding="utf-8"
        )
        before = _fingerprint(victim)
        _project_pointing_at(project, tmp_path, victim)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert _fingerprint(victim) == before
        _saved_elsewhere(cartograph, victim)
        dialog.close()

    def test_a_link_is_not_followed_or_replaced(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        target = tmp_path / "real.html"
        target.write_bytes(FOREIGN_PAGE)
        link = tmp_path / "link.html"
        link.symlink_to(target)
        before = _fingerprint(target)
        _project_pointing_at(project, tmp_path, link)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert _fingerprint(target) == before
        assert link.is_symlink() and link.resolve() == target
        _saved_elsewhere(cartograph, link)
        dialog.close()

    def test_a_link_to_its_own_earlier_output_is_still_a_link(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.settings import load_state

        first = tmp_path / "first"
        first.mkdir()
        _project_pointing_at(project, tmp_path, first / "survey.html")
        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)
        own = first / "survey.html"
        assert own.is_file()
        dialog.close()
        link = tmp_path / "link.html"
        link.symlink_to(own)
        before = _fingerprint(own)
        _repoint(project, tmp_path, link)

        dialog, cartograph, layout = _open_cartograph(qgis_app, project)

        assert link.is_symlink() and _fingerprint(own) == before
        assert Path(cartograph.file_edit.text()) != link
        assert load_state(project).cartograph_file_for(layout.name())
        dialog.close()

    def test_its_own_output_under_another_extension_is_not_rewritten_as_that(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        own = tmp_path / "survey.html"
        _project_pointing_at(project, tmp_path, own)
        dialog, _cartograph, _layout = _open_cartograph(qgis_app, project)
        dialog.close()
        renamed = tmp_path / "survey.txt"
        own.rename(renamed)
        before = _fingerprint(renamed)
        _repoint(project, tmp_path, renamed)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert _fingerprint(renamed) == before
        assert Path(cartograph.file_edit.text()).suffix == ".html"
        dialog.close()

    def test_a_folder_is_left_intact(self, qgis_app, project, export, tmp_path) -> None:
        folder = tmp_path / "out.html"
        folder.mkdir()
        (folder / "keep.txt").write_bytes(PRECIOUS)
        _project_pointing_at(project, tmp_path, folder)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert folder.is_dir() and (folder / "keep.txt").read_bytes() == PRECIOUS
        _saved_elsewhere(cartograph, folder)
        dialog.close()

    def test_a_missing_folder_is_not_made_up(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        gone = tmp_path / "no-such-folder" / "map.html"
        _project_pointing_at(project, tmp_path, gone)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert not gone.parent.exists()
        _saved_elsewhere(cartograph, gone)
        dialog.close()


class TestOwnOutputIsReplacedNormally:
    def test_autosave_keeps_working_across_sessions(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        target = tmp_path / "survey.html"
        qgz = _project_pointing_at(project, tmp_path, target)
        dialog, cartograph, layout = _open_cartograph(qgis_app, project)
        assert target.is_file() and "Old" in target.read_text(encoding="utf-8")
        assert project.write(str(qgz))
        dialog.close()

        project.clear()
        assert project.read(str(qgz))
        dialog, cartograph, layout = _open_cartograph(qgis_app, project)
        label = next(
            item
            for item in layout.items()
            if item.__class__.__name__ == "QgsLayoutItemLabel"
        )
        edit_label(layout, label, "Newer")
        assert _wait_for(
            qgis_app, lambda: "Newer" in target.read_text(encoding="utf-8")
        ), sorted(p.name for p in tmp_path.iterdir())
        assert Path(cartograph.file_edit.text()) == target
        assert sorted(p.name for p in tmp_path.glob("survey*.html")) == ["survey.html"]
        dialog.close()

    def test_a_page_saved_by_an_earlier_build_is_recognised_and_stamped(
        self, qgis_app, project, export, tmp_path
    ) -> None:
        # No stamp, as before this change; the sheet names the layout.
        legacy = tmp_path / "survey.html"
        legacy.write_text(
            '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n</head>\n'
            '<body><om-cartograph cartograph-id="Survey" format="cartograph/2" '
            'size="A4"></om-cartograph></body>\n</html>\n',
            encoding="utf-8",
        )
        _project_pointing_at(project, tmp_path, legacy)

        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)

        assert Path(cartograph.file_edit.text()) == legacy
        assert "Old" in legacy.read_text(encoding="utf-8")
        assert 'name="qgis2webmap-layout"' in legacy.read_text(encoding="utf-8")
        dialog.close()

    def test_the_real_writer_output_is_recognised_as_legacy(self, tmp_path) -> None:
        from nika_onlymap_exporter.core.cartograph_destination import is_own_output
        from nika_onlymap_exporter.core.cartograph_ir import CartographSpec
        from nika_onlymap_exporter.writers.cartograph_writer import (
            CartographRuntimeBundle,
            write_cartograph_html,
        )

        page = tmp_path / "old.html"
        page.write_text(
            write_cartograph_html(
                CartographSpec(
                    cartograph_id="Survey",
                    width=210.0,
                    height=297.0,
                    orientation="portrait",
                ),
                CartographRuntimeBundle(cartograph_js=b"", cartograph_css=""),
            ),
            encoding="utf-8",
        )
        assert is_own_output(page, "lid:x", "Survey")
        assert not is_own_output(page, "lid:x", "Another")


class TestAnExplicitChoiceIsHonoured:
    def test_replacing_a_file_in_the_save_dialog_is_allowed(
        self, qgis_app, project, export, tmp_path, monkeypatch
    ) -> None:
        mine = tmp_path / "mine.html"
        _project_pointing_at(project, tmp_path, mine)
        dialog, cartograph, _layout = _open_cartograph(qgis_app, project)
        replaced = tmp_path / "replace-me.html"
        replaced.write_bytes(FOREIGN_PAGE)
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            staticmethod(lambda *args, **kwargs: (str(replaced), "")),
        )

        cartograph._on_choose_file()
        assert _settled(qgis_app, cartograph)

        assert Path(cartograph.file_edit.text()) == replaced
        assert "Old" in replaced.read_text(encoding="utf-8")
        assert not (tmp_path / "replace-me (2).html").exists()
        dialog.close()


def test_the_ownership_stamp_is_in_the_head_and_replaced_not_repeated() -> None:
    from nika_onlymap_exporter.core.cartograph_destination import stamp_owner

    page = '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n</head>\n'
    once = stamp_owner(page, "lid:a")
    twice = stamp_owner(once, "lid:b")
    assert twice.count("qgis2webmap-layout") == 1
    assert twice.index("qgis2webmap-layout") < twice.index("</head>")
    assert 'content="lid:b"' in twice
