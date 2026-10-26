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
    "web": (
        '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c1.7 1.7 2.5 3.7 2.5 6'
        'S9.7 12.3 8 14M8 2C6.3 3.7 5.5 5.7 5.5 8s.8 4.3 2.5 6"/>'
    ),
    "page": (
        '<rect x="3.5" y="1.8" width="9" height="12.4" rx="1"/>'
        '<path d="M5.6 4.6h4.8M5.6 7h4.8v4.4H5.6z"/>'
    ),
    "contrast": (
        '<circle cx="8" cy="8" r="6"/><path d="M8 2a6 6 0 0 1 0 12z" fill="{c}"/>'
    ),
    "account": (
        '<circle cx="8" cy="5.5" r="2.5"/>'
        '<path d="M3 13.5c0-2.8 2.2-4.5 5-4.5s5 1.7 5 4.5"/>'
    ),
    "info": (
        '<circle cx="8" cy="8" r="6"/>'
        '<path d="M8 7.2v3.8"/><circle cx="8" cy="4.9" r="0.4" fill="{c}"/>'
    ),
    "settings": (
        '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>'
        '<circle cx="10" cy="4.5" r="1.6" fill="{c}"/>'
        '<circle cx="5.5" cy="8" r="1.6" fill="{c}"/>'
        '<circle cx="9" cy="11.5" r="1.6" fill="{c}"/>'
    ),
    "help": (
        '<circle cx="8" cy="8" r="6"/>'
        '<path d="M6.2 6.3a1.9 1.9 0 0 1 3.7.5c0 1.3-1.9 1.6-1.9 2.9"/>'
        '<circle cx="8" cy="11.4" r="0.4" fill="{c}"/>'
    ),
    "back": '<path d="M9.8 3.5 5.3 8l4.5 4.5"/>',
    # The page preview's bar when it has no room for words.
    "select": '<path d="M4 2.5v10l2.6-2.4 1.8 3.4 1.6-.8-1.8-3.3 3.4-.2z"/>',
    "pan": (
        '<path d="M8 1.8v12.4M1.8 8h12.4M6.3 3.5 8 1.8l1.7 1.7M6.3 12.5 8 '
        '14.2l1.7-1.7M3.5 6.3 1.8 8l1.7 1.7M12.5 6.3 14.2 8l-1.7 1.7"/>'
    ),
    "fit": (
        '<path d="M2 5.5V2h3.5M10.5 2H14v3.5M14 10.5V14h-3.5M5.5 14H2v-3.5"/>'
        '<rect x="5" y="5" width="6" height="6" rx="0.8"/>'
    ),
    # Adding to and editing items on the page preview.
    "add": '<path d="M8 3v10M3 8h10"/>',
    "trash": (
        '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M7 7v4M9 7v4"/>'
    ),
    "align-left": '<path d="M2.5 3.5h11M2.5 6.5h7M2.5 9.5h11M2.5 12.5h7"/>',
    "align-center": '<path d="M2.5 3.5h11M4.5 6.5h7M2.5 9.5h11M4.5 12.5h7"/>',
    "align-right": '<path d="M2.5 3.5h11M6.5 6.5h7M2.5 9.5h11M6.5 12.5h7"/>',
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
    # Lifted together so help text stands out from the dark plum; quiet
    # stays a step below muted so disabled text still reads as disabled.
    muted="#c4bcc6",
    quiet="#a69ba8",
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
    sheet = stylesheet(palette, dialog.objectName(), family)
    dialog.setStyleSheet(sheet)

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
    _dress_child_dialogs(dialog, palette, qpalette, sheet)
    return palette


class _ChildDialogDresser(QObject):
    """Gives every dialog opened from ours - message boxes above all, and the
    Colour vision window - the theme on the box itself, again on every switch.

    The scoped `QDialog` rule reaches them through the cascade, and Qt5 lets
    that go stale: after switching from Follow QGIS to Dark, every message box
    opened kept the light background under the new light text. Set on the box
    as it appears, the background cannot go stale.
    """

    def __init__(
        self, dialog: QWidget, paper: str, qpalette: QPalette, sheet: str
    ) -> None:
        super().__init__(dialog)
        self.paper = paper
        self.qpalette = qpalette
        self.sheet = sheet

    def dress(self, child: QDialog) -> None:
        # The whole theme, on the window itself: Qt 5 never restyles a child
        # window's widgets from its parent's new stylesheet, so an open
        # Colour vision table kept the old theme's background under the new
        # theme's text - light on white after a switch to Dark. Its rules are
        # scoped to the main dialog, which this window sits under.
        child.setStyleSheet(f"{self.sheet}\nQDialog {{ background: {self.paper}; }}")
        # The palette too: Qt does not hand a palette on to a child window.
        child.setPalette(self.qpalette)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ChildPolished:
            child = event.child()
            if isinstance(child, QDialog):
                self.dress(child)
        return False


