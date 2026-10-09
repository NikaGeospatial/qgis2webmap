"""Choosing the dialog's language, and offering the one QGIS already speaks.

Two widgets, both small:

- `LanguageButton`, a footer chip naming the language in use. Its menu lists
  "Follow QGIS", English, and every language this version has a pack for;
  a language not yet downloaded says so, and picking it downloads it.
- `LanguageOffer`, a one-line bar shown only while following QGIS when QGIS
  speaks a language we have but its pack is not here. Nothing is downloaded
  until the user presses its button.

Both emit `changed` when the language actually in use would be different;
the dialog reopens itself on that. See `languages` for the rules.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QSettings, Qt, pyqtSignal
from qgis.PyQt.QtGui import QActionGroup, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QWidget,
)

from . import languages
from .i18n import active_language, qgis_locale, tr


def _setting() -> str:
    return str(QSettings().value(languages.SETTING_KEY, languages.AUTO, type=str))


def _install(parent: QWidget, code: str, fetch: languages.Fetcher | None) -> bool:
    """Download `code` if it is missing. False, having said why, on failure."""
    if languages.installed_pack(code) is not None:
        return True
    QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        languages.download(code, fetch or languages.qgis_fetcher())
    except Exception as exc:
        QGuiApplication.restoreOverrideCursor()
        QMessageBox.warning(
            parent,
            tr("Language not downloaded"),
            tr("{language} could not be downloaded.").format(
                language=languages.NATIVE_NAMES.get(code, code)
            )
            + f"\n\n{exc}",
        )
        return False
    QGuiApplication.restoreOverrideCursor()
    return True


class LanguageButton(QToolButton):
    """A footer chip naming the language in use, with a menu to change it."""

    changed = pyqtSignal()

    def __init__(
        self, parent: QWidget | None = None, fetch: languages.Fetcher | None = None
    ) -> None:
        super().__init__(parent)
        self._fetch = fetch
        self.setProperty("role", "chip")
        self.setProperty("hasMenu", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setText(languages.NATIVE_NAMES.get(active_language(), active_language()))
        self.setToolTip(tr("The language this dialog is shown in"))
        self._menu = QMenu(self)
        self._menu.setToolTipsVisible(True)
        self._menu.aboutToShow.connect(self._build_menu)
        self.setMenu(self._menu)
        self.actions_by_choice: dict[str, object] = {}
        self._build_menu()

    def _build_menu(self) -> None:
        self._menu.clear()
        self.actions_by_choice = {}
        group = QActionGroup(self._menu)
        group.setExclusive(True)
        setting = _setting()

        follow = self._menu.addAction(tr("Follow QGIS"))
        follow.setToolTip(
            tr(
                "Use the language QGIS is set to, when there is a pack for it. "
                "Otherwise English."
            )
        )
        self._menu.addSeparator()
        entries = [(languages.AUTO, follow)]
        for code in languages.available():
            name = languages.NATIVE_NAMES.get(code, code)
            if code != languages.ENGLISH and languages.installed_pack(code) is None:
                name = tr("{language} - download").format(language=name)
            entries.append((code, self._menu.addAction(name)))

        for choice, action in entries:
            action.setCheckable(True)
            action.setChecked(choice == setting)
            group.addAction(action)
            action.triggered.connect(lambda _on=False, c=choice: self.choose(c))
            self.actions_by_choice[choice] = action

    def choose(self, choice: str) -> None:
        """Store `choice`, downloading its pack first if it needs one."""
        if choice not in (languages.AUTO, languages.ENGLISH) and not _install(
            self, choice, self._fetch
        ):
            return
        QSettings().setValue(languages.SETTING_KEY, choice)
        if languages.effective(choice, qgis_locale()) != active_language():
            self.changed.emit()


class LanguageOffer(QFrame):
    """'QGIS speaks X, and so can this dialog' - with a button, never automatic."""

    changed = pyqtSignal()

    def __init__(
        self,
        code: str,
        parent: QWidget | None = None,
        fetch: languages.Fetcher | None = None,
    ) -> None:
        super().__init__(parent)
        self.code = code
        self._fetch = fetch
        self.setProperty("role", "notice")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)

        name = languages.NATIVE_NAMES.get(code, code)
        # Named in its own script: someone who reads it is who it is for.
        layout.addWidget(
            QLabel(
                tr("This dialog is available in {language}.").format(language=name),
                self,
            ),
            1,
        )
        download = QPushButton(tr("Use {language}").format(language=name), self)
        download.clicked.connect(self._accept)
        layout.addWidget(download)
        dismiss = QPushButton(tr("No thanks"), self)
        dismiss.setProperty("role", "link")
        dismiss.clicked.connect(self._dismiss)
        layout.addWidget(dismiss)

    def _accept(self) -> None:
        if not _install(self, self.code, self._fetch):
            return
        self.hide()
        if languages.effective(_setting(), qgis_locale()) != active_language():
            self.changed.emit()

    def _dismiss(self) -> None:
        QSettings().setValue(languages.OFFER_DISMISSED_KEY, self.code)
        self.hide()


def make_offer(
    parent: QWidget, fetch: languages.Fetcher | None = None
) -> LanguageOffer | None:
    """The offer bar if one is due, else None."""
    dismissed = str(QSettings().value(languages.OFFER_DISMISSED_KEY, "", type=str))
    code = languages.offer(_setting(), qgis_locale(), dismissed)
    return LanguageOffer(code, parent, fetch) if code else None
