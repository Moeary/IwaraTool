"""Previous / next / jump-to-page navigation of search results."""

from __future__ import annotations

from ...i18n import tr
from ..fluent_dialogs import show_fluent_text_input


class SearchPaginationMixin:
    """Previous / next / jump-to-page navigation of search results."""

    def _load_more(self):
        """Compatibility alias for callers that used the old load-more action."""

        self._go_next_page()

    def _go_previous_page_if_enabled(self):
        if self._previous_page_btn.isEnabled():
            self._go_previous_page()

    def _go_next_page_if_enabled(self):
        if self._next_page_btn.isEnabled():
            self._go_next_page()

    def _go_previous_page(self):
        if self._current_page <= 0:
            return
        self._navigate_to_page(self._current_page - 1)

    def _go_next_page(self):
        if self._last_page is not None:
            if self._current_page >= self._last_page:
                return
            page = self._current_page + 1
        elif self._next_page is not None:
            page = self._next_page
        else:
            return
        self._navigate_to_page(page)

    def _jump_to_page(self):
        if (
            self._last_page is None
            and self._total is None
            and not self._all_videos
            and not self._all_authors
            and not self._all_playlists
        ):
            self._show_warning(
                tr(
                    "Run a search before jumping to a page.",
                    "请先执行搜索，再跳转页码。",
                    "ページ移動の前に検索を実行してください。",
                )
            )
            return

        current = max(1, self._current_page + 1)
        page_text, accepted = show_fluent_text_input(
            self,
            tr("Jump to page", "跳转页码", "ページへ移動"),
            tr("Page number:", "页码：", "ページ番号:"),
            text=str(current),
            accept_text=tr("Go", "跳转", "移動"),
            cancel_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not accepted:
            return
        try:
            page_number = int(page_text.strip())
        except (TypeError, ValueError):
            self._show_error(
                tr("Enter a valid page number.", "请输入有效的页码。", "有効なページ番号を入力してください。")
            )
            return
        if page_number < 1:
            self._show_error(
                tr("Page number must be at least 1.", "页码必须大于等于 1。", "ページ番号は1以上にしてください。")
            )
            return
        if self._last_page is not None and page_number > self._last_page + 1:
            self._show_error(
                tr(
                    f"Page number must be between 1 and {self._last_page + 1}.",
                    f"页码必须在 1 到 {self._last_page + 1} 之间。",
                    f"ページ番号は1～{self._last_page + 1}の範囲で指定してください。",
                )
            )
            return
        self._navigate_to_page(page_number - 1)

    def _navigate_to_page(self, page: int):
        self._auto_search_timer.stop()
        page = max(0, int(page))
        if self._last_page is not None:
            page = min(page, self._last_page)
        try:
            filters = self._build_filters()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        scope = str(self._scope_combo.currentData() or "videos")
        source = str(self._source_combo.currentData() or "oreno3d")
        if self._active_search_request != (filters, scope, source):
            # Editing a query/sort must not jump into the middle of a different
            # result set when automatic searches are disabled.
            self._start_search()
            return
        self._interrupt_search_workers()
        self._pending_open_video_ids.clear()
        self._pending_preview_video_ids.clear()
        self._pending_detail_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        self._current_page = page
        self._next_page = None
        self._run_search(filters, scope, source=source, page=page, replace_results=True)

    def _update_page_controls(self):
        if not hasattr(self, "_page_label"):
            return
        current = max(1, self._current_page + 1)
        if self._last_page is not None:
            total = max(current, self._last_page + 1)
            text = tr(f"Page {current} / {total}", f"第 {current} / {total} 页", f"{current} / {total} ページ")
        else:
            text = tr(f"Page {current}", f"第 {current} 页", f"{current} ページ")
        self._page_label.setText(text)
        if hasattr(self, "_jump_page_btn"):
            has_page_state = bool(
                self._last_page is not None
                or self._next_page is not None
                or self._total is not None
                or self._all_videos
                or self._all_authors
                or self._all_playlists
            )
            self._jump_page_btn.setEnabled(not self._loading and has_page_state)
