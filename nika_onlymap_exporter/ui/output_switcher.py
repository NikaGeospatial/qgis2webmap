"""What the dialog makes, as the dialog's title: `Web map ▾`.

The two outputs used to be a segmented `Make: [Web map | Cartograph (Beta)]`
on a row of its own above the tabs. It read as a second, louder tab bar, and
the research behind replacing it (Esri, Google, QGIS plugins, design tools)
found nobody switching a whole set of tabs with one: what they switch with is
a title that opens a short menu, the way Figma, Notion and Google Docs change
the file or workspace under the same window.

So the output is the heading, and the menu says in one line what each one is.
The menu rows are `QWidgetAction`s only for that second line; each is still a
checkable action in one exclusive group, so `trigger()` is how both a click
and a test choose an output.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QKeyEvent, QMouseEvent
from qgis.PyQt.QtWidgets import (
    QAction,
    QActionGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from ..core.settings import DialogOutput
from .i18n import tr

# The line icon each output wears, in the title and in its menu row.
OUTPUT_ICONS = {DialogOutput.WEB_MAP: "web", DialogOutput.CARTOGRAPH: "page"}


def _output_names() -> dict[DialogOutput, str]:
    return {
        DialogOutput.WEB_MAP: tr("Web map"),
        DialogOutput.CARTOGRAPH: tr("Cartograph"),
    }


def _output_descriptions() -> dict[DialogOutput, str]:
    return {
        DialogOutput.WEB_MAP: tr("An interactive map to pan, zoom and click"),
        DialogOutput.CARTOGRAPH: tr(
            "A print layout with interactive maps, to read or print"
        ),
    }


class _OutputRow(QFrame):
    """One menu row: icon, name (and Beta), the one-line description, a tick.

    A `QWidgetAction`'s widget gets the mouse and keys instead of the menu,
    so the row triggers its own action and closes the menu itself.
    """

    def __init__(self, action: QAction, menu: QMenu, icon: str, beta: bool) -> None:
        super().__init__(menu)
        self._action = action
        self._menu = menu
        self.setProperty("role", "outputRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        grid = QGridLayout(self)
        grid.setContentsMargins(10, 7, 12, 7)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(1)

        # A flat tool button only so `theme.paint_line_icons` colours it with
        # every other line icon; it takes no clicks of its own.
        self.icon = QToolButton(self)
        self.icon.setProperty("role", "outputRowIcon")
        self.icon.setProperty("lineIcon", icon)
        self.icon.setIconSize(QSize(18, 18))
        self.icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        grid.addWidget(self.icon, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.name = QLabel(action.text(), self)
        self.name.setProperty("role", "outputRowName")
        name_row.addWidget(self.name)
        if beta:
            chip = QLabel(tr("Beta"), self)
            chip.setProperty("role", "betaChip")
            name_row.addWidget(chip)
        name_row.addStretch(1)
        grid.addLayout(name_row, 0, 1)

        self.description = QLabel(action.toolTip(), self)
        self.description.setProperty("role", "outputRowDescription")
        grid.addWidget(self.description, 1, 1)

        self.tick = QLabel("✓", self)
        self.tick.setProperty("role", "outputRowTick")
        grid.addWidget(self.tick, 0, 2, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(1, 1)

        action.toggled.connect(self._show_checked)
        self._show_checked(action.isChecked())

    def _show_checked(self, checked: bool) -> None:
        # Kept in the layout either way, so the menu does not change width.
        self.tick.setText("✓" if checked else "")

    def _choose(self) -> None:
        self._menu.close()
        if not self._action.isChecked():
            self._action.trigger()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt override
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._choose()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt override
        if event is not None and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self._choose()
            return
        super().keyPressEvent(event)

    def event(self, event: QEvent | None) -> bool:
        # The menu marks a widget action active on hover; give the row focus
        # then, so arrow keys and the pointer agree on which row Enter picks.
        if event is not None and event.type() == QEvent.Type.Enter:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        return super().event(event)


class OutputSwitcher(QToolButton):
    """The title button and its menu. Emits `chosen` when the user picks."""

    chosen = pyqtSignal(object)  # DialogOutput

    def __init__(self, output: DialogOutput, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "outputTitle")
        self.setProperty("hasMenu", True)
        self.setIconSize(QSize(16, 16))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setToolTip(tr("Choose what to make"))

        menu = QMenu(self)
        self._group = QActionGroup(self)
        self._group.setExclusive(True)
        self.actions_by_output: dict[DialogOutput, QAction] = {}
        names, descriptions = _output_names(), _output_descriptions()
        for kind in DialogOutput:
            action = QWidgetAction(self)
            action.setText(names[kind])
            action.setToolTip(descriptions[kind])
            action.setCheckable(True)
            action.setDefaultWidget(
                _OutputRow(
                    action,
                    menu,
                    OUTPUT_ICONS[kind],
                    beta=kind is DialogOutput.CARTOGRAPH,
                )
            )
            self._group.addAction(action)
            menu.addAction(action)
            self.actions_by_output[kind] = action
        self.setMenu(menu)

        self.beta_chip = QLabel(tr("Beta"), parent)
        self.beta_chip.setProperty("role", "betaChip")

        # Checked before connecting, so opening the dialog is not a choice.
        self.set_output(output)
        self._group.triggered.connect(self._on_triggered)

    def set_output(self, output: DialogOutput) -> None:
        """Show `output` as the title, without emitting `chosen`."""
        self.actions_by_output[output].setChecked(True)
        self.setText(_output_names()[output])
        self.setProperty("lineIcon", OUTPUT_ICONS[output])
        self.beta_chip.setVisible(output is DialogOutput.CARTOGRAPH)

    def output(self) -> DialogOutput:
        for kind, action in self.actions_by_output.items():
            if action.isChecked():
                return kind
        return DialogOutput.WEB_MAP

    def _on_triggered(self, action: QAction) -> None:
        kind = next(k for k, a in self.actions_by_output.items() if a is action)
        self.set_output(kind)
        self.chosen.emit(kind)
