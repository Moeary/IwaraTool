"""The search page's state and its work (docs/flet_ui_plan.md 7.2).

``SearchStore`` lives on the shell, so a search survives switching away and
back.  Each new search cancels the previous one's token; every blocking call
(``run_search``, the history lookup, Oreno3D resolution) runs off the loop and
only results of the current generation are kept.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import flet as ft

from ...config import app_config
from ...core.cancellation import CancelToken
from ...core.rating import api_rating
from ...core.search.limits import configured_resolution_concurrency, resolution_mode
from ...core.search.models import SearchFilters, SearchPageResult
from ...core.search.post_filter import PostFilter, apply_post_filter
from ...core.search.resolution import OrenoLinkResolver, apply_oreno_link
from ...core.search.service import run_search
from ...logging_setup import get_logger
from ...presentation import preferences
from ...presentation.media.cards import CardView, card_for
from ...presentation.search import options, tag_completion
from ...presentation.search.download_status import SearchDownloadStatus, query_download_statuses
from ...presentation.search.history_entries import read_search_history, record_search
from ...presentation.search.request import PROBLEM_WARNING, ResultSet, page_size, request_problem

AUTO_SEARCH_NEEDS_KEYWORD = frozenset({"authors", "tags", "playlists"})

logger = get_logger(__name__)


@ft.observable
@dataclass
class SearchState:
    keyword: str = ""
    source: str = options.SOURCE_IWARA
    scope: str = "videos"
    sort: str = "date"
    loading: bool = False
    status: str = ""
    searched: bool = False
    revision: int = 0  # results or their download states changed
    selected: frozenset[str] = field(default_factory=frozenset)
    selecting: bool = False
    current_page: int = 0
    last_page: int | None = None
    has_next: bool = False
    history: list[dict[str, str]] = field(default_factory=list)
    post_filter: PostFilter = field(default_factory=PostFilter)
    suggestions: list[tuple[str, str, str]] = field(default_factory=list)  # (tag key, label, tooltip)


class SearchStore:
    def __init__(self, shell, manager: Any):
        self.shell = shell
        self.manager = manager
        default = str(app_config.search_default_source or options.SOURCE_ORENO3D)
        if default == options.SOURCE_ORENO3D and not options.oreno3d_available():
            default = options.SOURCE_IWARA
        self.state = SearchState(source=default, history=read_search_history())
        self.results = ResultSet()
        self.items: dict[str, Any] = {}  # card key -> SearchVideo / SearchAuthor / SearchPlaylist
        self.cards: list[CardView] = []
        self.statuses: dict[str, SearchDownloadStatus] = {}
        self._token = CancelToken()
        self._generation = 0
        self._request: tuple[SearchFilters, str, str] | None = None
        self._source_pinned = False
        self._auto_routed = False
        self._bump_pending = False
        self._suggest_serial = 0

    # ── query controls ───────────────────────────────────────────────────────

    def set_keyword(self, text: str) -> None:
        self.state.keyword = str(text or "")
        self._fix_sort()
        self._schedule_suggestions()

    # ── tag suggestions (the "tags" scope) ───────────────────────────────────

    def _schedule_suggestions(self) -> None:
        self._suggest_serial += 1
        if self.state.scope != "tags" or not self.state.keyword.strip():
            if self.state.suggestions:
                self.state.suggestions = []
            return
        page = getattr(self.shell, "page", None)
        if page is not None:
            page.run_task(self._suggest, self._suggest_serial)

    async def _suggest(self, serial: int) -> None:
        await asyncio.sleep(0.15)  # typing: wait for a pause
        if serial != self._suggest_serial:
            return
        state = self.state
        try:
            found = await asyncio.to_thread(
                tag_completion.suggestions_for, self.manager, state.keyword, scope=state.scope, source=state.source
            )
        except Exception:
            logger.exception("Tag suggestions failed")
            found = []
        if serial == self._suggest_serial:
            state.suggestions = [(s.key, s.display_text, tag_completion.suggestion_tooltip(s)) for s in found]

    def apply_suggestion(self, key: str) -> None:
        self.state.keyword = tag_completion.complete(self.state.keyword, key, self.state.source)
        self._suggest_serial += 1
        self.state.suggestions = []

    def set_source(self, source: str) -> None:
        if source == options.SOURCE_ORENO3D and not options.oreno3d_available():
            return
        self._source_pinned = True
        self._auto_routed = False
        self.state.source = source
        if self.state.scope not in options.supported_scopes(source):
            self.state.scope = "videos"
        self._fix_sort()
        self.auto_search()

    def set_scope(self, scope: str) -> None:
        self.state.scope = scope if scope in options.supported_scopes(self.state.source) else "videos"
        self._fix_sort()
        self._schedule_suggestions()
        self.auto_search()

    def set_sort(self, sort: str) -> None:
        self.state.sort = sort
        self._fix_sort()
        self.auto_search()

    def auto_search(self) -> None:
        """Search at once, as the classic page does, when "auto search" is on (Settings → Search).

        Opening the page and changing the source, scope or sort start it; authors,
        tags and playlists wait for a keyword.  Empty-keyword searches stay out of
        the history.
        """
        if not app_config.search_auto_search_enabled:
            return
        keyword = self.state.keyword.strip()
        if self.state.scope in AUTO_SEARCH_NEEDS_KEYWORD and not keyword:
            return
        self.shell.page.run_task(self._auto_search, bool(keyword))

    async def _auto_search(self, record: bool) -> None:
        await self.search(record=record)

    def sort_choices(self) -> list[tuple[str, str]]:
        return options.sort_choices(self.state.source, self.state.scope, self.state.keyword)

    def _fix_sort(self) -> None:
        keys = [key for _label, key in self.sort_choices()]
        if self.state.sort not in keys:
            self.state.sort = keys[0]

    def apply_history(self, entry: dict[str, str]) -> None:
        self._source_pinned = True
        self.state.source = entry.get("source", self.state.source)
        self.state.scope = entry.get("scope", "videos")
        self.state.keyword = entry.get("keyword", "")
        self.state.sort = entry.get("sort", "date")
        self._fix_sort()
        self.shell.page.run_task(self.search)

    # ── searching ────────────────────────────────────────────────────────────

    def _filters(self) -> SearchFilters:
        iwara = self.state.source == options.SOURCE_IWARA
        keyword = self.state.keyword.strip()
        rating = api_rating(preferences.current_rating()) if iwara else ""
        return SearchFilters(keyword=keyword, sort=self.state.sort, rating=rating, page_size=page_size(iwara, rating, keyword))

    async def search(self, *, record: bool = True) -> None:
        from ..services.notify import notice

        state = self.state
        target, self._auto_routed = options.keyword_route(
            state.keyword, state.scope, state.source, pinned=self._source_pinned, auto_routed=self._auto_routed
        )
        if target:
            state.source = target
            self._fix_sort()
            if target == options.SOURCE_ORENO3D:
                notice(self.shell.page, "info", *options.routed_text(), duration_ms=2500)
        filters = self._filters()
        problem = request_problem(state.scope, state.source, filters.keyword)
        if problem is not None:
            level, message = problem
            notice(self.shell.page, "warning" if level == PROBLEM_WARNING else "error", message)
            return
        if record:
            state.history = await asyncio.to_thread(record_search, state.keyword, state.source, state.scope, state.sort)
        self._request = (filters, state.scope, state.source)
        self._suggest_serial += 1
        state.suggestions = []
        self._restart()
        self.results.clear()
        self.items.clear()
        self.cards = []
        self.statuses.clear()
        state.selected = frozenset()
        state.selecting = False
        state.searched = True
        state.revision += 1
        await self._run_page(0, replace=True)

    async def rerun(self) -> None:
        """The rating or site changed: read the same query again."""
        if self._request is not None and self.state.source == options.SOURCE_IWARA:
            await self.search(record=False)

    def _restart(self) -> None:
        self._token.cancel("superseded")
        self._token = CancelToken()
        self._generation += 1

    async def go_page(self, page: int) -> None:
        if self._request is None or self.state.loading:
            return
        self._restart()
        self.state.selected = frozenset()
        await self._run_page(max(0, page), replace=True)

    async def load_more(self) -> None:
        if self._request is None or self.state.loading or not self.state.has_next:
            return
        next_page = self.results.next_page
        if next_page is None:
            next_page = self.results.current_page + 1
        await self._run_page(next_page, replace=False)

    async def _run_page(self, page: int, *, replace: bool) -> None:
        from ..services.notify import notice

        assert self._request is not None
        filters, scope, source = self._request
        generation, token = self._generation, self._token
        state = self.state
        state.loading = True
        state.status = options.searching_text()
        try:
            result: SearchPageResult = await asyncio.to_thread(
                run_search, filters, scope, page, source=source, client=self.manager, should_stop=lambda: token.cancelled
            )
        except Exception as exc:
            logger.exception("Search failed")
            result = SearchPageResult(scope=scope, error=str(exc))
        if generation != self._generation:
            return
        self.results.add(result, replace=replace)
        self._rebuild_cards()
        state.loading = False
        state.current_page = self.results.current_page
        state.last_page = self.results.last_page
        state.has_next = (
            self.results.current_page < self.results.last_page
            if self.results.last_page is not None
            else self.results.next_page is not None
        )
        state.status = options.failed_text(result.error) if result.error else self.found_text()
        state.revision += 1
        if result.error:
            notice(self.shell.page, "warning", result.error)
        self.shell.page.run_task(self.refresh_statuses)
        if result.videos and resolution_mode() == "eager":
            self.shell.page.run_task(self._resolve_oreno, list(result.videos), generation, token)

    def found_text(self) -> str:
        return options.found_text(
            self.state.scope,
            videos=len(self.results.videos),
            authors=len(self.results.authors),
            playlists=len(self.results.playlists),
            total=self.results.total,
            shown=len(self._visible_videos()) if self.post_filter_applies() else None,
        )

    # ── post-search filter ───────────────────────────────────────────────────

    def grid_only(self) -> bool:
        """Author and playlist results have no statistics to filter on."""
        return self.state.scope == "authors" or bool(self.results.playlists)

    def post_filter_applies(self) -> bool:
        return self.state.post_filter.is_active() and not self.grid_only()

    def _visible_videos(self) -> list:
        if self.post_filter_applies():
            return apply_post_filter(self.results.videos, self.state.post_filter)
        return list(self.results.videos)

    def set_post_filter(self, post_filter: PostFilter) -> None:
        if post_filter == self.state.post_filter:
            return
        self.state.post_filter = post_filter
        self._rebuild_cards()
        self.state.selected = frozenset(key for key in self.state.selected if key in self.items)
        self.state.selecting = bool(self.state.selected)
        if self.state.searched and not self.state.loading:
            self.state.status = self.found_text()
        self.state.revision += 1

    def _rebuild_cards(self) -> None:
        self.items.clear()
        cards = []
        for item in (*self.results.playlists, *self._visible_videos(), *self.results.authors):
            card = card_for(item)
            if card is not None and card.key not in self.items:
                self.items[card.key] = item
                cards.append(card)
        self.cards = cards

    # ── download states and Oreno3D links ────────────────────────────────────

    async def refresh_statuses(self) -> None:
        ids = [card.history_id for card in self.cards if card.history_id]
        if not ids:
            return
        generation = self._generation
        try:
            statuses = await asyncio.to_thread(query_download_statuses, self.manager.history, ids)
        except Exception:
            logger.exception("Could not read download states for search results")
            return
        if generation == self._generation and statuses != self.statuses:
            self.statuses = statuses
            self.state.revision += 1

    async def _resolve_oreno(self, videos: list, generation: int, token: CancelToken) -> None:
        candidates = [v for v in videos if v.source_kind == "oreno3d" and not v.iwara_url and not v.download_video_id]
        if not candidates:
            return
        loop = asyncio.get_running_loop()

        def on_item(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(self._on_link, event, generation)

        def on_progress(done: int, total: int) -> None:
            loop.call_soon_threadsafe(self._on_resolve_progress, done, total, generation)

        resolver = OrenoLinkResolver(
            self.manager,
            generation,
            candidates,
            concurrency=configured_resolution_concurrency(),
            hydrate_metadata=True,
            on_item=on_item,
            on_progress=on_progress,
            should_stop=lambda: token.cancelled,
        )
        try:
            await asyncio.to_thread(resolver.run)
        except Exception:
            logger.exception("Resolving Oreno3D results failed")
        if generation == self._generation:
            self.state.status = self.found_text()
            await self.refresh_statuses()

    def _on_resolve_progress(self, done: int, total: int, generation: int) -> None:
        if generation == self._generation and total and done < total:
            self.state.status = options.resolving_ids_text(done, total)

    def _on_link(self, event: dict[str, Any], generation: int) -> None:
        if generation != self._generation:
            return
        key = f"video:{event.get('video_id') or ''}"
        video = self.items.get(key)
        link = event.get("link")
        if video is None or not isinstance(link, dict) or not apply_oreno_link(video, link):
            return
        self._schedule_bump()

    def _schedule_bump(self) -> None:
        """Many links land at once: rebuild the cards at most every 200 ms."""
        if self._bump_pending:
            return
        self._bump_pending = True

        async def bump() -> None:
            await asyncio.sleep(0.2)
            self._bump_pending = False
            self._rebuild_cards()
            if self.post_filter_applies() and not self.state.loading:
                self.state.status = self.found_text()
            self.state.revision += 1

        self.shell.page.run_task(bump)

    # ── selection ────────────────────────────────────────────────────────────

    def toggle(self, card: CardView) -> None:
        if not card.selectable:
            return
        selected = set(self.state.selected)
        selected.symmetric_difference_update({card.key})
        self.state.selected = frozenset(selected)
        self.state.selecting = bool(selected)

    def select_all(self) -> None:
        self.state.selected = frozenset(card.key for card in self.cards if card.selectable)
        self.state.selecting = bool(self.state.selected)

    def clear_selection(self) -> None:
        self.state.selected = frozenset()
        self.state.selecting = False

    def selected_values(self) -> list[dict[str, Any]]:
        values = []
        for card in self.cards:
            if card.key in self.state.selected:
                kind = "playlist" if card.kind == "playlist" else "video"
                values.append({"kind": kind, "key": card.key, "data": self.items[card.key]})
        return values

    def close(self) -> None:
        self._token.cancel("closed")