def _dress_child_dialogs(
    dialog: QWidget, palette: Palette, qpalette: QPalette, sheet: str
) -> None:
    dresser = dialog.findChild(_ChildDialogDresser)
    if dresser is None:
        dresser = _ChildDialogDresser(dialog, palette.paper, qpalette, sheet)
        dialog.installEventFilter(dresser)
    dresser.paper = palette.paper
    dresser.qpalette = qpalette
    dresser.sheet = sheet
    # A dialog already open (Colour vision is kept between openings) takes
    # the new theme now, not only dialogs opened after the switch.
    for child in dialog.findChildren(QDialog):
        dresser.dress(child)


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


# Every attacher, until Qt destroys it. Its parent is often a widget Qt made
# (a combo box's list window), whose Python wrapper Python may collect at any
# time - taking the attacher's Python half with it while Qt still calls its
# `eventFilter`, which crashed the interpreter.
_ATTACHERS: dict[int, _MenuAttacher] = {}


def _keep(attacher: _MenuAttacher) -> _MenuAttacher:
    key = id(attacher)
    _ATTACHERS[key] = attacher
    attacher.destroyed.connect(lambda *_args, key=key: _ATTACHERS.pop(key, None))
    return attacher


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
    attacher = _keep(_MenuAttacher(menu, button))
    menu.installEventFilter(attacher)
    button.installEventFilter(attacher)
    button.setProperty("menuOpen", False)


