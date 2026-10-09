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

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextTable,
)
from qgis.PyQt.QtWidgets import QApplication, QFormLayout, QGroupBox, QWidget

PACKAGE_DIR = Path(__file__).resolve().parent.parent
FONT_DIR = PACKAGE_DIR / "fonts"
ICON_DIR = PACKAGE_DIR / "icons" / "theme"
FONT_FAMILY = "Inter"


@dataclass(frozen=True)
class Palette:
    """One NIKA theme, as the hex colours the stylesheet needs."""

    name: str
    paper: str  # the dialog's ground
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

    @property
    def is_dark(self) -> bool:
        return QColor(self.paper).lightness() < 128

    def icon(self, name: str) -> str:
        """A theme icon's path, in the form a stylesheet `url()` accepts."""
        variant = "dark" if self.is_dark else "light"
        return (ICON_DIR / f"{name}-{variant}.svg").as_posix()


PLUM_LIGHT = Palette(
    name="plum-light",
    paper="#fbf9fc",
    field="#f4f0f5",
    hover="#ece6ee",
    rule="#d3cad5",
    ink="#170d19",
    muted="#6b606e",
    quiet="#807683",
    accent="#eba941",
    accent_hover="#e09a2c",
    on_accent="#170d19",
    accent_soft="#f5e1c8",
    link="#8a5200",
    warn="#a2540a",
)

PLUM_DARK = Palette(
    name="plum-dark",
    paper="#0e0511",
    field="#1e1021",
    hover="#2a1a2e",
    rule="#3a2b3e",
    ink="#f5f2e7",
    muted="#a39aa5",
    quiet="#817683",
    accent="#abf051",
    accent_hover="#bdf76f",
    on_accent="#0e0511",
    accent_soft="#203501",
    link="#abf051",
    warn="#e78a45",
)


def pick_palette(qgis_palette: QPalette) -> Palette:
    """Plum-Dark under a dark QGIS theme, Plum-Light otherwise."""
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
    palette = pick_palette(app.palette() if app is not None else dialog.palette())

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
        QPalette.ColorRole.Window: palette.paper,
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
    dialog.setStyleSheet(stylesheet(palette, dialog.objectName()))

    # Rounded fields are taller than a bare label, and a form aligns labels to
    # the top of the row by default, so each label sat above its field's line.
    for form in dialog.findChildren(QFormLayout):
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(14)
    # Sections are a title and a hairline now, not a box, so their contents
    # line up with the title instead of sitting inset inside a frame.
    for box in dialog.findChildren(QGroupBox):
        layout = box.layout()
        if layout is not None:
            layout.setContentsMargins(0, 2, 0, 2)
    return palette


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


def stylesheet(p: Palette, object_name: str) -> str:
    """The dialog's stylesheet, every rule anchored on `#object_name`."""
    d = f"#{object_name}"
    return f"""
{d} {{
    background: {p.paper};
    color: {p.ink};
}}
{d} QWidget {{
    color: {p.ink};
}}
{d} QScrollArea, {d} QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
{d} QLabel {{
    background: transparent;
}}

/* ---- Tabs: text, with a bar under the open one ----------------------- */
{d} QTabWidget::pane {{
    border: none;
    border-top: 1px solid {p.rule};
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

/* ---- Sections: a title and a hairline, not a sunken box ---------------- */
{d} QGroupBox {{
    border: none;
    border-top: 1px solid {p.rule};
    margin-top: 26px;
    padding: 12px 0 0 0;
    font-weight: 600;
}}
{d} QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0;
    top: 4px;
    padding: 0;
    color: {p.ink};
}}
{d} QGroupBox QWidget {{
    font-weight: 400;
}}

/* ---- Fields --------------------------------------------------------- */
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
{d} QComboBox QAbstractItemView {{
    background: {p.paper};
    border: 1px solid {p.rule};
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

/* The two things a user does to the map: a filled pill and an outlined one. */
{d} QToolButton[role="primary"], {d} QToolButton[role="secondary"] {{
    border-radius: 15px;
    padding: 5px 34px 5px 16px;
    min-height: 20px;
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
{d} QToolButton[role="primary"]::menu-button,
{d} QToolButton[role="secondary"]::menu-button {{
    border: none;
    width: 26px;
    border-top-right-radius: 15px;
    border-bottom-right-radius: 15px;
}}
{d} QToolButton[role="primary"]::menu-button {{
    border-left: 1px solid {p.accent_hover};
}}
{d} QToolButton[role="secondary"]::menu-button {{
    border-left: 1px solid {p.rule};
}}
{d} QToolButton[role="primary"]:disabled::menu-button {{
    border-left: 1px solid {p.rule};
}}
{d} QToolButton[role="primary"]::menu-arrow {{
    image: url({p.icon("chevron-on-accent")});
    width: 10px;
    height: 10px;
}}
{d} QToolButton[role="secondary"]::menu-arrow {{
    image: url({p.icon("chevron")});
    width: 10px;
    height: 10px;
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
    padding: 5px 16px 5px 34px;
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
