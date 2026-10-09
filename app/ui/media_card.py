"""Poster-style cards and the responsive grid used by the Home page.

A card is painted by hand (one widget instead of five), which keeps a page of
dozens of covers cheap to build and lets the badges sit on the cover the way
they do on iwara.tv: duration bottom-right, an R-18 chip top-left and a
selection tick top-right.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from qfluentwidgets import FluentIcon, IconWidget, Slider

from ..config import app_config
from ..core.rating import is_adult
from ..core.search import IWARA_IMAGE_SOURCE_KIND, SearchVideo, small_cover_url
from ..i18n import tr
from ..signal_bus import signal_bus
from .click_dispatch import ACTION_DETAIL, ACTION_PLAY, ACTION_SELECT, ClickDispatcher, play_in_window
from .search_widgets import _format_count, _format_duration
from .theme import palette, to_qcolor

COVER_RATIO = 9 / 16
CARD_RADIUS = 8
CARD_PAD = 8

# One poster size shared by every grid (Home, "More", subscriptions, ...).
CARD_WIDTH_KEY = "media_card_width_v1"
CARD_WIDTH_MIN = 140
CARD_WIDTH_MAX = 440
CARD_WIDTH_DEFAULT = 224


def saved_card_width() -> int:
    try:
        value = int(app_config.get_ui_value(CARD_WIDTH_KEY, CARD_WIDTH_DEFAULT) or CARD_WIDTH_DEFAULT)
    except (TypeError, ValueError):
        value = CARD_WIDTH_DEFAULT
    return max(CARD_WIDTH_MIN, min(CARD_WIDTH_MAX, value))


CARD_WIDTH_STEP = 24


def set_card_width(width: int) -> int:
    """Save and broadcast one cover size to every grid (what the slider does)."""

    width = max(CARD_WIDTH_MIN, min(CARD_WIDTH_MAX, int(width)))
    app_config.set_ui_value(CARD_WIDTH_KEY, width)
    signal_bus.media_card_size_changed.emit(width)
    return width


def step_card_width(steps: int) -> int:
    """Make the covers larger (``steps`` > 0) or smaller by one slider notch per step."""

    return set_card_width(saved_card_width() + steps * CARD_WIDTH_STEP)


def reset_card_width() -> int:
    return set_card_width(CARD_WIDTH_DEFAULT)


def read_pixmap(path: str, max_width: int = 0) -> QPixmap:
    """A picture from disk, decoded no larger than ``max_width`` pixels wide.

    The reader scales while decoding (JPEG decodes straight to a fraction of its
    size), so a 3200 x 2000 original never exists in memory at full size: that
    is 25 MB as a pixmap, and image posts have dozens of them.
    """

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if max_width > 0 and size.isValid() and size.width() > max_width:
        reader.setScaledSize(QSize(max_width, max(1, round(size.height() * max_width / size.width()))))
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def image_size(path: str) -> QSize:
    """Pixel size of an image file from its header (nothing is decoded)."""

    return QImageReader(path).size()


class PixmapLRU:
    """Decoded covers, kept up to a byte budget and re-read from disk when evicted.

    A dict of every cover ever shown grows with the library (hundreds of
    ~300 KB pixmaps); the files are cached on disk anyway.
    """

    def __init__(self, max_bytes: int = 48 * 1024 * 1024):
        self._max = max_bytes
        self._items: dict[str, QPixmap] = {}
        self._bytes = 0

    @staticmethod
    def _size(pixmap: QPixmap) -> int:
        return pixmap.width() * pixmap.height() * max(1, pixmap.depth() // 8)

    def get(self, key: str, default=None):
        pixmap = self._items.pop(key, None)
        if pixmap is None:
            return default
        self._items[key] = pixmap  # most recently used goes last
        return pixmap

    def __setitem__(self, key: str, pixmap: QPixmap):
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= self._size(old)
        self._items[key] = pixmap
        self._bytes += self._size(pixmap)
        while self._bytes > self._max and len(self._items) > 1:
            _key, evicted = next(iter(self._items.items()))
            del self._items[_key]
            self._bytes -= self._size(evicted)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __getitem__(self, key: str) -> QPixmap:
        pixmap = self.get(key)
        if pixmap is None:
            raise KeyError(key)
        return pixmap

    def __len__(self) -> int:
        return len(self._items)

    def clear(self):
        self._items.clear()
        self._bytes = 0


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

        # Badges on the cover.
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

        if self._selectable and (self._hover or self._selected):
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

        # Text.
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

        border = QPen(accent if (self._hover or self._selected) else to_qcolor(p.border), 2 if self._selected else 1)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shape)
        if self._current:
            ring = QPainterPath()
            ring.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), CARD_RADIUS - 2, CARD_RADIUS - 2)
            painter.setPen(QPen(accent, 2))
            painter.drawPath(ring)


class MediaGrid(QWidget):
    """Cards in equal-width columns that follow the available width."""

    card_activated = Signal(object)
    card_context_requested = Signal(object, QPoint)
    selection_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        min_card_width: int = 224,
        gap: int = 14,
        selectable: bool = True,
        max_rows: int = 0,
        resizable: bool = False,
        compact: bool = False,
        configurable_clicks: bool = False,
    ):
        super().__init__(parent)
        # Clicks follow the Settings choices (Search / subscriptions / authors)
        # instead of "a click opens the post".
        self._configurable_clicks = configurable_clicks
        self._clicks = ClickDispatcher(self._perform_click, self) if configurable_clicks else None
        if resizable:
            # Follows the shared "card size" slider instead of a fixed width.
            min_card_width = saved_card_width()
            signal_bus.media_card_size_changed.connect(self.set_min_card_width)
        self._min_card_width = min_card_width
        self._gap = gap
        self._selectable = selectable
        self._max_rows = max(0, int(max_rows))
        self._compact = compact
        self._videos: list[SearchVideo] = []
        self._cards: list[MediaCard] = []
        self._covers = PixmapLRU(32 * 1024 * 1024)  # decoded covers; evicted ones are re-read from disk
        self._columns = 1
        self._fetcher = None
        self._stale_layout = False
        self._cursor = -1  # keyboard cursor: index into the shown cards
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._loading = False
        self._skeleton_phase = 0.0
        self._skeleton_timer = QTimer(self)
        self._skeleton_timer.setInterval(60)
        self._skeleton_timer.timeout.connect(self._tick_skeleton)
        self._probe: MediaCard | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def bind_fetcher(self, fetcher):
        """Let the grid request its own covers from a shared ``CoverFetcher``."""

        self._fetcher = fetcher
        fetcher.cover_ready.connect(self._on_fetched_cover)
        self._request_covers()

    def _request_covers(self):
        if self._fetcher is None:
            return
        self._fetcher.request([
            ("video", card.video.video_id, small_cover_url(card.video.thumbnail_url))
            for card in self._cards
            if card.video.thumbnail_url
        ])

    def _on_fetched_cover(self, kind: str, key: str, path: str):
        # A cover this grid already decoded is not read from disk again (a
        # rebuild re-announces every cached cover, once per grid on the page).
        if kind != "video" or key in self._covers or not any(card.video.video_id == key for card in self._cards):
            return
        pixmap = read_pixmap(path, 640)  # a cover is drawn ~300 px wide; never keep a huge original
        if not pixmap.isNull():
            self.set_cover(key, pixmap)

    # ── content ──────────────────────────────────────────────────────────────

    def set_max_rows(self, rows: int):
        rows = max(0, int(rows))
        if rows != self._max_rows:
            self._max_rows = rows
            self._rebuild()

    def set_min_card_width(self, width: int):
        width = max(CARD_WIDTH_MIN, min(CARD_WIDTH_MAX, int(width)))
        if width == self._min_card_width:
            return
        self._min_card_width = width
        if not self.isVisible():
            # Dragging the shared slider must not relayout every grid of every
            # hidden page; each catches up when it is shown again.
            self._stale_layout = True
            return
        self._apply_card_width()

    def _apply_card_width(self):
        self._stale_layout = False
        if self._max_rows:
            self._rebuild()  # the number of cards that fit changes
        else:
            self._layout_cards()

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale_layout:
            self._apply_card_width()

    def columns_for_width(self, width: int) -> int:
        return max(1, (max(1, width) + self._gap) // (self._min_card_width + self._gap))

    def visible_capacity(self) -> int:
        """How many items fit when ``max_rows`` is set (0 = unlimited)."""

        if not self._max_rows:
            return 0
        return self.columns_for_width(self.width() or self._min_card_width * 4) * self._max_rows

    def set_videos(self, videos: list[SearchVideo]):
        self._videos = list(videos)
        if videos:
            self._set_loading_state(False)
        self._rebuild()

    # ── loading placeholder ──────────────────────────────────────────────────

    def is_loading(self) -> bool:
        return self._loading

    def set_loading(self, loading: bool):
        """Show pulsing placeholder cards while an empty grid waits for data."""

        loading = bool(loading) and not self._cards
        if loading == self._loading:
            return
        self._set_loading_state(loading)
        self._layout_cards()
        self.update()

    def _set_loading_state(self, loading: bool):
        self._loading = loading
        if loading:
            self._skeleton_timer.start()
        else:
            self._skeleton_timer.stop()

    def _tick_skeleton(self):
        self._skeleton_phase = (self._skeleton_phase + 0.06) % 1.0
        self.update()

    def _placeholder_count(self, columns: int) -> int:
        return columns * (self._max_rows or 2)

    def _card_height(self, width: int) -> int:
        if self._probe is None:
            self._probe = MediaCard(SearchVideo(video_id="", title=""), self, selectable=False, compact=self._compact)
            self._probe.hide()
        return self._probe.height_for_width(width)

    def paintEvent(self, event):
        if not (self._loading and not self._cards and self.width() > 0):
            super().paintEvent(event)
            return
        import math

        p = palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        columns = self.columns_for_width(self.width())
        card_width = (self.width() - self._gap * (columns - 1)) // columns
        card_height = self._card_height(card_width)
        pulse = 0.5 + 0.5 * math.sin(self._skeleton_phase * 2 * math.pi)
        fill = to_qcolor(p.placeholder)
        fill.setAlphaF(0.55 + 0.45 * pulse)
        surface = to_qcolor(p.surface)
        border = to_qcolor(p.border)
        for index in range(self._placeholder_count(columns)):
            x = (index % columns) * (card_width + self._gap)
            y = (index // columns) * (card_height + self._gap)
            outer = QRectF(x + 0.5, y + 0.5, card_width - 1, card_height - 1)
            painter.setPen(QPen(border, 1))
            painter.setBrush(surface)
            painter.drawRoundedRect(outer, CARD_RADIUS, CARD_RADIUS)
            cover = QRectF(x + 1, y + 1, card_width - 2, round(card_width * COVER_RATIO) - 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(cover, CARD_RADIUS - 1, CARD_RADIUS - 1)
            bar_y = cover.bottom() + CARD_PAD + 2
            painter.drawRoundedRect(QRectF(x + CARD_PAD, bar_y, card_width * 0.78, 9), 4, 4)
            painter.drawRoundedRect(QRectF(x + CARD_PAD, bar_y + 17, card_width * 0.5, 8), 4, 4)

    def videos(self) -> list[SearchVideo]:
        return list(self._videos)

    def _perform_click(self, action: str, card):
        if card not in self._cards:
            return  # the card went away in the meantime
        if action == ACTION_SELECT:
            if self._selectable:
                self._set_cursor(self._cards.index(card), scroll=False)
                card.set_selected(not card.is_selected(), emit=True)
        elif action == ACTION_PLAY and play_in_window(card.video):
            pass
        elif action in (ACTION_DETAIL, ACTION_PLAY):
            self.card_activated.emit(card.video)

    def shown_videos(self) -> list[SearchVideo]:
        return [card.video for card in self._cards]

    def _rebuild(self):
        shown = self._videos
        capacity = self.visible_capacity()
        if capacity:
            shown = shown[:capacity]
        # Cards for videos that stay are reused as they are (a window resize or
        # the cover-size slider changes how many fit, not what they show), so
        # only the difference is created or destroyed.
        reusable = {id(card.video): card for card in self._cards}
        cards: list[MediaCard] = []
        for video in shown:
            card = reusable.pop(id(video), None)
            if card is None:
                card = MediaCard(video, self, selectable=self._selectable, compact=self._compact)
                card.activated.connect(self.card_activated)
                if self._clicks is not None:
                    card.configurable_clicks = True
                    card.plain_clicked.connect(self._clicks.click)
                    card.double_clicked.connect(self._clicks.double_click)
                card.context_requested.connect(self.card_context_requested)
                card.toggled.connect(lambda *_: self.selection_changed.emit())
                pixmap = self._covers.get(video.video_id)
                if pixmap is not None:
                    card.set_cover(pixmap)
            cards.append(card)
        for card in reusable.values():
            card.hide()
            card.deleteLater()
        self._cards = cards
        self._layout_cards()
        for card in cards:
            if card.isHidden():
                card.show()
        self._cursor = min(self._cursor, len(cards) - 1)
        self._paint_cursor()
        self._request_covers()
        self.selection_changed.emit()

    def set_cover(self, video_id: str, pixmap: QPixmap | None):
        if pixmap is None or pixmap.isNull():
            return
        self._covers[video_id] = pixmap
        for card in self._cards:
            if card.video.video_id == video_id:
                card.set_cover(pixmap)

    def selected_videos(self) -> list[SearchVideo]:
        return [card.video for card in self._cards if card.is_selected()]

    def select_all(self, selected: bool = True):
        for card in self._cards:
            card.set_selected(selected)
        self.selection_changed.emit()

    def clear_selection(self):
        self.select_all(False)

    def select_ids(self, video_ids: set[str]):
        """Re-select cards by video ID, e.g. after the same page was rebuilt."""

        for card in self._cards:
            card.set_selected(card.video.video_id in video_ids)
        self.selection_changed.emit()

    # ── layout ───────────────────────────────────────────────────────────────

    def _layout_cards(self):
        width = self.width()
        if width <= 0:
            return
        columns = self.columns_for_width(width)
        if self._max_rows and columns != self._columns and len(self._cards) != min(len(self._videos), columns * self._max_rows):
            # The window width changed enough to fit more or fewer cards.
            self._columns = columns
            self._rebuild()
            return
        self._columns = columns
        card_width = (width - self._gap * (columns - 1)) // columns
        y = 0
        row_height = 0
        for index, card in enumerate(self._cards):
            card.set_card_width(card_width)
            col = index % columns
            if col == 0 and index:
                y += row_height + self._gap
                row_height = 0
            card.move(col * (card_width + self._gap), y)
            row_height = max(row_height, card.height())
        total = y + row_height if self._cards else 0
        if self._loading and not self._cards:
            card_height = self._card_height(card_width)
            rows = self._placeholder_count(columns) // columns
            total = rows * card_height + (rows - 1) * self._gap
        if self.height() != total:
            self.setFixedHeight(total)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_cards()

    def sizeHint(self) -> QSize:
        return QSize(self._min_card_width, self.height())

    # ── keyboard ─────────────────────────────────────────────────────────────

    def cursor_index(self) -> int:
        return self._cursor

    def set_cursor(self, index: int):
        """Put the keyboard cursor back on a card (e.g. after returning to this list)."""

        if index < 0 or not self._cards:
            return
        self._set_cursor(index, scroll=False)

    def _set_cursor(self, index: int, *, scroll: bool = True):
        if not self._cards:
            self._cursor = -1
            return
        self._cursor = max(0, min(len(self._cards) - 1, index))
        self._paint_cursor()
        if scroll:
            self._reveal(self._cards[self._cursor])

    def _paint_cursor(self):
        for position, card in enumerate(self._cards):
            card.set_current(self.hasFocus() and position == self._cursor)

    def _reveal(self, card: QWidget):
        from PySide6.QtWidgets import QScrollArea

        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            parent.ensureWidgetVisible(card, 0, 24)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self._cards:
            self._set_cursor(self._cursor if self._cursor >= 0 else 0, scroll=False)
            self._reveal(self._cards[self._cursor])

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._paint_cursor()

    def event(self, event):
        # Esc / Ctrl+A would otherwise be taken by a page-level shortcut while the grid has them to use.
        if event.type() == QEvent.Type.ShortcutOverride and self._cards:
            key, mods = event.key(), event.modifiers()
            if key == Qt.Key.Key_Escape and self.selected_videos():
                event.accept()
                return True
            if key == Qt.Key.Key_A and mods == Qt.KeyboardModifier.ControlModifier and self._selectable:
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        count = len(self._cards)
        if not count:
            super().keyPressEvent(event)
            return
        key, mods = event.key(), event.modifiers()
        columns = max(1, self._columns)
        current = self._cursor if self._cursor >= 0 else 0
        moves = {
            Qt.Key.Key_Left: current - 1,
            Qt.Key.Key_Right: current + 1,
            Qt.Key.Key_Up: current - columns if current - columns >= 0 else current,
            Qt.Key.Key_Down: current + columns if current + columns < count else current,
            Qt.Key.Key_PageUp: max(0, current - columns * 3),
            Qt.Key.Key_PageDown: min(count - 1, current + columns * 3),
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: count - 1,
        }
        if key in moves and not mods & Qt.KeyboardModifier.ControlModifier:
            self._set_cursor(moves[key])
            event.accept()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.card_activated.emit(self._cards[current].video)
            event.accept()
        elif key == Qt.Key.Key_Space:
            card = self._cards[current]
            if self._selectable:
                self._set_cursor(current, scroll=False)
                card.set_selected(not card.is_selected(), emit=True)
            else:
                self.card_activated.emit(card.video)
            event.accept()
        elif key == Qt.Key.Key_A and mods == Qt.KeyboardModifier.ControlModifier and self._selectable:
            self.select_all(True)
            event.accept()
        elif key == Qt.Key.Key_Escape and self.selected_videos():
            self.clear_selection()
            event.accept()
        elif key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and mods == Qt.KeyboardModifier.ShiftModifier):
            card = self._cards[current]
            self.card_context_requested.emit(card.video, card.mapToGlobal(card.rect().center()))
            event.accept()
        else:
            super().keyPressEvent(event)


class CardSizeControl(QWidget):
    """Zoom slider for the poster grids; every grid follows it."""

    size_changed = Signal(int)  # the shared size changed (from this or any other slider)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        small = IconWidget(FluentIcon.ZOOM_OUT, self)
        small.setFixedSize(14, 14)
        big = IconWidget(FluentIcon.ZOOM_IN, self)
        big.setFixedSize(14, 14)
        self._slider = Slider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(CARD_WIDTH_MIN, CARD_WIDTH_MAX)
        self._slider.setSingleStep(8)
        self._slider.setPageStep(24)
        self._slider.setFixedWidth(130)
        self._slider.setValue(saved_card_width())
        # Re-laying out dozens of cards on every pixel of a drag is wasted work.
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(50)
        self._apply_timer.timeout.connect(self._apply)
        self._slider.valueChanged.connect(lambda _v: self._apply_timer.start())
        self._slider.sliderReleased.connect(app_config.sync)
        self.setToolTip(tr("Cover size", "封面大小", "カバーサイズ"))
        row.addWidget(small)
        row.addWidget(self._slider)
        row.addWidget(big)
        signal_bus.media_card_size_changed.connect(self._on_broadcast)

    def _apply(self):
        value = int(self._slider.value())
        app_config.set_ui_value(CARD_WIDTH_KEY, value, sync=False)
        signal_bus.media_card_size_changed.emit(value)

    def _on_broadcast(self, value: int):
        if self._slider.value() != value:
            self._slider.blockSignals(True)
            self._slider.setValue(int(value))
            self._slider.blockSignals(False)
        self.size_changed.emit(int(value))


def transparent_scroll_area(name: str, parent: QWidget | None = None):
    """A vertically scrolling Fluent area whose content sits on the page background."""

    from PySide6.QtWidgets import QFrame
    from qfluentwidgets import ScrollArea

    area = ScrollArea(parent)
    area.setObjectName(name)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setStyleSheet(
        f"QScrollArea#{name} {{ background: transparent; border: none; }}"
        f"QScrollArea#{name} > QWidget > QWidget {{ background: transparent; }}"
    )
    return area
