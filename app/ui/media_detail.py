"""In-app detail page for a video or image post, laid out like the website's.

Cover/player area on top, then title, stats, actions, the uploader, the
description, tags, related items and comments.  The post opens instantly from
the card that was clicked and fills in as the API answers.
"""
from __future__ import annotations

import html
import re
import webbrowser
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HyperlinkButton,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)

from ..config import app_config
from ..core.manager import download_manager
from ..core.rating import RATING_ALL, UI_RATING_KEY, filter_by_rating, is_adult, normalize_rating
from ..core.rules import active_rule_id
from ..core.search import (
    SearchVideo,
    avatar_url,
    normalize_image,
    normalize_video,
    small_cover_url,
)
from ..i18n import current_language, tr
from ..signal_bus import signal_bus
from .home_workers import CommentsWorker, CoverFetcher, DetailWorker, stop_workers
from .media_card import MediaGrid, transparent_scroll_area
from .search_widgets import _format_count, _format_duration
from .theme import PAGE_MARGINS, PAGE_SPACING, palette, set_secondary_text, to_qcolor
from .ui_state import ResponsiveFlowLayout

CONTENT_MAX_WIDTH = 1840
SIDE_WIDTH = 360
SIDE_MIN_PAGE_WIDTH = 1180  # below this the related list drops under the post
GALLERY_MAX_WIDTH = 1280
_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")


def linkify(text: str) -> str:
    """Escape ``text`` and turn bare URLs into links, keeping line breaks."""

    escaped = html.escape(str(text or ""))
    escaped = _URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
    return escaped.replace("\r\n", "\n").replace("\n", "<br>")


def format_date(value: str) -> str:
    return str(value or "")[:10]


