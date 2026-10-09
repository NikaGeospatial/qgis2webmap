"""Choosing the dialog's language, and offering the one QGIS already speaks.

Two widgets, both small:

- `LanguagePicker`, the list on the Settings tab: "Follow QGIS", English, and
  every language this version has a pack for. A language not yet downloaded
  says so, and picking it downloads it.
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
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
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


class LanguagePicker(QComboBox):
    """The Settings tab's language list: Follow QGIS, English, then the packs."""

    changed = pyqtSignal()

    def __init__(
        self, parent: QWidget | None = None, fetch: languages.Fetcher | None = None
    ) -> None:
        super().__init__(parent)
        self._fetch = fetch
        self.setToolTip(tr("The language this dialog is shown in"))
        self.refresh()
        # `activated`, not `currentIndexChanged`: only a pick by the user may
        # start a download, never the list being filled or reset.
        self.activated.connect(self._on_activated)

    def refresh(self) -> None:
        """Rebuild the list, marking languages that still need a download."""
        self.blockSignals(True)
        self.clear()
        follow = tr("Follow QGIS")
        locale_code = languages.match_locale(qgis_locale())
        if locale_code and languages.installed_pack(locale_code):
            follow += f" ({languages.NATIVE_NAMES.get(locale_code, locale_code)})"
        self.addItem(follow, languages.AUTO)
        for code in languages.available():
            name = languages.NATIVE_NAMES.get(code, code)
            if code != languages.ENGLISH and languages.installed_pack(code) is None:
                name = tr("{language} - download").format(language=name)
            self.addItem(name, code)
        self.setCurrentIndex(max(0, self.findData(_setting())))
        self.blockSignals(False)

    def _on_activated(self, index: int) -> None:
        self.choose(str(self.itemData(index)))

    def choose(self, choice: str) -> None:
        """Store `choice`, downloading its pack first if it needs one."""
        if choice not in (languages.AUTO, languages.ENGLISH) and not _install(
            self, choice, self._fetch
        ):
            self.refresh()  # back to what is actually in use
            return
        QSettings().setValue(languages.SETTING_KEY, choice)
        self.refresh()
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
