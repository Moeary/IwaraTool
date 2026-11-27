"""Home rows: each row paints from the cache, re-checks when stale.

The decisions are ``presentation.home.section_feed.SectionFeed``'s; a
``HomeRow`` runs the loads off the loop and keeps an observable ``RowState``
for its view.  ``HomeStore`` lives on the shell, so switching away and back
shows the rows at once.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import flet as ft

from ...config import app_config
from ...core.home.cache import shared_cache
from ...core.home.feed import HOME_PAGE_LIMIT, FeedSection, home_sections, load_specs
from ...core.home.loading import FeedResult, load_feed_page
from ...core.search.models import SearchVideo
from ...logging_setup import get_logger
from ...presentation import preferences
from ...presentation.home import section_feed as rules
from ...presentation.media.cards import CardView, video_card

logger = get_logger(__name__)

_CACHE_OWNER_KEY = "home_cache_account_v1"  # login whose rows the cache's account entries hold


@ft.observable
@dataclass
class RowState:
    cards: list[CardView] = field(default_factory=list)
    tab_id: str = ""
    collapsed: bool = False
    loading: bool = False  # nothing on screen while loading: skeleton / spinner
    busy: bool = False  # cards on screen, a re-check is running
    message: str = ""
    login_required: bool = False
    retry: bool = False
    age: str = ""
    age_tooltip: str = ""
    pending: bool = False
    selected: frozenset[str] = field(default_factory=frozenset)


class HomeRow:
    def __init__(self, store: "HomeStore", section: FeedSection):
        self.store = store
        self.section = section
        self.feed = rules.SectionFeed(section, store.cache)
        self.feed.set_rating(preferences.current_rating())
        self.state = RowState(tab_id=self.feed.tab_id, collapsed=self.feed.collapsed)
        self.videos: dict[str, SearchVideo] = {}

    # ── controls ─────────────────────────────────────────────────────────────

    def select_tab(self, tab_id: str) -> None:
        if self.feed.select_tab(tab_id):
            self.state.tab_id = self.feed.tab_id
            self.store.run(self.reload)

    def toggle_fold(self) -> None:
        self.set_folded(not self.feed.collapsed)

    def set_folded(self, folded: bool) -> None:
        if folded == self.feed.collapsed:
            return
        self.feed.set_collapsed(folded)
        self.state.collapsed = folded
        if not folded:
            self.store.run(self.reload)

    def toggle(self, card: CardView) -> None:
        if not card.selectable:
            return
        selected = set(self.state.selected)
        selected.symmetric_difference_update({card.key})
        self.state.selected = frozenset(selected)
        if not selected and self.feed.pending is not None:
            self.apply_pending()

    def clear_selection(self) -> None:
        self.state.selected = frozenset()
        if self.feed.pending is not None:
            self.apply_pending()

    def shown_videos(self) -> list[SearchVideo]:
        keys = self.state.selected or [card.key for card in self.state.cards]
        return [self.videos[key] for key in keys if key in self.videos]

    def queueable(self) -> list[SearchVideo]:
        ids = set(rules.queueable_ids(self.shown_videos()))
        return [video for video in self.shown_videos() if video.video_id in ids]

    # ── loading ──────────────────────────────────────────────────────────────

    def _show(self, rows: list[dict], saved_at: float, key: str) -> None:
        items = rules.items_for(self.feed, rows)
        self.feed.mark_shown(rows, saved_at, key)
        cards, videos = [], {}
        for item in items:
            card = video_card(item)
            if card.key not in videos:
                videos[card.key] = item
                cards.append(card)
        self.videos = videos
        self.state.pending = False
        self.state.cards = cards
        self.state.message = "" if cards else rules.empty_text()
        self.state.age = self.feed.age_text()

    def _clear(self) -> None:
        self.videos = {}
        self.state.cards = []
        self.feed.clear_shown()

    async def reload(self, force: bool = False) -> None:
        state = self.state
        state.retry = False
        plan = self.feed.plan_reload(force=force, logged_in=self.store.logged_in(), has_items=bool(state.cards))
        if plan.login_required:
            self._clear()
            state.login_required = True
            state.message = rules.login_prompt()
            state.age = ""
            return
        state.login_required = False
        if plan.clear:
            self._clear()
        if plan.show is not None:
            self._show(*plan.show)
        else:
            state.age = self.feed.age_text()
        if plan.fetch:
            await self._fetch(keep_cards=plan.keep_cards)

    async def _fetch(self, *, keep_cards: bool) -> None:
        token, tab, params = self.feed.begin_fetch()
        state = self.state
        if keep_cards:
            state.busy = True
        else:
            state.loading = True
            state.message = rules.loading_text()
        try:
            result = await asyncio.to_thread(
                load_feed_page, self.store.manager, tab.kind, params, 0, HOME_PAGE_LIMIT,
                mode=tab.mode, value=tab.value, rating=self.feed.rating, token=token,
            )
        except Exception as exc:
            logger.exception("Loading a Home row failed")
            result = FeedResult(token=token, error=str(exc))
        self._apply(result)

    def _apply(self, result: FeedResult) -> None:
        state = self.state
        outcome = self.feed.on_result(result, has_items=bool(state.cards), has_selection=bool(state.selected))
        if outcome.action == rules.IGNORE:
            return
        state.busy = False
        state.loading = False
        if outcome.action == rules.STASHED:
            return
        if outcome.action == rules.ERROR_KEPT:
            state.age, state.age_tooltip = rules.refresh_failed_text(), outcome.error
            return
        if outcome.action == rules.ERROR:
            state.message = rules.load_failed_text(outcome.error)
            state.retry = True
            return
        state.age_tooltip = ""
        if outcome.action == rules.UNCHANGED:
            state.age = self.feed.age_text()
        elif outcome.action == rules.PENDING:
            state.pending = True
        else:
            self._show(outcome.rows or [], time.time(), self.feed.cache_key())

    def apply_pending(self) -> None:
        rows = self.feed.take_pending()
        self.state.pending = False
        if rows is not None:
            self._show(rows, time.time(), self.feed.cache_key())

    def refresh_age(self) -> None:
        if self.state.cards and not self.state.age_tooltip:
            self.state.age = self.feed.age_text()


class HomeStore:
    def __init__(self, shell, manager: Any):
        self.shell = shell
        self.manager = manager
        self.cache = shared_cache()
        self.rows: list[HomeRow] = []
        self.revision = 0
        self.rebuild()

    def run(self, coroutine_fn, *args) -> None:
        self.shell.page.run_task(coroutine_fn, *args)

    def logged_in(self) -> bool:
        return bool(self.shell.state.logged_in)

    def rebuild(self) -> None:
        """Rows from the saved layout (after Settings changed it)."""
        self.rows = [HomeRow(self, section) for section in home_sections(load_specs())]
        self.revision += 1

    def row(self, section_id: str) -> HomeRow | None:
        return next((row for row in self.rows if row.section.id == section_id), None)

    async def reload_all(self, force: bool = False) -> None:
        await asyncio.gather(*(row.reload(force) for row in self.rows), return_exceptions=True)

    def set_rating(self, rating: str) -> None:
        for row in self.rows:
            row.feed.set_rating(rating)
        self.run(self.reload_all)

    def on_login_changed(self) -> None:
        """Sign-in, sign-out or another account: rows of the old account must not stay.

        The account rows drop what they show and any fetch still running (its
        token no longer matches).  Cached account rows are kept only for the
        account that saved them, so restoring the same login at start-up still
        paints from the cache, while another account starts empty.
        """
        rows = [row for row in self.rows if row.feed.tab.needs_login]
        for row in rows:
            row.feed.token += 1
            row._clear()
            row.state.selected = frozenset()
            row.state.loading = row.state.busy = False
        if self.logged_in():
            owner = str(app_config.username or "").strip().lower()
            if str(app_config.get_ui_value(_CACHE_OWNER_KEY, "") or "") != owner:
                self.cache.clear(account_only=True)
                app_config.set_ui_value(_CACHE_OWNER_KEY, owner)
        if rows:
            self.run(self._reload_rows, rows)

    async def _reload_rows(self, rows: list[HomeRow]) -> None:
        await asyncio.gather(*(row.reload() for row in rows), return_exceptions=True)

    def fold_all(self, folded: bool) -> None:
        for row in self.rows:
            row.set_folded(folded)

    def refresh_ages(self) -> None:
        for row in self.rows:
            row.refresh_age()
