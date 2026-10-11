"""Grid and cover views of the download history, with covers loaded as they scroll into view."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QIcon, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QWidget

from ...i18n import tr
from ..card_list import CardGridList
from ..media.pixmaps import PixmapLRU, read_pixmap
from ..subscription.cover_loader import CoverLoader
from ...signal_bus import signal_bus
from ..media.card_size import saved_card_width
from ..search.grid_style import _search_grid_style
from ..theme import qcolor
from .page_dependencies import download_manager
from .record_state import _date_only


MODE_GRID = "grid"  # compact tiles with title, author, date and watch state
MODE_COVER = "cover"  # big covers with only the title

# mode -> (card width relative to the shared cover-size slider, caption lines);
# a grid title may take two lines.
_MODE_LAYOUT = {MODE_GRID: (1.0, 4), MODE_COVER: (1.6, 1)}
_SPACING = 12  # as on the search page
_CARD_CHROME = 12 + 4  # the card style's padding (6 px a side) and its 2 px selected border
_MAX_COLUMNS = 12


def _ellipsize(value: str, limit: int) -> str:
    value = str(value or "")
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


class HistoryCoverGrid(CardGridList):
    """Downloaded videos as cards; the page keeps filtering, sorting and actions."""

    activated = Signal(str)  # video id, on double-click
    context_requested = Signal(QPoint)  # position in the viewport

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._mode = MODE_GRID
        self._records: list[dict[str, Any]] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._loaded: set[str] = set()  # ids whose item already shows its real cover
        self._paths: dict[str, str] = {}  # id -> local cover file
        self._pixmaps = PixmapLRU(48 * 1024 * 1024)
        self._relayout_pending = False
        self._text_width = 200  # caption width of one card, set by _relayout
        self._card_width = saved_card_width()  # follows the cover-size slider of every grid
        self._stale = False  # the card size changed while hidden
        self.covers = CoverLoader(self)
        self.covers.cover_ready.connect(self._on_cover_ready)
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(120)
        self._cover_timer.timeout.connect(self._load_visible_covers)
        self._configure()

    def _configure(self):
        self.setObjectName("HistoryCoverGrid")
        self.setStyleSheet(_search_grid_style())  # the same cards as the search results
        self.viewport().setAutoFillBackground(False)
        signal_bus.media_card_size_changed.connect(self.set_card_width)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setSpacing(_SPACING)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_requested)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.verticalScrollBar().valueChanged.connect(lambda _value: self._cover_timer.start())

    # ── content ──────────────────────────────────────────────────────────────

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str):
        mode = mode if mode in _MODE_LAYOUT else MODE_GRID
        if mode == self._mode:
            return
        self._mode = mode
        self.set_records(self._records)  # captions differ between the modes

    def set_card_width(self, width: int):
        width = int(width)
        if width == self._card_width:
            return
        self._card_width = width
        if self.isVisible():
            self.set_records(self._records)  # captions are elided to the new width
        else:
            self._stale = True

    def set_records(self, records: list[dict[str, Any]], *, file_ok=None):
        """Show ``records`` in order; ``file_ok(record)`` says whether its file is in place."""

        self._stale = False
        selected = set(self.selected_video_ids())
        self._records = list(records)
        if file_ok is not None:
            self._file_ok = file_ok
        self._relayout()
        placeholder = self._placeholder_icon()
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            self._items.clear()
            self._loaded.clear()
            for record in self._records:
                video_id = str(record.get("video_id", "") or "")
                if not video_id or video_id in self._items:
                    continue
                item = QListWidgetItem(placeholder, self._caption(record))
                item.setData(Qt.ItemDataRole.UserRole, video_id)
                item.setToolTip(self._tooltip(record))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                self.addItem(item)
                self._items[video_id] = item
                if video_id in selected:
                    item.setSelected(True)
        finally:
            self.setUpdatesEnabled(True)
        self._cover_timer.start()

    def _file_ok(self, record: dict[str, Any]) -> bool:
        path = str(record.get("file_path", "") or "")
        return bool(path) and os.path.isfile(path)

    def _caption(self, record: dict[str, Any]) -> str:
        title = str(record.get("title", "") or record.get("video_id", ""))
        metrics = QFontMetrics(self.font())
        if self._mode == MODE_COVER:
            return metrics.elidedText(title, Qt.TextElideMode.ElideRight, self._text_width)
        # Two lines' worth: Qt wraps it, and the details below stay in view.
        title = metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(self._text_width * 1.8))
        author = _ellipsize(str(record.get("author", "") or ""), 26)
        downloaded = _date_only(str(record.get("downloaded_at", "") or ""))
        details = "  ·  ".join(part for part in (author, downloaded) if part)
        if not self._file_ok(record):
            state = tr("Moved", "已移走", "移動済み")
        elif str(record.get("watched_at", "") or ""):
            state = tr("Watched", "已观看", "視聴済み")
        else:
            state = tr("● Unwatched", "● 未观看", "● 未視聴")
        return f"{title}\n{details}\n{state}"

    @staticmethod
    def _tooltip(record: dict[str, Any]) -> str:
        title = str(record.get("title", "") or record.get("video_id", ""))
        author = str(record.get("author", "") or "")
        downloaded = str(record.get("downloaded_at", "") or "")
        watched = str(record.get("watched_at", "") or "") or tr("never", "从未", "なし")
        return tr(
            f"{title}\nAuthor: {author}\nDownloaded: {downloaded}\nLast watched: {watched}\nDouble-click to play",
            f"{title}\n作者：{author}\n下载时间：{downloaded}\n上次观看：{watched}\n双击播放",
            f"{title}\n作者: {author}\n保存日時: {downloaded}\n最終視聴: {watched}\nダブルクリックで再生",
        )

    def selected_video_ids(self) -> list[str]:
        ids = [str(item.data(Qt.ItemDataRole.UserRole) or "") for item in self.selectedItems()]
        return [video_id for video_id in ids if video_id]

    def select_at(self, position: QPoint) -> bool:
        """Right-click on an unselected card selects just that card; False if none is there."""

        item = self.itemAt(position)
        if item is None:
            return False
        if not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item)
        return True

    def _on_double_clicked(self, item: QListWidgetItem):
        video_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if video_id:
            self.activated.emit(video_id)

    # ── layout ───────────────────────────────────────────────────────────────

    def _relayout(self):
        width = self.viewport().width()
        if width <= 0:
            return
        scale, lines = _MODE_LAYOUT[self._mode]
        min_width = round(self._card_width * scale)
        reserve = max(8, int(self.verticalScrollBar().sizeHint().width()) - 1)
        available = max(1, width - reserve)
        columns = max(1, min(_MAX_COLUMNS, (available + _SPACING) // (min_width + _SPACING)))
        card_width = max(40, (available - _SPACING * (columns - 1)) // columns)
        self._text_width = max(40, card_width - _CARD_CHROME - 4)
        icon_width = max(32, card_width - _CARD_CHROME)
        icon = QSize(icon_width, max(32, round(icon_width * 9 / 16)))
        text_height = QFontMetrics(self.font()).lineSpacing() * lines
        grid = QSize(card_width, icon.height() + text_height + _CARD_CHROME + 10)
        if icon != self.iconSize():
            self.setIconSize(icon)
            # Covers and placeholders were drawn for the old size (QIcon never scales up).
            self._loaded.clear()
            placeholder = self._placeholder_icon()
            for item in self._items.values():
                item.setIcon(placeholder)
        if grid != self.gridSize():
            self.setGridSize(grid)
            self.doItemsLayout()
        self._cover_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._relayout_pending:
            self._relayout_pending = True
            QTimer.singleShot(0, self._run_relayout)

    def _run_relayout(self):
        self._relayout_pending = False
        self._relayout()

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale:
            self.set_records(self._records)
        else:
            self._relayout()

    # ── covers ───────────────────────────────────────────────────────────────

    def _visible_ids(self) -> list[str]:
        area = self.viewport().rect().adjusted(0, -200, 0, 400)
        visible = []
        for video_id, item in self._items.items():
            if area.intersects(self.visualItemRect(item)):
                visible.append(video_id)
        return visible

    def _load_visible_covers(self):
        if not self.isVisible():
            return
        missing: list[tuple[str, str]] = []
        for video_id in self._visible_ids():
            if video_id in self._loaded:
                continue
            path = self._paths.get(video_id) or self._local_cover(video_id)
            if path:
                self._show_cover(video_id, path)
            else:
                missing.append((video_id, ""))
        if missing:
            self.covers.request(missing, urgent=True)

    def _local_cover(self, video_id: str) -> str:
        try:
            path = str(download_manager.subscription_cover_path(video_id) or "")
        except Exception:
            path = ""
        if path:
            self._paths[video_id] = path
        return path

    def _on_cover_ready(self, video_id: str, path: str):
        if path:
            self._paths[video_id] = path
            self._show_cover(video_id, path)

    def _show_cover(self, video_id: str, path: str):
        item = self._items.get(video_id)
        if item is None or not os.path.isfile(path):
            return
        size = self.iconSize()
        pixmap = self._pixmaps.get(path)
        want = max(240, round(size.width() * 1.25))
        if pixmap is None or pixmap.width() < min(want, 640):
            pixmap = read_pixmap(path, want)
            if pixmap.isNull():
                return
            self._pixmaps[path] = pixmap
        scaled = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        x = max(0, (scaled.width() - size.width()) // 2)
        y = max(0, (scaled.height() - size.height()) // 2)
        item.setIcon(QIcon(scaled.copy(QRect(x, y, size.width(), size.height()))))
        self._loaded.add(video_id)

    def _placeholder_icon(self) -> QIcon:
        pixmap = QPixmap(self.iconSize() if not self.iconSize().isEmpty() else QSize(208, 117))
        pixmap.fill(qcolor("placeholder"))
        return QIcon(pixmap)

    def refresh_theme(self):
        self.setStyleSheet(_search_grid_style())
        self.set_records(self._records)

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return self.covers.shutdown(timeout_ms)
