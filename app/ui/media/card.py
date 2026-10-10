"""Poster-style cards and the responsive grid used by the Home page.

A card is painted by hand (one widget instead of five), which keeps a page of
dozens of covers cheap to build and lets the badges sit on the cover the way
they do on iwara.tv: duration bottom-right, an R-18 chip top-left and a
selection tick top-right.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.rating import is_adult
from ...core.search import IWARA_IMAGE_SOURCE_KIND, SearchVideo
from ...i18n import tr
from ..formatting import _format_count, _format_duration
from ..theme import palette, to_qcolor

COVER_RATIO = 9 / 16
CARD_RADIUS = 8
CARD_PAD = 8


def wrap_lines(text: str, metrics: QFontMetrics, width: int, max_lines: int) -> list[str]:
    """Greedy wrap that works for CJK and Latin text, eliding the last line."""

    text = " ".join(str(text or "").split())
    lines: list[str] = []
    while text and len(lines) < max_lines:
        if len(lines) == max_lines - 1:
            lines.append(metrics.elidedText(text, Qt.TextElideMode.ElideRight, width))
            break
        if metrics.horizontalAdvance(text) <= width:
            lines.append(text)
            break
        cut = len(text)
        while cut > 1 and metrics.horizontalAdvance(text[:cut]) > width:
            cut -= 1
        space = text.rfind(" ", 0, cut)
        if space > cut * 0.55:  # break at a word boundary when it costs little
            cut = space
        lines.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return lines


class MediaCard(QWidget):
    """One video or image post."""

    activated = Signal(object)  # SearchVideo
    context_requested = Signal(object, QPoint)  # SearchVideo, global position
    toggled = Signal(object, bool)  # SearchVideo, selected
    # With ``configurable_clicks`` a plain click / double click is reported
    # instead of ``activated``, and the owner applies the Settings actions.
    plain_clicked = Signal(object)  # this card
    double_clicked = Signal(object)  # this card

    def __init__(
        self,
        video: SearchVideo,
        parent: QWidget | None = None,
        *,
        selectable: bool = True,
        compact: bool = False,
    ):
        super().__init__(parent)
        self.video = video
        self.configurable_clicks = False
        self._swallow_release = False  # the release that ends a double click
        self._compact = compact  # no author line: every card shares one author
        self._selectable = selectable
        self._selected = False
        self._hover = False
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scaled_size = QSize()
        self._current = False  # the keyboard cursor sits here (drawn as a ring)
        self._hover_t = 0.0  # 0..1, eased in and out so hovering feels alive
        self._cover_alpha = 1.0  # a freshly arrived cover fades in
        self._anim: QVariantAnimation | None = None
        self._fade: QVariantAnimation | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.context_requested.emit(self.video, self.mapToGlobal(pos))
        )
        self.setToolTip(self._tooltip())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ── model ────────────────────────────────────────────────────────────────

    @property
    def is_image(self) -> bool:
        return self.video.source_kind == IWARA_IMAGE_SOURCE_KIND

    def _tooltip(self) -> str:
        video = self.video
        author = video.author_name or video.author_username
        return "\n".join(part for part in (video.title, f"@{video.author_username} · {author}" if author else "") if part)

    def is_selected(self) -> bool:
        return self._selected

    def state_mark(self) -> tuple[str, QColor] | None:
        """Chip for a local state carried in ``video.raw["_state"]`` (subscriptions)."""

        state = str(self.video.raw.get("_state") or "") if isinstance(self.video.raw, dict) else ""
        marks = {
            "new": (tr("NEW", "新增", "新着"), QColor(0, 120, 212, 225)),
            "downloaded": (tr("Downloaded", "已下载", "保存済み"), QColor(16, 124, 16, 225)),
            "moved": (tr("Moved", "已移走", "移動済み"), QColor(120, 120, 120, 225)),
            "queued": (tr("Queued", "已入队", "キュー内"), QColor(202, 120, 0, 225)),
            "unavailable": (tr("Unavailable", "不可下载", "保存不可"), QColor(150, 60, 60, 225)),
        }
        return marks.get(state)

    def set_current(self, current: bool):
        if current != self._current:
            self._current = current
            self.update()

    def set_selected(self, selected: bool, *, emit: bool = False):
        selected = bool(selected) and self._selectable
        if selected == self._selected:
            return
        self._selected = selected
        self.update()
        if emit:
            self.toggled.emit(self.video, selected)

    def set_cover(self, pixmap: QPixmap | None):
        first = self._pixmap is None
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._scaled = None
        if first and self._pixmap is not None and self.isVisible() and self.width() > 0:
            self._start_fade()
        else:
            self._cover_alpha = 1.0
        self.update()

    def _start_fade(self):
        if self._fade is None:
            self._fade = QVariantAnimation(self)
            self._fade.setDuration(220)
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.valueChanged.connect(self._on_fade)
        self._fade.stop()
        self._cover_alpha = 0.0
        self._fade.start()

    def _on_fade(self, value):
        self._cover_alpha = float(value)
        self.update()

    def _animate_hover(self, target: float):
        if self._anim is None:
            self._anim = QVariantAnimation(self)
            self._anim.setDuration(150)
            self._anim.valueChanged.connect(self._on_hover_value)
        self._anim.stop()
        self._anim.setStartValue(self._hover_t)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_hover_value(self, value):
        self._hover_t = float(value)
        self.update()

    # ── geometry ─────────────────────────────────────────────────────────────

    def _fonts(self) -> tuple[QFont, QFont]:
        title = QFont(self.font())
        title.setPointSizeF(max(8.5, self.font().pointSizeF() or 9.5))
        title.setWeight(QFont.Weight.DemiBold)
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, (self.font().pointSizeF() or 9.5) - 1))
        return title, small

    def cover_rect(self) -> QRect:
        return QRect(0, 0, self.width(), round(self.width() * COVER_RATIO))

    def height_for_width(self, width: int) -> int:
        title, small = self._fonts()
        title_h = QFontMetrics(title).lineSpacing() * 2
        small_h = QFontMetrics(small).lineSpacing()
        return round(width * COVER_RATIO) + CARD_PAD + title_h + 2 + small_h * (1 if self._compact else 2) + CARD_PAD

    def set_card_width(self, width: int):
        width = max(120, int(width))
        self.setFixedSize(width, self.height_for_width(width))

    def _check_rect(self) -> QRect:
        return QRect(self.width() - 32, 6, 26, 26)

    # ── events ───────────────────────────────────────────────────────────────

    def enterEvent(self, event):
        self._hover = True
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        # A plain QWidget ignores presses, which would route the release to
        # the parent instead of completing the click here.
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parentWidget()
            if parent is not None and parent.focusPolicy() != Qt.FocusPolicy.NoFocus:
                parent.setFocus(Qt.FocusReason.MouseFocusReason)  # arrow keys continue from here
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._swallow_release:
            self._swallow_release = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            pos = event.position().toPoint()
            if self._selectable and self._check_rect().contains(pos):
                self.set_selected(not self._selected, emit=True)
            elif (
                self._selectable
                and event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            ):
                self.set_selected(not self._selected, emit=True)
            elif self.configurable_clicks:
                self.plain_clicked.emit(self)
            else:
                self.activated.emit(self.video)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if (
            self.configurable_clicks
            and event.button() == Qt.MouseButton.LeftButton
            and not (self._selectable and self._check_rect().contains(event.position().toPoint()))
            and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        ):
            self._swallow_release = True
            self.double_clicked.emit(self)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ── painting ─────────────────────────────────────────────────────────────

    def _scaled_cover(self, size: QSize) -> QPixmap | None:
        if self._pixmap is None:
            return None
        ratio = self.devicePixelRatioF()
        target = QSize(round(size.width() * ratio), round(size.height() * ratio))
        if self._scaled is None or self._scaled_size != target:
            scaled = self._pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - target.width()) // 2)
            y = max(0, (scaled.height() - target.height()) // 2)
            scaled = scaled.copy(x, y, target.width(), target.height())
            scaled.setDevicePixelRatio(ratio)
            self._scaled, self._scaled_size = scaled, target
        return self._scaled

    def _draw_badge(self, painter: QPainter, text: str, anchor: QPointF, *, right: bool, fill: QColor, fg: QColor, font: QFont):
        painter.save()
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 12
        height = metrics.height() + 4
        left = anchor.x() - width if right else anchor.x()
        rect = QRectF(left, anchor.y() - height if right else anchor.y(), width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def paintEvent(self, _event):
        p = palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        title_font, small_font = self._fonts()
        accent = to_qcolor(p.accent)

        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = QPainterPath()
        shape.addRoundedRect(outer, CARD_RADIUS, CARD_RADIUS)
        painter.fillPath(shape, to_qcolor(p.selected if self._selected else p.surface))

        cover = self.cover_rect()
        self._paint_cover(painter, shape, cover, p)
        self._paint_badges(painter, cover, small_font)
        if self._selectable and (self._hover or self._selected):
            self._paint_check(painter, accent)
        self._paint_text(painter, cover, p, title_font, small_font)

        border = QPen(accent if (self._hover or self._selected) else to_qcolor(p.border), 2 if self._selected else 1)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shape)
        if self._current:
            ring = QPainterPath()
            ring.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), CARD_RADIUS - 2, CARD_RADIUS - 2)
            painter.setPen(QPen(accent, 2))
            painter.drawPath(ring)

    def _paint_cover(self, painter: QPainter, shape: QPainterPath, cover, p):
        painter.save()
        painter.setClipPath(shape)
        painter.fillRect(cover, to_qcolor(p.placeholder))
        scaled = self._scaled_cover(cover.size())
        if scaled is not None:
            painter.setOpacity(self._cover_alpha)
            grow = round(cover.width() * 0.035 * self._hover_t)
            if grow:
                # A slow zoom on hover; the card's rounded clip keeps it tidy.
                target = cover.adjusted(-grow, -round(grow * COVER_RATIO), grow, round(grow * COVER_RATIO))
                painter.drawPixmap(target, scaled)
            else:
                painter.drawPixmap(cover.topLeft(), scaled)
            painter.setOpacity(1.0)
        if self._selected:
            painter.fillRect(cover, QColor(0, 0, 0, 38))
        elif self._hover_t > 0:
            painter.fillRect(cover, QColor(0, 0, 0, round(30 * self._hover_t)))
        painter.restore()

    def _paint_badges(self, painter: QPainter, cover, small_font: QFont):
        """Image count / duration, R-18 and download-state badges on the cover."""

        badge_font = QFont(small_font)
        badge_font.setWeight(QFont.Weight.DemiBold)
        dark = QColor(0, 0, 0, 170)
        white = QColor("#ffffff")
        video = self.video
        if self.is_image:
            count = int(video.raw.get("numImages") or 0) if isinstance(video.raw, dict) else 0
            if count > 1:
                self._draw_badge(
                    painter, tr(f"{count} images", f"{count} 张", f"{count} 枚"),
                    QPointF(cover.right() - 6, cover.bottom() - 6), right=True, fill=dark, fg=white, font=badge_font,
                )
        elif video.duration:
            self._draw_badge(
                painter, _format_duration(video.duration),
                QPointF(cover.right() - 6, cover.bottom() - 6), right=True, fill=dark, fg=white, font=badge_font,
            )
        if is_adult(video.rating):
            self._draw_badge(
                painter, "R-18", QPointF(cover.left() + 6, cover.top() + 6),
                right=False, fill=QColor(196, 43, 28, 225), fg=white, font=badge_font,
            )
        mark = self.state_mark()
        if mark is not None:
            text, color = mark
            metrics = QFontMetrics(badge_font)
            self._draw_badge(
                painter, text, QPointF(cover.left() + 6, cover.bottom() - 6 - metrics.height() - 4),
                right=False, fill=color, fg=white, font=badge_font,
            )

    def _paint_check(self, painter: QPainter, accent: QColor):
        white = QColor("#ffffff")
        box = self._check_rect()
        painter.save()
        painter.setPen(QPen(white, 1.6))
        painter.setBrush(accent if self._selected else QColor(0, 0, 0, 120))
        painter.drawEllipse(QRectF(box).adjusted(2, 2, -2, -2))
        if self._selected:
            painter.setPen(QPen(white, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            cx, cy = box.center().x(), box.center().y()
            painter.drawPolyline([QPointF(cx - 5, cy), QPointF(cx - 1.5, cy + 4), QPointF(cx + 5, cy - 4)])
        painter.restore()

    def _paint_text(self, painter: QPainter, cover, p, title_font: QFont, small_font: QFont):
        """Title (two lines), author and stats under the cover."""

        video = self.video
        x = CARD_PAD
        text_width = self.width() - 2 * CARD_PAD
        y = cover.bottom() + CARD_PAD
        painter.setPen(to_qcolor(p.selected_text if self._selected else p.text))
        painter.setFont(title_font)
        title_metrics = QFontMetrics(title_font)
        for line in wrap_lines(video.title or video.video_id, title_metrics, text_width, 2):
            painter.drawText(x, y + title_metrics.ascent(), line)
            y += title_metrics.lineSpacing()
        # Reserve the second title line even for one-line titles.
        y = cover.bottom() + CARD_PAD + title_metrics.lineSpacing() * 2 + 2

        small_metrics = QFontMetrics(small_font)
        painter.setFont(small_font)
        painter.setPen(to_qcolor(p.text_secondary))
        if not self._compact:
            author = video.author_name or video.author_username
            painter.drawText(
                x, y + small_metrics.ascent(),
                small_metrics.elidedText(author or tr("Unknown author", "未知作者", "作者不明"), Qt.TextElideMode.ElideRight, text_width),
            )
            y += small_metrics.lineSpacing()
        date = video.published_at[:10] if video.published_at else ""
        if not video.views and not video.likes:
            # Local records (subscriptions) know no counters; show the date alone.
            stats = date
        else:
            stats = tr(
                f"{_format_count(video.views)} views · {_format_count(video.likes)} likes",
                f"{_format_count(video.views)} 观看 · {_format_count(video.likes)} 喜欢",
                f"{_format_count(video.views)} 再生 · {_format_count(video.likes)} いいね",
            )
            if date and small_metrics.horizontalAdvance(f"{stats} · {date}") <= text_width:
                stats = f"{stats} · {date}"
        painter.drawText(x, y + small_metrics.ascent(), small_metrics.elidedText(stats, Qt.TextElideMode.ElideRight, text_width))
