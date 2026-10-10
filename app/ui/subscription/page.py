"""Subscription Interface — sources, update checks, and batch enqueue."""
from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QTimer, Qt, QThread, Signal
from PySide6.QtWidgets import QListWidgetItem, QWidget

# download_manager / isDarkTheme are not used here: they are the patch points
# the other subscription modules read through page_dependencies.
from qfluentwidgets import InfoBar, InfoBarPosition, ProgressBar, isDarkTheme  # noqa: F401

from ...config import app_config
from ...core.manager import download_manager  # noqa: F401
from ...i18n import tr
from ...signal_bus import signal_bus
from .actions import SubscriptionActionsMixin
from .helpers import _parse_ui_bool
from ..downloads.staging_flow import StagingFlow
from .components import (
    SubscriptionAvatarWorker,
    SubscriptionImportAuthorsWorker,
    SubscriptionRefreshWorker,
    SubscriptionThumbnailWorker,
    _SOURCE_SORT_FIELDS,
    _apply_fluent_scrollbars,
    _style_content_splitter,
    _thumbnail_list_style,
)
from .cover_loader import CoverLoader
from ..worker_lifecycle import stop_qthreads
from .item_grid import SubscriptionItemGridMixin
from .item_list import SubscriptionItemListMixin
from .panels import SubscriptionPanelsMixin
from .page_layout import SubscriptionPageLayoutMixin
from .source_list import SubscriptionSourceListMixin


