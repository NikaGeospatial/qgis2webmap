"""One template at a useful size, rendered only when somebody asks to see it.

The picker owns the choice; this dialog only inspects its current page.
Closing it cancels an outstanding QGIS render and changes no project state.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QGuiApplication, QImage, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr


class _Page(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(tr("Drawing the preview\u2026"), parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100, 100)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image: QImage | None = None

    def show_image(self, image: QImage | None) -> None:
        self.image = image
        if image is None or image.isNull():
            self.setPixmap(QPixmap())
            self.setText(tr("Preview could not be drawn"))
        else:
            self._refit()

    def _refit(self) -> None:
        if self.image is None or self.image.isNull():
            return
        ratio = max(1.0, self.devicePixelRatioF())
        size = QSize(round(self.width() * ratio), round(self.height() * ratio))
        pixmap = QPixmap.fromImage(self.image).scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(ratio)
        self.setPixmap(pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refit()


class TemplatePreviewDialog(QDialog):
    def __init__(
        self,
        title: str,
        description: str,
        page_label: str,
        render: Callable[[int], object],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._render = render
        self._pending = None
        self._closed = False
        column = QVBoxLayout(self)
        heading = QLabel(title, self)
        font = heading.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.3)
        heading.setFont(font)
        column.addWidget(heading)
        details = QLabel(f"{page_label} · {description}", self)
        details.setWordWrap(True)
        details.setProperty("role", "hint")
        column.addWidget(details)
        self.page = _Page(self)
        column.addWidget(self.page, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)
        screen = parent.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry().size() if screen else QSize(1280, 900)
        self.resize(
            min(1100, int(available.width() * 0.9)),
            min(850, int(available.height() * 0.88)),
        )

    def start(self) -> None:
        """Draw once at the inspection size, including the screen pixel ratio."""
        width = round(
            min(1400, max(960, self.width())) * max(1.0, self.devicePixelRatioF())
        )
        try:
            result = self._render(width)
        except Exception:
            result = None
        if result is None or isinstance(result, QImage):
            self.page.show_image(result)
        else:
            self._pending = result
            result.finished.connect(self._finished)

    def _finished(self, image: QImage | None) -> None:
        self._pending = None
        if not self._closed:
            self.page.show_image(image)

    def done(self, result: int) -> None:
        self._closed = True
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.cancel()
        super().done(result)