def attach_list(combo: QComboBox) -> None:
    """Pin a combo box's drop-down list under the box, at the box's width."""
    popup = combo.view().window()
    if popup is combo.window() or popup.property("attached"):
        return
    popup.setProperty("attached", True)
    popup.installEventFilter(_keep(_MenuAttacher(popup, combo, match_width=True)))


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
    base = f"""
{d} {{
    background: {p.canvas};
    color: {p.ink};
}}
/* Every plain container is see-through, so the card or window behind it shows
   instead of whatever the host QGIS stylesheet paints on a bare QWidget (white
   under a dark theme's light ink, near-black under a light one's dark ink).
   Controls that need a surface of their own are given one below. */
{d} QWidget {{
    background: transparent;
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
{d} QLineEdit, {d} QComboBox, {d} QAbstractSpinBox,
{d} QPlainTextEdit, {d} QTextEdit {{
    background: {p.field};
    border: 1px solid {p.rule};
    border-radius: 5px;
    padding: 5px 8px;
    min-height: 18px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
}}
{d} QLineEdit:hover, {d} QComboBox:hover, {d} QAbstractSpinBox:hover {{
    border-color: {p.muted};
}}
/* Open, a box keeps the thin brass outline its list continues, focused or
   not; the keyboard focus ring itself is in the focus block at the end. */
{d} QComboBox:on, {d} QComboBox:on:focus {{
    border: 1px solid {p.accent};
    padding: 5px 8px;
}}
{d} QLineEdit:disabled, {d} QComboBox:disabled, {d} QAbstractSpinBox:disabled,
{d} QPlainTextEdit:disabled, {d} QTextEdit:disabled {{
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
/* A spin box's two steppers, restyled with the box: once a box is drawn by the
   stylesheet its native arrows are gone. */
{d} QAbstractSpinBox::up-button, {d} QAbstractSpinBox::down-button {{
    subcontrol-origin: border;
    width: 20px;
    border: none;
    background: transparent;
}}
{d} QAbstractSpinBox::up-button {{
    subcontrol-position: top right;
}}
{d} QAbstractSpinBox::down-button {{
    subcontrol-position: bottom right;
}}
{d} QAbstractSpinBox::up-arrow {{
    image: url({p.icon("chevron-up")});
    width: 10px;
    height: 10px;
}}
{d} QAbstractSpinBox::down-arrow {{
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
    /* Room kept for the focus ring the keyboard's row wears. */
    border: 2px solid transparent;
    border-radius: 4px;
    padding: 0 4px;
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
    /* Room for the focus ring, which goes round box and label together: a
       ring on the box alone vanishes on a ticked lime box in the dark. */
    border: 2px solid transparent;
    border-radius: 4px;
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
    border: 2px solid transparent;
    border-radius: 4px;
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
    border-color: {p.link};
}}
/* A bare icon button (the column help "i"): room for the focus ring. */
{d} QToolButton[autoRaise="true"] {{
    background: transparent;
    border: 2px solid transparent;
    border-radius: 4px;
    padding: 0;
}}
{d} QToolButton[autoRaise="true"]:hover {{
    background: {p.hover};
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
{d} QToolButton[role="primary"][hasMenu="false"],
{d} QToolButton[role="secondary"][hasMenu="false"] {{
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
{d} QToolButton[role="corner"][iconOnly="true"] {{
    padding: 3px 6px;
}}
{d} QToolButton[role="corner"][iconOnly="true"]:checked {{
    background: {p.accent_soft};
    border-color: {p.muted};
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
    /* Transparent until focused: the 2 px is the focus ring's room, taken
       out of the padding so the parts keep their size. */
    border: 2px solid transparent;
    padding: 1px 7px 1px 7px;
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

/* Two checkable buttons in one outlined pill (the hand-off dialog's choice of
   where to paste), the same shape as the Preview pill. Modest on purpose - it
   borrows the corner's quiet styling rather than inventing a louder one. */
{d} QFrame[role="outputGroup"] {{
    background: {p.paper};
    border: 1px solid {p.rule};
    border-radius: 6px;
}}
{d} QToolButton[role="outputPart"] {{
    background: transparent;
    color: {p.ink};
    border: 2px solid transparent;
    padding: 2px 10px;
    min-height: 20px;
    font-weight: 600;
    margin: 0;
}}
{d} QToolButton[role="outputPart"][position="left"] {{
    border-top-left-radius: 5px;
    border-bottom-left-radius: 5px;
}}
{d} QToolButton[role="outputPart"][position="right"] {{
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}}
{d} QToolButton[role="outputPart"]:hover {{
    background: {p.hover};
}}
{d} QToolButton[role="outputPart"]:checked {{
    background: {p.accent};
    color: {p.on_accent};
}}

/* The dialog's title, `Web map ▾`: what it makes, and the menu that changes
   it. Flat until pointed at, like a heading that happens to open. */
{d} QToolButton[role="outputTitle"] {{
    /* Toolbar-sized and unfilled, so it does not outweigh the map; the
       chevron and the hover fill say it opens. The border stays clear for
       the focus ring. */
    background: transparent;
    color: {p.ink};
    border: 2px solid transparent;
    border-radius: 6px;
    padding: 2px 21px 2px 4px;
    min-height: 18px;
    font-size: 13px;
    font-weight: 600;
}}
{d} QToolButton[role="outputTitle"]:hover,
{d} QToolButton[role="outputTitle"][menuOpen="true"] {{
    background: {p.hover};
}}
{d} QToolButton[role="outputTitle"]::menu-indicator {{
    image: url({p.icon("chevron")});
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 10px;
    height: 10px;
    right: 6px;
}}
{d} QLabel[role="betaChip"] {{
    /* A quiet tag, not a badge: muted words in a hairline outline. */
    color: {p.muted};
    background: transparent;
    border: 1px solid {p.rule};
    border-radius: 4px;
    padding: 0px 5px;
    font-size: 11px;
}}
{d} QFrame[role="outputRow"] {{
    background: transparent;
    border: 2px solid transparent;
    border-radius: 5px;
    min-width: 260px;
}}
{d} QFrame[role="outputRow"]:hover, {d} QFrame[role="outputRow"]:focus {{
    background: {p.hover};
}}
{d} QToolButton[role="outputRowIcon"] {{
    background: transparent;
    border: none;
    padding: 0;
}}
{d} QLabel[role="outputRowName"] {{
    color: {p.ink};
    font-weight: 600;
}}
{d} QLabel[role="outputRowDescription"] {{
    color: {p.muted};
}}
{d} QLabel[role="outputRowTick"] {{
    color: {p.ink};
    font-weight: 600;
    min-width: 12px;
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
{d}[rtl="true"] QToolButton[role="primary"][hasMenu="false"],
{d}[rtl="true"] QToolButton[role="secondary"][hasMenu="false"] {{
    padding: 3px 16px;
}}
/* Nor the menu part's divider and rounded corners, which belong on its
   outer edge - the left one, right to left. */
{d}[rtl="true"] QToolButton[role="primary"]::menu-button,
{d}[rtl="true"] QToolButton[role="secondary"]::menu-button {{
    border-left: none;
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
    border-top-left-radius: 6px;
    border-bottom-left-radius: 6px;
}}
{d}[rtl="true"] QToolButton[role="primary"]::menu-button {{
    border-right: 1px solid {p.accent_hover};
}}
{d}[rtl="true"] QToolButton[role="primary"]:disabled::menu-button,
{d}[rtl="true"] QToolButton[role="secondary"]::menu-button {{
    border-right: 1px solid {p.rule};
}}
/* A chevron's position and its `right:` offset are mirrored by Qt, so only
   the padding that keeps room for it needs moving. */
{d}[rtl="true"] QToolButton[role="outputTitle"] {{
    padding: 2px 5px 2px 21px;
}}
/* A corner button's chevron room moves to the left. Its other lopsided
   padding stays: it offsets the slack Qt leaves on the right of a
   text-beside-icon label, which Qt does not mirror. */
{d}[rtl="true"] QToolButton[role="corner"][hasMenu="true"] {{
    padding-right: 9px;
    padding-left: 22px;
}}
/* Preview's pill: its first part sits on the right, right to left, so the
   rounded corners and the shared middle edge swap sides. */
{d}[rtl="true"] QToolButton[role="cornerPart"][position="left"] {{
    padding: 1px 7px 1px 0px;
    border-top-left-radius: 0;
    border-bottom-left-radius: 0;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}}
{d}[rtl="true"] QToolButton[role="cornerPart"][position="right"] {{
    padding: 1px 2px 1px 9px;
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
    border-top-left-radius: 5px;
    border-bottom-left-radius: 5px;
}}
{d}[rtl="true"] QToolButton[role="outputPart"][position="left"] {{
    border-top-left-radius: 0;
    border-bottom-left-radius: 0;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}}
{d}[rtl="true"] QToolButton[role="outputPart"][position="right"] {{
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
    border-top-left-radius: 5px;
    border-bottom-left-radius: 5px;
}}
/* A section header right to left: Qt mirrors the arrow but not the title,
   which it keeps left-aligned, so the two were drawn over each other and
   `text-align` does not move it. Laid out left to right, arrow and title
   sit together, readable, at the far end of the header. */
{d}[rtl="true"] QToolButton[role="sectionHeader"] {{
    qproperty-layoutDirection: LeftToRight;
}}
{d}[rtl="true"] QTabBar::tab {{
    margin-right: 0;
    margin-left: 2px;
}}
{d}[rtl="true"] QHeaderView::section {{
    border-right: none;
    border-left: 1px solid {p.rule};
}}
{d}[rtl="true"] QMenu::item {{
    padding: 4px 10px 4px 20px;
}}
{d}[rtl="true"] QMenu::indicator:non-exclusive {{
    margin-left: 0px;
    margin-right: 6px;
}}

/* Links that act inside the dialog. */
{d} QPushButton[role="link"] {{
    background: transparent;
    /* Room for the focus ring, out of the padding. */
    border: 2px solid transparent;
    border-radius: 4px;
    color: {p.link};
    padding: 0px 2px;
    font-weight: 600;
}}
{d} QPushButton[role="link"]:hover {{
    text-decoration: underline;
}}

/* The Cartograph tab: the page preview, and the panel beside it. */
{d} QgsLayoutView[role="layoutPreview"] {{
    background: {p.canvas};
    border: 1px solid {p.rule};
    border-radius: 6px;
}}
/* The preview takes the keyboard (arrows pan it); a 1 px ring, because a
   wider border would shrink the page view and refit it on every focus. */
{d} QgsLayoutView[role="layoutPreview"]:focus {{
    border-color: {p.link};
}}
{d} QFrame[role="layoutEmpty"] {{
    background: {p.paper};
    border: 1px dashed {p.rule};
    border-radius: 8px;
}}
{d} QLabel[role="layoutEmptyHeading"] {{
    color: {p.ink};
    font-size: 15px;
    font-weight: 650;
}}
{d} QLabel[role="layoutEmptyNote"], {d} QLabel[role="hint"],
{d} QLabel[role="saveLocation"] {{
    color: {p.muted};
}}
{d} QLabel[role="panelHeading"] {{
    color: {p.ink};
    font-weight: 600;
}}

/* The cartograph panel's sections: a quiet header line that opens. */
{d} QFrame[role="panelSection"] {{
    background: transparent;
    border: none;
    border-top: 1px solid {p.rule};
}}
{d} QToolButton[role="sectionHeader"] {{
    background: transparent;
    /* Room for the focus ring, out of the padding. */
    border: 2px solid transparent;
    border-radius: 4px;
    color: {p.ink};
    font-weight: 600;
    padding: 6px 0px;
    text-align: left;
}}
{d} QToolButton[role="sectionHeader"]:hover {{
    color: {p.link};
}}
{d} QLabel[role="sectionSummary"] {{
    color: {p.muted};
}}

/* The template picker's strip of names: pills that jump to a design. */
{d} QListWidget[role="templateChips"] {{
    background: transparent;
    border: none;
    outline: none;
}}
{d} QListWidget[role="templateChips"]::item {{
    background: {p.field};
    color: {p.ink};
    border: 2px solid transparent;
    border-radius: 13px;
    padding: 3px 11px;
}}
{d} QListWidget[role="templateChips"]::item:hover {{
    background: {p.hover};
}}
{d} QListWidget[role="templateChips"]::item:selected {{
    background: {p.accent_soft};
    color: {p.ink};
    border-color: {p.accent};
}}

/* Settings and Help: a plain back arrow, then the page's title. */
{d} QToolButton[role="asideBack"] {{
    background: transparent;
    border: 2px solid transparent;
    border-radius: 6px;
    padding: 4px;
}}
{d} QToolButton[role="asideBack"]:hover {{
    background: {p.hover};
}}
{d} QToolButton[role="asideBack"]:focus {{
    border-color: {p.link};
}}
{d} QLabel[role="asideHeading"] {{
    color: {p.ink};
    font-size: 16px;
    font-weight: 650;
}}

/* The page's caption, and the zoom bar's small buttons. */
{d} QLabel[role="pageCaption"] {{
    color: {p.muted};
}}
{d} QToolButton[role="secondary"][hasMenu="false"][zoom="true"] {{
    min-width: 0;
    padding: 2px 10px;
}}
/* A bar button that opens a menu (Add) shows no arrow: the label says it. */
{d} QToolButton[zoom="true"]::menu-indicator {{
    image: none;
    width: 0;
}}
/* Focused, the ring's extra pixel comes out of the padding, so the button
   keeps its size and its label is never cut short. */
{d} QToolButton[role="secondary"][hasMenu="false"][zoom="true"]:focus {{
    padding: 1px 9px;
}}
/* The bar short of room: pictures with tooltips instead of cut-off words. */
{d} QToolButton[role="secondary"][hasMenu="false"][zoom="true"][tight="true"] {{
    padding: 2px 7px;
}}
{d} QToolButton[role="secondary"][hasMenu="false"][zoom="true"][tight="true"]:focus {{
    padding: 1px 6px;
}}
/* The zoom bar's Select / Pan switch: the mode in use is the filled one. */
{d} QToolButton[role="secondary"][hasMenu="false"][zoom="true"]:checked {{
    background: {p.accent_soft};
    border-color: {p.muted};
}}
/* The mode of editing the view inside a map: the filled button says it is
   on (the banner over the page takes the palette's highlight). */
{d} QPushButton[role="secondary"]:checked {{
    background: {p.accent};
    color: {p.on_accent};
    border-color: {p.accent_hover};
}}
{d} QLabel[role="saveName"] {{
    color: {p.ink};
    font-weight: 600;
}}

/* Checks as lines, in the cartograph's narrow panel. */
{d} QWidget[role="checksList"] {{
    background: {p.paper};
}}
{d} QWidget[role="checksGroup"] {{
    background: transparent;
    border: none;
    border-top: 1px solid {p.rule};
}}
{d} QFrame[role="checkRow"] {{
    background: transparent;
    border: 2px solid transparent;
    border-radius: 5px;
}}
{d} QFrame[role="checkRow"]:hover {{
    background: {p.hover};
}}
{d} QFrame[role="checkRow"][expanded="true"] {{
    background: {p.accent_soft};
}}
{d} QLabel[role="checkVerdict"][tone="error"],
{d} QLabel[role="checkVerdict"][tone="warning"] {{
    color: {p.warn};
}}
{d} QLabel[role="checkVerdict"][tone="info"] {{
    color: {p.ink};
}}
{d} QLabel[role="checkVerdict"][tone="quiet"] {{
    color: {p.muted};
}}
{d} QPushButton[role="checksMore"] {{
    background: transparent;
    border: none;
    color: {p.link};
    padding: 4px 0;
    text-align: left;
}}

/* The selected page item's controls: the body of the panel's Edit tab. */
{d} QFrame[role="itemControls"] {{
    background: transparent;
    border: none;
}}
{d} QFrame[role="itemControls"] QLabel {{
    border: none;
    background: transparent;
}}

/* Map frames: a quiet entry per map item, ruled apart, the frame selected on
   the page marked by a bar at its left edge. */
{d} QFrame[role="frameCard"] {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {p.rule};
    border-left: 3px solid transparent;
}}
{d} QFrame[role="frameCard"][selected="true"] {{
    border-left: 3px solid {p.link};
}}
{d} QLabel[role="frameName"] {{
    color: {p.ink};
    font-weight: 600;
}}
{d} QFrame[role="frameCard"] QLabel {{
    border: none;
}}
{d} QToolButton[frameActions="true"]::menu-indicator {{
    image: none;
    width: 0;
}}
{d} QToolButton[role="frameIssues"] {{
    background: transparent;
    border: none;
    color: {p.warn};
    padding: 0 2px;
}}
{d} QToolButton[role="frameIssues"]:hover {{
    text-decoration: underline;
}}

/* The template picker: one design per row, the chosen one ringed. */
{d} QListWidget[role="templateList"] {{
    background: {p.canvas};
    border: none;
    outline: none;
}}
{d} QListWidget[role="templateList"]::item {{
    background: {p.paper};
    border: 1px solid {p.rule};
    border-radius: 8px;
}}
{d} QListWidget[role="templateList"]::item:hover {{
    background: {p.hover};
}}
{d} QListWidget[role="templateList"]::item:selected {{
    background: {p.accent_soft};
    border: 2px solid {p.accent};
}}
{d} QFrame[role="templateRow"] {{
    background: transparent;
}}
{d} QLabel[role="templateName"] {{
    color: {p.ink};
    font-size: 15px;
    font-weight: 650;
}}
{d} QLabel[role="templateDescription"] {{
    color: {p.muted};
}}
{d} QLabel[role="templateTick"] {{
    color: {p.ink};
    font-size: 15px;
    font-weight: 650;
}}
{d} QLabel[role="templateIncludes"] {{
    color: {p.ink};
    font-size: 12px;
}}
/* What the design would show with more content: quieter than what the
   page shows now. */
{d} QLabel[role="templateSupports"] {{
    color: {p.muted};
    font-size: 12px;
}}
/* A legend that left entries out to stay readable: a warning, not an
   error. */
{d} QLabel[role="templateCrowded"] {{
    color: {p.warn};
    font-size: 12px;
}}
/* A card under the pointer, unless it is the chosen one; inset so the
   card's own outline and the focus ring stay visible. */
{d} QFrame[role="templateRow"][chosen="false"]:hover {{
    background: {p.hover};
    border-radius: 7px;
    margin: 2px;
}}
/* The page box: a quiet field while the page is drawn, then the page. */
{d} QFrame[role="templatePreview"] {{
    background: transparent;
}}
{d} QLabel[role="templatePage"] {{
    background: {p.field};
    color: {p.muted};
    border-radius: 4px;
    padding: 6px;
}}
{d} QLabel[role="templatePage"][drawn="true"] {{
    background: transparent;
    padding: 0px;
}}
{d} QFrame[role="templateFailure"] {{
    background: {p.field};
    border: 1px dashed {p.warn};
    border-radius: 4px;
}}
{d} QLabel[role="templateFailureText"] {{
    color: {p.ink};
}}
/* The chosen design's key figures, under the list: a card like the rows. */
{d} QFrame[role="keyFigures"] {{
    background: {p.paper};
    border: 1px solid {p.rule};
    border-radius: 8px;
}}

/* The text size sliders: a transparent 2 px border that the keyboard's
   focus ring colours, so nothing moves when focus arrives. */
{d} QSlider {{
    border: 2px solid transparent;
    border-radius: 4px;
    min-height: 22px;
}}
{d} QSlider:focus {{
    border-color: {p.link};
}}

/* A one-line notice across the top: the language offer. */
{d} QFrame[role="notice"] {{
    background: {p.accent_soft};
    border: 1px solid {p.accent};
    border-radius: 6px;
}}

/* ---- Tables and the help pane: square, they are for reading ----------- */
{d} QTreeView, {d} QTableView, {d} QTextBrowser {{
    /* Set here, not left to the palette: a table in a window of its own
       (Colour vision) builds its palette from QGIS's, not the dialog's. */
    background: {p.paper};
    alternate-background-color: {p.field};
    gridline-color: {p.rule};
    border: 1px solid {p.rule};
    border-radius: 0;
    outline: none;
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
}}
/* Ordinary lists (the hand-off's file list): a card of their own. The template
   picker's lists, set apart by role, and a combo box's drop-down override. */
{d} QListWidget, {d} QListView {{
    background: {p.paper};
    border: 1px solid {p.rule};
    border-radius: 0;
    outline: none;
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
}}
{d} QListWidget::item:selected, {d} QListView::item:selected {{
    background: {p.accent_soft};
    color: {p.ink};
}}
{d} QTreeView::item {{
    /* Room for the focus ring on the keyboard's row, out of the padding. */
    border: 2px solid transparent;
    padding: 1px 0px;
}}
{d} QTreeView::item:hover {{
    background: {p.hover};
}}
{d} QTreeView::item:selected, {d} QTableView::item:selected {{
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
    /* Room for the ring the keyboard's row wears, out of the padding. */
    border: 2px solid transparent;
    padding: 4px 20px 4px 10px;
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
/* A menu's tick boxes (Checks > Filter) are the dialog's own checkboxes:
   Qt's bare tick looked foreign and showed nothing at all when off. */
{d} QMenu::indicator:non-exclusive {{
    width: 14px;
    height: 14px;
    margin-left: 6px;
    border: 1px solid {p.muted};
    border-radius: 4px;
    background: {p.paper};
}}
{d} QMenu::indicator:non-exclusive:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url({p.icon("check")});
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
    return base + focus_rules(p, object_name)


# Keyboard focus: one 2 px ring in the link colour (or ink round an accent
# fill), on every control the keyboard reaches. Qt stylesheets cannot draw an
# outline outside a widget, so the ring is the widget's own border: a control
# with a 1 px outline gets a 2 px one and gives the extra pixel back from its
# padding, so nothing moves when focus arrives. Each entry is a selector and
# its unfocused padding (top, right, bottom, left) and border width; a
# right-to-left twin is written for every lopsided padding the base rules
# mirror (see `_FOCUS_NOT_MIRRORED`). The entries run
# from plain to specific, the order the base rules cascade in.
FOCUS_WIDTH = 2
_FOCUS_PADDED: tuple[tuple[str, tuple[int, int, int, int], int, bool], ...] = (
    # selector, padding, border width, sits on an accent fill
    ("QLineEdit", (5, 8, 5, 8), 1, False),
    ("QComboBox", (5, 8, 5, 8), 1, False),
    ("QAbstractSpinBox", (5, 8, 5, 8), 1, False),
    ("QPlainTextEdit", (5, 8, 5, 8), 1, False),
    ("QTextEdit", (5, 8, 5, 8), 1, False),
    ("QPushButton", (6, 14, 6, 14), 1, False),
    ('QPushButton[role="chip"]', (4, 12, 4, 12), 1, False),
    ('QPushButton[role="link"]', (0, 2, 0, 2), 2, False),
    ('QToolButton[role="chip"]', (4, 12, 4, 12), 1, False),
    ('QToolButton[role="chip"][hasMenu="true"]', (4, 26, 4, 12), 1, False),
    ('QToolButton[role="secondary"]', (3, 30, 3, 14), 1, False),
    ('QToolButton[role="secondary"][hasMenu="false"]', (3, 16, 3, 16), 1, False),
    ('QToolButton[role="primary"]', (3, 30, 3, 14), 1, True),
    ('QToolButton[role="primary"][hasMenu="false"]', (3, 16, 3, 16), 1, True),
    ('QToolButton[role="corner"]', (3, 11, 3, 9), 1, False),
    ('QToolButton[role="corner"][hasMenu="true"]', (3, 22, 3, 9), 1, False),
    ('QToolButton[role="corner"][iconOnly="true"]', (3, 6, 3, 6), 1, False),
    ('QToolButton[role="corner"][accent="true"]', (3, 3, 3, 9), 1, True),
)
# Lopsided only to offset the slack Qt leaves right of a text-beside-icon
# label, which stays on the right in either direction: not mirrored.
_FOCUS_NOT_MIRRORED = (
    'QToolButton[role="corner"]',
    'QToolButton[role="corner"][accent="true"]',
)
# Controls whose unfocused state already keeps a transparent 2 px border:
# focus only colours it.
_FOCUS_RECOLOURED = (
    'QToolButton[role="sectionHeader"]',
    'QToolButton[role="outputTitle"]',
    'QToolButton[role="cornerPart"]',
    'QToolButton[role="outputPart"]',
    'QToolButton[autoRaise="true"]',
    'QFrame[role="outputRow"]',
    "QCheckBox",
    "QRadioButton",
)


def focus_colour(p: Palette, on_accent_fill: bool = False) -> str:
    """The focus ring's colour: link on the theme's grounds, and ink round a
    brass or lime fill, where link would vanish (lime is the dark link).

    Ink rather than the on-accent colour: in the dark that is near-black, so
    the ring merged with the window and the button only looked smaller."""
    return p.ink if on_accent_fill else p.link


def focus_rules(p: Palette, object_name: str) -> str:
    """The stylesheet's keyboard-focus block; see `_FOCUS_PADDED`."""
    d = f"#{object_name}"
    rules: list[str] = []

    def padding(values: tuple[int, int, int, int]) -> str:
        return " ".join(f"{max(0, v)}px" for v in values)

    for selector, (top, right, bottom, left), width, on_fill in _FOCUS_PADDED:
        give = FOCUS_WIDTH - width
        ltr = (top - give, right - give, bottom - give, left - give)
        colour = focus_colour(p, on_fill)
        rules.append(
            f"{d} {selector}:focus {{\n"
            f"    border: {FOCUS_WIDTH}px solid {colour};\n"
            f"    padding: {padding(ltr)};\n}}"
        )
        if right != left and selector not in _FOCUS_NOT_MIRRORED:
            rtl = (ltr[0], ltr[3], ltr[2], ltr[1])
            rules.append(
                f'{d}[rtl="true"] {selector}:focus {{\n    padding: {padding(rtl)};\n}}'
            )
    recoloured = ",\n".join(f"{d} {s}:focus" for s in _FOCUS_RECOLOURED)
    rules.append(f"{recoloured} {{\n    border-color: {p.link};\n}}")
    rules.append(
        f'{d} QToolButton[role="outputPart"]:checked:focus {{\n'
        f"    border-color: {focus_colour(p, True)};\n}}"
    )
    # Open, a combo box keeps the outline its list continues.
    rules.append(
        f"{d} QComboBox:on:focus {{\n"
        f"    border: 1px solid {p.accent};\n    padding: 5px 8px;\n}}"
    )
    # The current tab is the focused one: ring it, keeping its accent bar.
    rules.append(
        f"{d} QTabBar::tab:focus, {d} QTabBar::tab:selected:focus {{\n"
        f"    border: {FOCUS_WIDTH}px solid {p.link};\n"
        f"    border-bottom: 3px solid {p.accent};\n"
        f"    padding: 6px 10px 7px 10px;\n}}"
    )
    # Rows: the keyboard's row in a list, tree, menu or drop-down list.
    rows = ",\n".join(
        (
            f"{d} QTreeView::item:focus",
            f'{d} QListWidget[role="templateList"]::item:focus',
            f'{d} QListWidget[role="templateChips"]::item:focus',
            f"{d} QMenu::item:selected",
            f"{d} QComboBox QAbstractItemView::item:selected",
        )
    )
    rules.append(f"{rows} {{\n    border: {FOCUS_WIDTH}px solid {p.link};\n}}")
    # A Checks line in the cartograph panel: its reserved border, coloured.
    rules.append(
        f'{d} QFrame[role="checkRow"]:focus {{\n'
        f"    border: {FOCUS_WIDTH}px solid {p.link};\n}}"
    )
    return "\n".join(rules) + "\n"