class SubscriptionInterface(
    SubscriptionPageLayoutMixin,
    SubscriptionPanelsMixin,
    SubscriptionSourceListMixin,
    SubscriptionItemListMixin,
    SubscriptionItemGridMixin,
    SubscriptionActionsMixin,
    QWidget,
):
    """Page for tracking subscription updates and queueing downloads."""

    _SRC_AVATAR = 0
    _SRC_STATE = 1
    _SRC_TYPE = 2
    _SRC_TITLE = 3
    _SRC_NEW = 4
    _SRC_UNDOWNLOADED = 5
    _SRC_ITEMS = 6
    _SRC_CHECKED = 7
    _SRC_CREATED = 8
    _SRC_KEY = 9
    _SRC_URL = 10
    _SRC_OPEN = 11

    _ITEM_STATE = 0
    _ITEM_REASON = 1
    _ITEM_NEW = 2
    _ITEM_TITLE = 3
    _ITEM_AUTHOR = 4
    _ITEM_PUBLISHED = 5
    _ITEM_ID = 6
    _ITEM_SOURCE_URL = 7
    _ITEM_URL = 8
    _ITEM_FOLDER = 9
    _ITEM_FILE = 10

    # Emitted whenever the source list was re-read (refresh, add, delete, ...).
    sources_changed = Signal()

    _RENDER_BATCH_SIZE = 80
    _MIN_ITEM_TABLE_HEIGHT = 140
    _MIN_STACKED_SOURCE_HEIGHT = 200

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SubscriptionInterface")
        self._worker: SubscriptionRefreshWorker | None = None
        self._import_worker: SubscriptionImportAuthorsWorker | None = None
        self._staging_flow = StagingFlow(self)  # adds wait on the download workbench
        self._avatar_worker: SubscriptionAvatarWorker | None = None
        self._thumbnail_worker: SubscriptionThumbnailWorker | None = None
        self._cover_cache_worker: SubscriptionThumbnailWorker | None = None
        self._shutting_down = False
        self._avatar_requested_source_ids: set[int] = set()
        self._thumbnail_requested_video_ids: set[str] = set()
        self._thumbnail_force_refresh_ids: set[str] = set()
        self._thumbnail_force_refresh_pending = False
        self._thumbnail_items_by_video_id: dict[str, list[QListWidgetItem]] = {}
        self._all_sources: list[dict[str, Any]] = []
        self._sources: list[dict[str, Any]] = []
        self._all_items: list[dict[str, Any]] = []
        self._visible_items: list[dict[str, Any]] = []
        self._current_source_id: int | None = None
        self._pending_video_ids: set[str] = set()
        self._title_filter_error = ""
        self._refresh_info_bar: InfoBar | None = None
        self._refresh_progress: ProgressBar | None = None
        self._source_sort_field = str(
            app_config.get_ui_value("subscription_source_sort_field_v1", "title") or "title"
        ).strip().casefold()
        if self._source_sort_field not in _SOURCE_SORT_FIELDS:
            self._source_sort_field = "title"
        self._source_sort_desc = _parse_ui_bool(
            app_config.get_ui_value("subscription_source_sort_desc_v1", "0")
        )
        self._source_render_index = 0
        self._item_render_index = 0
        self._items_refresh_pending = False
        self._thumbnail_grid_resize_pending = False
        self._syncing_download_options = False
        self._source_render_timer = QTimer(self)
        self._source_render_timer.setInterval(0)
        self._source_render_timer.timeout.connect(self._render_source_batch)
        self._item_render_timer = QTimer(self)
        self._item_render_timer.setInterval(0)
        self._item_render_timer.timeout.connect(self._render_item_batch)
        self._covers = CoverLoader(self)
        self._views_timer = QTimer(self)
        self._views_timer.setSingleShot(True)
        self._views_timer.setInterval(350)
        self._views_timer.timeout.connect(self._refresh_views)
        self._build_ui()
        self.sources_changed.connect(self._schedule_views_refresh)
        self._load_sources()
        signal_bus.tasks_added.connect(self._on_tasks_changed)
        signal_bus.task_added.connect(self._on_task_changed)
        signal_bus.task_status_changed.connect(self._on_task_status_changed)
        signal_bus.tasks_removed.connect(self._on_tasks_changed)
        signal_bus.task_removed.connect(self._on_task_changed)
        signal_bus.download_options_changed.connect(self._sync_download_option_buttons)
        signal_bus.subscription_source_added.connect(self._on_subscription_source_added)
        signal_bus.subscription_sources_changed.connect(self._on_sources_changed_elsewhere)
        self._sync_download_option_buttons()

    def refresh_theme_styles(self):
        """Refresh custom styles that qfluentwidgets cannot recolor automatically."""
        if not hasattr(self, "_item_table"):
            return
        _apply_fluent_scrollbars(self._item_table)
        _apply_fluent_scrollbars(self._thumbnail_list)
        _apply_fluent_scrollbars(self._item_controls_scroll)
        self._thumbnail_list.setStyleSheet(_thumbnail_list_style())
        _style_content_splitter(self._splitter)
        _style_content_splitter(self._item_content_splitter)
        self._sync_download_option_buttons()
        # Pending rows may have been staged before the theme changed. Reapply
        # only their brush so old and newly staged rows always share one green.
        self._set_pending_visuals(list(self._pending_video_ids), True)
        # Rebuild placeholders so unloaded covers also follow the selected theme.
        placeholder = self._thumbnail_placeholder_icon()
        for index in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(index)
            video_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            data = next((entry for entry in self._visible_items if str(entry.get("video_id", "")) == video_id), None)
            path = str(data.get("thumbnail_path", "") or "") if data else ""
            if not path or not os.path.isfile(path):
                item.setIcon(placeholder)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Horizontal splitters are useful on wide screens, but at compact widths
        # they force both panels to keep their full toolbar width. Stack panels
        # vertically instead so every control can wrap naturally.
        should_stack = self.width() < 1100
        if should_stack != self._splitter_is_vertical:
            self._splitter_is_vertical = should_stack
            self._splitter.setOrientation(
                Qt.Orientation.Vertical if should_stack else Qt.Orientation.Horizontal
            )
            if self._source_panel_visible and self._items_panel_visible:
                if should_stack:
                    height = max(1, self._splitter.height())
                    # The video panel carries more controls, so it gets the larger share.
                    self._splitter.setSizes([max(220, int(height * 0.42)), max(300, int(height * 0.58))])
                else:
                    width = max(1, self._splitter.width())
                    self._splitter.setSizes([max(420, int(width * 0.50)), max(520, int(width * 0.50))])
            else:
                visible_index = 0 if self._source_panel_visible else 1
                total = max(1, self._splitter.height() if should_stack else self._splitter.width())
                sizes = [0, 0]
                sizes[visible_index] = total
                self._splitter.setSizes(sizes)
        self._fit_source_table_last_column()
        self._fit_item_table_last_column()
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()
        self._update_panel_toggle_buttons()
        QTimer.singleShot(0, self._fit_item_controls_height)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_item_controls_height)

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        """Stop timers and subscription workers before the page is destroyed."""
        self._shutting_down = True
        self._source_render_timer.stop()
        self._item_render_timer.stop()
        self._views_timer.stop()
        covers_stopped = all([
            self._covers.shutdown(timeout_ms),
            self._author_view.shutdown(timeout_ms),
            self._source_view.status_bar.shutdown(timeout_ms),
        ])
        workers: list[QThread | None] = [
            self._worker,
            self._import_worker,
            self._staging_flow.worker,
            self._avatar_worker,
            self._thumbnail_worker,
            self._cover_cache_worker,
        ]
        return stop_qthreads(workers, timeout_ms=timeout_ms) and covers_stopped

    def _show_error(self, msg: str):
        InfoBar.error(
            title=tr("Operation Failed", "操作失败", "操作失敗"),
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self,
        )
