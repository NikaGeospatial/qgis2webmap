"""Hand off to NIKA: a prompt to paste into NIKA desktop or another agent.

Issue #3 section 4. Opened from the Share menu's "Enhance with NIKA AI..." and
"Ask NIKA for a critique..." rows, for a web map and for a cartograph alike:
the dialog is implemented once and the output kind only changes its wording.

**The plugin never talks to NIKA.** It finds the app on disk
(`core.nika_desktop`), can open it, and hands over text through the clipboard
or a `.md` file. No network request, nothing sent anywhere - the person pastes
the prompt where they choose.

**The prompt is built in `core.handoff_prompt`**, not here, so its wording is
pinned by golden files and this module only gathers the inputs. Every control
above the prompt regenerates it, so what is on screen is always exactly what
Copy puts on the clipboard.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from qgis.PyQt.QtCore import QProcess, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import (
    QDesktopServices,
    QFontDatabase,
    QGuiApplication,
    QPalette,
)
from qgis.PyQt.QtWidgets import (
    QAction,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.handoff_prompt import (
    HandoffRequest,
    OutputKind,
    PageInfo,
    Target,
    Task,
    build_prompt,
)
from ..core.nika_desktop import DOWNLOAD_URL, NOT_FOUND, DesktopApp, detect
from . import theme
from .fitted_label import FittedLabel
from .i18n import plural, tr

# How long the Copy button reads "Copied" before it goes back.
COPIED_MS = 2000


@dataclass(frozen=True)
class HandoffContext:
    """What the caller knows about the output being handed off.

    `source_name` is the layout's name for a cartograph and the project's name
    for a web map. `cvd_findings` and `page` are optional facts the prompt
    mentions only when present.
    """

    output: OutputKind
    file_path: Path
    source_name: str
    plugin_version: str
    task: Task = "critique"
    cvd_findings: str | None = None
    page: PageInfo | None = None


def not_found_text(platform: str | None = None) -> str:
    """ "not found on this Mac/PC/computer" - the name the person uses for it."""
    platform = platform or sys.platform
    if platform == "darwin":
        return tr("not found on this Mac")
    if platform.startswith("win"):
        return tr("not found on this PC")
    return tr("not found on this computer")


def launch(app: DesktopApp) -> bool:
    """Open NIKA desktop. Returns whether the system accepted the request.

    A macOS bundle and a Windows `.exe` go through `QDesktopServices`, which
    hands them to LaunchServices and ShellExecute - the same as a double-click.
    A Linux desktop entry has no such route (`xdg-open` on a `.desktop` file
    opens it in a text editor on GNOME), so its own `Exec` program is started
    detached. That is the app the person asked to open, found by the probe,
    started with no shell and no arguments of ours: not a system command.
    """
    if not app.found or app.path is None:
        return False
    if app.path.suffix.lower() not in (".app", ".exe") and app.command:
        started = QProcess.startDetached(app.command[0], list(app.command[1:]))
        # PyQt5 returns a bool; PyQt6 returns (bool, pid).
        return bool(started[0] if isinstance(started, tuple) else started)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(app.path)))


class HandoffDialog(QDialog):
    """The hand-off dialog. Build with a `HandoffContext`; `exec()` it."""

    def __init__(
        self,
        context: HandoffContext,
        parent: QWidget | None = None,
        detector: Callable[[], DesktopApp] = detect,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("qgis2webmapHandoffDialog")
        self.context = context
        self._detector = detector
        self.app: DesktopApp = NOT_FOUND
        self._folder = context.file_path.parent
        self.files: tuple[str, ...] = (context.file_path.name,)

        self.setWindowTitle(
            tr("Hand off to NIKA - {name}").format(name=context.file_path.name)
        )
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(self._build_step_one())
        layout.addWidget(self._rule())
        layout.addWidget(self._build_step_two())
        layout.addWidget(self._rule())
        layout.addLayout(self._build_step_three(), stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(COPIED_MS)
        self._copied_timer.timeout.connect(self._restore_copy_label)

        self.palette_theme = theme.apply(self)
        self.recheck()
        self.regenerate()

    # ---- building ----------------------------------------------------------

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text, self)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _help(self, text: str) -> FittedLabel:
        label = FittedLabel(text, self)
        label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        return label

    def _rule(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        return line

    def _build_step_one(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self._heading(tr("1 · NIKA desktop")))

        row = QHBoxLayout()
        name = QLabel(tr("NIKA desktop"), box)
        row.addWidget(name)
        self.status_label = QLabel(box)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        row.addWidget(self.status_label, stretch=1)

        self.open_button = QPushButton(tr("Open NIKA desktop"), box)
        self.open_button.clicked.connect(self.on_open_desktop)
        row.addWidget(self.open_button)

        self.download_button = QPushButton(tr("Download NIKA desktop ↗"), box)
        self.download_button.setToolTip(
            tr("Opens {url} in your browser").format(url=DOWNLOAD_URL)
        )
        self.download_button.clicked.connect(self.on_download)
        row.addWidget(self.download_button)

        self.recheck_button = QPushButton(tr("Re-check"), box)
        self.recheck_button.setToolTip(
            tr("Looks for NIKA desktop again, after you have installed it")
        )
        self.recheck_button.clicked.connect(self.recheck)
        row.addWidget(self.recheck_button)
        column.addLayout(row)

        self.download_help = self._help(
            tr(
                "Free app for macOS, Windows, and Linux. Install it, then come back "
                "to this dialog."
            )
        )
        column.addWidget(self.download_help)
        return box

    def _build_step_two(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self._heading(tr("2 · What to ask")))

        # The two-segment control, built the way the main dialog's output
        # switch is so the theme already dresses it.
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel(tr("Paste into"), box))
        group_frame = QFrame(box)
        group_frame.setProperty("role", "outputGroup")
        group_layout = QHBoxLayout(group_frame)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(0)
        self.desktop_target_button = self._segment(
            group_frame, tr("NIKA desktop"), "left"
        )
        self.other_target_button = self._segment(
            group_frame, tr("Other agent"), "right"
        )
        self.desktop_target_button.setToolTip(
            tr("A prompt that NIKA desktop's map skills recognise")
        )
        self.other_target_button.setToolTip(
            tr("A prompt any coding agent can follow, starting from OnlyMap's docs")
        )
        group_layout.addWidget(self.desktop_target_button)
        group_layout.addWidget(self.other_target_button)
        self._target_group = QButtonGroup(box)
        self._target_group.setExclusive(True)
        self._target_group.addButton(self.desktop_target_button)
        self._target_group.addButton(self.other_target_button)
        self.desktop_target_button.setChecked(True)
        self.desktop_target_button.toggled.connect(self.regenerate)
        target_row.addWidget(group_frame)
        target_row.addStretch(1)
        column.addLayout(target_row)

        task_row = QHBoxLayout()
        task_label = QLabel(tr("Task"), box)
        task_row.addWidget(task_label)
        self.task_combo = QComboBox(box)
        if self.context.output == "cartograph":
            self.task_combo.addItem(tr("Critique this cartograph"), "critique")
            self.task_combo.addItem(tr("Enhance this cartograph"), "enhance")
        else:
            self.task_combo.addItem(tr("Critique this web map"), "critique")
            self.task_combo.addItem(tr("Enhance this web map"), "enhance")
        self.task_combo.setCurrentIndex(self.task_combo.findData(self.context.task))
        self.task_combo.currentIndexChanged.connect(self.regenerate)
        task_label.setBuddy(self.task_combo)
        task_row.addWidget(self.task_combo, stretch=1)
        column.addLayout(task_row)

        files_row = QHBoxLayout()
        files_row.addWidget(QLabel(tr("Files"), box))
        files_row.addStretch(1)
        self.change_files_button = QPushButton(tr("Change files..."), box)
        self.change_files_button.setToolTip(
            tr("Choose which files in {folder} the prompt points at").format(
                folder=self._folder
            )
        )
        self.change_files_button.clicked.connect(self.on_change_files)
        files_row.addWidget(self.change_files_button)
        column.addLayout(files_row)

        self.files_list = QListWidget(box)
        self.files_list.setMaximumHeight(84)
        self.files_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        column.addWidget(self.files_list)
        self.files_note = self._help(
            tr("NIKA can read the whole folder; it focuses on these.")
        )
        column.addWidget(self.files_note)
        self._fill_files()

        self.instructions_edit = QPlainTextEdit(box)
        self.instructions_edit.setPlaceholderText(tr("Add instructions (optional)"))
        self.instructions_edit.setTabChangesFocus(True)
        self.instructions_edit.setFixedHeight(64)
        self.instructions_edit.textChanged.connect(self.regenerate)
        column.addWidget(self.instructions_edit)
        return box

    def _segment(self, parent: QWidget, text: str, position: str) -> QToolButton:
        button = QToolButton(parent)
        button.setText(text)
        button.setProperty("role", "outputPart")
        button.setProperty("position", position)
        button.setCheckable(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return button

    def _build_step_three(self) -> QVBoxLayout:
        column = QVBoxLayout()
        header = QHBoxLayout()
        header.addWidget(self._heading(tr("3 · The prompt")))
        header.addStretch(1)

        self.copy_button = QToolButton(self)
        self.copy_button.setProperty("role", "primary")
        self.copy_button.setText(tr("Copy prompt"))
        self.copy_button.setToolTip(tr("Copies the prompt below to the clipboard"))
        self.copy_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.copy_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.copy_button.clicked.connect(self.on_copy)
        self.copy_menu = QMenu(self.copy_button)
        self.copy_menu.setToolTipsVisible(True)
        self.copy_action = QAction(tr("Copy prompt"), self)
        self.copy_action.triggered.connect(self.on_copy)
        self.copy_menu.addAction(self.copy_action)
        self.open_action = QAction(tr("Open NIKA desktop"), self)
        self.open_action.triggered.connect(self.on_open_desktop)
        self.copy_menu.addAction(self.open_action)
        self.save_action = QAction(tr("Save prompt as .md..."), self)
        self.save_action.setToolTip(tr("Writes the prompt to a Markdown file"))
        self.save_action.triggered.connect(self.on_save)
        self.copy_menu.addAction(self.save_action)
        self.copy_button.setMenu(self.copy_menu)
        header.addWidget(self.copy_button)
        column.addLayout(header)

        self.prompt_view = QPlainTextEdit(self)
        self.prompt_view.setReadOnly(True)
        self.prompt_view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.prompt_view.setMinimumHeight(150)
        self.prompt_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        column.addWidget(self.prompt_view, stretch=1)
        return column

    def _fill_files(self) -> None:
        self.files_list.clear()
        tick = theme.qgis_icon("mIconSuccess.svg")
        for name in self.files:
            QListWidgetItem(tick, name, self.files_list)

    # ---- state -------------------------------------------------------------

    def target(self) -> Target:
        return (
            "nika_desktop" if self.desktop_target_button.isChecked() else "other_agent"
        )

    def task(self) -> Task:
        return "enhance" if self.task_combo.currentData() == "enhance" else "critique"

    def request(self) -> HandoffRequest:
        return HandoffRequest(
            target=self.target(),
            task=self.task(),
            output=self.context.output,
            file_path=str(self.context.file_path),
            source_name=self.context.source_name,
            plugin_version=self.context.plugin_version,
            files=self.files,
            extra=self.instructions_edit.toPlainText(),
            cvd_findings=self.context.cvd_findings,
            page=self.context.page,
        )

    def prompt(self) -> str:
        return build_prompt(self.request())

    def regenerate(self, *_: object) -> None:
        """Rebuild the prompt. Connected to every control above it."""
        self.prompt_view.setPlainText(self.prompt())

    def recheck(self) -> None:
        """Probe for NIKA desktop again and show what was found."""
        self.app = self._detector()
        found = self.app.found
        if found:
            self.status_label.setText(
                tr("{version} found").format(version=self.app.version)
                if self.app.version
                else tr("found")
            )
            self.status_label.setStyleSheet("")
            if self.app.path is not None:
                self.status_label.setToolTip(str(self.app.path))
        else:
            self.status_label.setText(not_found_text())
            self.status_label.setStyleSheet(f"color: {self.palette_theme.warn};")
            self.status_label.setToolTip("")

        reason = tr(
            "NIKA desktop is {status}. Download and install it, then press Re-check."
        ).format(status=not_found_text())
        for control in (self.open_button, self.open_action):
            control.setEnabled(found)
            control.setToolTip(
                tr("Opens NIKA desktop and copies the prompt, ready to paste")
                if found
                else reason
            )
        self.download_button.setVisible(not found)
        self.download_help.setVisible(not found)

    # ---- actions -----------------------------------------------------------

    def on_copy(self) -> None:
        QGuiApplication.clipboard().setText(self.prompt())
        self.copy_button.setText(tr("Copied"))
        self._copied_timer.start()

    def _restore_copy_label(self) -> None:
        self.copy_button.setText(tr("Copy prompt"))

    def on_open_desktop(self) -> None:
        """Copy the prompt and open the app: the next step is to paste it."""
        if not self.app.found:
            return
        self.on_copy()
        if not launch(self.app):
            QMessageBox.warning(
                self,
                tr("Hand off to NIKA"),
                tr(
                    "NIKA desktop could not be opened from {path}. The prompt is on "
                    "the clipboard; open NIKA desktop yourself and paste it."
                ).format(path=self.app.path),
            )

    def on_download(self) -> None:
        QDesktopServices.openUrl(QUrl(DOWNLOAD_URL))

    def on_change_files(self) -> None:
        """Pick the files to focus on, from the output's own folder only.

        `QFileDialog` cannot be fenced into one folder, so anything chosen from
        elsewhere is left out and said so: NIKA's workspace is this folder, and
        a path outside it is one the agent may not be able to read.
        """
        chosen, _ = QFileDialog.getOpenFileNames(
            self, tr("Choose the files to focus on"), str(self._folder)
        )
        if not chosen:
            return
        inside: list[str] = []
        outside = 0
        folder = self._folder.resolve()
        for raw in chosen:
            try:
                relative = Path(raw).resolve().relative_to(folder)
            except ValueError:
                outside += 1
                continue
            inside.append(relative.as_posix())
        if inside:
            self.files = tuple(dict.fromkeys(inside))
            self._fill_files()
            self.regenerate()
        if outside:
            self.files_note.setText(
                plural(
                    outside,
                    "Left out 1 file outside {folder}.",
                    "Left out {n} files outside {folder}.",
                    folder=self._folder,
                )
            )
        else:
            self.files_note.setText(
                tr("NIKA can read the whole folder; it focuses on these.")
            )

    def on_save(self) -> None:
        stem = self.context.file_path.stem
        suggested = self._folder / f"{stem}-{self.task()}-prompt.md"
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("Save prompt as Markdown"),
            str(suggested),
            tr("Markdown (*.md)"),
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".md":
            target = target.with_name(target.name + ".md")
        try:
            target.write_text(self.prompt(), encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(
                self,
                tr("Hand off to NIKA"),
                tr("Could not save the prompt to {path}: {error}").format(
                    path=target, error=error.strerror or error
                ),
            )
