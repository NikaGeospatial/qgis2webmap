"""A word-wrapped label exactly as tall as its text.

Qt asks a wrapped `QLabel` for its height before it knows the width it will
get, guesses narrow, and the layout then spreads the surplus as blank space
around it: in a form inside a scroll area every help line took about twice its
height, and the basemap warning three times. This label measures its text at
its real width whenever that width changes and reports that height to the
layout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtWidgets import QLabel, QSizePolicy, QWidget


class FittedLabel(QLabel):
    """A wrapped label whose height follows its text at its current width.

    Reported through the size hint rather than pinned with `setFixedHeight`:
    `QLabel.heightForWidth` never answers below the label's minimum height, so
    a label pinned tall at a narrow first width could never measure smaller
    again.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _height_for_text(self) -> int | None:
        if not self.text():
            return 0
        return self.heightForWidth(self.width()) if self.width() > 0 else None

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().sizeHint()
        height = self._height_for_text()
        if height is not None:
            hint.setHeight(height)
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        height = self._height_for_text()
        if height is not None:
            hint.setHeight(height)
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        super().setText(text)
        self.updateGeometry()
