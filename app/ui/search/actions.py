"""User actions for search results, kept separate from page layout."""
from __future__ import annotations

from typing import Any

from ...i18n import tr
from .author_navigation import SearchAuthorNavigationMixin
from .author_subscription import SearchAuthorSubscriptionMixin
from .context_menu import SearchContextMenuMixin
from .opening import SearchOpeningMixin
from .queueing import SearchQueueingMixin


class SearchActionsMixin(
    SearchQueueingMixin,
    SearchOpeningMixin,
    SearchContextMenuMixin,
    SearchAuthorNavigationMixin,
    SearchAuthorSubscriptionMixin,
):
    def _sync_selection_buttons(self):
        selected_values = self._selected_data()
        selected = bool(selected_values)
        queueable = any(self._is_queueable(value) for value in selected_values)
        resolving = self._queue_resolve_worker is not None and self._queue_resolve_worker.isRunning()
        self._queue_selected_btn.setEnabled(selected and queueable and not resolving)
        self._open_selected_btn.setEnabled(selected)

    def _selected_data(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = []
        if self._is_list_view():
            for index in self._results_table.selectionModel().selectedRows():
                item = self._results_table.item(index.row(), 0)
                value = item.data(self._DATA_ROLE) if item else None
                if isinstance(value, dict):
                    data.append(value)
        else:
            for item in self._results.selectedItems():
                value = item.data(self._DATA_ROLE)
                if isinstance(value, dict):
                    data.append(value)
        return data

    def _reset_filters(self):
        self._clear_author_navigation()
        self._query_drafts.clear()
        self._query_context = None
        self._active_search_request = None
        self._search_error = ""
        self._hide_search_history_popup()
        self._keyword_edit.clear()
        self._source_combo.setCurrentIndex(0)
        self._scope_combo.setCurrentIndex(0)
        self._sort_combo.setCurrentIndex(0)
        self._results.clearSelection()
        self._results_table.clearSelection()
        for worker in self._oreno_link_workers:
            worker.requestInterruption()
        for worker in self._iwara_author_workers:
            worker.requestInterruption()
        for worker in self._oreno_author_workers:
            worker.requestInterruption()
        self._pending_author_subscription_video_ids.clear()
        self._pending_open_video_ids.clear()
        self._pending_preview_video_ids.clear()
        self._pending_detail_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        self._current_page = 0
        self._last_page = None
        self._next_page = None
        self._total = None
        self._all_videos.clear()
        self._all_authors.clear()
        self._all_playlists.clear()
        self._image_path_by_key.clear()
        self._image_pending_keys.clear()
        self._render_results()
        self._set_loading(False)
        self._status_label.setText(
            tr(
                "Enter a query or search the latest videos",
                "输入条件后开始搜索，也可以直接查看最新视频",
                "条件を入力して検索してください",
            )
        )
