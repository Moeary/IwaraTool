"""Build the detail view: top bar, post column, side column, author card, related and comments."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    ToggleButton,
)

from ...i18n import tr
from ...signal_bus import signal_bus
from ..author_status import AuthorStatusBar
from ..shortcuts import attach_hint
from .grid import MediaGrid, transparent_scroll_area
from ..theme import PAGE_MARGINS, PAGE_SPACING, set_secondary_text
from ..ui_state import ResponsiveFlowLayout
from .detail_metrics import CONTENT_MAX_WIDTH, SIDE_WIDTH
from .stage import MediaStage, StageBox


class DetailLayoutMixin:
    """Build the detail view: top bar, post column, side column, author card, related and comments."""

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_MARGINS[0], PAGE_MARGINS[1], PAGE_MARGINS[2], 0)
        root.setSpacing(PAGE_SPACING)

        root.addLayout(self._build_top_bar())

        self._scroll = transparent_scroll_area("MediaDetailScroll", self)
        page, content = self._build_columns()
        self._build_stage_and_title(content)
        self._build_action_row(content)
        self._build_author_card(content)
        self._build_description_and_tags(content)
        self._build_related(content)
        self._build_comments(content)

        self._scroll.setWidget(page)
        self._scroll.viewport().installEventFilter(self)
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.setInterval(250)
        self._release_timer.timeout.connect(self._release_far_pictures)
        self._scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._release_timer.start())
        root.addWidget(self._scroll, 1)
        self._reset_content()

    def _build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._back_btn = PushButton(tr("Back", "返回", "戻る"), self, FluentIcon.LEFT_ARROW)
        self._back_btn.clicked.connect(self.back_requested)
        bar.addWidget(self._back_btn)
        bar.addStretch(1)
        self._status = CaptionLabel("", self)
        set_secondary_text(self._status)
        bar.addWidget(self._status)
        return bar

    def _build_columns(self) -> tuple[QWidget, QWidget]:
        """The centred page with the post column and the side column for related posts."""

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
        return page, content

    def _build_stage_and_title(self, content: QWidget):
        self._stage = MediaStage(content)
        self._stage.clicked.connect(self._play)
        self._body.addWidget(self._stage)
        # The player takes the cover's place while a video plays on the page.
        self._player_box = StageBox(content)
        player_layout = QVBoxLayout(self._player_box)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(0)
        self._player_box.hide()
        self._body.addWidget(self._player_box)
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

    def _build_action_row(self, content: QWidget):
        actions = ResponsiveFlowLayout(spacing=8)
        self._play_btn = PrimaryPushButton(tr("Play", "播放", "再生"), content, FluentIcon.PLAY)
        self._play_btn.clicked.connect(self._play)
        self._window_play_btn = PushButton(
            tr("Play in window", "独立窗口播放", "別ウィンドウで再生"), content, FluentIcon.APPLICATION,
        )
        self._window_play_btn.clicked.connect(self._play_in_window)
        self._download_btn = PushButton(tr("Download", "下载", "ダウンロード"), content, FluentIcon.DOWNLOAD)
        self._download_btn.clicked.connect(self._queue)
        self._browser_btn = PushButton(tr("Open in browser", "在浏览器打开", "ブラウザーで開く"), content, FluentIcon.GLOBE)
        self._browser_btn.clicked.connect(self._open_in_browser)
        self._copy_btn = PushButton(tr("Copy link", "复制链接", "リンクをコピー"), content, FluentIcon.COPY)
        self._copy_btn.clicked.connect(self._copy_link)
        self._like_btn = ToggleButton(tr("Like", "点赞", "いいね"), content, FluentIcon.HEART)
        self._like_btn.clicked.connect(self._toggle_like)
        attach_hint(self._play_btn, tr("Play", "播放", "再生"), "detail_play")
        attach_hint(self._download_btn, tr("Download", "下载", "ダウンロード"), "detail_download")
        attach_hint(self._browser_btn, tr("Open in browser", "在浏览器打开", "ブラウザーで開く"), "detail_open_browser")
        attach_hint(self._copy_btn, tr("Copy link", "复制链接", "リンクをコピー"), "detail_copy_link")
        for button in (
            self._play_btn, self._window_play_btn, self._download_btn, self._like_btn, self._browser_btn, self._copy_btn,
        ):
            actions.addWidget(button)
        self._body.addLayout(actions)

    def _build_author_card(self, content: QWidget):
        self._author_card = QWidget(content)
        author_column = QVBoxLayout(self._author_card)
        author_column.setContentsMargins(0, 4, 0, 4)
        author_column.setSpacing(8)
        author_row = QHBoxLayout()
        author_row.setSpacing(12)
        author_column.addLayout(author_row)
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
        self._page_btn = PushButton(tr("Author page", "作者页", "作者ページ"), self._author_card, FluentIcon.PEOPLE)
        self._page_btn.setToolTip(tr("Open this author inside the app", "在程序内打开该作者", "このアプリ内で作者を開く"))
        self._page_btn.clicked.connect(self._author_page)
        self._works_btn = PushButton(tr("Their works", "查看作品", "作品を見る"), self._author_card, FluentIcon.SEARCH)
        self._works_btn.clicked.connect(self._author_works)
        author_row.addWidget(self._page_btn)
        author_row.addWidget(self._works_btn)
        # Subscribed here? Followed on Iwara? — and the buttons to change either.
        self._author_status = AuthorStatusBar(self._author_card)
        self._author_status.open_subscription_requested.connect(
            lambda source_id: signal_bus.subscription_source_requested.emit(source_id)
        )
        author_column.addWidget(self._author_status)
        self._body.addWidget(self._author_card)

    def _build_description_and_tags(self, content: QWidget):
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

    def _build_related(self, content: QWidget):
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

    def _build_comments(self, content: QWidget):
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

    def _release_far_pictures(self):
        """Free the decoded pictures of an image post that are far from the viewport.

        A post can have dozens of pictures; only those on (or just off) the screen
        keep their pixels in memory.
        """

        viewport = self._scroll.viewport()
        height = viewport.height()
        for image in self._gallery:
            if not image.is_decoded():
                continue
            top = image.mapTo(viewport, QPoint(0, 0)).y()
            bottom = top + image.height()
            if bottom < -height * 1.5 or top > height * 2.5:
                image.release()

    def _fit_stage(self):
        """Let the player area use most of the visible height, not a fixed cap."""

        height = round(self._scroll.viewport().height() * 0.8)
        self._stage.set_max_height(height)
        self._player_box.set_max_height(height)

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

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
