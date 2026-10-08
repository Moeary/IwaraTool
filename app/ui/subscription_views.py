"""Overview list and per-source grid for the Subscriptions page.

The overview is the page's front door: one large row per author or playlist
(avatar, counters and the latest videos as a cover strip).  Opening a row shows
that source as a poster grid, the same way search results look.  The original
table view stays available from the page header; every download, refresh and
delete here goes through the page so both views behave the same.
"""
from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SubtitleLabel,
    ToolButton,
)

from ..config import app_config
from ..core.search import SearchVideo
from ..i18n import tr
from ..signal_bus import signal_bus
from .author_status import AuthorStatusBar
from .chrome import EmptyState, StatusChip
from .media_card import (
    CardSizeControl,
    MediaCard,
    MediaGrid,
    saved_card_width,
    transparent_scroll_area,
)
from .rules_page import RulePicker
from .subscription_components import SubscriptionThumbnailWorker
from .subscription_helpers import (
    _date_only,
    _item_date_key,
    _source_origin_label,
    _source_search_text,
    _source_sort_key,
    _source_sort_label,
    _source_type_label,
    _title_matcher,
)
from .theme import PAGE_SPACING, set_secondary_text, summary_text
from .ui_state import ResponsiveFlowLayout
from .worker_lifecycle import stop_qthreads

RECENT_PER_SOURCE = 8  # cover strip length
GRID_PAGE_SIZE = 60
OVERVIEW_SORT_FIELDS = (
    "title",
    "new_count",
    "undownloaded_count",
    "item_count",
    "last_checked_at",
    "created_at",
)
STATE_FILTERS = ("all", "ready", "new", "downloaded", "moved", "queued", "unavailable")
ITEM_SORTS = ("date_desc", "date_asc", "new_first", "title")


# ── pure helpers (also used by the tests) ────────────────────────────────────


def is_feed_source(source: dict[str, Any]) -> bool:
    """The old "account feed" source; the Home page replaced it."""

    return str(source.get("source_type", "") or "") == "feed"


def item_state(item: dict[str, Any]) -> str:
    """One word for what the user cares about: unavailable/downloaded/moved/queued/new/''."""

    if str(item.get("download_state", "") or ""):
        return "unavailable"
    if item.get("downloaded"):
        return "downloaded" if item.get("download_file_exists") else "moved"
    if item.get("queued"):
        return "queued"
    if int(item.get("is_new", 0) or 0):
        return "new"
    return ""


def item_to_video(item: dict[str, Any], *, author_name: str = "") -> SearchVideo:
    """A poster-card record for a stored subscription item."""

    video_id = str(item.get("video_id", "") or "")
    author = str(item.get("author", "") or "")
    return SearchVideo(
        video_id=video_id,
        title=str(item.get("title", "") or video_id),
        author_username=author,
        author_name=author_name or author,
        published_at=str(item.get("published_at", "") or ""),
        thumbnail_url=str(item.get("thumbnail_url", "") or ""),
        source_url=str(item.get("source_url", "") or "") or f"https://www.iwara.tv/video/{video_id}",
        raw={"_state": item_state(item), "_thumbnail_path": str(item.get("thumbnail_path", "") or "")},
    )


def filter_items(items: list[dict[str, Any]], mode: str, query: str = "") -> list[dict[str, Any]]:
    result = list(items)
    query = str(query or "").strip()
    if query:
        matcher = _title_matcher(query, regex_mode=False)
        result = [i for i in result if matcher(str(i.get("title", "") or i.get("video_id", "") or ""))]
    if mode == "ready":
        result = [i for i in result if item_state(i) in {"", "new"}]
    elif mode in {"new", "downloaded", "moved", "queued", "unavailable"}:
        result = [i for i in result if item_state(i) == mode]
    return result


