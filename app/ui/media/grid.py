"""Responsive, keyboard-navigable grid of media cards."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.search.models import SearchVideo
from ...core.search.normalize import small_cover_url
from ...signal_bus import signal_bus
from ..click_dispatch import (
    ACTION_DETAIL,
    ACTION_PLAY,
    ACTION_SELECT,
    ClickDispatcher,
    play_in_window,
)
from ..theme import palette, to_qcolor
from .card import CARD_PAD, CARD_RADIUS, COVER_RATIO, MediaCard
from .card_size import CARD_WIDTH_MAX, CARD_WIDTH_MIN, saved_card_width
from .pixmaps import PixmapLRU, read_pixmap


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
        # Cocoa can focus an empty grid before its data arrives; no second
        # focusInEvent follows when the first cards are populated.
        if cards and self.hasFocus() and self._cursor < 0:
            self._cursor = 0
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
