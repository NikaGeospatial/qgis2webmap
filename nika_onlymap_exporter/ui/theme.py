"""The export dialog's NIKA look: two palettes, one font, one stylesheet.

**Scoped to the dialog, never the application.** A plugin that calls
`QApplication.setStyleSheet` restyles every QGIS panel and every other plugin.
Everything here is set on the dialog itself, and every selector is anchored on
its object name, so the rules travel down to the dialog's own children - menus
and message boxes parented to it included - and nowhere else.

**Light or dark follows QGIS.** Night Mapping and Blend of Gray users would get
a pale panel glowing out of a dark window otherwise. The palettes are NIKA's
Plum-Light (brass accent) and Plum-Dark (lime accent) from the design system,
with the muted, link and warning colours darkened or lightened until every text
pairing reads at WCAG AA - brass itself is far too pale to be text on paper, so
it is only ever a fill, with ink on top.

**Rounded only where it means "press me".** Buttons and fields are rounded;
tables, the help pane, sections and the tab pane stay square, because they are
places to read rather than things to press.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QByteArray, QEvent, QObject, QPoint, Qt
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPalette,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextTable,
)
from qgis.PyQt.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QMenu,
    QToolButton,
    QWidget,
)

PACKAGE_DIR = Path(__file__).resolve().parent.parent
FONT_DIR = PACKAGE_DIR / "fonts"
ICON_DIR = PACKAGE_DIR / "icons" / "theme"
FONT_FAMILY = "Inter"

# The corner's 16 px line icons, drawn for this plugin. `{c}` is the colour.
# Made at runtime rather than shipped as files: each needs a colour per theme
# and per state, which would be a dozen files for four shapes.
LINE_ICONS = {
    "preview": (
        '<path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8s-2.4 4.5-6.5 4.5S1.5 8 '
        '1.5 8z"/><circle cx="8" cy="8" r="2"/>'
    ),
    "live": '<circle cx="8" cy="8" r="4" fill="{c}" stroke="none"/>',
    "live-off": '<circle cx="8" cy="8" r="3.5"/>',
    "share": '<path d="M8 10V2.5M5 5.2 8 2.2l3 3M3.5 8.5v4.5h9V8.5"/>',
    "export": '<path d="M8 2.5V10M5 7.2l3 3 3-3M3.5 13.2h9"/>',
    "account": (
        '<circle cx="8" cy="5.5" r="2.5"/>'
        '<path d="M3 13.5c0-2.8 2.2-4.5 5-4.5s5 1.7 5 4.5"/>'
    ),
    "info": (
        '<circle cx="8" cy="8" r="6"/>'
        '<path d="M8 7.2v3.8"/><circle cx="8" cy="4.9" r="0.4" fill="{c}"/>'
    ),
}


@dataclass(frozen=True)
class Palette:
    """One NIKA theme, as the hex colours the stylesheet needs."""

    name: str
    canvas: str  # the window behind everything, a step darker than a card
    paper: str  # cards: sections, tables, menus - where content sits
    field: str  # inputs, table headers, the inset behind quiet controls
    hover: str  # a pressable thing under the pointer
    rule: str  # hairlines and outlines
    ink: str  # primary text
    muted: str  # help text and secondary labels
    quiet: str  # placeholders, disabled text
    accent: str  # the one colour: fills only, never text on paper
    accent_hover: str
    on_accent: str  # text and icons drawn on the accent
    accent_soft: str  # selection behind ink
    link: str  # accent hue, dark or light enough to read as text
    warn: str
    live: str  # the live-preview switch while on: a recording red

    @property
    def is_dark(self) -> bool:
        return QColor(self.paper).lightness() < 128

    def icon(self, name: str) -> str:
        """A theme icon's path, in the form a stylesheet `url()` accepts."""
        variant = "dark" if self.is_dark else "light"
        return (ICON_DIR / f"{name}-{variant}.svg").as_posix()


