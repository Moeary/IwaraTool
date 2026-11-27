"""Subscriptions: sources, their recent works, one source's items.

``SubscriptionStore`` lives on the shell.  Every database read and every
network refresh runs off the loop; refresh progress comes back through
``call_soon_threadsafe``.  Item lists, filters and sorts reuse
``presentation.subscription``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

import flet as ft

from ...core.subscription.batch import refresh_sources
from ...core.search.models import SearchVideo
from ...logging_setup import get_logger
from ...presentation.media.cards import CardView, video_card
from ...presentation.subscription import text as sub_text
from ..services.images import SUBSCRIPTION
from ...presentation.subscription.helpers import _source_search_text, _source_sort_key
from ...presentation.subscription.item_queries import (
    downloadable_ids,
    filter_items,
    is_feed_source,
    item_state,
    item_to_video,
    sort_items,
)
from ...presentation.subscription.source_actions import add_source_from_text, remove_sources

logger = get_logger(__name__)


def subscription_card(video: SearchVideo) -> CardView:
    """A work's card whose cover goes through the subscription cover cache, which finds the
    covers list endpoints leave out (a video stub without a thumbnail URL)."""
    card = video_card(video)
    local = str((video.raw or {}).get("_thumbnail_path", "") or "")
    return replace(card, cover_kind=SUBSCRIPTION, cover_key=video.video_id, cover_local=local or card.cover_local)

RECENT_PER_SOURCE = 8


@ft.observable
@dataclass
class SubscriptionState:
    loaded: bool = False
    loading: bool = False
    query: str = ""
    sort: str = "title"
    revision: int = 0  # sources or their recent works changed
    refreshing: bool = False
    refresh_done: int = 0
    refresh_total: int = 0
    refresh_line: str = ""
    selected_source: int = 0  # the source shown in the right pane (two-pane layout)


@ft.observable
@dataclass
class ItemsState:
    loading: bool = True
    mode: str = "all"
    query: str = ""
    sort: str = "date_desc"
    revision: int = 0
    selected: frozenset[str] = field(default_factory=frozenset)


class SourceItems:
    """One source's stored works, filtered and sorted for its grid."""

    def __init__(self, store: "SubscriptionStore", source_id: int):
        self.store = store
        self.source_id = source_id
        self.state = ItemsState()
        self.items: list[dict[str, Any]] = []
        self.visible: list[dict[str, Any]] = []
        self.cards: list[CardView] = []
        self.videos: dict[str, SearchVideo] = {}
        self.states: dict[str, str] = {}  # card key -> item_state

    @property
    def source(self) -> dict[str, Any]:
        return self.store.source(self.source_id) or {}

    async def load(self) -> None:
        self.state.loading = True
        try:
            items = await asyncio.to_thread(self.store.manager.get_subscription_items, self.source_id)
        except Exception:
            logger.exception("Reading subscription items failed")
            items = []
        self.items = list(items or [])
        self.state.loading = False
        self._rebuild()

    def _rebuild(self) -> None:
        state = self.state
        self.visible = sort_items(filter_items(self.items, state.mode, state.query), state.sort)
        source = self.source
        author = sub_text.source_title(source) if sub_text.source_is_author(source) else ""
        cards, videos, states = [], {}, {}
        for item in self.visible:
            video = item_to_video(item, author_name=author)
            card = subscription_card(video)
            if card.key in videos:
                continue
            videos[card.key] = video
            states[card.key] = item_state(item)
            cards.append(card)
        self.cards, self.videos, self.states = cards, videos, states
        state.selected = frozenset(key for key in state.selected if key in videos)
        state.revision += 1

    def set_mode(self, mode: str) -> None:
        self.state.mode = mode
        self._rebuild()

    def set_query(self, query: str) -> None:
        self.state.query = str(query or "")
        self._rebuild()

    def set_sort(self, sort: str) -> None:
        self.state.sort = sort
        self._rebuild()

    # ── selection ────────────────────────────────────────────────────────────

    def toggle(self, card: CardView) -> None:
        selected = set(self.state.selected)
        selected.symmetric_difference_update({card.key})
        self.state.selected = frozenset(selected)

    def select_all(self) -> None:
        self.state.selected = frozenset(self.videos)

    def clear_selection(self) -> None:
        self.state.selected = frozenset()

    def selected_ids(self) -> list[str]:
        return [self.videos[key].video_id for key in self.videos if key in self.state.selected]

    def operation_items(self) -> list[dict[str, Any]]:
        ids = set(self.selected_ids())
        return [item for item in self.visible if str(item.get("video_id", "") or "") in ids] if ids else list(self.visible)

    def download_ids(self, *, only_new: bool = False) -> list[str]:
        return downloadable_ids(self.operation_items(), only_new=only_new)