def sort_items(items: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    result = list(items)
    if mode == "date_asc":
        result.sort(key=_item_date_key)
    elif mode == "title":
        result.sort(key=lambda i: str(i.get("title", "") or i.get("video_id", "") or "").casefold())
    else:
        result.sort(key=_item_date_key, reverse=True)
        if mode == "new_first":
            result.sort(key=lambda i: 0 if int(i.get("is_new", 0) or 0) else 1)
    return result


def downloadable_ids(items: list[dict[str, Any]], *, only_new: bool = False) -> list[str]:
    """Ids still worth queueing: not on disk, not queued, not blocked."""

    ids: list[str] = []
    for item in items:
        state = item_state(item)
        if state in {"downloaded", "queued", "unavailable"}:
            continue
        if only_new and state != "new":
            continue
        video_id = str(item.get("video_id", "") or "")
        if video_id and video_id not in ids:
            ids.append(video_id)
    return ids


def load_pixmap(path: str, max_width: int = 360) -> QPixmap | None:
    """A cover from disk, scaled down so hundreds of tiles stay light."""

    if not path or not os.path.isfile(path):
        return None
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    if pixmap.width() > max_width:
        pixmap = pixmap.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    return pixmap


# ── cover loading ────────────────────────────────────────────────────────────


class CoverLoader(QObject):
    """Resolves and caches subscription covers, one small batch at a time.

    Requests made for what the user is looking at *now* (``urgent``: the grid of
    the opened subscription) jump ahead of the background ones (the overview's
    cover strips), so opening a subscription never waits behind hundreds of
    covers for other rows.  A cover that failed to download is forgotten and is
    asked for again the next time something requests it.
    """

    cover_ready = Signal(str, str)  # video id, local path
    queue_changed = Signal()  # something was queued, finished or dropped

    BATCH = 16

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: SubscriptionThumbnailWorker | None = None
        self._queue: list[tuple[str, str, bool]] = []  # video id, url, urgent
        self._requested: set[str] = set()
        self._batch: list[str] = []
        self._delivered: set[str] = set()

    def request(self, requests: list[tuple[str, str]], *, urgent: bool = False):
        fresh: list[tuple[str, str, bool]] = []
        for video_id, url in requests:
            if not video_id:
                continue
            if video_id in self._requested:
                if urgent:
                    # Already waiting behind background work: move it up.
                    for index, (queued_id, queued_url, queued_urgent) in enumerate(self._queue):
                        if queued_id == video_id and not queued_urgent:
                            self._queue[index] = (queued_id, queued_url, True)
                            fresh.append(self._queue.pop(index))
                            break
                continue
            self._requested.add(video_id)
            fresh.append((video_id, url, urgent))
        if urgent:
            self._queue = fresh + self._queue
        else:
            self._queue.extend(fresh)
        self._pump()
        self.queue_changed.emit()

    def _make_worker(self, batch: list[tuple[str, str]]) -> SubscriptionThumbnailWorker:
        try:
            concurrency = max(1, min(8, int(app_config.get_ui_value("cover_download_workers_v1", 6) or 6)))
        except (TypeError, ValueError):
            concurrency = 6
        return SubscriptionThumbnailWorker(batch, concurrency=concurrency)

    def _pump(self):
        if self._worker is not None or not self._queue:
            return
        batch, self._queue = self._queue[: self.BATCH], self._queue[self.BATCH:]
        self._batch = [video_id for video_id, _url, _urgent in batch]
        self._delivered = set()
        worker = self._make_worker([(video_id, url) for video_id, url, _urgent in batch])
        self._worker = worker
        worker.thumbnail_ready.connect(self._on_ready)
        worker.finished.connect(lambda worker=worker: self._on_finished(worker))
        worker.start()

    def _on_ready(self, video_id: str, path: str):
        self._delivered.add(video_id)
        self.cover_ready.emit(video_id, path)

    def _on_finished(self, worker):
        if self._worker is worker:
            self._worker = None
            # Failures are forgotten so a later look at the same covers retries.
            self._requested.difference_update(v for v in self._batch if v not in self._delivered)
            self._batch = []
        worker.deleteLater()
        self._pump()
        self.queue_changed.emit()

    def pending(self) -> int:
        """Covers queued or downloading right now."""

        return len(self._queue) + len(self._batch)

    def drop_background(self):
        """Forget queued non-urgent requests (the overview was left)."""

        kept = [entry for entry in self._queue if entry[2]]
        dropped = [video_id for video_id, _url, urgent in self._queue if not urgent]
        self._queue = kept
        self._requested.difference_update(dropped)
        if dropped:
            self.queue_changed.emit()

    def forget(self, video_ids: list[str]):
        """Allow these covers to be requested again (e.g. after a manual refresh)."""

        self._requested.difference_update(video_ids)

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        self._queue.clear()
        worker, self._worker = self._worker, None
        if worker is None:
            return True
        worker.requestInterruption()
        return stop_qthreads([worker], timeout_ms=timeout_ms)


# ── overview ─────────────────────────────────────────────────────────────────


class TileStrip(QWidget):
    """The latest videos of one source as a single row of compact covers."""

    video_activated = Signal(object)
    video_context = Signal(object, QPoint)

    GAP = 12

    def __init__(self, videos: list[SearchVideo], tile_min_width: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._tile_min = tile_min_width
        self.cards: list[MediaCard] = []
        for video in videos:
            card = MediaCard(video, self, selectable=False, compact=True)
            card.activated.connect(self.video_activated)
            card.context_requested.connect(self.video_context)
            self.cards.append(card)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._relayout(max(self.width(), self._tile_min * 3))

    def set_tile_min_width(self, width: int):
        self._tile_min = max(120, int(width))
        self._relayout(self.width())

    def shown_cards(self) -> list[MediaCard]:
        return [card for card in self.cards if not card.isHidden()]

    def _relayout(self, width: int):
        if not self.cards or width <= 0:
            return
        columns = max(1, (width + self.GAP) // (self._tile_min + self.GAP))
        tile = max(120, (width - self.GAP * (columns - 1)) // columns)
        for index, card in enumerate(self.cards):
            if index < columns:
                card.set_card_width(tile)
                card.move(index * (tile + self.GAP), 0)
                card.show()
            else:
                card.hide()
        self.setFixedHeight(self.cards[0].height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(event.size().width())


class SourceRow(CardWidget):
    """One subscription: who it is, how it stands, and its latest videos."""

    open_requested = Signal(int)
    refresh_requested = Signal(int)
    menu_requested = Signal(int, QPoint)
    video_activated = Signal(object)
    video_context = Signal(object, QPoint)

    def __init__(
        self,
        source: dict[str, Any],
        items: list[dict[str, Any]],
        tile_min_width: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.source = source
        self.source_id = int(source.get("id", 0) or 0)
        self.setBorderRadius(10)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.menu_requested.emit(self.source_id, self.mapToGlobal(pos))
        )
        self.clicked.connect(lambda: self.open_requested.emit(self.source_id))

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        self.avatar = AvatarWidget(self)
        self.avatar.setRadius(28)
        title = str(source.get("title", "") or source.get("source_key", "") or "?")
        self.avatar.setText(title[:1].upper())
        head.addWidget(self.avatar)

        names = QVBoxLayout()
        names.setSpacing(2)
        is_author = str(source.get("source_type", "") or "") == "author"
        name = SubtitleLabel(title, self)
        names.addWidget(name)
        key = str(source.get("source_key", "") or "")
        kind = _source_type_label(str(source.get("source_type", "") or ""))
        enabled = bool(int(source.get("enabled", 1) or 0))
        sub = f"@{key}" if is_author else key
        sub = f"{sub}  ·  {kind}" if sub else kind
        if not enabled:
            sub += "  ·  " + tr("Paused", "已停用", "停止中")
        sub_label = CaptionLabel(sub, self)
        set_secondary_text(sub_label)
        sub_label.setToolTip(_source_origin_label(source))
        names.addWidget(sub_label)
        stats = CaptionLabel(self._stats_text(source), self)
        set_secondary_text(stats)
        names.addWidget(stats)
        head.addLayout(names, 1)

        new_count = int(source.get("new_count", 0) or 0)
        if new_count:
            head.addWidget(StatusChip(tr(f"{new_count} new", f"{new_count} 个新增", f"新着 {new_count}"), "accent", self))
        if not enabled:
            head.addWidget(StatusChip(tr("Paused", "已停用", "停止中"), "warning", self))

        refresh = ToolButton(FluentIcon.SYNC, self)
        refresh.setToolTip(tr("Refresh this source", "刷新此订阅源", "この購読元を更新"))
        refresh.clicked.connect(lambda: self.refresh_requested.emit(self.source_id))
        head.addWidget(refresh)
        open_btn = PushButton(tr("View all", "查看全部", "すべて表示"), self, FluentIcon.VIEW)
        open_btn.clicked.connect(lambda: self.open_requested.emit(self.source_id))
        head.addWidget(open_btn)
        more = ToolButton(FluentIcon.MORE, self)
        more.clicked.connect(lambda: self.menu_requested.emit(self.source_id, more.mapToGlobal(more.rect().bottomLeft())))
        head.addWidget(more)
        root.addLayout(head)

        videos = [item_to_video(item, author_name=title if is_author else "") for item in items]
        self.strip: TileStrip | None = None
        if videos:
            self.strip = TileStrip(videos, tile_min_width, self)
            self.strip.video_activated.connect(self.video_activated)
            self.strip.video_context.connect(self.video_context)
            root.addWidget(self.strip)
        else:
            empty = CaptionLabel(
                tr("No videos yet — refresh this source.", "还没有视频，刷新一下订阅源吧。", "動画はまだありません。更新してください。"),
                self,
            )
            set_secondary_text(empty)
            root.addWidget(empty)

    @staticmethod
    def _stats_text(source: dict[str, Any]) -> str:
        parts = [
            tr(
                f"{int(source.get('item_count', 0) or 0)} videos",
                f"共 {int(source.get('item_count', 0) or 0)} 个视频",
                f"{int(source.get('item_count', 0) or 0)} 本",
            ),
            tr(
                f"{int(source.get('undownloaded_count', 0) or 0)} not downloaded",
                f"{int(source.get('undownloaded_count', 0) or 0)} 个未下载",
                f"未保存 {int(source.get('undownloaded_count', 0) or 0)}",
            ),
        ]
        checked = _date_only(str(source.get("last_checked_at", "") or ""))
        if checked:
            parts.append(tr(f"checked {checked}", f"刷新于 {checked}", f"{checked} 更新"))
        return "  ·  ".join(parts)

    def set_avatar(self, path: str):
        pixmap = load_pixmap(path, 160)
        if pixmap is not None:
            self.avatar.setImage(pixmap)

    def set_tile_min_width(self, width: int):
        if self.strip is not None:
            self.strip.set_tile_min_width(width)


class SubscriptionOverview(QWidget):
    """Large rows, one per subscription, with a toolbar for finding and adding."""

    source_opened = Signal(int)

    def __init__(self, page, covers: CoverLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self._page = page
        self._covers = covers
        self._rows: dict[int, SourceRow] = {}
        self._pixmaps: dict[str, QPixmap] = {}
        self._dirty = True
        self._tile_min = int(saved_card_width() * 0.8)
        field = str(app_config.get_ui_value("subscription_overview_sort_v1", "title") or "title")
        self._sort_field = field if field in OVERVIEW_SORT_FIELDS else "title"
        self._sort_desc = str(app_config.get_ui_value("subscription_overview_desc_v1", "0")) in {"1", "true", "True"}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(PAGE_SPACING)

        bar = ResponsiveFlowLayout(spacing=8)
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(tr("Filter authors and playlists…", "筛选作者 / 播放列表…", "作者・リストを絞り込み…"))
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(lambda _text: self._filter_timer.start())
        bar.addWidget(self._search)

        self._type_combo = ComboBox(self)
        for label, data in (
            (tr("All", "全部", "すべて"), ""),
            (tr("Authors", "作者", "作者"), "author"),
            (tr("Playlists", "播放列表", "プレイリスト"), "playlist"),
        ):
            self._type_combo.addItem(label)
            self._type_combo.setItemData(self._type_combo.count() - 1, data)
        self._type_combo.setFixedWidth(116)
        self._type_combo.currentIndexChanged.connect(lambda _i: self.reload())
        bar.addWidget(self._type_combo)

        self._sort_combo = ComboBox(self)
        for field_name in OVERVIEW_SORT_FIELDS:
            self._sort_combo.addItem(_source_sort_label(field_name))
            self._sort_combo.setItemData(self._sort_combo.count() - 1, field_name)
        self._sort_combo.setCurrentIndex(OVERVIEW_SORT_FIELDS.index(self._sort_field))
        self._sort_combo.setFixedWidth(132)
        self._sort_combo.setToolTip(tr("Sort by", "排序字段", "並び替え項目"))
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        bar.addWidget(self._sort_combo)
        self._sort_dir = ToolButton(self)
        self._sort_dir.clicked.connect(self._toggle_direction)
        bar.addWidget(self._sort_dir)
        self._sync_direction_button()

        bar.addWidget(CardSizeControl(self))

        add_btn = PrimaryPushButton(tr("Add author / playlist", "添加作者 / 播放列表", "作者/リストを追加"), self, FluentIcon.ADD)
        add_btn.clicked.connect(page._add_source)
        bar.addWidget(add_btn)
        import_btn = PushButton(tr("Import followed", "导入关注作者", "フォローを取込"), self, FluentIcon.PEOPLE)
        import_btn.clicked.connect(page._import_followed_authors)
        bar.addWidget(import_btn)
        self.refresh_all_btn = PushButton(tr("Refresh all", "全部刷新", "全件更新"), self, FluentIcon.SYNC)
        self.refresh_all_btn.clicked.connect(page._refresh_all)
        bar.addWidget(self.refresh_all_btn)
        root.addLayout(bar)

        self._summary = CaptionLabel("", self)
        set_secondary_text(self._summary)
        root.addWidget(self._summary)

        self._scroll = transparent_scroll_area("SubscriptionOverviewScroll", self)
        holder = QWidget()
        self._column = QVBoxLayout(holder)
        self._column.setContentsMargins(0, 0, 8, 24)
        self._column.setSpacing(12)
        self._column.addStretch(1)
        self._scroll.setWidget(holder)
        root.addWidget(self._scroll, 1)

        self._empty = EmptyState(
            FluentIcon.PEOPLE, "", "", self, action_text=tr("Add author / playlist", "添加作者 / 播放列表", "作者/リストを追加"),
        )
        self._empty.action_clicked.connect(page._add_source)
        root.addWidget(self._empty, 1)
        self._empty.hide()

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self.reload)
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(120)
        self._cover_timer.timeout.connect(self._request_visible_covers)
        self._scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._cover_timer.start())

        covers.cover_ready.connect(self._on_cover_ready)
        signal_bus.media_card_size_changed.connect(self._on_card_size)

    # ── data ─────────────────────────────────────────────────────────────────

    def mark_dirty(self):
        self._dirty = True

    def reload_if_dirty(self):
        if self._dirty:
            self.reload()

    def visible_sources(self) -> list[dict[str, Any]]:
        sources = [s for s in self._page._all_sources if not is_feed_source(s)]
        wanted = str(self._type_combo.currentData() or "")
        if wanted:
            sources = [s for s in sources if str(s.get("source_type", "") or "") == wanted]
        query = self._search.text().strip().casefold()
        if query:
            sources = [s for s in sources if query in _source_search_text(s)]
        sources.sort(key=lambda s: _source_sort_key(s, self._sort_field), reverse=self._sort_desc)
        return sources

    def reload(self):
        self._dirty = False
        sources = self.visible_sources()
        recent = self._page._recent_items(RECENT_PER_SOURCE)
        scroll_value = self._scroll.verticalScrollBar().value()
        for row in self._rows.values():
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._rows = {}
        for source in sources:
            source_id = int(source.get("id", 0) or 0)
            row = SourceRow(source, recent.get(source_id, []), self._tile_min, self)
            row.open_requested.connect(self.source_opened)
            row.refresh_requested.connect(self._page._refresh_source_ids_from_view)
            row.menu_requested.connect(self._page._show_source_menu)
            row.video_activated.connect(self._page._show_video_detail)
            row.video_context.connect(self._page._show_video_menu)
            avatar = str(source.get("avatar_path", "") or "")
            if avatar:
                row.set_avatar(avatar)
            self._column.insertWidget(self._column.count() - 1, row)
            self._rows[source_id] = row
        total_new = sum(int(s.get("new_count", 0) or 0) for s in sources)
        total_missing = sum(int(s.get("undownloaded_count", 0) or 0) for s in sources)
        self._summary.setText(
            summary_text([
                (tr("Subscriptions", "订阅源", "購読元"), len(sources)),
                (tr("New", "新增", "新規"), total_new),
                (tr("Missing", "未下载", "未保存"), total_missing),
            ])
        )
        has_any = bool(self._page._all_sources) and any(not is_feed_source(s) for s in self._page._all_sources)
        self._scroll.setVisible(bool(sources))
        self._empty.setVisible(not sources)
        if not sources:
            if has_any:
                self._empty.set_text(tr("No match", "没有符合条件的订阅源", "該当する購読元がありません"))
            else:
                self._empty.set_text(
                    tr("No subscriptions yet", "还没有订阅", "購読はまだありません"),
                    tr(
                        "Add an author or playlist, or subscribe from a video's page or an author page.",
                        "添加作者或播放列表，也可以在视频详情页或作者页里订阅作者。",
                        "作者やプレイリストを追加するか、動画ページ・作者ページから購読できます。",
                    ),
                )
            if self._empty.button is not None:
                self._empty.button.setVisible(not has_any)
        QTimer.singleShot(0, lambda: self._restore_scroll(scroll_value))
        self._cover_timer.start()

    def _restore_scroll(self, value: int):
        try:
            self._scroll.verticalScrollBar().setValue(value)
        except RuntimeError:
            pass  # the page was closed before the deferred call ran

    def set_avatar(self, source_id: int, path: str):
        row = self._rows.get(int(source_id))
        if row is not None:
            row.set_avatar(path)

    # ── toolbar ──────────────────────────────────────────────────────────────

    def _on_sort_changed(self, index: int):
        field = str(self._sort_combo.itemData(index) or "title")
        self._sort_field = field if field in OVERVIEW_SORT_FIELDS else "title"
        app_config.set_ui_value("subscription_overview_sort_v1", self._sort_field)
        self.reload()

    def _toggle_direction(self):
        self._sort_desc = not self._sort_desc
        app_config.set_ui_value("subscription_overview_desc_v1", "1" if self._sort_desc else "0")
        self._sync_direction_button()
        self.reload()

    def _sync_direction_button(self):
        self._sort_dir.setIcon(FluentIcon.DOWN if self._sort_desc else FluentIcon.UP)
        self._sort_dir.setToolTip(
            tr("Descending order", "倒序排列", "降順") if self._sort_desc else tr("Ascending order", "正序排列", "昇順")
        )

    def _on_card_size(self, width: int):
        self._tile_min = max(120, int(width * 0.8))
        for row in self._rows.values():
            row.set_tile_min_width(self._tile_min)
        self._cover_timer.start()

    # ── covers ───────────────────────────────────────────────────────────────

    def _start_cover_requests(self, requests: list[tuple[str, str]]):
        self._covers.request(requests)

    def _request_visible_covers(self):
        if not self.isVisible():
            return
        bar = self._scroll.verticalScrollBar()
        top = bar.value() - 400
        bottom = bar.value() + self._scroll.viewport().height() + 400
        requests: list[tuple[str, str]] = []
        for row in self._rows.values():
            if row.y() + row.height() < top or row.y() > bottom or row.strip is None:
                continue
            for card in row.strip.shown_cards():
                video = card.video
                cached = self._pixmaps.get(video.video_id)
                if cached is None:
                    cached = load_pixmap(str(video.raw.get("_thumbnail_path", "") or ""))
                    if cached is not None:
                        self._pixmaps[video.video_id] = cached
                if cached is not None:
                    card.set_cover(cached)
                elif video.video_id:
                    requests.append((video.video_id, video.thumbnail_url))
        if requests:
            self._start_cover_requests(requests)

    def _on_cover_ready(self, video_id: str, path: str):
        pixmap = load_pixmap(path)
        if pixmap is None:
            return
        self._pixmaps[video_id] = pixmap
        for row in self._rows.values():
            if row.strip is None:
                continue
            for card in row.strip.cards:
                if card.video.video_id == video_id:
                    card.set_cover(pixmap)

    def showEvent(self, event):
        super().showEvent(event)
        self.reload_if_dirty()
        self._cover_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        # Whatever the user opens next matters more than these strips.
        self._covers.drop_background()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cover_timer.start()


# ── one source as a grid ─────────────────────────────────────────────────────


class SourceItemsView(QWidget):
    """A subscription's videos as selectable posters, with the download tools."""

    back_requested = Signal()

    def __init__(self, page, covers: CoverLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self._page = page
        self._covers = covers
        self._source: dict[str, Any] = {}
        self._items: list[dict[str, Any]] = []
        self._filtered: list[dict[str, Any]] = []
        self._pixmaps: dict[str, QPixmap] = {}
        self._page_index = 0
        self._stale = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(PAGE_SPACING)

        head = QHBoxLayout()
        head.setSpacing(12)
        back = PushButton(tr("Back", "返回", "戻る"), self, FluentIcon.LEFT_ARROW)
        back.clicked.connect(self.back_requested)
        head.addWidget(back)
        self._avatar = AvatarWidget(self)
        self._avatar.setRadius(22)
        head.addWidget(self._avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        self._title = SubtitleLabel("", self)
        self._subtitle = CaptionLabel("", self)
        set_secondary_text(self._subtitle)
        names.addWidget(self._title)
        names.addWidget(self._subtitle)
        head.addLayout(names, 1)
        refresh = PushButton(tr("Refresh", "刷新", "更新"), self, FluentIcon.SYNC)
        refresh.setToolTip(tr("Fetch this source's newest videos", "获取此订阅源的最新视频", "最新の動画を取得"))
        refresh.clicked.connect(self._refresh_source)
        head.addWidget(refresh)
        self._page_btn = PushButton(tr("Open page", "打开主页", "ページを開く"), self, FluentIcon.GLOBE)
        self._page_btn.clicked.connect(self._open_source_page)
        head.addWidget(self._page_btn)
        more = ToolButton(FluentIcon.MORE, self)
        more.clicked.connect(lambda: page._show_source_menu(self.source_id, more.mapToGlobal(more.rect().bottomLeft())))
        head.addWidget(more)
        root.addLayout(head)

        # Whether this author is subscribed here / followed on Iwara, with the buttons to change it.
        self.status_bar = AuthorStatusBar(self, show_open=False)
        self.status_bar.hide()
        root.addWidget(self.status_bar)

        filters = ResponsiveFlowLayout(spacing=8)
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(tr("Search titles…", "搜索标题…", "タイトルを検索…"))
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(lambda _t: self._filter_timer.start())
        filters.addWidget(self._search)
        self._state_combo = ComboBox(self)
        for key, label in (
            ("all", tr("All", "全部", "すべて")),
            ("ready", tr("Downloadable", "可下载", "保存可能")),
            ("new", tr("New", "新增", "新規")),
            ("downloaded", tr("Downloaded", "已下载", "保存済み")),
            ("moved", tr("Moved", "已移走", "移動済み")),
            ("queued", tr("Queued", "已入队", "キュー内")),
            ("unavailable", tr("Unavailable", "不可下载", "保存不可")),
        ):
            self._state_combo.addItem(label)
            self._state_combo.setItemData(self._state_combo.count() - 1, key)
        self._state_combo.setFixedWidth(124)
        self._state_combo.currentIndexChanged.connect(lambda _i: self._apply(reset_page=True))
        filters.addWidget(self._state_combo)
        self._sort_combo = ComboBox(self)
        for key, label in (
            ("date_desc", tr("Newest first", "时间倒序", "新しい順")),
            ("date_asc", tr("Oldest first", "时间升序", "古い順")),
            ("new_first", tr("New first", "新增优先", "新規優先")),
            ("title", tr("Title A-Z", "标题 A-Z", "タイトル A-Z")),
        ):
            self._sort_combo.addItem(label)
            self._sort_combo.setItemData(self._sort_combo.count() - 1, key)
        self._sort_combo.setFixedWidth(124)
        self._sort_combo.currentIndexChanged.connect(lambda _i: self._apply(reset_page=True))
        filters.addWidget(self._sort_combo)
        filters.addWidget(CardSizeControl(self))
        root.addLayout(filters)

        tools = ResponsiveFlowLayout(spacing=8)
        select_all = PushButton(tr("Select all", "全选", "すべて選択"), self)
        select_all.clicked.connect(lambda: self._grid.select_all(True))
        clear = PushButton(tr("Clear", "取消选择", "選択解除"), self)
        clear.clicked.connect(lambda: self._grid.clear_selection())
        tools.addWidget(select_all)
        tools.addWidget(clear)
        self._selected_label = CaptionLabel("", self)
        set_secondary_text(self._selected_label)
        tools.addWidget(self._selected_label)
        self._rule_picker = RulePicker(self)
        tools.addWidget(self._rule_picker)
        self._download_selected_btn = PrimaryPushButton(tr("Download selected", "下载选中", "選択を保存"), self, FluentIcon.DOWNLOAD)
        self._download_selected_btn.clicked.connect(self._download_selected)
        tools.addWidget(self._download_selected_btn)
        self._download_new_btn = PushButton(tr("Download new", "下载新增", "新規を保存"), self, FluentIcon.DOWNLOAD)
        self._download_new_btn.clicked.connect(self._download_new)
        tools.addWidget(self._download_new_btn)
        self._download_all_btn = PushButton(tr("Download all shown", "下载全部筛选结果", "表示中をすべて保存"), self, FluentIcon.DOWNLOAD)
        self._download_all_btn.clicked.connect(self._download_all)
        tools.addWidget(self._download_all_btn)
        root.addLayout(tools)

        self._status = CaptionLabel("", self)
        set_secondary_text(self._status)
        root.addWidget(self._status)

        self._scroll = transparent_scroll_area("SubscriptionSourceScroll", self)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 8, 24)
        column.setSpacing(12)
        self._grid = MediaGrid(body, selectable=True, resizable=True)
        self._grid.card_activated.connect(page._show_video_detail)
        self._grid.card_context_requested.connect(self._on_card_context)
        self._grid.selection_changed.connect(self._sync_buttons)
        column.addWidget(self._grid)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self._prev_btn = PushButton(tr("Previous page", "上一页", "前のページ"), body, FluentIcon.LEFT_ARROW)
        self._prev_btn.clicked.connect(lambda: self._go(self._page_index - 1))
        self._page_label = BodyLabel("", body)
        self._next_btn = PushButton(tr("Next page", "下一页", "次のページ"), body, FluentIcon.RIGHT_ARROW)
        self._next_btn.clicked.connect(lambda: self._go(self._page_index + 1))
        footer.addWidget(self._prev_btn)
        footer.addWidget(self._page_label)
        footer.addWidget(self._next_btn)
        footer.addStretch(1)
        column.addLayout(footer)
        column.addStretch(1)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(lambda: self._apply(reset_page=True))
        covers.cover_ready.connect(self._on_cover_ready)
        covers.queue_changed.connect(self._sync_status)
        self._sync_buttons()

    # ── data ─────────────────────────────────────────────────────────────────

    @property
    def source_id(self) -> int:
        return int(self._source.get("id", 0) or 0)

    def open_source(self, source: dict[str, Any]):
        self._source = dict(source)
        title = str(source.get("title", "") or source.get("source_key", "") or "")
        self._title.setText(title)
        key = str(source.get("source_key", "") or "")
        is_author = str(source.get("source_type", "") or "") == "author"
        self._subtitle.setText(
            (f"@{key}" if is_author else key) + "  ·  " + _source_type_label(str(source.get("source_type", "") or ""))
        )
        self._avatar.setText(title[:1].upper())
        pixmap = load_pixmap(str(source.get("avatar_path", "") or ""), 120)
        if pixmap is not None:
            self._avatar.setImage(pixmap)
        self.status_bar.setVisible(is_author)
        if is_author:
            self.status_bar.set_author(
                key, title, str(source.get("remote_id", "") or ""), str(source.get("avatar_url", "") or ""),
            )
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._state_combo.setCurrentIndex(0)
        self._page_btn.setEnabled(bool(self._page._source_page_url(source)))
        self.reload()

    def set_avatar(self, source_id: int, path: str):
        if int(source_id) == self.source_id:
            pixmap = load_pixmap(path, 120)
            if pixmap is not None:
                self._avatar.setImage(pixmap)

    def reload(self):
        """Re-read the source's items (download states change under us)."""

        self._stale = False
        if not self.source_id:
            return
        self._items = self._page._source_items(self.source_id)
        self._apply(reset_page=False)

    def refresh_status(self):
        """Re-read whether the author is still a local subscription."""

        if self.status_bar.isVisibleTo(self):
            self.status_bar.refresh_local()

    def reload_if_visible(self):
        if self.isVisible():
            self.reload()
        else:
            self._stale = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale:
            self.reload()

    def _apply(self, *, reset_page: bool):
        mode = str(self._state_combo.currentData() or "all")
        order = str(self._sort_combo.currentData() or "date_desc")
        self._filtered = sort_items(filter_items(self._items, mode, self._search.text()), order)
        if reset_page:
            self._page_index = 0
        self._go(self._page_index)

    def _page_count(self) -> int:
        return max(1, -(-len(self._filtered) // GRID_PAGE_SIZE))

    def _go(self, index: int):
        self._page_index = max(0, min(index, self._page_count() - 1))
        start = self._page_index * GRID_PAGE_SIZE
        chunk = self._filtered[start:start + GRID_PAGE_SIZE]
        title = str(self._source.get("title", "") or "")
        is_author = str(self._source.get("source_type", "") or "") == "author"
        videos = [item_to_video(item, author_name=title if is_author else "") for item in chunk]
        for video in videos:
            cached = self._pixmaps.get(video.video_id) or load_pixmap(str(video.raw.get("_thumbnail_path", "") or ""))
            if cached is not None:
                self._pixmaps[video.video_id] = cached
                self._grid.set_cover(video.video_id, cached)
        self._grid.set_videos(videos)
        self._scroll.verticalScrollBar().setValue(0)
        self._request_covers(videos)
        self._prev_btn.setEnabled(self._page_index > 0)
        self._next_btn.setEnabled(self._page_index < self._page_count() - 1)
        self._page_label.setText(
            tr(
                f"Page {self._page_index + 1} / {self._page_count()}",
                f"第 {self._page_index + 1} / {self._page_count()} 页",
                f"{self._page_index + 1} / {self._page_count()} ページ",
            )
        )
        self._page_video_ids = [v.video_id for v in videos]
        self._sync_status()
        self._sync_buttons()

    def _sync_status(self):
        """Counts, plus how many covers of this page are still on their way."""

        new_count = sum(1 for item in self._items if item_state(item) == "new")
        text = tr(
            f"{len(self._filtered)} of {len(self._items)} videos · {new_count} new",
            f"显示 {len(self._filtered)} / {len(self._items)} 个视频 · {new_count} 个新增",
            f"{len(self._filtered)} / {len(self._items)} 本 · 新規 {new_count}",
        )
        missing = sum(1 for video_id in getattr(self, "_page_video_ids", []) if video_id not in self._pixmaps)
        if missing and self._covers.pending():
            text += tr(
                f" · loading covers ({missing} left)",
                f" · 正在加载封面（还剩 {missing} 个）",
                f" · カバー読み込み中（残り {missing}）",
            )
        elif missing:
            text += tr(
                f" · {missing} cover(s) unavailable — Refresh to retry",
                f" · {missing} 个封面暂未取到，点“刷新”重试",
                f" · カバー {missing} 件が未取得です。更新で再試行できます",
            )
        self._status.setText(text)

    def _request_covers(self, videos: list[SearchVideo]):
        requests = [
            (v.video_id, v.thumbnail_url)
            for v in videos
            if v.video_id not in self._pixmaps
        ]
        if requests:
            # What is on screen now goes first, ahead of the overview's strips.
            self._covers.request(requests, urgent=True)

    def _on_cover_ready(self, video_id: str, path: str):
        pixmap = load_pixmap(path)
        if pixmap is not None:
            self._pixmaps[video_id] = pixmap
            self._grid.set_cover(video_id, pixmap)
            self._sync_status()

    # ── actions ──────────────────────────────────────────────────────────────

    def _sync_buttons(self):
        selected = self._grid.selected_videos()
        self._selected_label.setText(
            tr(f"{len(selected)} selected", f"已选 {len(selected)} 个", f"{len(selected)} 件選択")
        )
        self._download_selected_btn.setEnabled(bool(selected))
        self._download_new_btn.setEnabled(bool(downloadable_ids(self._items, only_new=True)))
        self._download_all_btn.setEnabled(bool(downloadable_ids(self._filtered)))

    def _queue(self, ids: list[str]):
        self._rule_picker.apply_selected(show_notice=False)
        self._page._enqueue_ids(ids, rule_id=self._rule_picker.selected_rule_id())

    def _download_selected(self):
        ids = [v.video_id for v in self._grid.selected_videos()]
        self._queue(ids)
        self._grid.clear_selection()

    def _download_new(self):
        self._queue(downloadable_ids(self._items, only_new=True))

    def _download_all(self):
        self._queue(downloadable_ids(self._filtered))

    def _refresh_source(self):
        if self.source_id:
            self._covers.forget([item["video_id"] for item in self._items if item.get("video_id")])
            self._page._refresh_source_ids_from_view(self.source_id)

    def _open_source_page(self):
        self._page._open_source_page(self._source)

    def _on_card_context(self, video: SearchVideo, pos: QPoint):
        selected = [v.video_id for v in self._grid.selected_videos()]
        self._page._show_video_menu(video, pos, selected if video.video_id in selected else None)
