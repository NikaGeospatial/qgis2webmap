"""The account button: what it says, what its menu offers, and what it refuses.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

The backend is faked throughout. Nothing here opens a socket or a browser - the
point of splitting `AccountBackend` out was that every decision this widget
makes could be tested without a control plane to talk to.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.account import AccountIdentity

qgis_core = pytest.importorskip("qgis.core")


class FakeBackend:
    """A backend that records what was asked of it and never leaves the process."""

    def __init__(self, identity: AccountIdentity | None = None, available: bool = True):
        self._identity = identity or AccountIdentity()
        self._available = available
        self.sign_ins = 0
        self.sign_outs = 0
        # The order calls arrived in. `TestSwitching` asserts against this
        # rather than against the counts, because the invariant it protects is
        # about sequence: a switch must never revoke before it has replaced.
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    def identity(self) -> AccountIdentity:
        return self._identity

    def sign_in(self) -> None:
        self.sign_ins += 1
        self.calls.append("sign_in")
        self._identity = AccountIdentity(email="new@example.org", plan_label="Free")

    def sign_out(self) -> None:
        self.sign_outs += 1
        self.calls.append("sign_out")
        self._identity = AccountIdentity()


SIGNED_IN = AccountIdentity(
    email="mapper@example.org",
    display_name="Sam Mapper",
    org_name="Coastal Research",
    plan_label="Free",
)


def _button(backend):
    from nika_onlymap_exporter.ui.account_button import AccountButton

    return AccountButton(backend)


def _action_texts(button) -> list[str]:
    menu = button.menu()
    if menu is None:
        return []
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def _find_action(button, text: str):
    for action in button.menu().actions():
        if action.text() == text:
            return action
    raise AssertionError(f"no action named {text!r} in {_action_texts(button)}")


class TestSignedOut:
    def test_it_invites_a_sign_in(self, qgis_app):
        from nika_onlymap_exporter.ui.account_button import SIGN_IN_TEXT

        button = _button(FakeBackend())

        assert button.text() == SIGN_IN_TEXT

    def test_it_carries_no_menu(self, qgis_app):
        """A one-item menu is a click spent discovering there was no choice."""
        button = _button(FakeBackend())

        assert button.menu() is None

    def test_pressing_it_signs_in(self, qgis_app):
        backend = FakeBackend()
        button = _button(backend)

        button.click()

        assert backend.sign_ins == 1

    def test_it_names_the_account_once_signed_in(self, qgis_app):
        backend = FakeBackend()
        button = _button(backend)

        button.click()

        assert button.text() == "new@example.org"
        assert button.menu() is not None


def _ink_pixels(icon, size: int = 32) -> set[str]:
    """The colours an icon draws in, ignoring transparent and antialiased edges."""
    image = icon.pixmap(size, size).toImage()
    colours = set()
    for x in range(image.width()):
        for y in range(image.height()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() == 255:
                colours.add(pixel.name())
    return colours


class TestTheSignInIcon:
    """QGIS's `user.svg` was a dark glyph, invisible on the dark theme's chip."""

    def test_it_is_drawn_in_the_chips_text_colour(self, qgis_app):
        from qgis.PyQt.QtGui import QColor, QPalette

        button = _button(FakeBackend())
        palette = button.palette()
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f5f2e7"))
        button.setPalette(palette)  # a theme switch, as the dialog does it

        assert _ink_pixels(button.icon()) == {"#f5f2e7"}

    def test_signed_in_keeps_the_initials(self, qgis_app):
        from qgis.PyQt.QtGui import QColor, QPalette

        button = _button(FakeBackend(SIGNED_IN))
        before = button.icon().cacheKey()
        palette = button.palette()
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f5f2e7"))
        button.setPalette(palette)

        assert button.icon().cacheKey() == before


class TestSignedIn:
    def test_the_address_is_on_the_face_not_buried_in_the_menu(self, qgis_app):
        """The whole point is noticing the wrong account before pressing Share."""
        button = _button(FakeBackend(SIGNED_IN))

        assert button.text() == "mapper@example.org"

    def test_the_menu_heads_with_the_address_and_the_plan(self, qgis_app):
        button = _button(FakeBackend(SIGNED_IN))

        assert _action_texts(button)[:2] == [
            "mapper@example.org",
            "Free · Coastal Research",
        ]

    def test_the_header_rows_are_statements_not_actions(self, qgis_app):
        button = _button(FakeBackend(SIGNED_IN))
        actions = [a for a in button.menu().actions() if not a.isSeparator()]

        assert not actions[0].isEnabled()
        assert not actions[1].isEnabled()

    def test_it_offers_exactly_switch_and_sign_out(self, qgis_app):
        """No upgrade or billing row: a desktop token may not reach /billing/*."""
        assert _action_texts(button=_button(FakeBackend(SIGNED_IN)))[2:] == [
            "Switch account…",
            "Sign out",
        ]

    def test_an_unknown_plan_raises_no_subject(self, qgis_app):
        button = _button(FakeBackend(AccountIdentity(email="a@b.co")))

        assert _action_texts(button) == ["a@b.co", "Switch account…", "Sign out"]

    def test_the_tooltip_names_the_account(self, qgis_app):
        button = _button(FakeBackend(SIGNED_IN))

        assert "mapper@example.org" in button.toolTip()


class TestSwitching:
    def test_it_signs_in_without_signing_out_first(self, qgis_app):
        """A cancelled switch must leave the user on the account they had.

        The device flow stores a token only once the browser approves one, so
        starting a new sign-in while the old session is still valid is what
        makes an abandoned attempt a no-op. Revoking first would punish a
        misclick by logging somebody out of an account they never meant to
        leave.
        """
        backend = FakeBackend(SIGNED_IN)
        button = _button(backend)

        _find_action(button, "Switch account…").trigger()

        assert backend.calls == ["sign_in"]
        assert backend.sign_outs == 0

    def test_the_face_follows_the_new_account(self, qgis_app):
        backend = FakeBackend(SIGNED_IN)
        button = _button(backend)

        _find_action(button, "Switch account…").trigger()

        assert button.text() == "new@example.org"


class TestSigningOut:
    def test_it_asks_before_revoking(self, qgis_app, monkeypatch):
        from nika_onlymap_exporter.ui import account_button as module

        asked = []

        class Stub:
            StandardButton = module.QMessageBox.StandardButton

            @staticmethod
            def question(*args, **kwargs):
                asked.append(args[1])
                return module.QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(module, "QMessageBox", Stub)
        backend = FakeBackend(SIGNED_IN)
        button = _button(backend)

        _find_action(button, "Sign out").trigger()

        assert asked == ["Sign out of NIKA"]
        assert backend.sign_outs == 0, "Cancel must not revoke the session"

    def test_confirming_revokes_and_returns_to_the_invitation(
        self, qgis_app, monkeypatch
    ):
        from nika_onlymap_exporter.ui import account_button as module
        from nika_onlymap_exporter.ui.account_button import SIGN_IN_TEXT

        class Stub:
            StandardButton = module.QMessageBox.StandardButton

            @staticmethod
            def question(*args, **kwargs):
                return module.QMessageBox.StandardButton.Yes

        monkeypatch.setattr(module, "QMessageBox", Stub)
        backend = FakeBackend(SIGNED_IN)
        button = _button(backend)

        _find_action(button, "Sign out").trigger()

        assert backend.sign_outs == 1
        assert button.text() == SIGN_IN_TEXT
        assert button.menu() is None


class TestABuildWithoutHosting:
    """This branch ships the button before the transport it will drive.

    A control that silently does nothing is worse than one that says why, so
    the unavailable backend must produce a notice and must never be asked to do
    something it cannot.
    """

    def test_pressing_sign_in_explains_rather_than_failing(self, qgis_app, monkeypatch):
        from nika_onlymap_exporter.ui import account_button as module

        shown = []

        class Stub:
            StandardButton = module.QMessageBox.StandardButton

            @staticmethod
            def information(*args, **kwargs):
                shown.append(args[1])

        monkeypatch.setattr(module, "QMessageBox", Stub)
        backend = FakeBackend(available=False)
        button = _button(backend)

        button.click()

        assert shown == ["Not in this version yet"]
        assert backend.sign_ins == 0

    def test_the_placeholder_backend_reports_itself_unavailable(self, qgis_app):
        from nika_onlymap_exporter.ui.account_button import PendingHostingBackend

        assert not PendingHostingBackend().available

    def test_the_placeholder_ignores_an_account_another_build_cached(self, qgis_app):
        """A hosting build's sign-in is in the shared cache. Showing it here
        offered a Sign out that could only say "not in this version yet"."""
        from nika_onlymap_exporter.core.account import save_identity
        from nika_onlymap_exporter.ui.account_button import PendingHostingBackend

        save_identity(AccountIdentity(email="cached@example.org"))
        try:
            assert not PendingHostingBackend().identity().is_known
            button = _button(PendingHostingBackend())
            assert "cached@example.org" not in button.text()
        finally:
            save_identity(AccountIdentity())


class TestTheCacheRoundTrips:
    def test_what_is_saved_comes_back(self, qgis_app):
        from nika_onlymap_exporter.core.account import (
            clear_identity,
            load_identity,
            save_identity,
        )

        save_identity(SIGNED_IN)
        try:
            assert load_identity() == SIGNED_IN
        finally:
            clear_identity()

    def test_clearing_leaves_nobody_named(self, qgis_app):
        from nika_onlymap_exporter.core.account import (
            clear_identity,
            load_identity,
            save_identity,
        )

        save_identity(SIGNED_IN)
        clear_identity()

        assert not load_identity().is_known

    def test_a_blank_field_is_removed_not_stored_empty(self, qgis_app):
        """ "Not known" must be one state in the file rather than two."""
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.core.account import SETTING_ORG, save_identity

        save_identity(SIGNED_IN)
        save_identity(AccountIdentity(email="a@b.co"))
        try:
            assert not QSettings().contains(SETTING_ORG)
        finally:
            save_identity(AccountIdentity())


class TestTheDialogSeatsIt:
    def test_the_settings_tab_carries_the_account(self, qgis_app, project):
        """True of every project, so it sits with the other settings."""
        from nika_onlymap_exporter.ui.account_button import AccountButton
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        dialog = MainDialog(FakeIface(), None)
        try:
            assert isinstance(dialog.account_button, AccountButton)
            settings = next(
                dialog.tabs.widget(i)
                for i in range(dialog.tabs.count())
                if dialog.tabs.tabText(i) == "Settings"
            )
            assert dialog.account_button in settings.findChildren(AccountButton)
        finally:
            dialog.close()

    def test_it_is_not_in_the_tab_corner(self, qgis_app, project):
        """The corner is for things that act on the map. An account is not."""
        from qgis.PyQt.QtCore import Qt

        from nika_onlymap_exporter.ui.account_button import AccountButton
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        dialog = MainDialog(FakeIface(), None)
        try:
            corner = dialog.tabs.cornerWidget(Qt.Corner.TopRightCorner)
            assert corner.findChild(AccountButton) is None
        finally:
            dialog.close()


class TestItLooksPressable:
    """An outlined chip, not bare text, so it reads as something to press."""

    def test_it_is_a_chip_signed_out_and_in(self, qgis_app):
        out = _button(FakeBackend())
        assert out.property("role") == "chip"
        assert out.property("hasMenu") is False

        known = _button(FakeBackend(AccountIdentity(email="ada@example.org")))
        assert known.property("role") == "chip"
        assert known.property("hasMenu") is True

    def test_signed_in_shows_initials(self, qgis_app):
        from nika_onlymap_exporter.ui.account_button import initials

        assert initials(AccountIdentity(display_name="Ada Lovelace")) == "AL"
        assert initials(AccountIdentity(email="ada.lovelace@example.org")) == "AL"
        assert initials(AccountIdentity(email="mapper@example.org")) == "M"

        known = _button(FakeBackend(AccountIdentity(email="ada@example.org")))
        assert not known.icon().isNull()