class MediaStage(QWidget):
    """Letterboxed cover with a play button, standing in for the player."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._backdrop: QPixmap | None = None
        self._playable = False
        self._badge = ""
        self._max_height = 640
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(180)

    def set_pixmap(self, pixmap: QPixmap | None):
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        # A tiny copy stretched over the stage reads as a blurred backdrop, so a
        # 16:9 cover on a very wide window is not flanked by empty black bars.
        self._backdrop = (
            self._pixmap.scaledToWidth(40, Qt.TransformationMode.SmoothTransformation)
            if self._pixmap is not None
            else None
        )
        self.update()

    def set_max_height(self, height: int):
        height = max(240, int(height))
        if height != self._max_height:
            self._max_height = height
            self._fit_height()

    def _fit_height(self):
        height = max(180, min(self._max_height, round(self.width() * 9 / 16)))
        if height != self.height():
            self.setFixedHeight(height)

    def set_playable(self, playable: bool, badge: str = ""):
        self._playable = playable
        self._badge = badge
        self.setCursor(Qt.CursorShape.PointingHandCursor if playable else Qt.CursorShape.ArrowCursor)
        self.update()

    def resizeEvent(self, event):
        self._fit_height()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        # Accept the press so the matching release reaches this widget.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._playable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.setClipPath(shape)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._backdrop is not None:
            painter.drawPixmap(self.rect(), self._backdrop)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 150))
        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
        if self._playable:
            radius = 34
            center = self.rect().center()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawEllipse(center, radius, radius)
            painter.setBrush(QColor("#ffffff"))
            cx, cy = center.x(), center.y()
            painter.drawPolygon(QPolygonF([QPointF(cx - 9, cy - 15), QPointF(cx - 9, cy + 15), QPointF(cx + 16, cy)]))
        if self._badge:
            font = QFont(self.font())
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(self._badge) + 14
            rect = QRectF(self.width() - width - 10, self.height() - metrics.height() - 16, width, metrics.height() + 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._badge)


class GalleryImage(QWidget):
    """One picture of an image post, shown at the page width."""

    clicked = Signal(str)  # original url

    def __init__(self, file_info: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.file_id = str(file_info.get("id") or "")
        name = str(file_info.get("name") or "")
        self.original_url = f"https://i.iwara.tv/image/original/{self.file_id}/{name}" if name else ""
        self.thumb_url = f"https://i.iwara.tv/image/thumbnail/{self.file_id}/{self.file_id}.jpg"
        width, height = file_info.get("width"), file_info.get("height")
        self._ratio = (height / width) if isinstance(width, int) and isinstance(height, int) and width and height else 0.62
        self._pixmap: QPixmap | None = None
        self._final = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaximumWidth(GALLERY_MAX_WIDTH)

    def set_pixmap(self, pixmap: QPixmap, *, final: bool):
        if pixmap.isNull() or (self._final and not final):
            return
        self._pixmap = pixmap
        self._final = self._final or final
        if final and pixmap.width():
            self._ratio = pixmap.height() / pixmap.width()
        self._fit_height()
        self.update()

    def _fit_height(self):
        self.setFixedHeight(max(120, round(self.width() * self._ratio)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def mousePressEvent(self, event):
        # Accept the press so the matching release reaches this widget.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.original_url:
            self.clicked.emit(self.original_url)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), 8, 8)
        painter.setClipPath(shape)
        painter.fillRect(self.rect(), to_qcolor(palette().placeholder))
        if self._pixmap is not None:
            painter.drawPixmap(
                self.rect(),
                self._pixmap.scaled(
                    self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation,
                ),
            )


class CommentWidget(QWidget):
    """A comment with its author, text and (expandable) replies."""

    replies_requested = Signal(str, object)  # comment id, this widget

    def __init__(self, row: dict[str, Any], fetcher: CoverFetcher, *, nested: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.comment_id = str(row.get("id") or "")
        user = row.get("user") if isinstance(row.get("user"), dict) else {}
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(10)
        self._avatar = AvatarWidget(self)
        self._avatar.setRadius(16 if nested else 20)
        outer.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignTop)
        self._avatar_key = str(user.get("id") or self.comment_id)
        self._fetcher = fetcher
        avatar = avatar_url(user)
        if avatar:
            cached = fetcher.path_for("avatar", self._avatar_key)
            if cached:
                self._set_avatar(cached)
            else:
                fetcher.cover_ready.connect(self._on_cover)
                fetcher.request([("avatar", self._avatar_key, avatar)])

        column = QVBoxLayout()
        column.setSpacing(2)
        header = QHBoxLayout()
        header.setSpacing(8)
        name = BodyLabel(str(user.get("name") or user.get("username") or "?"), self)
        font = name.font()
        font.setBold(True)
        name.setFont(font)
        header.addWidget(name)
        meta = CaptionLabel(f"@{user.get('username', '')} · {format_date(row.get('createdAt', ''))}", self)
        set_secondary_text(meta)
        header.addWidget(meta)
        header.addStretch(1)
        column.addLayout(header)
        body = BodyLabel(self)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setText(linkify(str(row.get("body") or "")))
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        column.addWidget(body)
        self._replies_box = QVBoxLayout()
        self._replies_box.setContentsMargins(0, 4, 0, 0)
        replies = int(row.get("numReplies") or 0)
        self._replies_btn: HyperlinkButton | None = None
        if replies and not nested:
            self._replies_btn = HyperlinkButton("", tr(f"Show {replies} replies", f"查看 {replies} 条回复", f"{replies} 件の返信を表示"), self)
            self._replies_btn.clicked.connect(self._on_replies_clicked)
            column.addWidget(self._replies_btn, 0, Qt.AlignmentFlag.AlignLeft)
        column.addLayout(self._replies_box)
        outer.addLayout(column, 1)

    def _on_cover(self, kind: str, key: str, path: str):
        if kind == "avatar" and key == self._avatar_key:
            self._set_avatar(path)

    def _set_avatar(self, path: str):
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._avatar.setImage(pixmap)

    def _on_replies_clicked(self):
        if self._replies_btn is not None:
            self._replies_btn.setEnabled(False)
        self.replies_requested.emit(self.comment_id, self)

    def add_replies(self, widgets: list["CommentWidget"]):
        for widget in widgets:
            self._replies_box.addWidget(widget)
        if self._replies_btn is not None:
            self._replies_btn.hide()


class DetailView(QWidget):
    """The post page."""

    back_requested = Signal()
    open_requested = Signal(object)  # related SearchVideo
    context_requested = Signal(object, QPoint)

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
        self._detail_worker: DetailWorker | None = None
        self._detail_workers: list[DetailWorker] = []  # includes superseded, still-running loads
        self._comment_workers: list[CommentsWorker] = []
        self._gallery: list[GalleryImage] = []
        self._pending_replies: dict[str, CommentWidget] = {}
        self._build_ui()
        fetcher.cover_ready.connect(self._on_cover)

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_MARGINS[0], PAGE_MARGINS[1], PAGE_MARGINS[2], 0)
        root.setSpacing(PAGE_SPACING)

        bar = QHBoxLayout()
        self._back_btn = PushButton(tr("Back", "返回", "戻る"), self, FluentIcon.LEFT_ARROW)
        self._back_btn.clicked.connect(self.back_requested)
        bar.addWidget(self._back_btn)
        bar.addStretch(1)
        self._status = CaptionLabel("", self)
        set_secondary_text(self._status)
        bar.addWidget(self._status)
        root.addLayout(bar)

        self._scroll = transparent_scroll_area("MediaDetailScroll", self)
        page = QWidget()
        page_row = QHBoxLayout(page)
        page_row.setContentsMargins(0, 0, 0, 24)
        page_row.addStretch(1)
        content = QWidget(page)
        content.setMaximumWidth(CONTENT_MAX_WIDTH)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        page_row.addWidget(content, 100)
        page_row.addStretch(1)
        columns = QHBoxLayout(content)
        columns.setContentsMargins(0, 0, 8, 0)
        columns.setSpacing(22)
        main = QWidget(content)
        columns.addWidget(main, 1)
        # Wide windows list related posts in a column beside the post, as the
        # website does, instead of leaving the right half of the page empty.
        self._side = QWidget(content)
        self._side.setFixedWidth(SIDE_WIDTH)
        self._side_layout = QVBoxLayout(self._side)
        self._side_layout.setContentsMargins(0, 0, 0, 0)
        self._side_layout.setSpacing(10)
        self._side_layout.addStretch(1)
        columns.addWidget(self._side, 0, Qt.AlignmentFlag.AlignTop)
        self._side_mode = False
        self._side.hide()
        content = main
        self._body = QVBoxLayout(content)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(14)

        self._stage = MediaStage(content)
        self._stage.clicked.connect(self._play)
        self._body.addWidget(self._stage)
        self._gallery_box = QVBoxLayout()
        self._gallery_box.setSpacing(10)

        self._title = SubtitleLabel("", content)
        self._title.setWordWrap(True)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.addWidget(self._title)
        self._meta = BodyLabel("", content)
        self._meta.setWordWrap(True)
        set_secondary_text(self._meta)
        self._body.addWidget(self._meta)

        actions = ResponsiveFlowLayout(spacing=8)
        self._play_btn = PrimaryPushButton(tr("Play", "播放", "再生"), content, FluentIcon.PLAY)
        self._play_btn.clicked.connect(self._play)
        self._download_btn = PushButton(tr("Download", "下载", "ダウンロード"), content, FluentIcon.DOWNLOAD)
        self._download_btn.clicked.connect(self._queue)
        self._browser_btn = PushButton(tr("Open in browser", "在浏览器打开", "ブラウザーで開く"), content, FluentIcon.GLOBE)
        self._browser_btn.clicked.connect(self._open_in_browser)
        self._copy_btn = PushButton(tr("Copy link", "复制链接", "リンクをコピー"), content, FluentIcon.COPY)
        self._copy_btn.clicked.connect(self._copy_link)
        for button in (self._play_btn, self._download_btn, self._browser_btn, self._copy_btn):
            actions.addWidget(button)
        self._body.addLayout(actions)

        self._author_card = QWidget(content)
        author_row = QHBoxLayout(self._author_card)
        author_row.setContentsMargins(0, 4, 0, 4)
        author_row.setSpacing(12)
        self._avatar = AvatarWidget(self._author_card)
        self._avatar.setRadius(24)
        author_row.addWidget(self._avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        self._author_name = BodyLabel("", self._author_card)
        font = self._author_name.font()
        font.setBold(True)
        self._author_name.setFont(font)
        self._author_user = CaptionLabel("", self._author_card)
        set_secondary_text(self._author_user)
        names.addWidget(self._author_name)
        names.addWidget(self._author_user)
        author_row.addLayout(names)
        author_row.addStretch(1)
        self._works_btn = PushButton(tr("Their works", "查看作品", "作品を見る"), self._author_card, FluentIcon.SEARCH)
        self._works_btn.clicked.connect(self._author_works)
        self._subscribe_btn = PushButton(tr("Subscribe", "加入订阅", "購読"), self._author_card, FluentIcon.PEOPLE)
        self._subscribe_btn.clicked.connect(self._subscribe_author)
        author_row.addWidget(self._works_btn)
        author_row.addWidget(self._subscribe_btn)
        self._body.addWidget(self._author_card)

        self._description = BodyLabel("", content)
        self._description.setWordWrap(True)
        self._description.setTextFormat(Qt.TextFormat.RichText)
        self._description.setOpenExternalLinks(True)
        self._description.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._body.addWidget(self._description)

        self._tags_widget = QWidget(content)
        self._tags_layout = ResponsiveFlowLayout(self._tags_widget, spacing=6)
        self._body.addWidget(self._tags_widget)
        # Image posts show their pictures after the title block, as on the site.
        self._body.addLayout(self._gallery_box)

        self._related_box = QWidget(content)
        related_layout = QVBoxLayout(self._related_box)
        related_layout.setContentsMargins(0, 0, 0, 0)
        related_layout.setSpacing(10)
        self._related_title = SubtitleLabel(tr("Related", "相关推荐", "関連"), self._related_box)
        self._related_grid = MediaGrid(self._related_box, min_card_width=210, selectable=False, max_rows=2)
        self._related_grid.card_activated.connect(self.open_requested)
        self._related_grid.card_context_requested.connect(self.context_requested)
        self._related_grid.bind_fetcher(self._fetcher)
        related_layout.addWidget(self._related_title)
        related_layout.addWidget(self._related_grid)
        self._related_index = self._body.count()
        self._body.addWidget(self._related_box)

        self._comments_title = SubtitleLabel(tr("Comments", "评论", "コメント"), content)
        self._body.addWidget(self._comments_title)
        self._comments_box = QVBoxLayout()
        self._comments_box.setSpacing(2)
        self._body.addLayout(self._comments_box)
        self._comments_hint = CaptionLabel("", content)
        set_secondary_text(self._comments_hint)
        self._body.addWidget(self._comments_hint)
        self._more_comments_btn = PushButton(tr("Load more comments", "加载更多评论", "コメントをさらに読み込む"), content)
        self._more_comments_btn.clicked.connect(self._load_more_comments)
        self._body.addWidget(self._more_comments_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._body.addStretch(1)

        self._scroll.setWidget(page)
        self._scroll.viewport().installEventFilter(self)
        root.addWidget(self._scroll, 1)
        self._reset_content()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_stage()
        self._place_related(self.width() >= SIDE_MIN_PAGE_WIDTH)

    def eventFilter(self, watched, event):
        # The viewport settles after the page itself has been resized.
        if watched is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_stage()
        return super().eventFilter(watched, event)

    def _fit_stage(self):
        """Let the player area use most of the visible height, not a fixed cap."""

        self._stage.set_max_height(round(self._scroll.viewport().height() * 0.8))

    def _place_related(self, side: bool):
        """Wide windows list related posts beside the post, narrow ones below it."""

        if side == self._side_mode:
            return
        self._side_mode = side
        if side:
            self._body.removeWidget(self._related_box)
            self._side_layout.insertWidget(0, self._related_box)
            self._related_grid.set_max_rows(0)
        else:
            self._side_layout.removeWidget(self._related_box)
            self._body.insertWidget(self._related_index, self._related_box)
            self._related_grid.set_max_rows(2)
        self._side.setVisible(side)

    # ── state ────────────────────────────────────────────────────────────────

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _reset_content(self):
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
        for button in (self._play_btn, self._download_btn, self._browser_btn, self._copy_btn):
            button.setEnabled(False)
        self._play_btn.setVisible(True)
        self._download_btn.setVisible(True)
        self._info = None
        self._author_target = None
        self._comment_page = 0
        self._comment_total = None
        self._comment_count = 0
        self._pending_replies.clear()

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return stop_workers([*self._detail_workers, *self._comment_workers], timeout_ms)

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

    def _on_worker_finished(self, worker: DetailWorker):
        if self._detail_worker is worker:
            self._detail_worker = None
        if worker in self._detail_workers:
            self._detail_workers.remove(worker)
        worker.deleteLater()

    # ── filling ──────────────────────────────────────────────────────────────

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
        self._download_btn.setEnabled(can_play)
        self._play_btn.setVisible(self._kind == "video")
        self._download_btn.setVisible(self._kind == "video")

        user = info.get("user") if isinstance(info.get("user"), dict) else {}
        username = str(user.get("username") or video.author_username or "")
        if username:
            name = str(user.get("name") or username)
            self._author_card.show()
            self._author_name.setText(name)
            self._author_user.setText(f"@{username}")
            self._author_target = (username, name, str(user.get("id") or ""), avatar_url(user))
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
        pixmap = QPixmap(path)
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
        # The related endpoint ignores the SFW/NSFW choice; honour it here.
        videos = filter_by_rating(videos, normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL)))
        self._related_grid.set_videos(videos)
        self._related_box.setVisible(bool(videos))

    def _on_cover(self, kind: str, key: str, path: str):
        pixmap = None
        if kind in {"video", "hero"} and self._video is not None and key == self._video.video_id and self._kind == "video":
            pixmap = QPixmap(path)
            if kind == "hero" or self._stage._pixmap is None:
                self._stage.set_pixmap(pixmap)
        elif kind == "avatar":
            user = (self._info or {}).get("user") if isinstance((self._info or {}).get("user"), dict) else {}
            if key == str(user.get("id") or user.get("username") or ""):
                self._set_avatar(path)
        elif kind in {"gallery", "gallery_thumb"}:
            for image in self._gallery:
                if image.file_id == key:
                    pixmap = QPixmap(path)
                    image.set_pixmap(pixmap, final=kind == "gallery")

    # ── comments ─────────────────────────────────────────────────────────────

    def _on_comments(self, token: int, rows: object, total: object, error: str):
        if token != self._token:
            return
        self._comments_title.show()
        self._append_comments(rows if isinstance(rows, list) else [], total if isinstance(total, int) else None, error)

    def _append_comments(self, rows: list[dict[str, Any]], total: int | None, error: str):
        if total is not None:
            self._comment_total = total
        for row in rows:
            widget = CommentWidget(row, self._fetcher, parent=self)
            widget.replies_requested.connect(self._load_replies)
            self._comments_box.addWidget(widget)
            self._comment_count += 1
        if self._comment_total is not None:
            self._comments_title.setText(tr(f"Comments ({self._comment_total})", f"评论（{self._comment_total}）", f"コメント（{self._comment_total}）"))
        if error:
            self._comments_hint.setText(error)
        elif not self._comment_count:
            self._comments_hint.setText(tr("No comments yet", "暂无评论", "コメントはまだありません"))
        else:
            self._comments_hint.setText("")
        more = self._comment_total is not None and self._comment_count < self._comment_total and bool(rows)
        self._more_comments_btn.setVisible(more)
        self._more_comments_btn.setEnabled(True)

    def _load_more_comments(self):
        self._more_comments_btn.setEnabled(False)
        self._comment_page += 1
        self._start_comment_worker(page=self._comment_page, parent="")

    def _load_replies(self, comment_id: str, widget: object):
        self._pending_replies[comment_id] = widget  # type: ignore[assignment]
        self._start_comment_worker(page=0, parent=comment_id)

    def _start_comment_worker(self, *, page: int, parent: str):
        worker = CommentsWorker(self._token, self._kind, self._item_id, page=page, parent=parent)
        worker.comments_ready.connect(self._on_more_comments)
        worker.finished.connect(lambda worker=worker: self._comment_workers.remove(worker) if worker in self._comment_workers else None)
        worker.finished.connect(worker.deleteLater)
        self._comment_workers.append(worker)
        worker.start()

    def _on_more_comments(self, token: int, rows: object, total: object, error: str, parent: str, _page: int):
        if token != self._token:
            return
        rows = rows if isinstance(rows, list) else []
        if parent:
            owner = self._pending_replies.pop(parent, None)
            if owner is not None:
                owner.add_replies([CommentWidget(row, self._fetcher, nested=True, parent=owner) for row in rows])
            return
        self._append_comments(rows, total if isinstance(total, int) else None, error)

    # ── actions ──────────────────────────────────────────────────────────────

    def _page_url(self) -> str:
        return f"https://www.iwara.tv/{self._kind}/{self._item_id}"

    def _play(self):
        if self._video is not None and self._kind == "video" and self._video.downloadable:
            signal_bus.video_preview_requested.emit(self._video.video_id, self._video.title, "")

    def _queue(self):
        if self._kind != "video" or not self._item_id:
            return
        accepted = download_manager.enqueue_video_ids(
            [self._item_id], source_label=tr("Home", "首页", "ホーム"), rule_id=active_rule_id(),
        )
        InfoBar.success(
            title=tr("Added to queue", "已加入队列", "キューに追加"),
            content=tr(f"Accepted {accepted} video(s)", f"已接受 {accepted} 个视频", f"{accepted} 件を追加しました"),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3000, parent=self.window(),
        )

    def _open_in_browser(self):
        webbrowser.open(self._page_url())

    def _copy_link(self):
        QApplication.clipboard().setText(self._page_url())
        InfoBar.success(
            title=tr("Link copied", "链接已复制", "リンクをコピーしました"), content=self._page_url(),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2000, parent=self.window(),
        )

    def _search_tag(self, tag_id: str):
        scope = "images" if self._kind == "image" else "tags"
        signal_bus.search_requested.emit({"scope": scope, "keyword": tag_id})

    def _author_works(self):
        if self._author_target:
            signal_bus.search_requested.emit({"author": self._author_target})

    def _subscribe_author(self):
        if not self._author_target:
            return
        username, title, remote_id, avatar = self._author_target
        try:
            source_id = int(download_manager.add_author_subscription(
                username, title=title, remote_id=remote_id, avatar_url=avatar,
            ) or 0)
        except Exception as exc:
            InfoBar.error(
                title=tr("Subscription failed", "订阅失败", "購読に失敗"), content=str(exc),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=4000, parent=self.window(),
            )
            return
        if source_id:
            signal_bus.subscription_source_added.emit(source_id)
            InfoBar.success(
                title=tr("Author added", "作者已加入订阅", "作者を購読に追加しました"),
                content=tr(f"@{username} is now in your local subscriptions", f"@{username} 已加入本地订阅", f"@{username} をローカル購読に追加しました"),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self.window(),
            )

    def refresh_theme_styles(self):
        self._related_grid.update()
        for card in self._related_grid.findChildren(QWidget):
            card.update()
