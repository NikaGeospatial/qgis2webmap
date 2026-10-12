"""The signed-in account, shown in the bottom row and swapped from its menu.

**Why the bottom row and not the tab corner.** The corner holds Preview and
Share because those act on the map in front of you. Which NIKA account this
QGIS profile is paired with is not a property of the map - it is the same in
every project you open - so it belongs with the other session chrome, beside
Discord and Close. `main_dialog._build_button_row` states that division; this
widget is the second thing to honour it.

**Why the address is on the face of the button rather than inside the menu.**
The question this answers is "which account am I about to publish to", and an
answer you have to open a menu to see does not answer it. Somebody with a work
account and a personal one needs to notice the wrong one *before* pressing
Share, not after.

**What this deliberately cannot do.** There is no upgrade or billing action
here. The plugin's token is issued with the scopes `identity` and `maps` only,
and the control plane's route table refuses `/billing/*` to a desktop bearer
outright - the comment on that gate records a QGIS token reaching the billing
portal as the bug it was written to fix. Changing a plan is therefore a thing
that happens in a browser, where a human is present and the credential is a
cookie rather than a file on disk.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from typing import Protocol

from qgis.PyQt.QtCore import QEvent, QRectF, Qt
from qgis.PyQt.QtGui import QFont, QIcon, QPainter, QPalette, QPixmap
from qgis.PyQt.QtWidgets import QMenu, QMessageBox, QToolButton, QWidget

from ..core.account import AccountIdentity, load_identity
from .i18n import tr
from .theme import line_icon

# Icon names verified present in QGIS 4.2's compiled theme. As with
# `main_dialog.SHARE_MENU_ICONS` these are resource paths rather than files, and
# a name that has gone yields a null icon rather than raising - the row keeps
# its text and loses its picture.
ICON_SWITCH = "mActionRefresh.svg"
ICON_SIGN_OUT = "mActionRemove.svg"

SIGN_IN_TEXT = "Sign in to NIKA"


class AccountBackend(Protocol):
    """What the button needs from whatever is holding the credential.

    Kept to four members so that the hosting implementation and the placeholder
    below are genuinely interchangeable, and so this widget can be exercised
    without a network, a token or a running control plane.
    """

    @property
    def available(self) -> bool:
        """Whether this build can talk to NIKA at all.

        False on a build without the hosting package, where the button explains
        itself rather than offering an action that cannot work.
        """

    def identity(self) -> AccountIdentity:
        """Who we were last told we are. Never raises; never blocks."""

    def sign_in(self) -> None:
        """Begin the device flow. Returns once it has been *started*."""

    def sign_out(self) -> None:
        """Revoke the session and forget the token and the cached identity."""


class PendingHostingBackend:
    """The stand-in on a build where `hosting/` is not present.

    This branch carries the dialog work for the release *after* hosting, so the
    button, its menu and everything they say are built and tested here while the
    transport lands separately. Reporting `available = False` is what turns the
    button into an honest notice instead of a control that silently does
    nothing - the same treatment the NIKA and QGIS2Carto rows get in the Share
    menu.

    It still reads the cached identity rather than hardcoding an empty one, so
    that on the merged build the button is correct from the first paint, before
    anything has been fetched.
    """

    @property
    def available(self) -> bool:
        return False

    def identity(self) -> AccountIdentity:
        return load_identity()

    def sign_in(self) -> None:
        raise NotImplementedError

    def sign_out(self) -> None:
        raise NotImplementedError


class AccountButton(QToolButton):
    """A flat button naming the signed-in account, with a menu to change it."""

    def __init__(self, backend: AccountBackend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self.setAutoRaise(True)
        # An outlined chip in the dialog's theme. Bare text with a person
        # glyph did not read as something to press.
        self.setProperty("role", "chip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Text beside the icon, not under it: this sits in a row of ordinary
        # push buttons and a two-line control would set the row's height.
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._menu = QMenu(self)
        self._menu.setToolTipsVisible(True)
        self.clicked.connect(self._on_clicked)
        self.refresh()

    # ---- State -----------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the face and the menu from the backend.

        Called on construction and after anything that can change who we are.
        Cheap by design - it reads a cache, never the network - so a caller may
        call it whenever it is unsure rather than tracking when it must.
        """
        identity = self._backend.identity()
        if identity.is_known:
            self._show_signed_in(identity)
        else:
            self._show_signed_out()

    def _show_signed_in(self, identity: AccountIdentity) -> None:
        self.setText(identity.label)
        self.setIcon(_initials_icon(initials(identity), self.palette(), self.font()))
        self._set_has_menu(True)
        self.setToolTip(_signed_in_tooltip(identity))
        self._build_menu(identity)
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def _show_signed_out(self) -> None:
        self.setText(SIGN_IN_TEXT)
        self.setIcon(self._person_icon())
        self._set_has_menu(False)
        self.setToolTip(
            tr(
                "Pair this QGIS profile with your NIKA account so you can publish "
                "maps to it. Your browser opens NIKA's sign-in page; the plugin "
                "never sees your password."
            )
        )
        # No menu at all when signed out. A menu holding one item is a click
        # somebody has to make to discover there was no choice to make.
        self.setMenu(None)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)

    def _person_icon(self) -> QIcon:
        """A person outline in the chip's own text colour.

        QGIS's `user.svg` is a dark glyph, invisible on the dark theme's chip.
        The palette is the theme's - the dialog stylesheet sets it - so this
        follows a theme switch through `changeEvent`.
        """
        palette = self.palette()
        return line_icon(
            "account",
            palette.color(QPalette.ColorRole.ButtonText).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText
            ).name(),
        )

    def changeEvent(self, event: QEvent | None) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if (
            event is not None
            and event.type() == QEvent.Type.PaletteChange
            and self.menu() is None  # signed out: the person icon, not initials
        ):
            self.setIcon(self._person_icon())

    def _set_has_menu(self, has_menu: bool) -> None:
        """Flag whether the chip opens a menu, for the stylesheet's chevron.

        A dynamic property only restyles after a repolish.
        """
        self.setProperty("hasMenu", has_menu)
        self.style().unpolish(self)
        self.style().polish(self)

    def _build_menu(self, identity: AccountIdentity) -> None:
        self._menu.clear()

        # The header is the whole answer for most people who open this: it says
        # who, and on what plan, and then gets out of the way. Disabled because
        # it is a statement rather than an action.
        header = self._menu.addAction(identity.email or identity.display_name)
        header.setEnabled(False)
        if identity.plan_line:
            plan = self._menu.addAction(identity.plan_line)
            plan.setEnabled(False)
        self._menu.addSeparator()

        switch = self._menu.addAction(tr("Switch account…"))
        switch.setIcon(_theme_icon(ICON_SWITCH))
        switch.setToolTip(
            tr(
                "Sign in as somebody else. Your browser opens NIKA's sign-in page; "
                "this account stays paired until the new one is approved."
            )
        )
        switch.triggered.connect(self._on_switch)

        sign_out = self._menu.addAction(tr("Sign out"))
        sign_out.setIcon(_theme_icon(ICON_SIGN_OUT))
        sign_out.setToolTip(
            tr(
                "Unpair this QGIS profile and revoke its access to your NIKA "
                "account. Maps you have already published stay online."
            )
        )
        sign_out.triggered.connect(self._on_sign_out)

    # ---- Actions ---------------------------------------------------------

    def _on_clicked(self) -> None:
        """The face of the button only acts when signed out.

        With a menu attached the press opens it and never reaches here, so this
        is the sign-in path and nothing else.
        """
        if self._backend.identity().is_known:
            return
        self._start_sign_in()

    def _on_switch(self) -> None:
        """Sign in as somebody else, without signing out first.

        **Deliberately not sign-out-then-sign-in.** The device flow stores a
        token only once the browser has approved it, so starting a new sign-in
        while the old one is still valid means a cancelled or abandoned attempt
        leaves the user exactly where they were. Revoking first would punish a
        misclick by logging them out of an account they never meant to leave.
        """
        self._start_sign_in()

    def _start_sign_in(self) -> None:
        if not self._backend.available:
            self._explain_pending()
            return
        self._backend.sign_in()
        self.refresh()

    def _on_sign_out(self) -> None:
        if not self._backend.available:
            self._explain_pending()
            return
        identity = self._backend.identity()
        # Asked because signing out is not free: it revokes the session server
        # side, so the device disappears from the account's device list and
        # coming back means going through the browser again.
        confirmed = QMessageBox.question(
            self,
            tr("Sign out of NIKA"),
            f"Sign out of {identity.label} and revoke this computer's access?\n\n"
            "Maps you have already published stay online. You will need to "
            "sign in again through your browser to publish anything new.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._backend.sign_out()
        self.refresh()

    def _explain_pending(self) -> None:
        """Say plainly that the transport is not in this build yet."""
        QMessageBox.information(
            self,
            tr("Not in this version yet"),
            tr(
                "Signing in to NIKA arrives with map hosting in a later release. "
                "Everything this plugin exports today is a file you own and can "
                "share yourself - nothing here needs an account."
            ),
        )


def _signed_in_tooltip(identity: AccountIdentity) -> str:
    lines = [f"Signed in to NIKA as {identity.email or identity.display_name}."]
    if identity.plan_line:
        lines.append(identity.plan_line)
    lines.append("Click to switch account or sign out.")
    return "\n".join(lines)


def initials(identity: AccountIdentity) -> str:
    """One or two letters for the avatar: "Ada Lovelace" or ada.lovelace@ -> AL."""
    source = identity.display_name or identity.email.split("@", 1)[0]
    words = [
        w
        for w in source.replace(".", " ").replace("_", " ").replace("-", " ").split()
        if w
    ]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _initials_icon(
    letters: str, palette: QPalette, base_font: QFont, size: int = 20
) -> QIcon:
    """The letters on a disc in the palette's accent, drawn at 2x for HiDPI."""
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
    painter.drawEllipse(QRectF(0, 0, size * scale, size * scale))
    font = QFont(base_font)
    font.setPixelSize(int(size * scale * (0.42 if len(letters) > 1 else 0.5)))
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(palette.color(QPalette.ColorRole.HighlightedText))
    painter.drawText(
        QRectF(0, 0, size * scale, size * scale), Qt.AlignmentFlag.AlignCenter, letters
    )
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def _theme_icon(name: str) -> QIcon:
    """A QGIS theme icon, or an empty one if this build has not got it.

    The same wrapper `main_dialog` uses, repeated rather than imported to keep
    this widget free of a dependency on the dialog that hosts it.
    """
    from qgis.core import QgsApplication

    with contextlib.suppress(Exception):
        icon: QIcon = QgsApplication.getThemeIcon(f"/{name}")
        return icon
    return QIcon()
