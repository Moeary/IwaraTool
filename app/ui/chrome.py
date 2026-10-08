"""Small shared building blocks so every page looks like the same app.

Page headers, titled sections, status chips and empty states live here instead
of being re-invented (slightly differently) on each page.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, CaptionLabel, IconWidget, PrimaryPushButton, SubtitleLabel, TitleLabel

from .theme import palette, set_secondary_text, to_qcolor


class PageHeader(QWidget):
    """Title (and optional one-line caption) on the left, tool widgets on the right."""

    def __init__(self, title: str, parent: QWidget | None = None, *, subtitle: str = ""):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        self.title = TitleLabel(title, self)
        names.addWidget(self.title)
        self.subtitle = CaptionLabel(subtitle, self)
        set_secondary_text(self.subtitle)
        self.subtitle.setVisible(bool(subtitle))
        names.addWidget(self.subtitle)
        row.addLayout(names)
        row.addStretch(1)
        self.tools = QHBoxLayout()
        self.tools.setContentsMargins(0, 0, 0, 0)
        self.tools.setSpacing(10)
        row.addLayout(self.tools)

    def set_subtitle(self, text: str):
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))


class SectionTitle(QWidget):
    """A section heading with a short accent bar, as used by every titled row.

    With ``collapsible`` the heading is clickable and shows a small chevron
    (down = open, right = folded); the owner reacts to ``toggled``.
    """

    toggled = Signal(bool)  # True when now collapsed

    def __init__(self, text: str, parent: QWidget | None = None, *, collapsible: bool = False):
        super().__init__(parent)
        self._collapsible = collapsible
        self._collapsed = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self._bar = _AccentBar(self)
        row.addWidget(self._bar)
        self.label = SubtitleLabel(text, self)
        row.addWidget(self.label)
        self._chevron = _Chevron(self)
        self._chevron.setVisible(collapsible)
        row.addWidget(self._chevron)
        if collapsible:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def setText(self, text: str):
        self.label.setText(text)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self._chevron.set_collapsed(self._collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def mousePressEvent(self, event):
        if self._collapsible and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._collapsible and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.set_collapsed(not self._collapsed)
            self.toggled.emit(self._collapsed)
        super().mouseReleaseEvent(event)


class _Chevron(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._collapsed = False
        self.setFixedSize(16, 16)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = collapsed
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(to_qcolor(palette().text_secondary), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx, cy = self.width() / 2, self.height() / 2
        if self._collapsed:
            painter.drawPolyline([QPointF(cx - 2, cy - 4), QPointF(cx + 2, cy), QPointF(cx - 2, cy + 4)])
        else:
            painter.drawPolyline([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)])


class _AccentBar(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(4, 20)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(palette().accent))
        painter.drawRoundedRect(QRectF(self.rect()), 2, 2)


_TONES = {
    # tone: (palette attribute for the text, palette attribute for the fill)
    "accent": ("accent", "selected"),
    "success": ("success", "success_bg"),
    "warning": ("warning", "warning_bg"),
    "neutral": ("text_secondary", "hover"),
    "info": ("info", "selected"),
}


class StatusChip(QWidget):
    """A small rounded pill: "Subscribed in app", "Followed on web", "NEW"…"""

    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._tone = tone if tone in _TONES else "neutral"
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._refit()

    def text(self) -> str:
        return self._text

    def tone(self) -> str:
        return self._tone

    def set_state(self, text: str, tone: str | None = None):
        self._text = text
        if tone in _TONES:
            self._tone = tone
        self._refit()
        self.update()

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, (font.pointSizeF() or 9.5) - 0.5))
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _refit(self):
        metrics = QFontMetrics(self._font())
        self.setFixedSize(metrics.horizontalAdvance(self._text) + 20, metrics.height() + 8)

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _event):
        p = palette()
        fg_name, bg_name = _TONES[self._tone]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = to_qcolor(getattr(p, bg_name))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.height() / 2, self.height() / 2)
        painter.setPen(QColor(to_qcolor(getattr(p, fg_name))))
        painter.setFont(self._font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


class EmptyState(QWidget):
    """Centered icon, headline, explanation and an optional call to action."""

    action_clicked = Signal()

    def __init__(
        self,
        icon,
        title: str,
        message: str = "",
        parent: QWidget | None = None,
        *,
        action_text: str = "",
    ):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 36, 24, 36)
        column.setSpacing(10)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(44, 44)
        column.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = BodyLabel(title, self)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.title)
        self.message = CaptionLabel(message, self)
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_secondary_text(self.message)
        self.message.setVisible(bool(message))
        column.addWidget(self.message)
        self.button: PrimaryPushButton | None = None
        if action_text:
            self.button = PrimaryPushButton(action_text, self)
            self.button.clicked.connect(self.action_clicked)
            column.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_text(self, title: str, message: str = ""):
        self.title.setText(title)
        self.message.setText(message)
        self.message.setVisible(bool(message))


def format_age(seconds: float) -> str:
    """"just now" / "5 min ago" / "3 h ago" / "2 d ago" in the UI language."""

    from ..i18n import tr

    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return tr("just now", "刚刚", "たった今")
    minutes = int(seconds // 60)
    if minutes < 60:
        return tr(f"{minutes} min ago", f"{minutes} 分钟前", f"{minutes}分前")
    hours = minutes // 60
    if hours < 48:
        return tr(f"{hours} h ago", f"{hours} 小时前", f"{hours}時間前")
    days = hours // 24
    return tr(f"{days} d ago", f"{days} 天前", f"{days}日前")


__all__ = [
    "EmptyState",
    "PageHeader",
    "SectionTitle",
    "StatusChip",
    "format_age",
]
