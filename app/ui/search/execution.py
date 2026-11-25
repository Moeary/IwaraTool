"""Start, run and stop searches and report their status."""

from __future__ import annotations

from dataclasses import replace as dataclass_replace

from PySide6.QtCore import QThread, QTimer

from ...config import app_config
from ...core.search.models import SearchFilters, SearchPageResult
from ...core.rating import api_rating
from ...core.search.limits import configured_cover_concurrency, configured_resolution_concurrency, resolution_mode
from ...i18n import tr
from ...presentation.search.request import PROBLEM_WARNING, merge_page, request_problem
from ...presentation.search.request import page_size as search_page_size
from .search_worker import SearchWorker
from ..common.worker_lifecycle import on_finished, stop_qthreads


class SearchExecutionMixin:
    """Start, run and stop searches and report their status."""

    @staticmethod
    def _search_resolution_mode() -> str:
        return resolution_mode()

    @staticmethod
    def _search_resolution_concurrency() -> int:
        return configured_resolution_concurrency()

    @staticmethod
    def _cover_download_concurrency() -> int:
        return configured_cover_concurrency()

    def _on_login_state_changed(self, *_args):
        self._account_epoch += 1

    def _schedule_auto_search(self):
        # Drop a timer scheduled under the previous source/scope state.  A
        # stale timer can otherwise start a new generation during paging and
        # make the requested page result get discarded as obsolete.
        self._auto_search_timer.stop()
        if not self._auto_search_ready or not app_config.search_auto_search_enabled:
            return
        scope = str(self._scope_combo.currentData() or "videos")
        if scope in {"authors", "tags", "playlists"} and not self._keyword_edit.text().strip():
            return
        self._auto_search_timer.start()

    def _auto_start_search(self):
        if self._auto_search_ready:
            self._start_search()

    def _build_filters(self) -> SearchFilters:
        author_target = self._author_video_target
        author_id = (
            str(author_target[2] or "")
            if author_target is not None
            and str(self._source_combo.currentData() or "oreno3d") == "iwara"
            and str(self._scope_combo.currentData() or "videos") == "videos"
            else ""
        )
        iwara = str(self._source_combo.currentData() or "oreno3d") == "iwara"
        keyword = self._keyword_edit.text().strip()
        rating = api_rating(self._selected_rating()) if iwara else ""
        return SearchFilters(
            keyword=keyword,
            author_id=author_id,
            sort=str(self._sort_combo.currentData() or "date"),
            rating=rating,
            page_size=self._page_size(iwara, rating, keyword),
        )

    @staticmethod
    def _page_size(iwara: bool, rating: str, keyword: str) -> int:
        return search_page_size(iwara, rating, keyword)

    def _rerated_filters(self, filters: SearchFilters) -> SearchFilters:
        """The same Iwara query under the current global content rating."""

        rating = api_rating(self._selected_rating())
        return dataclass_replace(
            filters, rating=rating, page_size=self._page_size(True, rating, filters.keyword),
        )

    def _start_search(self, *_args, record_history: bool = True):
        self._auto_search_timer.stop()
        self._hide_search_history_popup()
        self._sync_source_options_for_site()  # a restored or default Oreno3D search on iwara.ai
        self._route_source_for_keyword()
        try:
            filters = self._build_filters()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        scope = str(self._scope_combo.currentData() or "videos")
        source = str(self._source_combo.currentData() or "oreno3d")
        problem = request_problem(scope, source, filters.keyword)
        if problem is not None:
            level, message = problem
            (self._show_warning if level == PROBLEM_WARNING else self._show_error)(message)
            return

        if record_history:
            self._record_current_search()
        self._active_search_request = (filters, scope, source)
        self._search_error = ""
        self._interrupt_search_workers()
        self._current_page = 0
        self._last_page = None
        self._pending_open_video_ids.clear()
        self._pending_preview_video_ids.clear()
        self._pending_detail_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        self._next_page = 0
        self._total = None
        self._all_videos.clear()
        self._all_authors.clear()
        self._all_playlists.clear()
        self._image_path_by_key.clear()
        self._image_pending_keys.clear()
        self._render_results()
        self._run_search(filters, scope, source=source, page=0, replace_results=True)

    def _interrupt_search_workers(self):
        self._generation += 1
        self._reset_download_statuses()
        self._pending_author_actions.clear()
        self._pending_author_subscription_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        for worker in self._search_workers:
            worker.requestInterruption()
        for worker in self._image_workers:
            worker.requestInterruption()
        for worker in self._oreno_link_workers:
            worker.requestInterruption()
        for worker in self._iwara_author_workers:
            worker.requestInterruption()
        for worker in self._oreno_author_workers:
            worker.requestInterruption()
        for worker in self._author_profile_workers:
            worker.requestInterruption()

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        """Stop all page-owned search and image workers before window teardown."""
        self._stop_download_status_refresh()
        self._interrupt_search_workers()
        workers: list[QThread | None] = [
            *self._search_workers,
            *self._image_workers,
            *self._oreno_link_workers,
            *self._iwara_author_workers,
            *self._oreno_author_workers,
            *self._author_profile_workers,
            self._download_status_worker,
            self._queue_resolve_worker,
            self._staging_flow.worker,
        ]
        return stop_qthreads(workers, timeout_ms=timeout_ms)

    def _run_search(self, filters: SearchFilters, scope: str, *, source: str, page: int, replace_results: bool):
        worker = SearchWorker(
            filters,
            scope if scope in {"videos", "images", "authors", "tags", "playlists"} else "videos",
            page,
            self._generation,
            replace_results=replace_results,
            source=source,
        )
        self._search_workers.append(worker)
        worker.result_ready.connect(self._on_search_result)
        on_finished(worker, self, self._cleanup_search_worker)
        self._set_loading(True)
        worker.start()

    def _on_search_result(self, result: SearchPageResult):
        worker = self.sender()
        if not isinstance(worker, SearchWorker) or worker.generation != self._generation:
            return
        merge_page(self._all_videos, self._all_authors, self._all_playlists, result, replace=worker.replace_results)
        self._total = result.total
        self._current_page = max(0, int(result.current_page or 0))
        self._last_page = result.last_page
        self._next_page = result.next_page
        self._render_results()
        # The first result can arrive before the parent window has completed
        # its final layout.  Recalculate once on the next event-loop turn so
        # the first paint uses the selected column count, not a stale width.
        QTimer.singleShot(0, self._resize_grid)
        self._set_loading(False)
        if result.error:
            self._show_warning(result.error)
        self._start_image_loading()
        if self._search_resolution_mode() == "eager":
            self._start_oreno_link_resolution(result.videos, hydrate_metadata=True)
        self._update_status(result)

    def _cleanup_search_worker(self, worker: SearchWorker):
        if worker in self._search_workers:
            self._search_workers.remove(worker)
        worker.deleteLater()
        if not any(item.isRunning() and item.generation == self._generation for item in self._search_workers):
            self._set_loading(False)

    def _set_loading(self, loading: bool):
        self._loading = bool(loading)
        self._search_btn.setEnabled(not loading)
        self._reset_btn.setEnabled(not loading)
        self._previous_page_btn.setEnabled(not loading and self._current_page > 0)
        has_next = (
            self._current_page < self._last_page
            if self._last_page is not None
            else self._next_page is not None
        )
        self._next_page_btn.setEnabled(not loading and has_next)
        self._update_page_controls()
        if loading:
            self._status_label.setText(tr("Searching…", "搜索中…", "検索中…"))

    def _update_status(self, result: SearchPageResult | None = None):
        if result is not None:
            self._search_error = result.error
        if self._search_error:
            self._status_label.setText(tr(
                f"Search failed: {self._search_error}",
                f"搜索失败：{self._search_error}",
                f"検索失敗: {self._search_error}",
            ))
            return
        scope = result.scope if result is not None else str(self._scope_combo.currentData() or "videos")
        total = f" / {self._total}" if self._total is not None else ""
        if scope == "authors":
            count = len(self._all_authors)
            self._status_label.setText(
                tr(f"Found {count} author(s){total}", f"找到 {count} 位作者{total}", f"作者 {count} 件{total}")
            )
        elif self._all_playlists:
            count = len(self._all_playlists)
            self._status_label.setText(
                tr(f"Found {count} playlist(s){total}", f"找到 {count} 个播放列表{total}", f"プレイリスト {count} 件{total}")
            )
        elif scope == "images":
            count = len(self._all_videos)
            self._status_label.setText(
                tr(f"Found {count} image post(s){total}", f"找到 {count} 个图片作品{total}", f"画像 {count} 件{total}")
                + self._post_filter_status_suffix()
            )
        else:
            count = len(self._all_videos)
            self._status_label.setText(
                tr(f"Found {count} video(s){total}", f"找到 {count} 个视频{total}", f"動画 {count} 件{total}")
                + self._post_filter_status_suffix()
            )
