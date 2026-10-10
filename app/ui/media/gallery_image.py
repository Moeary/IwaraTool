"""One picture of an image post, decoded only while it is near the viewport."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .pixmaps import image_size, read_pixmap
from ..theme import palette, to_qcolor


GALLERY_MAX_WIDTH = 1280


GALLERY_DECODE_WIDTH = 1920  # decoded pictures are capped at this width


class GalleryImage(QWidget):
    """One picture of an image post, shown at the page width."""

    clicked = Signal(str)  # original url

    def __init__(self, file_info: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.file_id = str(file_info.get("id") or "")
        name = str(file_info.get("name") or "")
        self.original_url = f"https://i.iwara.tv/image/original/{self.file_id}/{name}" if name else ""
        self.thumb_url = f"https://i.iwara.tv/image/thumbnail/{self.file_id}/{self.file_id}.jpg"
        width, height = file_info.get("width"), file_info.get("height")
        self._ratio = (height / width) if isinstance(width, int) and isinstance(height, int) and width and height else 0.62
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scaled_key: tuple | None = None
        self._final = False
        self._path = ""  # the file to decode when this picture is on screen
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaximumWidth(GALLERY_MAX_WIDTH)

    def set_source(self, path: str, *, final: bool):
        """Point at a picture on disk; it is decoded only while it is near the viewport."""

        if self._final and not final:
            return
        size = image_size(path)
        if not size.isValid() or not size.width():
            return
        self._path = path
        self._final = self._final or final
        if final:
            self._ratio = size.height() / size.width()
        self._pixmap = self._scaled = self._scaled_key = None
        self._fit_height()
        self.update()

    def release(self):
        """Drop the decoded picture (it is re-read from disk if it scrolls back into view)."""

        if self._path:
            self._pixmap = self._scaled = self._scaled_key = None

    def is_decoded(self) -> bool:
        return self._pixmap is not None

    def set_pixmap(self, pixmap: QPixmap, *, final: bool):
        if pixmap.isNull() or (self._final and not final):
            return
        self._pixmap = pixmap
        self._final = self._final or final
        if final and pixmap.width():
            self._ratio = pixmap.height() / pixmap.width()
        self._fit_height()
        self.update()

    def _fit_height(self):
        self.setFixedHeight(max(120, round(self.width() * self._ratio)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def mousePressEvent(self, event):
        # Accept the press so the matching release reaches this widget.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.original_url:
            self.clicked.emit(self.original_url)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), 8, 8)
        painter.setClipPath(shape)
        painter.fillRect(self.rect(), to_qcolor(palette().placeholder))
        if self._pixmap is None and self._path:
            # Painting means some of it is visible; decode at what is displayed
            # (never more than GALLERY_DECODE_WIDTH), not at the original size.
            self._pixmap = read_pixmap(self._path, min(GALLERY_DECODE_WIDTH, max(64, round(self.width() * self.devicePixelRatioF()))))
            self._scaled_key = None
        if self._pixmap is not None and not self._pixmap.isNull():
            key = (self.width(), self.height(), self._pixmap.cacheKey())
            if self._scaled_key != key:
                self._scaled = self._pixmap.scaled(
                    self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation,
                )
                self._scaled_key = key
            painter.drawPixmap(self.rect(), self._scaled)
