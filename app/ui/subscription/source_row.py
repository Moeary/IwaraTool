"""One overview row: a source's avatar, counters and latest-cover strip."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import (
    AvatarWidget,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PushButton,
    SubtitleLabel,
    ToolButton,
)

from ...core.search import SearchVideo
from ...i18n import tr
from ..chrome import StatusChip
from ..click_dispatch import ACTION_DETAIL, ACTION_PLAY, ClickDispatcher, play_in_window
from ..media.card import MediaCard
from .helpers import _date_only, _source_origin_label, _source_type_label
from ..theme import set_secondary_text
from .cover_loader import load_pixmap
from .item_queries import item_to_video


class TileStrip(QWidget):
    """The latest videos of one source as a single row of compact covers."""

    video_activated = Signal(object)
    video_context = Signal(object, QPoint)

    GAP = 12

    def __init__(self, videos: list[SearchVideo], tile_min_width: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._tile_min = tile_min_width
        self.cards: list[MediaCard] = []
        self._clicks = ClickDispatcher(self._perform_click, self)
        for video in videos:
            card = MediaCard(video, self, selectable=False, compact=True)
            card.activated.connect(self.video_activated)
            card.configurable_clicks = True
            card.plain_clicked.connect(self._clicks.click)
            card.double_clicked.connect(self._clicks.double_click)
            card.context_requested.connect(self.video_context)
            self.cards.append(card)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._relayout(max(self.width(), self._tile_min * 3))

    def set_tile_min_width(self, width: int):
        self._tile_min = max(120, int(width))
        self._relayout(self.width())

    def _perform_click(self, action: str, card):
        # The overview row has no selection; "select" does nothing here.
        if card not in self.cards:
            return
        if action == ACTION_PLAY and play_in_window(card.video):
            return
        if action in (ACTION_DETAIL, ACTION_PLAY):
            self.video_activated.emit(card.video)

    def shown_cards(self) -> list[MediaCard]:
        return [card for card in self.cards if not card.isHidden()]

    def _relayout(self, width: int):
        if not self.cards or width <= 0:
            return
        columns = max(1, (width + self.GAP) // (self._tile_min + self.GAP))
        tile = max(120, (width - self.GAP * (columns - 1)) // columns)
        for index, card in enumerate(self.cards):
            if index < columns:
                card.set_card_width(tile)
                card.move(index * (tile + self.GAP), 0)
                card.show()
            else:
                card.hide()
        self.setFixedHeight(self.cards[0].height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(event.size().width())


class SourceRow(CardWidget):
    """One subscription: who it is, how it stands, and its latest videos."""

    open_requested = Signal(int)
    refresh_requested = Signal(int)
    menu_requested = Signal(int, QPoint)
    video_activated = Signal(object)
    video_context = Signal(object, QPoint)

    def __init__(
        self,
        source: dict[str, Any],
        items: list[dict[str, Any]],
        tile_min_width: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.source = source
        self.source_id = int(source.get("id", 0) or 0)
        self.signature = None
        self.avatar_path = ""
        self.setBorderRadius(10)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.menu_requested.emit(self.source_id, self.mapToGlobal(pos))
        )
        self.clicked.connect(lambda: self.open_requested.emit(self.source_id))

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(12)
        title = str(source.get("title", "") or source.get("source_key", "") or "?")
        is_author = str(source.get("source_type", "") or "") == "author"
        root.addLayout(self._build_head(source, title, is_author))
        self._build_strip(root, items, title if is_author else "", tile_min_width)

    def _build_head(self, source: dict[str, Any], title: str, is_author: bool) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(14)
        self.avatar = AvatarWidget(self)
        self.avatar.setRadius(28)
        self.avatar.setText(title[:1].upper())
        head.addWidget(self.avatar)
        enabled = bool(int(source.get("enabled", 1) or 0))
        head.addLayout(self._build_names(source, title, is_author, enabled), 1)

        new_count = int(source.get("new_count", 0) or 0)
        if new_count:
            head.addWidget(StatusChip(tr(f"{new_count} new", f"{new_count} 个新增", f"新着 {new_count}"), "accent", self))
        if not enabled:
            head.addWidget(StatusChip(tr("Paused", "已停用", "停止中"), "warning", self))

        refresh = ToolButton(FluentIcon.SYNC, self)
        refresh.setToolTip(tr("Refresh this source", "刷新此订阅源", "この購読元を更新"))
        refresh.clicked.connect(lambda: self.refresh_requested.emit(self.source_id))
        head.addWidget(refresh)
        open_btn = PushButton(tr("View all", "查看全部", "すべて表示"), self, FluentIcon.VIEW)
        open_btn.clicked.connect(lambda: self.open_requested.emit(self.source_id))
        head.addWidget(open_btn)
        more = ToolButton(FluentIcon.MORE, self)
        more.clicked.connect(lambda: self.menu_requested.emit(self.source_id, more.mapToGlobal(more.rect().bottomLeft())))
        head.addWidget(more)
        return head

    def _build_names(self, source: dict[str, Any], title: str, is_author: bool, enabled: bool) -> QVBoxLayout:
        names = QVBoxLayout()
        names.setSpacing(2)
        name = SubtitleLabel(title, self)
        names.addWidget(name)
        key = str(source.get("source_key", "") or "")
        kind = _source_type_label(str(source.get("source_type", "") or ""))
        sub = f"@{key}" if is_author else key
        sub = f"{sub}  ·  {kind}" if sub else kind
        if not enabled:
            sub += "  ·  " + tr("Paused", "已停用", "停止中")
        sub_label = CaptionLabel(sub, self)
        set_secondary_text(sub_label)
        sub_label.setToolTip(_source_origin_label(source))
        names.addWidget(sub_label)
        stats = CaptionLabel(self._stats_text(source), self)
        set_secondary_text(stats)
        names.addWidget(stats)
        return names

    def _build_strip(self, root: QVBoxLayout, items: list[dict[str, Any]], author_name: str, tile_min_width: int):
        videos = [item_to_video(item, author_name=author_name) for item in items]
        self.strip: TileStrip | None = None
        if videos:
            self.strip = TileStrip(videos, tile_min_width, self)
            self.strip.video_activated.connect(self.video_activated)
            self.strip.video_context.connect(self.video_context)
            root.addWidget(self.strip)
        else:
            empty = CaptionLabel(
                tr("No videos yet — refresh this source.", "还没有视频，刷新一下订阅源吧。", "動画はまだありません。更新してください。"),
                self,
            )
            set_secondary_text(empty)
            root.addWidget(empty)

    @staticmethod
    def _stats_text(source: dict[str, Any]) -> str:
        parts = [
            tr(
                f"{int(source.get('item_count', 0) or 0)} videos",
                f"共 {int(source.get('item_count', 0) or 0)} 个视频",
                f"{int(source.get('item_count', 0) or 0)} 本",
            ),
            tr(
                f"{int(source.get('undownloaded_count', 0) or 0)} not downloaded",
                f"{int(source.get('undownloaded_count', 0) or 0)} 个未下载",
                f"未保存 {int(source.get('undownloaded_count', 0) or 0)}",
            ),
        ]
        checked = _date_only(str(source.get("last_checked_at", "") or ""))
        if checked:
            parts.append(tr(f"checked {checked}", f"刷新于 {checked}", f"{checked} 更新"))
        return "  ·  ".join(parts)

    def set_avatar(self, path: str):
        pixmap = load_pixmap(path, 160)
        if pixmap is not None:
            self.avatar.setImage(pixmap)
            self.avatar_path = path

    def set_tile_min_width(self, width: int):
        if self.strip is not None:
            self.strip.set_tile_min_width(width)
