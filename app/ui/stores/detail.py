"""A post's detail page: the record, related posts, comments, likes.

``DetailStore`` keeps the last few posts on the shell, so Back to a post
repaints from the answers already fetched (no new request, comments and
opened replies kept).  Each ``DetailModel`` loads off the loop and pins the
site the post was opened on, like the old view.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import flet as ft

from ...core import site as sites
from ...core.home.loading import call_with_client, load_comments, load_detail
from ...core.playback import video_player
from ...core.search.models import SearchVideo
from ...core.search.normalize import normalize_image, normalize_video
from ...logging_setup import get_logger
from ...presentation.media import comments as comment_text
from ...presentation.media import detail_info
from ...presentation.media.cards import CardView, video_card
from ...presentation.media.comments import CommentView, comment_view

logger = get_logger(__name__)

KEEP_POSTS = 12


@ft.observable
@dataclass
class DetailViewState:
    loading: bool = True
    error: str = ""
    video: SearchVideo | None = None
    revision: int = 0  # the record changed (likes after a like, …)
    liked: bool = False
    like_busy: bool = False
    playing: bool = False  # the cover turned into the player (reset on leaving the page)
    local: str = ""  # the downloaded copy (file, or its NAS URL): plays even when the post cannot load
    author: detail_info.AuthorInfo | None = None
    related: list[CardView] = field(default_factory=list)
    comments: list[CommentView] = field(default_factory=list)
    comment_total: int | None = None
    comment_error: str = ""
    comments_loading: bool = False
    more_comments: bool = False
    replies: dict[str, list[CommentView]] = field(default_factory=dict)
    replies_loading: frozenset[str] = field(default_factory=frozenset)


class DetailModel:
    def __init__(self, store: "DetailStore", kind: str, item_id: str):
        self.store = store
        self.kind = "image" if kind == "image" else "video"
        self.item_id = str(item_id)
        self.site = sites.current_site()
        self.info: dict[str, Any] = {}
        self.related: dict[str, SearchVideo] = {}
        self.comment_page = 0
        self.state = DetailViewState()
        self.started = False

    # ── what the page shows ──────────────────────────────────────────────────

    @property
    def page_url(self) -> str:
        return detail_info.page_url(self.kind, self.item_id, self.site)

    @property
    def can_play(self) -> bool:
        video = self.state.video
        if self.kind != "video" or video is None:
            return False
        return video.downloadable or bool(self.state.local)

    async def check_local(self) -> None:
        """Look for a downloaded copy (each visit: it may have just finished downloading)."""
        if self.kind != "video":
            return
        try:
            self.state.local = await asyncio.to_thread(video_player.downloaded_copy, self.item_id, self.store.manager)
        except Exception:
            logger.debug("Looking for a downloaded copy failed", exc_info=True)

    def meta_text(self) -> str:
        return detail_info.meta_text(self.state.video, self.kind) if self.state.video else ""

    def tags(self) -> list[tuple[str, str]]:
        return detail_info.tag_chips(self.info.get("tags"), getattr(self.store.manager, "tag_dictionary", None))

    def gallery(self) -> list[detail_info.GalleryFile]:
        return [detail_info.gallery_file(f) for f in detail_info.gallery_files(self.info)] if self.kind == "image" else []

    def description(self) -> str:
        return str(self.info.get("body") or "").strip()

    def quality(self) -> str:
        return detail_info.file_quality(self.info)

    def embedded(self) -> bool:
        return self.kind == "video" and self.state.video is not None and not self.state.video.downloadable and bool(self.info.get("embedUrl"))

    def comments_title(self) -> str:
        return comment_text.comments_title(self.state.comment_total)

    # ── loading ──────────────────────────────────────────────────────────────

    async def start(self, preview: SearchVideo | None = None) -> None:
        if self.started:
            return
        self.started = True
        if preview is not None:
            self.state.video = preview
        loop = asyncio.get_running_loop()

        def deliver(fn, *args):
            loop.call_soon_threadsafe(fn, *args)

        def work():
            with sites.site_scope(self.site):
                load_detail(
                    self.store.manager, self.kind, self.item_id,
                    on_info=lambda info, error: deliver(self._on_info, info, error),
                    on_related=lambda videos: deliver(self._on_related, videos),
                    on_comments=lambda rows, total, error: deliver(self._on_comments, rows, total, error, 0),
                    should_stop=lambda: self.store.closed,
                )

        self.state.comments_loading = True
        try:
            await asyncio.to_thread(work)
        except Exception as exc:
            logger.exception("Loading a post failed")
            self._on_info(None, str(exc))
        self.state.loading = False
        self.state.comments_loading = False

    def _on_info(self, info: dict | None, error: str) -> None:
        state = self.state
        state.loading = False
        if not isinstance(info, dict):
            state.error = detail_info.load_failed_text(error)
            state.comments_loading = False
            self.started = False  # a later visit tries again
            return
        self.info = info
        self.site = sites.site_of_item(info, self.site) or self.site
        video = (normalize_image if self.kind == "image" else normalize_video)(info)
        if video is not None:
            if state.video is not None and not video.thumbnail_url:
                video.thumbnail_url = state.video.thumbnail_url
            state.video = video
        state.liked = bool(info.get("liked"))
        state.author = detail_info.author_of(info, video)
        state.error = ""
        state.revision += 1

    def _on_related(self, videos: list[SearchVideo]) -> None:
        videos = detail_info.rated_related([v for v in videos if isinstance(v, SearchVideo)])
        self.related = {}
        cards = []
        for video in videos:
            card = video_card(video)
            if card.key not in self.related and video.video_id != self.item_id:
                self.related[card.key] = video
                cards.append(card)
        self.state.related = cards

    def _on_comments(self, rows: list[dict], total: int | None, error: str, page: int) -> None:
        state = self.state
        views = [comment_view(row) for row in rows if isinstance(row, dict)]
        state.comments = (state.comments if page else []) + views
        state.comment_total = total
        state.comment_error = error
        state.comments_loading = False
        self.comment_page = page
        state.more_comments = comment_text.more_comments_available(total, len(state.comments), rows)

    async def more_comments(self) -> None:
        if self.state.comments_loading or not self.state.more_comments:
            return
        page = self.comment_page + 1
        self.state.comments_loading = True
        rows, total, error = await asyncio.to_thread(self._comments, page, "")
        self._on_comments(rows, total, error, page)

    async def load_replies(self, comment_id: str) -> None:
        state = self.state
        if comment_id in state.replies or comment_id in state.replies_loading:
            return
        state.replies_loading = state.replies_loading | {comment_id}
        rows, _total, error = await asyncio.to_thread(self._comments, 0, comment_id)
        state.replies_loading = state.replies_loading - {comment_id}
        if error and not rows:
            from ..services.notify import notice

            notice(self.store.shell.page, "warning", error)
            return
        state.replies = {**state.replies, comment_id: [comment_view(row) for row in rows if isinstance(row, dict)]}

    def _comments(self, page: int, parent: str):
        with sites.site_scope(self.site):
            return load_comments(self.store.manager, self.kind, self.item_id, page=page, parent=parent)

    # ── like ─────────────────────────────────────────────────────────────────

    async def toggle_like(self) -> None:
        from ..services.notify import notice

        state = self.state
        if state.like_busy or state.video is None:
            return
        page = self.store.shell.page
        if not self.store.shell.state.logged_in:
            notice(page, "warning", *detail_info.sign_in_to_like())
            return
        wanted = not state.liked
        kind, item_id = self.kind, self.item_id
        state.like_busy = True

        def work():
            with sites.site_scope(self.site):
                return call_with_client(self.store.manager, lambda client: client.set_liked(kind, item_id, wanted))

        result, error = await asyncio.to_thread(work)
        state.like_busy = False
        ok, message = result if isinstance(result, tuple) and len(result) == 2 else (False, error)
        if not ok:
            notice(page, "error", detail_info.like_failed_title(), str(message or error))
            return
        state.liked = wanted
        if state.video is not None:
            state.video.likes = max(0, state.video.likes + (1 if wanted else -1))
        state.revision += 1


class DetailStore:
    def __init__(self, shell, manager: Any):
        self.shell = shell
        self.manager = manager
        self.closed = False
        self._models: OrderedDict[tuple[str, str], DetailModel] = OrderedDict()
        self.previews: dict[str, SearchVideo] = {}  # what the card already knew, painted at once

    def model(self, kind: str, item_id: str) -> DetailModel:
        key = ("image" if kind == "image" else "video", str(item_id))
        model = self._models.get(key)
        if model is None:
            model = self._models[key] = DetailModel(self, *key)
        self._models.move_to_end(key)
        while len(self._models) > KEEP_POSTS:
            self._models.popitem(last=False)
        return model

    def remember(self, video: SearchVideo | None, key: str = "") -> None:
        key = key or (video.video_id if video is not None else "")
        if isinstance(video, SearchVideo) and key:
            self.previews[key] = video
            if len(self.previews) > 200:
                self.previews.pop(next(iter(self.previews)))

    def close(self) -> None:
        self.closed = True
