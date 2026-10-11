"""Load a post and fill the detail view: summary, info, tags, gallery and related posts."""

from __future__ import annotations

import copy
import webbrowser
from typing import Any

from PySide6.QtCore import Qt

from qfluentwidgets import PushButton

from ...config import app_config
from ...core.rating import RATING_ALL, UI_RATING_KEY, filter_by_rating, is_adult, normalize_rating
from ...core.search.models import SearchVideo
from ...core.search.normalize import avatar_url, normalize_image, normalize_video, small_cover_url
from ...i18n import current_language, tr
from .pixmaps import read_pixmap
from ..navigation import restore_scroll
from ..formatting import _format_count, _format_duration
from .comment_widget import CommentWidget
from .detail_state import DetailState
from .detail_text import format_date, linkify
from .gallery_image import GALLERY_DECODE_WIDTH, GalleryImage
from .page_dependencies import DetailWorker, download_manager


class DetailContentMixin:
    """Load a post and fill the detail view: summary, info, tags, gallery and related posts."""

    def _reset_content(self):
        self._stop_player()
        self._stage.set_pixmap(None)
        self._stage.set_playable(False)
        self._stage.show()
        self._clear_layout(self._gallery_box)
        self._gallery = []
        self._title.setText("")
        self._meta.setText("")
        self._description.setText("")
        self._description.hide()
        self._author_card.hide()
        self._avatar.hide()
        self._clear_layout(self._tags_layout)
        self._tags_widget.hide()
        self._related_grid.set_videos([])
        self._related_box.hide()
        self._clear_layout(self._comments_box)
        self._comments_title.hide()
        self._comments_hint.setText("")
        self._more_comments_btn.hide()
        for button in (
            self._play_btn, self._window_play_btn, self._download_btn, self._like_btn, self._browser_btn, self._copy_btn,
        ):
            button.setEnabled(False)
        self._liked = False
        self._sync_like()
        self._play_btn.setVisible(True)
        self._window_play_btn.setVisible(True)
        self._download_btn.setVisible(True)
        self._info = None
        self._author_target = None
        self._comment_page = 0
        self._comment_total = None
        self._comment_count = 0
        self._pending_replies.clear()
        self._related = None
        self._comment_batches = []
        self._reply_rows = {}

    def load(self, kind: str, item_id: str, preview: SearchVideo | None = None):
        """Show a post; ``preview`` (the clicked card) fills the page immediately."""

        self._token += 1
        self._kind = "image" if kind == "image" else "video"
        self._item_id = str(item_id)
        for worker in [*self._detail_workers, *self._comment_workers]:
            worker.requestInterruption()
        self._reset_content()
        self._video = preview
        self._scroll.verticalScrollBar().setValue(0)
        self._status.setText(tr("Loading…", "加载中…", "読み込み中…"))
        if preview is not None:
            self._apply_summary(preview)
            self._request_stage_cover(preview)
        worker = DetailWorker(self._token, self._kind, self._item_id)
        worker.info_ready.connect(self._on_info)
        worker.related_ready.connect(self._on_related)
        worker.comments_ready.connect(self._on_comments)
        worker.finished.connect(lambda worker=worker: self._on_worker_finished(worker))
        self._detail_worker = worker
        self._detail_workers.append(worker)
        worker.start()

    @property
    def item(self) -> tuple[str, str]:
        return self._kind, self._item_id

    def view_state(self) -> DetailState:
        return DetailState(
            kind=self._kind,
            item_id=self._item_id,
            video=copy.copy(self._video),
            info=self._info,
            related=list(self._related) if self._related is not None else None,
            comment_batches=list(self._comment_batches),
            comment_page=self._comment_page,
            replies=dict(self._reply_rows),
            liked=self._liked,
            scroll=self._scroll.verticalScrollBar().value(),
        )

    def restore_state(self, state: DetailState):
        """Show a recorded post again from its saved answers (loads it only if it never arrived)."""

        if state.info is None:
            self.load(state.kind, state.item_id, state.video)
            return
        self._token += 1
        token = self._token
        self._kind = "image" if state.kind == "image" else "video"
        self._item_id = str(state.item_id)
        for worker in [*self._detail_workers, *self._comment_workers]:
            worker.requestInterruption()
        self._reset_content()
        self._video = copy.copy(state.video)
        self._status.setText("")
        self._on_info(token, state.info, "")
        self._liked = state.liked
        if self._video is not None:
            if state.video is not None:
                self._video.likes = state.video.likes
            self._meta.setText(self._meta_text(self._video))
        self._sync_like()
        if state.related is not None:
            self._on_related(token, list(state.related))
        for rows, total, error in state.comment_batches:
            self._on_comments(token, rows, total, error)
        self._comment_page = state.comment_page
        for comment_id, rows in state.replies.items():
            owner = self._comment_widget(comment_id)
            if owner is not None:
                self._reply_rows[comment_id] = rows
                owner.add_replies([CommentWidget(row, self._fetcher, nested=True, parent=owner) for row in rows])
        restore_scroll(self._scroll, state.scroll)

    def _on_worker_finished(self, worker: DetailWorker):
        if self._detail_worker is worker:
            self._detail_worker = None
        if worker in self._detail_workers:
            self._detail_workers.remove(worker)
        worker.deleteLater()

    def _apply_summary(self, video: SearchVideo):
        self._title.setText(video.title)
        self._meta.setText(self._meta_text(video))
        author = video.author_name or video.author_username
        if author:
            self._author_card.show()
            self._author_name.setText(author)
            self._author_user.setText(f"@{video.author_username}" if video.author_username else "")
        self._browser_btn.setEnabled(True)
        self._copy_btn.setEnabled(True)
        self._like_btn.setEnabled(True)
        self._sync_like()

    def _meta_text(self, video: SearchVideo) -> str:
        parts = [
            tr(f"{_format_count(video.views)} views", f"{_format_count(video.views)} 次观看", f"{_format_count(video.views)} 回再生"),
            tr(f"{_format_count(video.likes)} likes", f"{_format_count(video.likes)} 喜欢", f"{_format_count(video.likes)} いいね"),
            tr(f"{_format_count(video.comments)} comments", f"{_format_count(video.comments)} 评论", f"{_format_count(video.comments)} コメント"),
        ]
        if self._kind == "video" and video.duration:
            parts.append(_format_duration(video.duration))
        if video.published_at:
            parts.append(format_date(video.published_at))
        if is_adult(video.rating):
            parts.append("R-18")
        return "  ·  ".join(parts)

    def _request_stage_cover(self, video: SearchVideo):
        if self._kind == "image":
            return  # the gallery shows the pictures themselves
        jobs = []
        if video.thumbnail_url:
            # Small cover first (already cached by the grid), then the sharp one.
            jobs.append(("video", video.video_id, small_cover_url(video.thumbnail_url)))
            jobs.append(("hero", video.video_id, video.thumbnail_url))
        self._fetcher.request(jobs)

    def _on_info(self, token: int, info: object, error: str):
        if token != self._token:
            return
        if not isinstance(info, dict):
            self._status.setText(tr(f"Could not load this post: {error}", f"无法加载该作品：{error}", f"読み込めませんでした: {error}"))
            return
        self._status.setText("")
        self._info = info
        normalize = normalize_image if self._kind == "image" else normalize_video
        video = normalize(info)
        if video is None:
            return
        self._liked = bool(info.get("liked"))
        if self._video is not None and not video.thumbnail_url:
            video.thumbnail_url = self._video.thumbnail_url
        self._video = video
        self._apply_summary(video)
        if self._kind == "image":
            self._stage.hide()
            self._build_gallery(info)
        else:
            self._stage.show()
            playable = video.downloadable
            quality = ""
            file_info = info.get("file") if isinstance(info.get("file"), dict) else {}
            if file_info.get("height"):
                quality = f"{file_info['height']}p"
            self._stage.set_playable(playable, quality)
            if not playable and info.get("embedUrl"):
                self._status.setText(tr("Embedded from another site — open it in the browser.", "该视频为外站嵌入，请在浏览器中打开。", "外部サイトの埋め込み動画です。ブラウザーで開いてください。"))
            self._request_stage_cover(video)
        can_play = self._kind == "video" and video.downloadable
        self._play_btn.setEnabled(can_play)
        self._window_play_btn.setEnabled(can_play)
        self._download_btn.setEnabled(can_play)
        self._play_btn.setVisible(self._kind == "video")
        self._window_play_btn.setVisible(self._kind == "video")
        self._download_btn.setVisible(self._kind == "video")

        user = info.get("user") if isinstance(info.get("user"), dict) else {}
        username = str(user.get("username") or video.author_username or "")
        if username:
            name = str(user.get("name") or username)
            self._author_card.show()
            self._author_name.setText(name)
            self._author_user.setText(f"@{username}")
            self._author_target = (username, name, str(user.get("id") or ""), avatar_url(user))
            self._author_status.set_author(username, name, str(user.get("id") or ""), avatar_url(user))
            avatar = avatar_url(user)
            if avatar:
                self._fetcher.request([("avatar", str(user.get("id") or username), avatar)])
            cached = self._fetcher.path_for("avatar", str(user.get("id") or username))
            if cached:
                self._set_avatar(cached)
        body = str(info.get("body") or "").strip()
        if body:
            self._description.setText(linkify(body))
            self._description.show()
        self._build_tags(info.get("tags"))

    def _set_avatar(self, path: str):
        pixmap = read_pixmap(path, 256)
        if not pixmap.isNull():
            self._avatar.setImage(pixmap)
            self._avatar.show()

    def _build_tags(self, tags: object):
        self._clear_layout(self._tags_layout)
        shown = 0
        for tag in tags if isinstance(tags, list) else []:
            tag_id = str(tag.get("id") if isinstance(tag, dict) else tag or "").strip()
            if not tag_id or tag_id == "uncategorized":
                continue
            label = tag_id
            try:
                entry = download_manager.tag_dictionary.entry_for(tag_id)
            except Exception:
                entry = None
            if entry is not None:
                label = getattr(entry, current_language(), "") or tag_id
            button = PushButton(f"#{label}", self._tags_widget)
            button.setFixedHeight(28)
            button.setToolTip(tag_id)
            button.clicked.connect(lambda _=False, tag_id=tag_id: self._search_tag(tag_id))
            self._tags_layout.addWidget(button)
            shown += 1
        self._tags_widget.setVisible(bool(shown))

    def _build_gallery(self, info: dict[str, Any]):
        self._clear_layout(self._gallery_box)
        self._gallery = []
        files = [f for f in info.get("files") or [] if isinstance(f, dict) and f.get("id")]
        if not files and isinstance(info.get("thumbnail"), dict):
            files = [info["thumbnail"]]
        jobs = []
        for file_info in files:
            image = GalleryImage(file_info, self)
            image.clicked.connect(webbrowser.open)
            self._gallery_box.addWidget(image, 0, Qt.AlignmentFlag.AlignHCenter)
            self._gallery.append(image)
            jobs.append(("gallery_thumb", image.file_id, image.thumb_url))
        self._fetcher.request(jobs)
        self._fetcher.request([("gallery", image.file_id, image.original_url) for image in self._gallery if image.original_url])
        if len(files) > 1:
            self._status.setText(tr(f"{len(files)} images — click one to open the original", f"共 {len(files)} 张图片，点击图片打开原图", f"{len(files)} 枚 — クリックで原寸を開く"))
        elif files:
            self._status.setText(tr("Click the image to open the original", "点击图片打开原图", "クリックで原寸を開く"))

    def _on_related(self, token: int, videos: object):
        if token != self._token or not isinstance(videos, list):
            return
        videos = [v for v in videos if isinstance(v, SearchVideo)]
        self._related = list(videos)
        # The related endpoint ignores the SFW/NSFW choice; honour it here.
        videos = filter_by_rating(videos, normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL)))
        self._related_grid.set_videos(videos)
        self._related_box.setVisible(bool(videos))

    def _on_cover(self, kind: str, key: str, path: str):
        pixmap = None
        if kind in {"video", "hero"} and self._video is not None and key == self._video.video_id and self._kind == "video":
            pixmap = read_pixmap(path, GALLERY_DECODE_WIDTH)
            if kind == "hero" or self._stage._pixmap is None:
                self._stage.set_pixmap(pixmap)
        elif kind == "avatar":
            user = (self._info or {}).get("user") if isinstance((self._info or {}).get("user"), dict) else {}
            if key == str(user.get("id") or user.get("username") or ""):
                self._set_avatar(path)
        elif kind in {"gallery", "gallery_thumb"}:
            for image in self._gallery:
                if image.file_id == key:
                    image.set_source(path, final=kind == "gallery")