class SubscriptionStore:
    def __init__(self, shell, manager: Any):
        self.shell = shell
        self.manager = manager
        self.state = SubscriptionState()
        self.sources: list[dict[str, Any]] = []
        self.recent: dict[int, list[CardView]] = {}
        self.recent_videos: dict[int, dict[str, SearchVideo]] = {}
        self.items: dict[int, SourceItems] = {}

    # ── sources ──────────────────────────────────────────────────────────────

    def source(self, source_id: int) -> dict[str, Any] | None:
        return next((s for s in self.sources if int(s.get("id", 0) or 0) == int(source_id)), None)

    def visible_sources(self) -> list[dict[str, Any]]:
        query = self.state.query.strip().casefold()
        sources = [s for s in self.sources if not query or query in _source_search_text(s)]
        sort = self.state.sort
        return sorted(sources, key=lambda s: _source_sort_key(s, sort), reverse=sort in {"new_count", "undownloaded_count", "item_count", "last_checked_at", "created_at"})

    def set_query(self, query: str) -> None:
        self.state.query = str(query or "")
        self.state.revision += 1

    def set_sort(self, sort: str) -> None:
        self.state.sort = sort
        self.state.revision += 1

    async def load(self) -> None:
        state = self.state
        state.loading = True

        def read():
            sources = [s for s in self.manager.get_subscription_sources() if not is_feed_source(s)]
            recent = self.manager.get_subscription_recent_items(RECENT_PER_SOURCE, resolve_thumbnails=False)
            return sources, recent

        try:
            sources, recent = await asyncio.to_thread(read)
        except Exception:
            logger.exception("Reading subscriptions failed")
            sources, recent = [], {}
        self.sources = list(sources)
        self.recent, self.recent_videos = {}, {}
        for source in self.sources:
            source_id = int(source.get("id", 0) or 0)
            author = sub_text.source_title(source) if sub_text.source_is_author(source) else ""
            cards, videos = [], {}
            for item in recent.get(source_id, []) or []:
                video = item_to_video(item, author_name=author)
                card = subscription_card(video)
                if card.key not in videos:
                    videos[card.key] = video
                    cards.append(card)
            self.recent[source_id], self.recent_videos[source_id] = cards, videos
        ids = {int(s.get("id", 0) or 0) for s in self.sources}
        if state.selected_source not in ids:
            visible = self.visible_sources()
            state.selected_source = int(visible[0].get("id", 0) or 0) if visible else 0
        state.loading = False
        state.loaded = True
        state.revision += 1

    def items_for(self, source_id: int) -> SourceItems:
        model = self.items.get(source_id)
        if model is None:
            model = self.items[source_id] = SourceItems(self, source_id)
        return model

    def select_source(self, source_id: int) -> None:
        self.state.selected_source = int(source_id)

    async def reload_items(self, source_id: int | None = None) -> None:
        targets = [self.items[source_id]] if source_id in self.items else list(self.items.values())
        for model in targets:
            await model.load()

    # ── refreshing ───────────────────────────────────────────────────────────

    async def refresh(self, source_ids: list[int] | None = None, *, ignore_disabled: bool = False) -> None:
        from ..services.notify import notice

        state = self.state
        if state.refreshing:
            notice(self.shell.page, "info", sub_text.wait_for_refresh_text())
            return
        loop = asyncio.get_running_loop()

        def progress(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(self._on_progress, dict(event))

        state.refreshing = True
        state.refresh_done, state.refresh_total, state.refresh_line = 0, len(source_ids or []) or 1, sub_text.refreshing_title()
        try:
            summary = await asyncio.to_thread(
                refresh_sources, self.manager, list(source_ids or []), ignore_disabled=ignore_disabled, on_progress=progress
            )
        except Exception as exc:
            logger.exception("Refreshing subscriptions failed")
            summary = {"errors": [{"error": str(exc)}]}
        state.refreshing = False
        kind, title, content = sub_text.refresh_finished(summary or {})
        notice(self.shell.page, kind, title, content)
        await self.load()
        await self.reload_items(source_ids[0] if source_ids and len(source_ids) == 1 else None)

    def _on_progress(self, event: dict[str, Any]) -> None:
        if not self.state.refreshing or "total" not in event:
            return
        done, total, line = sub_text.refresh_progress(event)
        self.state.refresh_done, self.state.refresh_total, self.state.refresh_line = done, total, line

    # ── managing ─────────────────────────────────────────────────────────────

    async def add(self, text: str) -> bool:
        from ..services.notify import notice

        page = self.shell.page
        try:
            source_id = await asyncio.to_thread(add_source_from_text, self.manager, text)
        except Exception as exc:
            notice(page, "error", sub_text.add_title(), str(exc))
            return False
        if source_id is None:
            notice(page, "warning", sub_text.invalid_input_text())
            return False
        await self.load()
        if source_id:
            self.state.selected_source = source_id
            notice(page, "success", *sub_text.added_notice())
            page.run_task(self.refresh, [source_id], ignore_disabled=True)
        return True

    async def import_followed(self) -> None:
        from ..services.notify import notice

        try:
            result = await asyncio.to_thread(self.manager.import_followed_author_subscriptions)
        except Exception as exc:
            result = {"error": str(exc)}
        await self.load()
        kind, title, content = sub_text.import_finished(result or {})
        notice(self.shell.page, kind, title, content)

    async def remove(self, source_ids: list[int]) -> None:
        from ..services.notify import notice

        ids = list(dict.fromkeys(int(i) for i in source_ids if i))
        if not ids:
            return
        _question, summary = sub_text.delete_confirmation(self.sources, ids)
        await asyncio.to_thread(remove_sources, self.manager, ids)
        for source_id in ids:
            self.items.pop(source_id, None)
        await self.load()
        notice(self.shell.page, "success", sub_text.deleted_title(), summary)

    async def set_enabled(self, source_id: int, enabled: bool) -> None:
        await asyncio.to_thread(self.manager.set_subscription_enabled, source_id, enabled)
        await self.load()

    async def mark_source_seen(self, source_id: int) -> None:
        await asyncio.to_thread(self.manager.mark_subscription_source_seen, source_id)
        await self.load()
        await self.reload_items(source_id)

    # ── items ────────────────────────────────────────────────────────────────

    async def download(self, model: SourceItems, *, rule_id: str = "", only_new: bool = False) -> None:
        from ..services.notify import notice

        ids = model.download_ids(only_new=only_new)
        if not ids:
            notice(self.shell.page, "warning", sub_text.no_downloadable_text())
            return
        label = sub_text.source_title(model.source) if model.source else sub_text.default_batch_label()
        rule_id = rule_id or self.shell.downloads.page_rule("subscription")
        await self.shell.downloads.stage_job(
            lambda: self.manager.preview_subscription_items(ids, rule_id=rule_id, label=label)
        )
        model.clear_selection()
        await self.load()
        await model.load()

    async def mark_seen(self, model: SourceItems) -> None:
        ids = model.selected_ids() or [str(i.get("video_id", "") or "") for i in model.visible if i.get("is_new")]
        await asyncio.to_thread(self.manager.mark_subscription_items_seen, ids)
        await self.load()
        await model.load()

    async def mark_moved(self, model: SourceItems) -> None:
        from ..services.notify import notice

        ids = model.selected_ids()
        if not ids:
            notice(self.shell.page, "warning", sub_text.select_videos_first_text())
            return
        count = await asyncio.to_thread(self.manager.mark_subscription_items_downloaded, ids)
        notice(self.shell.page, "success", *sub_text.marked_notice(int(count or 0)))
        model.clear_selection()
        await self.load()
        await model.load()

    async def restore_moved(self, model: SourceItems) -> None:
        from ..services.notify import notice

        ids = model.selected_ids()
        if not ids:
            notice(self.shell.page, "warning", sub_text.select_moved_first_text())
            return
        count = await asyncio.to_thread(self.manager.restore_subscription_items_downloaded, ids)
        notice(self.shell.page, "success", *sub_text.restored_notice(int(count or 0)))
        model.clear_selection()
        await self.load()
        await model.load()
