"""The library (docs/flet_ui_plan.md 7.7): downloaded records, filtered, sorted and acted on.

Reading the database and checking every file on disk run off the loop; the
filters, sorting and counts come from ``presentation.history.library`` and
work on the in-memory list.  Removing records is undoable for a few seconds
(the delete runs when the notice goes away or the window closes).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

import flet as ft

from ...config import app_config
from ...core.nfo import NfoInfo, nfo_for_video
from ...logging_setup import get_logger
from ...presentation.history import library as lib
from ...presentation.media.cards import CardView
from ..services.images import SUBSCRIPTION

logger = get_logger(__name__)

PAGE_ROWS = 200  # rows built at a time; scrolling near the end adds the next batch
UNDO_SECONDS = 6.0
_VIEW_KEY = "history_view_mode_v1"  # same keys as the old page (separate data folders)
_SCOPE_KEY = "history_scope_v1"
_AUTHOR_SORT_KEY = "history_author_sort_v1"
_AUTOPLAY_KEY = "flet_library_autoplay"
VIEW_LIST, VIEW_COVER = "list", "cover"


def _ui(key: str, default: str, allowed) -> str:
    value = str(app_config.get_ui_value(key, default) or default)
    if key == _VIEW_KEY and value == "grid":
        value = VIEW_COVER  # the old grid and cover views are one card view here
    return value if value in allowed else default


@ft.observable
@dataclass
class LibraryState:
    loading: bool = False
    loaded: bool = False
    query: str = ""
    search_field: int = 0
    state_filter: int = lib.STATE_ALL
    scope: str = lib.SCOPE_ALL
    author: str = ""
    author_sort: str = lib.AUTHOR_SORT_COUNT
    sort: str = "downloaded"
    reverse: bool = True
    view: str = VIEW_LIST
    limit: int = PAGE_ROWS
    revision: int = 0
    selected: frozenset[str] = field(default_factory=frozenset)
    focused: str = ""  # the record in the side panel (wide windows)
    busy: bool = False
    tags: frozenset[str] = field(default_factory=frozenset)  # every one must match
    playing: str = ""  # the record playing in its panel / page (the library as a local player)
    autoplay: bool = True  # when one ends, play the next one in the list
    viewer: str = ""  # the record whose cover is shown full size
    sheet: str = ""  # the filter sheet: "" closed, "all" (phones: every filter) or "tags"


class LibraryStore:
    def __init__(self, shell, manager: Any):
        self.shell = shell
        self.manager = manager
        self.state = LibraryState(
            view=_ui(_VIEW_KEY, VIEW_LIST, (VIEW_LIST, VIEW_COVER)),
            scope=_ui(_SCOPE_KEY, lib.SCOPE_ALL, lib.SCOPES),
            author_sort=_ui(_AUTHOR_SORT_KEY, lib.AUTHOR_SORT_COUNT, (lib.AUTHOR_SORT_COUNT, lib.AUTHOR_SORT_NAME)),
            autoplay=app_config.get_ui_flag(_AUTOPLAY_KEY, True),
        )
        self.records: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self.states: dict[str, tuple[str, str, str, bool]] = {}
        self.visible: list[dict[str, Any]] = []
        self.counts = lib.LibraryCounts()
        self.authors: list[tuple[str, int]] = []
        self.tags: dict[str, tuple[str, ...]] = {}  # per record: NFO tags, then the saved Iwara ones
        self.nfo: dict[str, NfoInfo] = {}
        self.tag_list: list[tuple[str, int]] = []
        self._cards: dict[str, CardView] = {}
        self._pending: dict[str, dict[str, Any]] = {}  # removed, not deleted yet (undo)
        self._play_order: list[str] = []  # the list as it was when playing started (see neighbour)
        self._pending_handle: asyncio.TimerHandle | None = None
        self._generation = 0

    # ── reading ──────────────────────────────────────────────────────────────

    def state_of(self, record: dict[str, Any]) -> tuple[str, str, str]:
        found = self.states.get(str(record.get("video_id", "") or ""))
        return found[:3] if found else lib.compute_record_state(record)[:3]

    def record(self, video_id: str) -> dict[str, Any] | None:
        return self.by_id.get(video_id)

    async def load(self) -> None:
        state = self.state
        state.loading = True
        self._generation += 1
        generation = self._generation

        def read():
            records = self.manager.get_history_records()
            states = {str(r.get("video_id", "") or ""): lib.compute_record_state(r) for r in records}
            try:
                saved = self.manager.get_history_tags()
            except Exception:
                logger.exception("Reading the saved tags failed")
                saved = {}
            infos: dict[str, NfoInfo] = {}
            for record in records:
                video_id = str(record.get("video_id", "") or "")
                path = str(record.get("file_path", "") or "")
                if path and states.get(video_id, ("", ""))[1] == "ok":
                    info = nfo_for_video(path)
                    if info is not None:
                        infos[video_id] = info
            tags = {video_id: merged for video_id in set(saved) | set(infos)
                    if (merged := lib.merge_tags(infos[video_id].tags if video_id in infos else (), saved.get(video_id, ())))}
            return records, states, tags, infos

        try:
            records, states, tags, infos = await asyncio.to_thread(read)
        except Exception:
            logger.exception("Reading the download history failed")
            records, states, tags, infos = [], {}, {}, {}
        if generation != self._generation:
            return
        self.records = [r for r in records if str(r.get("video_id", "") or "") not in self._pending]
        self.by_id = {str(r.get("video_id", "") or ""): r for r in self.records if r.get("video_id")}
        self.states = states
        self.tags, self.nfo = tags, infos
        self._cards = {}
        self._rebuild_authors()
        self._rebuild_tags()
        state.loading = False
        state.loaded = True
        if state.focused and state.focused not in self.by_id:
            state.focused = ""
        self.apply()

    def _rebuild_authors(self) -> None:
        from ...i18n import current_language

        self.authors = lib.sort_authors(lib.author_counts(self.records), self.state.author_sort, language=current_language())
        if self.state.author and self.state.author not in dict(self.authors):
            self.state.author = ""

    def _rebuild_tags(self) -> None:
        self.tag_list = lib.tag_counts(self.records, self.tags_of)
        known = {tag.casefold() for tag, _count in self.tag_list}
        if any(tag.casefold() not in known for tag in self.state.tags):
            self.state.tags = frozenset(tag for tag in self.state.tags if tag.casefold() in known)

    def tags_of(self, record: dict[str, Any]) -> tuple[str, ...]:
        return self.tags.get(str(record.get("video_id", "") or ""), ())

    def apply(self) -> None:
        state = self.state
        visible, self.counts = lib.filter_records(
            self.records, state_of=self.state_of, query=state.query, field=state.search_field,
            state_filter=state.state_filter, scope=state.scope, author=state.author,
            tags=state.tags, tags_of=self.tags_of,
        )
        self.visible = lib.sort_records(visible, state.sort, state.reverse, state_of=self.state_of)
        state.selected = frozenset(i for i in state.selected if i in self.by_id)
        state.limit = PAGE_ROWS
        state.revision += 1

    def shown(self) -> list[dict[str, Any]]:
        return self.visible[: self.state.limit]

    def more(self) -> None:
        if self.state.limit < len(self.visible):
            self.state.limit += PAGE_ROWS

    def card(self, record: dict[str, Any]) -> CardView:
        video_id = str(record.get("video_id", "") or "")
        card = self._cards.get(video_id)
        if card is None:
            # Records rarely keep a cover URL: as in Classic, the subscription cover cache
            # looks the work up by id, and its copy is reused next time.
            card = self._cards[video_id] = replace(lib.record_card(record), cover_kind=SUBSCRIPTION, cover_key=video_id)
        return card

    @property
    def filtered(self) -> bool:
        state = self.state
        return (bool(state.query.strip()) or state.state_filter != lib.STATE_ALL or state.scope != lib.SCOPE_ALL
                or bool(state.tags))

    # ── filters ──────────────────────────────────────────────────────────────

    def set_query(self, value: str) -> None:
        self.state.query = str(value or "")
        self.apply()

    def set_field(self, value: str) -> None:
        self.state.search_field = lib.SEARCH_FIELDS.index(value) if value in lib.SEARCH_FIELDS else 0
        self.apply()

    def set_state_filter(self, value: int) -> None:
        self.state.state_filter = int(value)
        self.apply()

    def set_scope(self, scope: str) -> None:
        if scope in lib.SCOPES:
            self.state.scope = scope
            app_config.set_ui_value(_SCOPE_KEY, scope)
            self.apply()

    def toggle_tag(self, tag: str) -> None:
        chosen = {t.casefold(): t for t in self.state.tags}
        if tag.casefold() in chosen:
            chosen.pop(tag.casefold())
        else:
            chosen[tag.casefold()] = tag
        self.state.tags = frozenset(chosen.values())
        self.apply()

    def clear_tags(self) -> None:
        if self.state.tags:
            self.state.tags = frozenset()
            self.apply()

    def set_author(self, author: str) -> None:
        self.state.author = str(author or "")
        self.apply()

    def set_author_sort(self, order: str) -> None:
        self.state.author_sort = order
        app_config.set_ui_value(_AUTHOR_SORT_KEY, order)
        self._rebuild_authors()
        self.state.revision += 1

    def set_sort(self, column: str) -> None:
        """A new column starts in its natural direction; the same column again flips it."""
        state = self.state
        if column == state.sort:
            state.reverse = not state.reverse
        else:
            state.sort, state.reverse = column, column in lib.DESCENDING_FIRST
        self.apply()

    def set_view(self, view: str) -> None:
        self.state.view = view
        app_config.set_ui_value(_VIEW_KEY, view)

    # ── selection ────────────────────────────────────────────────────────────

    def toggle(self, video_id: str) -> None:
        self.state.selected = frozenset(set(self.state.selected) ^ {video_id})

    def select_all(self) -> None:
        self.state.selected = frozenset(str(r.get("video_id", "") or "") for r in self.visible)

    def clear_selection(self) -> None:
        self.state.selected = frozenset()

    def focus(self, video_id: str) -> None:
        self.state.focused = video_id

    def selected_moved(self) -> list[str]:
        return [i for i in self.state.selected if i in self.by_id and self.state_of(self.by_id[i])[1] == "moved"]

    # ── browsing and playing ─────────────────────────────────────────────────

    def neighbour(self, video_id: str, step: int) -> str:
        """The record ``step`` places away in the list as filtered and sorted now; "" past either end."""
        ids = [str(r.get("video_id", "") or "") for r in self.visible]
        if video_id in ids:
            index = ids.index(video_id) + step
            return ids[index] if 0 <= index < len(ids) else ""
        # Playing can take the record out of the list (the "unwatched" scope): walk on from where it
        # was when playing started, to the nearest record still listed.
        order, shown = self._play_order, set(ids)
        if video_id not in order:
            return ""
        index = order.index(video_id) + step
        while 0 <= index < len(order):
            if order[index] in shown:
                return order[index]
            index += step
        return ""

    def position(self, video_id: str) -> int:
        return next((i for i, r in enumerate(self.visible) if str(r.get("video_id", "") or "") == video_id), -1)

    def play(self, video_id: str) -> None:
        self.state.playing = video_id
        ids = [str(r.get("video_id", "") or "") for r in self.visible]
        if video_id in ids:
            self._play_order = ids
        record = self.by_id.get(video_id) or {}
        if video_id and not str(record.get("watched_at", "") or ""):
            self.shell.page.run_task(asyncio.to_thread, self.manager.set_videos_watched, [video_id], True)

    def stop(self) -> None:
        self.state.playing = ""

    def set_autoplay(self, value: bool) -> None:
        self.state.autoplay = bool(value)
        app_config.set_ui_value(_AUTOPLAY_KEY, self.state.autoplay)

    def view_cover(self, video_id: str) -> None:
        self.state.viewer = video_id

    # ── managing records ─────────────────────────────────────────────────────

    async def edit_details(self, video_id: str, fields: dict[str, str]) -> bool:
        return bool(await asyncio.to_thread(self.manager.update_history_details, video_id, fields))

    async def relocate(self, video_id: str, path: str) -> tuple[bool, str]:
        ok, message = await asyncio.to_thread(self.manager.relocate_history_file, video_id, path)
        return bool(ok), str(message or "")

    async def set_watched(self, video_ids: list[str], watched: bool) -> int:
        return int(await asyncio.to_thread(self.manager.set_videos_watched, list(video_ids), watched) or 0)

    # ── removing (undoable) ──────────────────────────────────────────────────

    def remove(self, video_ids: list[str]) -> None:
        from ..services.notify import notice

        ids = [i for i in dict.fromkeys(video_ids) if i in self.by_id]
        if not ids:
            return
        self.flush()  # an earlier removal is final once a new one starts
        for video_id in ids:
            self._pending[video_id] = self.by_id.pop(video_id)
        self.records = [r for r in self.records if str(r.get("video_id", "") or "") not in self._pending]
        if self.state.focused in self._pending:
            self.state.focused = ""
        self._rebuild_authors()
        self.apply()
        title, content = lib.removed_notice(len(ids))
        notice(self.shell.page, "success", title, content, action=lib.undo_text(), on_action=self.undo,
               duration_ms=int(UNDO_SECONDS * 1000))
        self._pending_handle = asyncio.get_running_loop().call_later(UNDO_SECONDS + 0.5, self._commit_later)

    def undo(self) -> None:
        if self._pending_handle is not None:
            self._pending_handle.cancel()
            self._pending_handle = None
        restored, self._pending = self._pending, {}
        if not restored:
            return
        self.records.extend(restored.values())
        self.by_id.update(restored)
        self._rebuild_authors()
        self.apply()

    def _commit_later(self) -> None:
        self._pending_handle = None
        ids, self._pending = list(self._pending), {}
        if ids:
            self.shell.page.run_task(asyncio.to_thread, self.manager.remove_history_records, ids)

    def flush(self) -> None:
        """Delete what is waiting for undo now (a new removal, or the window closing)."""
        if self._pending_handle is not None:
            self._pending_handle.cancel()
            self._pending_handle = None
        ids, self._pending = list(self._pending), {}
        if ids:
            try:
                self.manager.remove_history_records(ids)
            except Exception:
                logger.exception("Removing history records failed")

    # ── other actions ────────────────────────────────────────────────────────

    async def clean_moved(self, video_ids: list[str] | None = None) -> dict[str, Any]:
        """Selected moved records, or (``None``) every moved record via the download-folder sync."""
        self.state.busy = True
        try:
            if video_ids is None:
                result = await asyncio.to_thread(self.manager.sync_history_with_download_folder)
            else:
                result = {"removed": await asyncio.to_thread(self.manager.remove_history_records, video_ids)}
        finally:
            self.state.busy = False
        self.clear_selection()
        await self.load()
        return result or {}

    async def rename(self, video_id: str, new_name: str) -> tuple[bool, str]:
        ok, message = await asyncio.to_thread(self.manager.rename_history_file, video_id, new_name)
        if ok:
            await self.load()
        return bool(ok), str(message or "")

    async def export(self, path: str) -> int:
        records = list(self.visible)
        await asyncio.to_thread(lib.write_history_csv, path, records)
        return len(records)
