"""Snapshot and restore a search for back / forward navigation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import Qt

from ...config import app_config
from ...core.search.models import SearchAuthor, SearchFilters, SearchPlaylist, SearchVideo
from ..navigation import has_focus_within, restore_scroll

from .keyword_language import is_chinese_keyword


@dataclass
class SearchSession:
    """One search as the user left it: the query, its results and where they were looking.

    Restoring it needs no network: the result cards come from here and their
    covers from the local cache.  (Search history only keeps the query.)
    """

    source: str
    scope: str
    keyword: str
    sort: str
    author_target: tuple[str, str, str, str] | None = None
    drafts: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    active_request: Any = None
    error: str = ""
    videos: list[SearchVideo] = field(default_factory=list)
    authors: list[SearchAuthor] = field(default_factory=list)
    playlists: list[SearchPlaylist] = field(default_factory=list)
    current_page: int = 0
    last_page: int | None = None
    next_page: int | None = None
    total: int | None = None
    image_paths: dict[str, str] = field(default_factory=dict)
    status: str = ""
    selected: tuple[str, ...] = ()
    scroll: int = 0
    focus: str = ""  # "results" | "keyword" | ""
    pending: bool = False  # the request was still loading: rerun it on restore
    account_epoch: int = 0  # login changes seen when recorded; account-bound results expire


class SearchSessionMixin:
    """Navigation history support: save, restore and replay a search."""

    def nav_snapshot(self) -> SearchSession:
        if has_focus_within(self._results_stack):
            focus = "results"
        elif has_focus_within(self._keyword_edit):
            focus = "keyword"
        else:
            focus = ""
        drafts = dict(self._query_drafts)
        if self._query_context is not None:
            drafts[self._query_context] = (self._keyword_edit.text(), str(self._sort_combo.currentData() or "date"))
        return SearchSession(
            source=str(self._source_combo.currentData() or "oreno3d"),
            scope=str(self._scope_combo.currentData() or "videos"),
            keyword=self._keyword_edit.text(),
            sort=str(self._sort_combo.currentData() or "date"),
            author_target=self._author_video_target,
            drafts=drafts,
            active_request=self._active_search_request,
            error=self._search_error,
            videos=list(self._all_videos),
            authors=list(self._all_authors),
            playlists=list(self._all_playlists),
            current_page=self._current_page,
            last_page=self._last_page,
            next_page=self._next_page,
            total=self._total,
            image_paths=dict(self._image_path_by_key),
            status="" if self._loading else self._status_label.text(),
            selected=self._selected_keys(),
            scroll=self._result_view().verticalScrollBar().value(),
            focus=focus,
            pending=self._loading and self._active_search_request is not None,
            account_epoch=self._account_epoch,
        )

    def nav_restore(self, session: SearchSession):
        """Put a recorded search back exactly: query, results, page, selection and scroll."""

        self._auto_search_timer.stop()
        self._hide_search_history_popup()
        if self._tag_popup is not None:
            self._tag_popup.hide()
        self._interrupt_search_workers()
        self._source_pinned = False
        self._source_auto_routed = (
            session.source == "oreno3d"
            and app_config.search_default_source == "iwara"
            and is_chinese_keyword(session.keyword)
        )
        ready, self._auto_search_ready = self._auto_search_ready, False
        combos = (self._source_combo, self._scope_combo, self._sort_combo)
        try:
            for combo in combos:
                combo.blockSignals(True)
            self._set_combo_data(self._source_combo, session.source)
            self._sync_scope_options_for_source(session.source)
            self._rating_group.setVisible(session.source == "iwara")
            self._set_combo_data(self._scope_combo, session.scope)
            self._query_context = (session.source, session.scope)
            self._query_drafts = dict(session.drafts)
            self._keyword_edit.blockSignals(True)
            self._keyword_edit.setText(session.keyword)
            self._keyword_edit.blockSignals(False)
            self._sync_sort_options()
            self._set_combo_data(self._sort_combo, session.sort)
        finally:
            for combo in combos:
                combo.blockSignals(False)
        try:
            self._on_scope_changed(trigger_search=False)  # placeholder, hint and view controls
        finally:
            self._auto_search_ready = ready
        if session.author_target is not None:
            self._enter_author_mode(session.author_target)
        self._active_search_request = session.active_request
        self._search_error = session.error
        self._all_videos = list(session.videos)
        self._all_authors = list(session.authors)
        self._all_playlists = list(session.playlists)
        self._current_page = session.current_page
        self._last_page = session.last_page
        self._next_page = session.next_page
        self._total = session.total
        self._image_path_by_key = dict(session.image_paths)
        self._image_pending_keys.clear()
        rerun = self._restored_request_to_rerun(session)
        if rerun is not None:
            # Unfinished, or recorded under another rating/account: the
            # cached cards cannot be shown as they are, so ask again.
            self._active_search_request = rerun
            self._search_error = ""
            self._all_videos.clear()
            self._all_authors.clear()
            self._all_playlists.clear()
            self._image_path_by_key.clear()
            self._next_page = None
            self._render_results()
            self._status_label.setText("")
            filters, scope, source = rerun
            self._run_search(filters, scope, source=source, page=session.current_page, replace_results=True)
            if session.focus == "results":
                self._result_view().setFocus(Qt.FocusReason.OtherFocusReason)
            elif session.focus == "keyword":
                self._keyword_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._render_results()
        self._set_loading(False)
        self._status_label.setText(session.status)
        self._select_keys(set(session.selected))
        restore_scroll(self._result_view(), session.scroll)
        if session.focus == "results":
            self._result_view().setFocus(Qt.FocusReason.OtherFocusReason)
        elif session.focus == "keyword":
            self._keyword_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._start_image_loading()  # covers that were still missing come from the cache or the site

    def _restored_request_to_rerun(self, session: SearchSession):
        """The request to send again on restore, or None when the cached results still hold."""

        request = session.active_request
        if not isinstance(request, tuple) or len(request) != 3:
            return None
        filters, scope, source = request
        if source != "iwara" or not isinstance(filters, SearchFilters):
            return request if session.pending else None
        fresh = (self._rerated_filters(filters), scope, source)
        if session.pending or fresh != request or session.account_epoch != self._account_epoch:
            return fresh
        return None

    def dismiss_transient(self) -> bool:
        """Esc on the page: first drop the selection, if there is one."""

        if not self._selected_data():
            return False
        self._results.clearSelection()
        self._results_table.clearSelection()
        self._sync_selection_buttons()
        return True

    def apply_external_query(self, request: dict):
        """Run an Iwara search asked for by another page (tag click, author, etc.).

        The window records the current search session first, so Back returns to it.
        """

        scope = str(request.get("scope") or "videos")
        keyword = str(request.get("keyword") or "").strip()
        sort = str(request.get("sort") or "")
        author = request.get("author")
        self._auto_search_timer.stop()
        self._set_combo_data(self._source_combo, "iwara")
        if isinstance(author, (tuple, list)) and len(author) == 4:
            self._show_author_works_target(tuple(author), record=False)
            return
        self._clear_author_navigation()
        self._set_combo_data(self._scope_combo, scope)
        self._keyword_edit.setText(keyword)
        self._sync_sort_options()
        if sort:
            self._sort_combo.blockSignals(True)
            self._set_combo_data(self._sort_combo, sort)
            self._sort_combo.blockSignals(False)
        self._start_search()
