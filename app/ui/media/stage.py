"""The cover / player stage at the top of the detail view."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget


class StageBox(QWidget):
    """A 16:9 area at the page width, never taller than the height it is given."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._max_height = 640
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(180)

    def set_max_height(self, height: int):
        height = max(240, int(height))
        if height != self._max_height:
            self._max_height = height
            self._fit_height()

    def _fit_height(self):
        height = max(180, min(self._max_height, round(self.width() * 9 / 16)))
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        self._fit_height()
        super().resizeEvent(event)


class MediaStage(StageBox):
    """Letterboxed cover with a play button, standing in for the player."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._backdrop: QPixmap | None = None
        self._playable = False
        self._badge = ""
        self._scaled: QPixmap | None = None
        self._scaled_key: tuple | None = None

    def _scaled_pixmap(self) -> QPixmap:
        """The cover fitted to the stage, scaled once per size rather than on every paint."""

        key = (self.size().width(), self.size().height(), self._pixmap.cacheKey())
        if self._scaled_key != key:
            self._scaled = self._pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_key = key
        return self._scaled

    def set_pixmap(self, pixmap: QPixmap | None):
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._scaled_key = None
        # A tiny copy stretched over the stage reads as a blurred backdrop, so a
        # 16:9 cover on a very wide window is not flanked by empty black bars.
        self._backdrop = (
            self._pixmap.scaledToWidth(40, Qt.TransformationMode.SmoothTransformation)
            if self._pixmap is not None
            else None
        )
        self.update()

    def set_playable(self, playable: bool, badge: str = ""):
        self._playable = playable
        self._badge = badge
        self.setCursor(Qt.CursorShape.PointingHandCursor if playable else Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, event):
        # Accept the press so the matching release reaches this widget.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._playable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.setClipPath(shape)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._backdrop is not None:
            painter.drawPixmap(self.rect(), self._backdrop)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 150))
        if self._pixmap is not None:
            scaled = self._scaled_pixmap()
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
        if self._playable:
            radius = 34
            center = self.rect().center()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawEllipse(center, radius, radius)
            painter.setBrush(QColor("#ffffff"))
            cx, cy = center.x(), center.y()
            painter.drawPolygon(QPolygonF([QPointF(cx - 9, cy - 15), QPointF(cx - 9, cy + 15), QPointF(cx + 16, cy)]))
        if self._badge:
            font = QFont(self.font())
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(self._badge) + 14
            rect = QRectF(self.width() - width - 10, self.height() - metrics.height() - 16, width, metrics.height() + 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._badge)