PLUM_LIGHT = Palette(
    name="plum-light",
    canvas="#efeaf1",
    paper="#fdfcfe",
    field="#f5f1f6",
    hover="#ece6ee",
    rule="#d9cfdc",
    ink="#170d19",
    muted="#6b606e",
    quiet="#807683",
    accent="#eba941",
    accent_hover="#e09a2c",
    on_accent="#170d19",
    accent_soft="#f5e1c8",
    link="#8a5200",
    warn="#a2540a",
    live="#d92d20",
)

PLUM_DARK = Palette(
    name="plum-dark",
    # Wider steps than the light theme's: the same ratios read as one flat
    # plum in the dark, so window, card and field are each clearly lifted.
    canvas="#08020a",
    paper="#22142a",
    field="#311f3a",
    hover="#3d2a46",
    rule="#56425e",
    ink="#f5f2e7",
    muted="#a39aa5",
    quiet="#817683",
    accent="#abf051",
    accent_hover="#bdf76f",
    on_accent="#0e0511",
    accent_soft="#203501",
    link="#abf051",
    warn="#e78a45",
    live="#ff6b6b",
)


# Settings > Appearance: "auto" follows QGIS, or "light" / "dark" on purpose.
THEME_KEY = "qgis2webmap/theme"
THEME_CHOICES = ("auto", "light", "dark")


def chosen_theme() -> str:
    from qgis.PyQt.QtCore import QSettings

    value = str(QSettings().value(THEME_KEY, "auto", type=str))
    return value if value in THEME_CHOICES else "auto"


def pick_palette(qgis_palette: QPalette, choice: str = "auto") -> Palette:
    """The palette for `choice`; "auto" is Plum-Dark under a dark QGIS."""
    if choice == "light":
        return PLUM_LIGHT
    if choice == "dark":
        return PLUM_DARK
    window = qgis_palette.color(QPalette.ColorRole.Window)
    return PLUM_DARK if window.lightness() < 128 else PLUM_LIGHT


_loaded_family: str | None = None


