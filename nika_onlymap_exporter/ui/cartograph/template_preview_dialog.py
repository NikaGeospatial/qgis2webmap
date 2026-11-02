"""One template at a useful size, rendered only when somebody asks to see it.

The picker owns the choice; this dialog only inspects its current page.
Closing it cancels an outstanding QGIS render and changes no project state.

The card's thumbnail, when there is one, is shown at once while the larger
page is drawn ("Refining preview..."), and stays if that fails, with a Retry.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qgis.PyQt.QtCore import QSize, Qt, QTimer
from qgis.PyQt.QtGui import QGuiApplication, QImage, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.canvas_cartograph import render_slot_busy
from ..i18n import tr

PREVIEW_TIMEOUT_MS = 30_000
#: How soon to look again while a cancelled page still holds the render slot.
_SLOT_RECHECK_MS = 100


class _Page(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(tr("Drawing the preview\u2026"), parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100, 100)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        #: The drawn larger page, once there is one; never the thumbnail.
        self.image: QImage | None = None
        #: What is on show: the larger page, else the card's thumbnail.
        self.shown: QImage | None = None

    def show_thumbnail(self, thumbnail: QImage | None) -> None:
        """The card's page while the larger one is drawn."""
        self.image = None
        usable = thumbnail is not None and not thumbnail.isNull()
        self.shown = thumbnail if usable else None
        self.setPixmap(QPixmap())
        if self.shown is None:
            self.setText(tr("Drawing the preview\u2026"))
        else:
            self._refit()

    def show_image(self, image: QImage | None) -> None:
        if image is None or image.isNull():
            # The thumbnail, if any, stays on show.
            self.image = None
            if self.shown is None:
                self.setPixmap(QPixmap())
                self.setText(tr("Preview could not be drawn"))
            return
        self.image = image
        self.shown = image
        self._refit()

    def _refit(self) -> None:
        if self.shown is None or self.shown.isNull():
            return
        ratio = max(1.0, self.devicePixelRatioF())
        size = QSize(round(self.width() * ratio), round(self.height() * ratio))
        pixmap = QPixmap.fromImage(self.shown).scaled(
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
        thumbnail: QImage | None = None,
    ) -> None:
        super().__init__(parent)
        self._thumbnail = thumbnail
        self.setWindowTitle(title)
        self.setModal(True)
        self._render = render
        self._pending: Any = None
        self._closed = False
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timed_out)
        self._slot_wait = QTimer(self)
        self._slot_wait.setSingleShot(True)
        self._slot_wait.timeout.connect(self._begin)
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
        # Under the page: that a sharper version is being drawn, or why the
        # thumbnail is all there is.
        self.progress = QLabel("", self)
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress.setProperty("role", "hint")
        self.progress.setVisible(False)
        column.addWidget(self.progress)
        self.retry_button = QPushButton(tr("Retry preview"), self)
        self.retry_button.setAutoDefault(False)
        self.retry_button.hide()
        self.retry_button.clicked.connect(self.start)
        column.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignHCenter)
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
        if self._closed:
            return
        self._deadline.stop()
        self._slot_wait.stop()
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.cancel()
        self.retry_button.hide()
        self.page.show_thumbnail(self._thumbnail)
        if self._thumbnail is not None and not self._thumbnail.isNull():
            self.progress.setText(tr("Refining preview\u2026"))
            self.progress.setVisible(True)
        else:
            self.progress.setVisible(False)
        self._begin()

    def _begin(self) -> None:
        """Draw the larger page -- once a page cancelled a moment ago has
        let go of the render slot."""
        if self._closed:
            return
        if render_slot_busy():
            self._slot_wait.start(_SLOT_RECHECK_MS)
            return
        width = round(
            min(1400, max(960, self.width())) * max(1.0, self.devicePixelRatioF())
        )
        result: Any
        try:
            result = self._render(width)
        except Exception:
            result = None
        if result is None or isinstance(result, QImage):
            self._show_result(result)
        else:
            self._pending = result
            # A bound method of this dialog, not a closure: the render can
            # finish after the dialog is gone, and Qt drops the connection then.
            result.finished.connect(self._finished)
            self._deadline.start(PREVIEW_TIMEOUT_MS)

    def _finished(self, image: QImage | None) -> None:
        if self._closed or self._pending is None:
            return
        emitter = self.sender()
        if emitter is not None and emitter is not self._pending:
            return
        self._pending = None
        self._deadline.stop()
        self._show_result(image)

    def _show_result(self, image: QImage | None) -> None:
        failed = image is None or image.isNull()
        self.page.show_image(image)
        self.retry_button.setVisible(failed)
        self.progress.setVisible(failed and self.page.shown is not None)
        if failed:
            self.progress.setText(tr("The sharper preview could not be drawn."))

    def _timed_out(self) -> None:
        if self._closed:
            return
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.cancel()
        self._show_result(None)
        message = tr("The preview took too long. Retry, or close to choose a template.")
        if self.page.shown is None:
            self.page.setText(message)
        else:
            self.progress.setText(message)

    def done(self, result: int) -> None:
        self._closed = True
        self._deadline.stop()
        self._slot_wait.stop()
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.cancel()
        super().done(result)
