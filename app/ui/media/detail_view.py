"""In-app detail page for a video or image post, laid out like the website's.

Cover/player area on top, then title, stats, actions, the uploader, the
description, tags, related items and comments.  The post opens instantly from
the card that was clicked and fills in as the API answers.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QPoint, Signal
from PySide6.QtWidgets import QWidget

from ...core.manager import download_manager  # noqa: F401 - patch point, see page_dependencies
from ...core.search.models import SearchVideo
from ..home.workers import ApiCallWorker, CommentsWorker, CoverFetcher, DetailWorker, stop_workers
from ..player.video_player import VideoPlayer
from ..player.backend import create_backend
from .comment_widget import CommentWidget
from .gallery_image import GalleryImage
from .detail_actions import DetailActionsMixin
from .detail_comments import DetailCommentsMixin
from .detail_content import DetailContentMixin
from .detail_layout import DetailLayoutMixin
from .detail_metrics import SIDE_MIN_PAGE_WIDTH


class DetailView(
    DetailLayoutMixin,
    DetailContentMixin,
    DetailCommentsMixin,
    DetailActionsMixin,
    QWidget,
):
    """The post page."""

    back_requested = Signal()
    open_requested = Signal(object)  # related SearchVideo
    context_requested = Signal(object, QPoint)

    # Builds the multimedia objects of the embedded player (replaced in tests).
    player_backend_factory = staticmethod(create_backend)

    def __init__(self, fetcher: CoverFetcher, parent: QWidget | None = None):
        super().__init__(parent)
        self._fetcher = fetcher
        self._token = 0
        self._kind = "video"
        self._item_id = ""
        self._video: SearchVideo | None = None
        self._info: dict[str, Any] | None = None
        self._author_target: tuple[str, str, str, str] | None = None
        self._comment_page = 0
        self._comment_total: int | None = None
        self._comment_count = 0
        self._liked = False
        self._like_busy = False
        self._like_workers: list[ApiCallWorker] = []
        self._detail_worker: DetailWorker | None = None
        self._detail_workers: list[DetailWorker] = []  # includes superseded, still-running loads
        self._comment_workers: list[CommentsWorker] = []
        self._gallery: list[GalleryImage] = []
        self._pending_replies: dict[str, CommentWidget] = {}
        self._related: list[SearchVideo] | None = None
        self._comment_batches: list[tuple[list[dict[str, Any]], int | None, str]] = []
        self._reply_rows: dict[str, list[dict[str, Any]]] = {}
        self._player: VideoPlayer | None = None  # created on the first Play
        self._build_ui()
        fetcher.cover_ready.connect(self._on_cover)

    # ── layout ───────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_stage()
        self._place_related(self.width() >= SIDE_MIN_PAGE_WIDTH)

    def eventFilter(self, watched, event):
        # The viewport settles after the page itself has been resized.
        if watched is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_stage()
        return super().eventFilter(watched, event)

    # ── state ────────────────────────────────────────────────────────────────

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        if self._player is not None:
            self._player.shutdown()
        return all([
            stop_workers([*self._detail_workers, *self._comment_workers, *self._like_workers], timeout_ms),
            self._author_status.shutdown(timeout_ms),
        ])

    # ── actions ──────────────────────────────────────────────────────────────

    def hideEvent(self, event):
        # Leaving the post (another page or post) ends the video; minimizing does not.
        if not event.spontaneous() and not (self._player is not None and self._player.is_fullscreen()):
            self._stop_player()
        super().hideEvent(event)

    def refresh_theme_styles(self):
        self._related_grid.update()
        for card in self._related_grid.findChildren(QWidget):
            card.update()