def load_fonts() -> str | None:
    """Register the bundled Inter for this process, once.

    Registering a font makes it *available*; it changes nothing on screen until
    a widget asks for it, so QGIS's own font is untouched. Returns the family
    name, or None if the files are missing or Qt refused them - the dialog then
    keeps the system font, which is a cosmetic loss and nothing more.
    """
    global _loaded_family
    if _loaded_family is not None:
        return _loaded_family
    families: set[str] = set()
    for path in sorted(FONT_DIR.glob("Inter-*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(font_id))
    if FONT_FAMILY in families:
        _loaded_family = FONT_FAMILY
    return _loaded_family


def apply(dialog: QWidget) -> Palette:
    """Dress `dialog` in the NIKA theme that matches QGIS, and return it."""
    app = QApplication.instance()
    palette = pick_palette(
        app.palette() if app is not None else dialog.palette(), chosen_theme()
    )

    family = load_fonts()
    if family is not None:
        font = QFont(dialog.font())
        font.setFamily(family)
        dialog.setFont(font)

    # The palette as well as the stylesheet: labels that take their colour
    # from a palette role (help text, the fidelity summary) and the rich text
    # in the Help tab's links read these, not the stylesheet.
    qpalette = QPalette(dialog.palette())
    roles = {
        QPalette.ColorRole.Window: palette.canvas,
        QPalette.ColorRole.WindowText: palette.ink,
        QPalette.ColorRole.Base: palette.paper,
        QPalette.ColorRole.AlternateBase: palette.field,
        QPalette.ColorRole.Text: palette.ink,
        QPalette.ColorRole.Button: palette.field,
        QPalette.ColorRole.ButtonText: palette.ink,
        QPalette.ColorRole.Highlight: palette.accent,
        QPalette.ColorRole.HighlightedText: palette.on_accent,
        QPalette.ColorRole.PlaceholderText: palette.muted,
        QPalette.ColorRole.Link: palette.link,
        QPalette.ColorRole.LinkVisited: palette.link,
        QPalette.ColorRole.ToolTipBase: palette.field,
        QPalette.ColorRole.ToolTipText: palette.ink,
    }
    for role, colour in roles.items():
        qpalette.setColor(role, QColor(colour))
    with contextlib.suppress(AttributeError):  # Qt5 has no Accent role
        qpalette.setColor(QPalette.ColorRole.Accent, QColor(palette.accent))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        qpalette.setColor(QPalette.ColorGroup.Disabled, role, QColor(palette.quiet))
    dialog.setPalette(qpalette)

    dialog.setProperty(
        "rtl", dialog.layoutDirection() == Qt.LayoutDirection.RightToLeft
    )
    dialog.setStyleSheet(stylesheet(palette, dialog.objectName(), family))

    # Rounded fields are taller than a bare label, and a form aligns labels to
    # the top of the row by default, so each label sat above its field's line.
    for form in dialog.findChildren(QFormLayout):
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(14)
    attach_menus(dialog)
    paint_line_icons(dialog, palette)
    # Sections are cards: their contents sit inset from the card's edge, with
    # the title outside and above it.
    for box in dialog.findChildren(QGroupBox):
        layout = box.layout()
        if layout is not None:
            layout.setContentsMargins(14, 12, 14, 12)
    _dress_child_dialogs(dialog, palette)
    return palette


class _ChildDialogDresser(QObject):
    """Gives every dialog opened from ours - message boxes above all - the
    theme's background on the box itself.

    The scoped `QDialog` rule reaches them through the cascade, and Qt5 lets
    that go stale: after switching from Follow QGIS to Dark, every message box
    opened kept the light background under the new light text. Set on the box
    as it appears, the background cannot go stale.
    """

    def __init__(self, dialog: QWidget, paper: str) -> None:
        super().__init__(dialog)
        self.paper = paper

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ChildPolished:
            child = event.child()  # type: ignore[attr-defined]
            if isinstance(child, QDialog):
                child.setStyleSheet(f"QDialog {{ background: {self.paper}; }}")
        return False


def _dress_child_dialogs(dialog: QWidget, palette: Palette) -> None:
    dresser = dialog.findChild(_ChildDialogDresser)
    if dresser is None:
        dresser = _ChildDialogDresser(dialog, palette.paper)
        dialog.installEventFilter(dresser)
    dresser.paper = palette.paper


class _MenuAttacher(QObject):
    """Opens a menu flush under its button, and tells the button it is open.

    Qt places a button's menu wherever fits and draws it as a separate box,
    so it reads as something that spawned out of nowhere. Here the menu takes
    the button's left edge (right edge, right to left), overlaps its bottom
    border by a pixel and is at least as wide; the button squares off its
    bottom corners while the menu is open, so the two read as one shape.
    """

    def __init__(
        self, menu: QWidget, button: QWidget, match_width: bool = False
    ) -> None:
        super().__init__(menu)
        self._menu = menu
        self._button = button
        self._match_width = match_width

    def _set_open(self, is_open: bool) -> None:
        self._button.setProperty("menuOpen", is_open)
        self._button.style().unpolish(self._button)
        self._button.style().polish(self._button)

    def _grow_to_button(self) -> None:
        if sip.isdeleted(self._menu) or sip.isdeleted(self._button):
            return
        width = self._button.width()
        if self._menu.isVisible() and self._menu.width() < width:
            self._menu.setMinimumWidth(width)
            self._menu.resize(width, self._menu.height())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._menu:
            if event.type() == QEvent.Type.Show:
                button = self._button
                self._set_open(True)
                if self._match_width:
                    self._menu.setFixedWidth(button.width())
                else:
                    self._menu.setMinimumWidth(button.width())
                    # Qt5 has sized the window by the time it shows, and a new
                    # minimum alone does not grow it.
                    self._grow_to_button()
                # Hang from the edge nearer the outside of the window: a menu
                # wider than a button at the right edge must grow leftwards,
                # or it runs off the dialog. Right to left mirrors this.
                window = button.window()
                centre = button.mapTo(window, QPoint(button.width() // 2, 0)).x()
                from_right = centre > window.width() // 2
                if button.layoutDirection() == Qt.LayoutDirection.RightToLeft:
                    from_right = not from_right
                corner = QPoint(
                    button.width() if from_right else 0, button.height() - 1
                )
                anchor = button.mapToGlobal(corner)
                if from_right:
                    anchor.setX(anchor.x() - self._menu.width())
                self._menu.move(anchor)
            elif event.type() == QEvent.Type.Hide:
                self._set_open(False)
        elif watched is self._button and event.type() == QEvent.Type.Resize:
            # A button laid out after its menu opened - or restyled wider for
            # its open state, as Qt5 does - takes the open menu with it.
            self._grow_to_button()
        return False


def _line_pixmap(name: str, colour: str, scale: float) -> QPixmap:
    size = round(16 * scale)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 16 16" fill="none" stroke="{colour}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f"{LINE_ICONS[name].format(c=colour)}</svg>"
    )
    pixmap = QPixmap()
    pixmap.loadFromData(QByteArray(svg.encode()), "SVG")
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def qgis_icon(name: str) -> QIcon:
    """A QGIS theme icon, or an empty one if this build has not got it.

    Icon names are resource paths compiled into QGIS, not files on disk, and
    they come and go between releases. `getThemeIcon` returns a null `QIcon` for
    a name that has gone rather than raising, so a menu built against a newer
    QGIS still opens on an older one - it just loses a picture.

    Wrapped rather than called inline so that the failure is stated once, here,
    instead of being invisible at every call site.
    """
    from qgis.core import QgsApplication

    with contextlib.suppress(Exception):
        icon: QIcon = QgsApplication.getThemeIcon(f"/{name}")
        return icon
    return QIcon()


def line_icon(name: str, colour: str, disabled: str) -> QIcon:
    """One of `LINE_ICONS`, sharp at 1x and 2x, greyed when disabled."""
    icon = QIcon()
    for scale in (1.0, 2.0):
        icon.addPixmap(_line_pixmap(name, colour, scale), QIcon.Mode.Normal)
        icon.addPixmap(_line_pixmap(name, disabled, scale), QIcon.Mode.Disabled)
    return icon


def paint_line_icons(root: QWidget, palette: Palette) -> None:
    """Colour every button that names a `lineIcon` for the current theme.

    The live switch's dot is red while on, like a recording light, and a
    muted ring while off. Everything else matches its button's text.
    """
    for button in root.findChildren(QToolButton):
        name = button.property("lineIcon")
        if not name:
            continue
        if name == "live":
            colour = palette.live
        elif name == "live-off":
            colour = palette.muted
        elif button.property("accent"):
            colour = palette.on_accent
        else:
            colour = palette.ink
        button.setIcon(line_icon(name, colour, palette.quiet))


def attach_menu(menu: QMenu, button: QWidget) -> None:
    """Make `menu` open as part of `button`. Safe to call more than once."""
    if menu.property("attached"):
        return
    menu.setProperty("attached", True)
    menu.setProperty("anchorRole", button.property("role") or "")
    # Rounded corners on a top-level menu need a see-through window behind
    # them, or the corners fill with black.
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    attacher = _MenuAttacher(menu, button)
    menu.installEventFilter(attacher)
    button.installEventFilter(attacher)
    button.setProperty("menuOpen", False)


def attach_list(combo: QComboBox) -> None:
    """Pin a combo box's drop-down list under the box, at the box's width."""
    popup = combo.view().window()
    if popup is combo.window() or popup.property("attached"):
        return
    popup.setProperty("attached", True)
    popup.installEventFilter(_MenuAttacher(popup, combo, match_width=True))


def attach_menus(dialog: QWidget) -> None:
    """Attach every button menu and drop-down list in `dialog`."""
    for combo in dialog.findChildren(QComboBox):
        attach_list(combo)
    for button in dialog.findChildren(QToolButton):
        if button.menu() is not None:
            attach_menu(button.menu(), button)
        for menu in button.findChildren(
            QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly
        ):
            attach_menu(menu, button)


def recolour_document(document: QTextDocument, palette: Palette) -> None:
    """Give a rendered Markdown document the theme's links and table rules.

    `setMarkdown` writes the application's link colour into every anchor as a
    fixed character format at import time, so the palette set on the dialog
    afterwards never reaches it - the Help tab kept QGIS blue on plum paper.
    Tables get the same treatment: their border colour is baked in too.
    """
    link = QTextCharFormat()
    link.setForeground(QBrush(QColor(palette.link)))
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.charFormat().isAnchor():
                cursor = QTextCursor(document)
                cursor.setPosition(fragment.position())
                cursor.setPosition(
                    fragment.position() + fragment.length(),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                cursor.mergeCharFormat(link)
            it += 1
        block = block.next()

    for frame in document.rootFrame().childFrames():
        if isinstance(frame, QTextTable):
            table_format = frame.format()
            table_format.setBorderBrush(QBrush(QColor(palette.rule)))
            table_format.setBorder(1)
            table_format.setCellPadding(5)
            frame.setFormat(table_format)


def stylesheet(p: Palette, object_name: str, family: str | None = None) -> str:
    """The dialog's stylesheet, every rule anchored on `#object_name`.

    `family` is named here as well as set on the dialog: a widget whose rule
    sets `font-weight` gets its font rebuilt on every repolish, from the
    application font rather than the dialog's, so without it a button
    reverted to the system font - and changed width - the first time it
    opened a menu.
    """
    d = f"#{object_name}"
    font_family = f'font-family: "{family}";' if family else ""
    return f"""
{d} {{
    background: {p.canvas};
    color: {p.ink};
}}
{d} QWidget {{
    color: {p.ink};
    {font_family}
}}
{d} QScrollArea, {d} QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
{d} QLabel {{
    background: transparent;
}}
/* A message box parented to the dialog takes the ink above, so it needs the
   matching background - left alone it kept the light platform one, and dark
   mode drew light text on it. */
{d} QDialog {{
    background: {p.paper};
}}

/* ---- Tabs: text, with a bar under the open one ----------------------- */
{d} QTabWidget::pane {{
    border: none;
    border-top: 1px solid {p.rule};
    border-bottom: 1px solid {p.rule};
    top: -1px;
}}
{d} QTabBar {{
    background: transparent;
}}
{d} QTabBar::tab {{
    background: transparent;
    color: {p.muted};
    border: none;
    border-bottom: 3px solid transparent;
    padding: 8px 12px 7px 12px;
    margin-right: 2px;
}}
{d} QTabBar::tab:hover {{
    color: {p.ink};
}}
{d} QTabBar::tab:selected {{
    color: {p.ink};
    font-weight: 600;
    border-bottom: 3px solid {p.accent};
}}

/* ---- Sections: a card a step lighter than the window, title above ------- */
{d} QGroupBox {{
    background: {p.paper};
    border: 1px solid {p.rule};
    border-radius: 8px;
    margin-top: 28px;
    padding: 0;
    font-weight: 600;
}}
{d} QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    top: 6px;
    padding: 0;
    color: {p.ink};
}}
{d} QGroupBox QWidget {{
    font-weight: 400;
}}

/* ---- Fields --------------------------------------------------------- */
{d} QComboBox {{
    /* Drop the list below the box, as part of it, rather than over it. */
    combobox-popup: 0;
}}
{d} QComboBox:on {{
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
}}
{d} QLineEdit, {d} QComboBox {{
    background: {p.field};
    border: 1px solid {p.rule};
    border-radius: 5px;
    padding: 5px 8px;
    min-height: 18px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
}}
{d} QLineEdit:hover, {d} QComboBox:hover {{
    border-color: {p.muted};
}}
{d} QLineEdit:focus, {d} QComboBox:focus, {d} QComboBox:on {{
    border: 1px solid {p.accent};
}}
{d} QLineEdit:disabled, {d} QComboBox:disabled {{
    color: {p.quiet};
}}
{d} QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
{d} QComboBox::down-arrow {{
    image: url({p.icon("chevron")});
    width: 10px;
    height: 10px;
}}
{d} QComboBoxPrivateContainer {{
    background: {p.paper};
    border: none;
}}
{d} QComboBox QAbstractItemView::item {{
    min-height: 24px;
    padding: 0 6px;
}}
{d} QComboBox QAbstractItemView {{
    background: {p.paper};
    border: 1px solid {p.accent};
    border-top: none;
    padding: 4px;
    outline: none;
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
}}

/* ---- Checkboxes ---------------------------------------------------- */
{d} QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
{d} QCheckBox::indicator, {d} QTreeView::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {p.muted};
    border-radius: 4px;
    background: {p.paper};
}}
{d} QCheckBox::indicator:hover, {d} QTreeView::indicator:hover {{
    border-color: {p.ink};
}}
{d} QCheckBox::indicator:checked, {d} QTreeView::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url({p.icon("check")});
}}
{d} QCheckBox::indicator:disabled, {d} QTreeView::indicator:disabled {{
    border-color: {p.rule};
    background: {p.field};
}}

{d} QRadioButton {{
    spacing: 8px;
    background: transparent;
}}
{d} QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {p.muted};
    border-radius: 8px;
    background: {p.paper};
}}
{d} QRadioButton::indicator:hover {{
    border-color: {p.ink};
}}
/* A thick accent ring reads as the filled dot of a chosen option. */
{d} QRadioButton::indicator:checked {{
    border: 5px solid {p.accent};
    width: 6px;
    height: 6px;
    background: {p.paper};
}}

/* ---- Buttons ------------------------------------------------------- */
{d} QPushButton {{
    background: {p.field};
    color: {p.ink};
    border: 1px solid {p.rule};
    border-radius: 6px;
    padding: 6px 14px;
}}
{d} QPushButton:hover {{
    background: {p.hover};
    border-color: {p.muted};
}}
{d} QPushButton:pressed {{
    background: {p.rule};
}}
{d} QPushButton:disabled {{
    color: {p.quiet};
    background: transparent;
    border-color: {p.rule};
}}
{d} QPushButton:focus, {d} QToolButton:focus {{
    outline: none;
    border-color: {p.accent};
}}

/* The two things a user does to the map: a filled button and an outlined one.
   Gently rounded and no taller than the tab bar, which crops anything taller
   from the top. */
{d} QToolButton[role="primary"], {d} QToolButton[role="secondary"] {{
    border-radius: 6px;
    padding: 3px 30px 3px 14px;
    min-height: 18px;
    font-weight: 600;
}}
{d} QToolButton[role="primary"] {{
    background: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
}}
{d} QToolButton[role="primary"]:hover {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}
{d} QToolButton[role="secondary"] {{
    background: transparent;
    color: {p.ink};
    border: 1px solid {p.muted};
}}
{d} QToolButton[role="secondary"]:hover {{
    background: {p.hover};
    border-color: {p.ink};
}}
{d} QToolButton[role="primary"]:disabled, {d} QToolButton[role="secondary"]:disabled {{
    background: transparent;
    color: {p.quiet};
    border: 1px solid {p.rule};
}}
{d} QToolButton[role="primary"]::menu-button {{
    border: none;
    border-left: 1px solid {p.accent_hover};
    width: 24px;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}
{d} QToolButton[role="primary"]:disabled::menu-button {{
    border-left: 1px solid {p.rule};
}}
{d} QToolButton[role="primary"]::menu-arrow {{
    image: url({p.icon("chevron-on-accent")});
    width: 10px;
    height: 10px;
}}
{d} QToolButton[role="secondary"]::menu-button {{
    border: none;
    border-left: 1px solid {p.rule};
    width: 24px;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}
{d} QToolButton[role="secondary"]::menu-arrow {{
    image: url({p.icon("chevron")});
    width: 10px;
    height: 10px;
}}
/* Export has no menu, so no room is kept for one. */
{d} QToolButton[role="primary"][hasMenu="false"] {{
    padding: 3px 16px;
}}
/* Share opens its menu from anywhere on the button: one chevron, no split. */
{d} QToolButton[role="secondary"]::menu-indicator {{
    image: url({p.icon("chevron")});
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 10px;
    height: 10px;
    right: 10px;
}}

/* The corner: Preview and Share quiet and outlined, Export filled and last -
   separate buttons, because they are separate acts rather than a choice of
   one. 20 + 2 x 3 padding + 2 x 1 border = 28 px, the tab bar's height. */
{d} QToolButton[role="corner"] {{
    background: {p.paper};
    color: {p.ink};
    border: 1px solid {p.rule};
    border-radius: 6px;
    padding: 3px 11px 3px 9px;
    min-height: 20px;
    font-weight: 600;
    margin: 0;
}}
{d} QToolButton[role="corner"]:hover,
{d} QToolButton[role="corner"][menuOpen="true"] {{
    background: {p.hover};
    border-color: {p.muted};
}}
{d} QToolButton[role="corner"][hasMenu="true"] {{
    padding-right: 22px;
}}
{d} QToolButton[role="corner"]::menu-indicator {{
    image: url({p.icon("chevron")});
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 10px;
    height: 10px;
    right: 7px;
}}
/* Preview is one outlined pill in two parts: the button, and the live switch
   where Share has its arrow. The pill draws the outline; the parts are flat
   and only light up under the pointer, each on its own side. 26 px parts in a
   1 px border make the same 28 px as the buttons beside it. */
{d} QFrame[role="cornerGroup"] {{
    background: {p.paper};
    border: 1px solid {p.rule};
    border-radius: 6px;
}}
{d} QFrame[role="cornerGroup"]:hover {{
    border-color: {p.muted};
}}
/* A running live preview: the pill's outline joins the switch's red. */
{d} QFrame[role="cornerGroup"][live="true"] {{
    border-color: {p.live};
}}
{d} QToolButton[role="cornerPart"] {{
    background: transparent;
    color: {p.ink};
    border: none;
    padding: 3px 9px 3px 9px;
    min-height: 20px;
    font-weight: 600;
    margin: 0;
}}
{d} QToolButton[role="cornerPart"][position="left"] {{
    padding-right: 0px;
    border-top-left-radius: 5px;
    border-bottom-left-radius: 5px;
}}
{d} QToolButton[role="cornerPart"][position="right"] {{
    padding: 3px 7px 3px 0px;
    min-width: 10px;
    max-width: 10px;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}}
{d} QToolButton[role="cornerPart"]:hover {{
    background: {p.hover};
}}
{d} QToolButton[role="cornerPart"]:disabled {{
    color: {p.quiet};
}}
{d} QToolButton[role="corner"][accent="true"] {{
    background: {p.accent};
    color: {p.on_accent};
    border-color: {p.accent};
    /* Qt leaves about 6 px of its own after a text-beside-icon label, so a
       smaller right padding is what centres the content. */
    padding-right: 3px;
}}
{d} QToolButton[role="corner"][accent="true"]:hover {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}
{d} QToolButton[role="corner"]:disabled {{
    color: {p.quiet};
    border-color: {p.rule};
}}
{d} QToolButton[role="corner"][accent="true"]:disabled {{
    background: {p.field};
}}

/* Footer chips: outlined, so they read as things to press. */
{d} QPushButton[role="chip"], {d} QToolButton[role="chip"] {{
    background: transparent;
    color: {p.ink};
    border: 1px solid {p.rule};
    /* Qt silently drops a radius over half the height, so the height is
       pinned: 18 + 2 x 4 padding + 2 x 1 border = 28, radius 13 fits. */
    border-radius: 13px;
    padding: 4px 12px;
    min-height: 18px;
}}
{d} QPushButton[role="chip"]:hover, {d} QToolButton[role="chip"]:hover {{
    background: {p.hover};
    border-color: {p.muted};
}}
{d} QToolButton[role="chip"]::menu-indicator {{
    image: url({p.icon("chevron")});
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 10px;
    height: 10px;
    right: 8px;
}}
{d} QToolButton[role="chip"][hasMenu="true"] {{
    padding-right: 26px;
}}

/* Right to left (Arabic): Qt mirrors the layout and subcontrol positions,
   but not stylesheet padding, so the room kept for each chevron is moved
   to the other side by hand. */
{d}[rtl="true"] QToolButton[role="primary"],
{d}[rtl="true"] QToolButton[role="secondary"] {{
    padding: 3px 14px 3px 30px;
}}
{d}[rtl="true"] QToolButton[role="chip"][hasMenu="true"] {{
    padding-right: 12px;
    padding-left: 26px;
}}

/* Links that act inside the dialog. */
{d} QPushButton[role="link"] {{
    background: transparent;
    border: none;
    color: {p.link};
    padding: 2px 4px;
    font-weight: 600;
}}
{d} QPushButton[role="link"]:hover {{
    text-decoration: underline;
}}

/* A one-line notice across the top: the language offer. */
{d} QFrame[role="notice"] {{
    background: {p.accent_soft};
    border: 1px solid {p.accent};
    border-radius: 6px;
}}

/* ---- Tables and the help pane: square, they are for reading ----------- */
{d} QTreeView, {d} QTextBrowser {{
    background: {p.paper};
    alternate-background-color: {p.field};
    border: 1px solid {p.rule};
    border-radius: 0;
    outline: none;
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
}}
{d} QTreeView::item {{
    padding: 3px 2px;
}}
{d} QTreeView::item:hover {{
    background: {p.hover};
}}
{d} QTreeView::item:selected {{
    background: {p.accent_soft};
    color: {p.ink};
}}
{d} QHeaderView::section {{
    background: {p.field};
    color: {p.muted};
    border: none;
    border-bottom: 1px solid {p.rule};
    border-right: 1px solid {p.rule};
    padding: 5px 6px;
    font-weight: 600;
}}

/* ---- Progress ------------------------------------------------------ */
{d} QProgressBar {{
    background: {p.field};
    border: 1px solid {p.rule};
    border-radius: 5px;
    text-align: center;
    color: {p.ink};
    min-height: 18px;
}}
{d} QProgressBar::chunk {{
    background: {p.accent};
    border-radius: 4px;
}}

/* ---- Scrollbars: slim, out of the way --------------------------------- */
{d} QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
{d} QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
{d} QScrollBar::handle {{
    background: {p.rule};
    border-radius: 4px;
    margin: 2px;
}}
{d} QScrollBar::handle:hover {{
    background: {p.muted};
}}
{d} QScrollBar::handle:vertical {{
    min-height: 28px;
}}
{d} QScrollBar::handle:horizontal {{
    min-width: 28px;
}}
{d} QScrollBar::add-line, {d} QScrollBar::sub-line,
{d} QScrollBar::add-page, {d} QScrollBar::sub-page {{
    background: none;
    border: none;
    width: 0;
    height: 0;
}}

/* ---- Menus --------------------------------------------------------- */
{d} QMenu {{
    background: {p.paper};
    border: 1px solid {p.rule};
    padding: 4px;
}}
/* A button's own menu: continues the button's outline, rounded only below. */
{d} QMenu[attached="true"] {{
    border: 1px solid {p.muted};
    border-top-left-radius: 0;
    border-top-right-radius: 0;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}}
{d} QMenu[anchorRole="primary"] {{
    border-color: {p.accent};
}}
{d} QToolButton[menuOpen="true"] {{
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
}}
{d} QToolButton[role="secondary"][menuOpen="true"],
{d} QToolButton[role="chip"][menuOpen="true"] {{
    background: {p.hover};
    border-color: {p.muted};
}}
{d} QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 4px;
    background: transparent;
}}
{d} QMenu::item:selected {{
    background: {p.accent_soft};
    color: {p.ink};
}}
{d} QMenu::item:disabled {{
    color: {p.quiet};
}}
{d} QMenu::separator {{
    height: 1px;
    background: {p.rule};
    margin: 4px 6px;
}}
{d} QMenu::section {{
    color: {p.muted};
    font-weight: 600;
    padding: 6px 12px 2px 12px;
}}
"""
