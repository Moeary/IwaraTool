"""Fluent search interface for videos, authors, tags, and playlists."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QListWidgetItem, QWidget

from qfluentwidgets import InfoBar, InfoBarPosition, LineEdit

from ...core.manager import download_manager
from ...core.search import SearchAuthor, SearchPlaylist, SearchVideo
from ...i18n import tr
from ...signal_bus import signal_bus
from .actions import SearchActionsMixin
from .download_status import SearchDownloadStatusMixin
from .widgets import TagSuggestionPopup, _search_grid_style
from .workers import (
    SearchImageWorker,
    SearchIwaraAuthorWorker,
    SearchOrenoAuthorWorker,
    SearchOrenoLinkWorker,
    SearchQueueResolveWorker,
    SearchWorker,
)
# read_pixmap is not used here: it is the patch point for page_dependencies.
from ..media_card import PixmapLRU, read_pixmap  # noqa: F401
from ..theme import qcolor
from .covers import SearchCoversMixin
from .execution import SearchExecutionMixin
from .history import SearchHistoryMixin
from .oreno_links import SearchOrenoLinksMixin
from .page_layout import SearchPageLayoutMixin
from .pagination import SearchPaginationMixin
from .query_controls import SearchQueryControlsMixin
from .result_items import SearchResultItemsMixin
from .results_layout import SearchResultsLayoutMixin
from .session import SearchSessionMixin
from .source_routing import SearchSourceRoutingMixin
from .tag_suggestions import SearchTagSuggestionsMixin


class SearchInterface(
    SearchPageLayoutMixin,
    SearchSessionMixin,
    SearchHistoryMixin,
    SearchQueryControlsMixin,
    SearchSourceRoutingMixin,
    SearchTagSuggestionsMixin,
    SearchExecutionMixin,
    SearchPaginationMixin,
    SearchOrenoLinksMixin,
    SearchResultsLayoutMixin,
    SearchCoversMixin,
    SearchResultItemsMixin,
    SearchDownloadStatusMixin,
    SearchActionsMixin,
    QWidget,
):
    """Search page with Fluent controls, cached covers, and a configurable list."""

    _DATA_ROLE = Qt.ItemDataRole.UserRole
    _RESULT_COLUMN_KEYS = (
        "iwara_id",
        "title",
        "author",
        "views",
        "likes",
        "comments",
        "duration",
        "published",
        "tags",
        "iwara_url",
        "source_url",
        "source",
        "download_status",
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SearchInterface")
        self._auto_search_ready = False
        self._search_controls_collapsed = False
        self._loading = False
        self._auto_search_timer = QTimer(self)
        self._auto_search_timer.setSingleShot(True)
        self._auto_search_timer.setInterval(120)
        self._auto_search_timer.timeout.connect(self._auto_start_search)
        self._generation = 0
        self._current_page = 0
        self._last_page: int | None = None
        self._next_page: int | None = None
        self._total: int | None = None
        self._query_context: tuple[str, str] | None = None
        self._query_drafts: dict[tuple[str, str], tuple[str, str]] = {}
        self._active_search_request = None
        self._search_error = ""
        self._account_epoch = 0
        # The source was switched to Oreno3D only for a Chinese keyword.
        self._source_auto_routed = False
        # The user picked the source by hand for the keyword now in the box.
        self._source_pinned = False
        self._all_videos: list[SearchVideo] = []
        self._all_authors: list[SearchAuthor] = []
        self._all_playlists: list[SearchPlaylist] = []
        self._author_video_target = None
        self._author_profile_workers = []
        self._pending_author_actions = {}
        self._item_by_key: dict[str, QListWidgetItem] = {}
        self._image_path_by_key: dict[str, str] = {}
        self._source_pixmaps = PixmapLRU(64 * 1024 * 1024)
        self._small_sources: set[str] = set()
        self._search_workers: list[SearchWorker] = []
        self._image_workers: list[SearchImageWorker] = []
        self._image_pending_keys: set[str] = set()
        self._grid_resize_pending = False
        self._oreno_link_workers: list[SearchOrenoLinkWorker] = []
        self._iwara_author_workers: list[SearchIwaraAuthorWorker] = []
        self._oreno_author_workers: list[SearchOrenoAuthorWorker] = []
        self._queue_resolve_worker: SearchQueueResolveWorker | None = None
        self._queue_resolve_rule_id = ""
        self._tag_popup: TagSuggestionPopup | None = None
        self._active_tag_edit: LineEdit | None = None
        self._pending_open_video_ids: set[str] = set()
        self._pending_preview_video_ids: set[str] = set()
        self._pending_detail_video_ids: set[str] = set()
        self._pending_open_author_video_ids: set[str] = set()
        self._pending_author_subscription_video_ids: set[str] = set()
        self._init_download_status(download_manager.history, signal_bus)
        self._build_ui()
        self._auto_search_ready = True

    def _connect_global_signals(self):
        signal_bus.content_rating_changed.connect(self._on_rating_broadcast)
        signal_bus.login_state_changed.connect(self._on_login_state_changed)
        signal_bus.search_source_changed.connect(self._on_default_source_changed)

    def showEvent(self, event):
        super().showEvent(event)
        self._request_download_status_refresh(force=True)
        # The history popup is a non-activating tool window.  Explicitly
        # refresh and hide it on navigation restore so it cannot retain a
        # stale hidden state or hover/focus target from the previous page.
        self._refresh_search_history_popup()
        self._hide_search_history_popup()
        if self._tag_popup is not None:
            self._tag_popup.hide()
        QTimer.singleShot(0, self._resize_grid)
        # After the event loop turn, so a restore or an external query that
        # comes with this switch has already put its own search in place.
        QTimer.singleShot(0, self, self._load_initial_results)

    def _load_initial_results(self):
        """An empty page loads the listing at once instead of waiting for a refresh."""

        if not self.isVisible() or self._loading or self._active_search_request is not None:
            return
        if self._all_videos or self._all_authors or self._all_playlists:
            return
        if self._author_video_target is not None:
            return
        if str(self._scope_combo.currentData() or "videos") not in {"videos", "images"}:
            return  # the other scopes need a keyword first
        self._start_search(record_history=False)

    def hideEvent(self, event):
        self._auto_search_timer.stop()
        self._download_status_refresh_timer.stop()
        self._hide_search_history_popup()
        if self._tag_popup is not None:
            self._tag_popup.hide()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_grid()
        self._schedule_grid_resize()
        self._fit_results_table_last_column()

    def refresh_theme_styles(self):
        self._resize_grid()
        self._results.setStyleSheet(_search_grid_style())
        for key, item in self._item_by_key.items():
            item.setForeground(qcolor("text"))
            if key not in self._image_path_by_key:
                item.setIcon(self._placeholder_icon(key.split(":", 1)[0]))

    def _show_error(self, message: str):
        InfoBar.error(
            title=tr("Search failed", "搜索失败", "検索失敗"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _show_warning(self, message: str):
        InfoBar.warning(
            title=tr("Search warning", "搜索提示", "検索の注意"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4500,
            parent=self,
        )
