"""Subscription Interface — sources, update checks, and batch enqueue."""
from __future__ import annotations

import webbrowser
import os
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QSize, QTimer, Qt, QThread, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    ToolButton,
    isDarkTheme,
)

from ..config import app_config
from ..core.manager import download_manager
from ..core.models import STATUS_LABELS, TaskStatus
from ..i18n import tr
from ..signal_bus import signal_bus
from .download_page import FilterDialog, _OPTION_OFF_STYLE, _OPTION_ON_STYLE
from .ui_state import (
    ResponsiveFlowLayout,
    connect_splitter_saver,
    connect_table_column_saver,
    connect_table_width_saver,
    open_table_column_dialog,
    restore_table_columns,
    restore_splitter_sizes,
    restore_table_widths,
)


class SubscriptionRefreshWorker(QThread):
    finished = Signal(dict)

    def __init__(self, source_id: int | None = None):
        super().__init__()
        self._source_id = source_id

    def run(self):
        if self._source_id:
            result = download_manager.refresh_subscription_source(self._source_id)
            summary = download_manager._subscription_refresh_summary([result])
        else:
            summary = download_manager.refresh_all_subscriptions()
        self.finished.emit(summary)


class SubscriptionImportAuthorsWorker(QThread):
    finished = Signal(dict)

    def run(self):
        self.finished.emit(download_manager.import_followed_author_subscriptions())


class SubscriptionEnqueueWorker(QThread):
    finished = Signal(dict)

    def __init__(self, video_ids: list[str]):
        super().__init__()
        self._video_ids = list(video_ids)

    def run(self):
        self.finished.emit(download_manager.submit_subscription_items(self._video_ids))


class SubscriptionAvatarWorker(QThread):
    avatar_ready = Signal(int, str, str)
    done = Signal()

    def __init__(self, source_ids: list[int]):
        super().__init__()
        self._source_ids = list(source_ids)

    def run(self):
        for source_id in self._source_ids:
            result = download_manager.refresh_subscription_source_avatar(source_id)
            avatar_path = str(result.get("avatar_path", "") or "")
            if avatar_path:
                self.avatar_ready.emit(
                    int(result.get("source_id", source_id) or source_id),
                    str(result.get("avatar_url", "") or ""),
                    avatar_path,
                )
        self.done.emit()


class SubscriptionThumbnailWorker(QThread):
    thumbnail_ready = Signal(str, str)
    done = Signal()

    def __init__(self, requests: list[tuple[str, str]]):
        super().__init__()
        self._requests = list(requests)

    def run(self):
        for video_id, thumbnail_url in self._requests:
            path = download_manager.cache_subscription_thumbnail(video_id, thumbnail_url)
            if path:
                self.thumbnail_ready.emit(video_id, path)
        self.done.emit()


_CONTROL_HEIGHT = 36
_ROW_SPACING = 10



class ResponsiveCoverList(QListWidget):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


def _style_action_button(button: PrimaryPushButton, *, min_width: int = 0):
    button.setFixedHeight(_CONTROL_HEIGHT)
    if min_width:
        button.setMinimumWidth(min_width)
    font = button.font()
    if font.pointSize() < 10:
        font.setPointSize(10)
    button.setFont(font)


def _style_inline_label(label: BodyLabel):
    label.setFixedHeight(_CONTROL_HEIGHT)
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter)


def _apply_fluent_scrollbars(widget: QWidget):
    """Use a compact Fluent-like scrollbar instead of the native Windows arrows."""
    if isDarkTheme():
        track = "rgba(255, 255, 255, 0.06)"
        handle = "rgba(255, 255, 255, 0.30)"
        hover = "rgba(255, 255, 255, 0.46)"
        pressed = "rgba(255, 255, 255, 0.58)"
    else:
        track = "rgba(0, 0, 0, 0.045)"
        handle = "rgba(0, 145, 158, 0.54)"
        hover = "rgba(0, 128, 140, 0.70)"
        pressed = "rgba(0, 112, 124, 0.82)"
    widget.setStyleSheet(
        f"""
        QScrollBar:vertical {{
            background: {track};
            width: 10px;
            margin: 4px 2px 4px 2px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical {{
            background: {handle};
            min-height: 36px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {hover}; }}
        QScrollBar::handle:vertical:pressed {{ background: {pressed}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: {track};
            height: 10px;
            margin: 2px 4px 2px 4px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal {{
            background: {handle};
            min-width: 36px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {hover}; }}
        QScrollBar::handle:horizontal:pressed {{ background: {pressed}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
            width: 0px;
        }}
        """
    )


class _FluentSplitterHandle(QSplitterHandle):
    """Small rounded grip that makes the otherwise subtle splitter discoverable."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self._grip = QFrame(self)
        self._grip.setObjectName("FluentSplitterGrip")
        self._grip.setFrameShape(QFrame.Shape.NoFrame)
        self._grip.setStyleSheet(
            "QFrame#FluentSplitterGrip { background: rgba(0, 160, 170, 0.48); border-radius: 3px; }"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.orientation() == Qt.Orientation.Vertical:
            grip_width, grip_height = min(56, max(32, self.width() - 12)), 4
        else:
            grip_width, grip_height = 4, min(56, max(32, self.height() - 12))
        self._grip.setGeometry(
            max(0, (self.width() - grip_width) // 2),
            max(0, (self.height() - grip_height) // 2),
            grip_width,
            grip_height,
        )


class _FluentContentSplitter(QSplitter):
    def createHandle(self):
        return _FluentSplitterHandle(self.orientation(), self)


def _style_content_splitter(splitter: QSplitter):
    """Make the vertical content splitter look like a subtle Fluent grab handle."""
    if isDarkTheme():
        hover = "rgba(255, 255, 255, 0.18)"
        pressed = "rgba(255, 255, 255, 0.28)"
    else:
        hover = "rgba(0, 0, 0, 0.10)"
        pressed = "rgba(0, 0, 0, 0.18)"
    splitter.setStyleSheet(
        f"""
        QSplitter::handle {{ background: transparent; }}
        QSplitter::handle:horizontal {{ height: 10px; }}
        QSplitter::handle:vertical {{ width: 10px; }}
        QSplitter::handle:hover {{ background: {hover}; }}
        QSplitter::handle:pressed {{ background: {pressed}; }}
        """
    )


class SubscriptionInterface(QWidget):
    """Page for tracking subscription updates and queueing downloads."""

    _SRC_AVATAR = 0
    _SRC_STATE = 1
    _SRC_TYPE = 2
    _SRC_TITLE = 3
    _SRC_NEW = 4
    _SRC_UNDOWNLOADED = 5
    _SRC_ITEMS = 6
    _SRC_CHECKED = 7
    _SRC_KEY = 8
    _SRC_URL = 9
    _SRC_OPEN = 10

    _ITEM_STATE = 0
    _ITEM_REASON = 1
    _ITEM_NEW = 2
    _ITEM_TITLE = 3
    _ITEM_AUTHOR = 4
    _ITEM_PUBLISHED = 5
    _ITEM_ID = 6
    _ITEM_SOURCE_URL = 7
    _ITEM_URL = 8
    _ITEM_FOLDER = 9
    _ITEM_FILE = 10

    _RENDER_BATCH_SIZE = 80

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SubscriptionInterface")
        self._worker: SubscriptionRefreshWorker | None = None
        self._import_worker: SubscriptionImportAuthorsWorker | None = None
        self._enqueue_worker: SubscriptionEnqueueWorker | None = None
        self._avatar_worker: SubscriptionAvatarWorker | None = None
        self._thumbnail_worker: SubscriptionThumbnailWorker | None = None
        self._avatar_requested_source_ids: set[int] = set()
        self._thumbnail_requested_video_ids: set[str] = set()
        self._thumbnail_items_by_video_id: dict[str, list[QListWidgetItem]] = {}
        self._all_sources: list[dict[str, Any]] = []
        self._sources: list[dict[str, Any]] = []
        self._all_items: list[dict[str, Any]] = []
        self._visible_items: list[dict[str, Any]] = []
        self._current_source_id: int | None = None
        self._source_render_index = 0
        self._item_render_index = 0
        self._items_refresh_pending = False
        self._syncing_download_options = False
        self._source_render_timer = QTimer(self)
        self._source_render_timer.setInterval(0)
        self._source_render_timer.timeout.connect(self._render_source_batch)
        self._item_render_timer = QTimer(self)
        self._item_render_timer.setInterval(0)
        self._item_render_timer.timeout.connect(self._render_item_batch)
        self._build_ui()
        self._load_sources()
        signal_bus.tasks_added.connect(self._on_tasks_changed)
        signal_bus.task_added.connect(self._on_task_changed)
        signal_bus.task_status_changed.connect(self._on_task_status_changed)
        signal_bus.tasks_removed.connect(self._on_tasks_changed)
        signal_bus.task_removed.connect(self._on_task_changed)
        signal_bus.download_options_changed.connect(self._sync_download_option_buttons)
        signal_bus.subscription_source_added.connect(self._on_subscription_source_added)
        self._sync_download_option_buttons()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 16)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("Subscriptions", "订阅页", "購読"), self))
        title_row.addStretch()
        root.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter = splitter
        self._splitter_is_vertical = False
        self._source_panel_visible = True
        self._items_panel_visible = True
        self._last_splitter_sizes = [800, 1200]
        splitter.splitterMoved.connect(self._on_splitter_moved)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

        left_panel = CardWidget(self)
        self._source_panel = left_panel
        left_panel.setObjectName("SubscriptionSourcesCard")
        left_panel.setMinimumWidth(0)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.setSpacing(_ROW_SPACING)
        splitter.addWidget(left_panel)

        right_panel = CardWidget(self)
        self._items_panel = right_panel
        right_panel.setObjectName("SubscriptionItemsCard")
        right_panel.setMinimumWidth(0)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(_ROW_SPACING)
        splitter.addWidget(right_panel)

        self._item_content_splitter = _FluentContentSplitter(Qt.Orientation.Vertical, right_panel)
        self._item_content_splitter.setObjectName("SubscriptionItemContentSplitter")
        self._item_content_splitter.setChildrenCollapsible(True)
        self._item_content_splitter.setHandleWidth(10)
        self._item_content_splitter.setToolTip(
            tr(
                "Drag this handle to resize or collapse the video controls.",
                "拖动此分隔条调整或隐藏视频控制区。",
                "このハンドルをドラッグして動画操作領域を調整・折りたたみます。",
            )
        )
        _style_content_splitter(self._item_content_splitter)
        item_controls_panel = QWidget(self._item_content_splitter)
        item_controls_panel.setObjectName("SubscriptionItemControlsPanel")
        item_controls_panel.setMinimumHeight(0)
        item_controls_layout = QVBoxLayout(item_controls_panel)
        item_controls_layout.setContentsMargins(0, 0, 0, 0)
        item_controls_layout.setSpacing(_ROW_SPACING)
        item_content_panel = QWidget(self._item_content_splitter)
        item_content_panel.setObjectName("SubscriptionItemContentPanel")
        item_content_panel.setMinimumHeight(0)
        item_content_layout = QVBoxLayout(item_content_panel)
        item_content_layout.setContentsMargins(0, 0, 0, 0)
        item_content_layout.setSpacing(0)
        self._item_content_splitter.addWidget(item_controls_panel)
        self._item_content_splitter.addWidget(item_content_panel)
        self._item_content_splitter.setStretchFactor(0, 0)
        self._item_content_splitter.setStretchFactor(1, 1)
        right_layout.addWidget(self._item_content_splitter, stretch=1)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        restore_splitter_sizes(splitter, "subscription_splitter_sizes", [800, 1200])
        connect_splitter_saver(splitter, "subscription_splitter_sizes")
        self._last_splitter_sizes = list(splitter.sizes()) or [800, 1200]

        self._toggle_sources_btn = ToolButton(self)
        self._toggle_items_btn = ToolButton(self)
        for button in (self._toggle_sources_btn, self._toggle_items_btn):
            button.setFixedSize(40, _CONTROL_HEIGHT)
        self._toggle_sources_btn.clicked.connect(self._toggle_source_panel)
        self._toggle_items_btn.clicked.connect(self._toggle_item_panel)
        title_row.addWidget(self._toggle_sources_btn)
        title_row.addWidget(self._toggle_items_btn)
        self._update_panel_toggle_buttons()

        source_header = QHBoxLayout()
        source_header.setSpacing(8)
        source_header.addWidget(SubtitleLabel(tr("Subscriptions", "订阅源", "購読元"), self))
        source_header.addStretch()
        left_layout.addLayout(source_header)

        source_actions = ResponsiveFlowLayout()
        source_actions.setSpacing(_ROW_SPACING)
        import_authors_btn = PrimaryPushButton(tr("Import Followed", "导入关注作者", "フォローを取込"), self, FluentIcon.PEOPLE)
        _style_action_button(import_authors_btn)
        import_authors_btn.clicked.connect(self._import_followed_authors)
        source_actions.addWidget(import_authors_btn)

        add_feed_btn = PrimaryPushButton(tr("Account Feed", "账号订阅流", "購読フィード"), self, FluentIcon.HISTORY)
        _style_action_button(add_feed_btn)
        add_feed_btn.clicked.connect(self._add_following_feed)
        source_actions.addWidget(add_feed_btn)

        add_source_btn = PrimaryPushButton(tr("Add Author / Playlist", "添加作者/播放列表", "作者/リストを追加"), self, FluentIcon.PEOPLE)
        _style_action_button(add_source_btn)
        add_source_btn.clicked.connect(self._add_source)
        source_actions.addWidget(add_source_btn)

        delete_source_btn = PrimaryPushButton(
            tr("Delete Subscription", "删除订阅", "購読を削除"),
            self,
            FluentIcon.DELETE,
        )
        _style_action_button(delete_source_btn)
        delete_source_btn.clicked.connect(self._delete_selected_source)
        source_actions.addWidget(delete_source_btn)

        refresh_selected_btn = PrimaryPushButton(tr("Refresh Selected", "刷新选中", "選択を更新"), self, FluentIcon.SYNC)
        _style_action_button(refresh_selected_btn)
        refresh_selected_btn.clicked.connect(self._refresh_selected)
        source_actions.addWidget(refresh_selected_btn)

        refresh_all_btn = PrimaryPushButton(tr("Refresh All", "刷新全部", "全件更新"), self, FluentIcon.SYNC)
        _style_action_button(refresh_all_btn)
        refresh_all_btn.clicked.connect(self._refresh_all)
        source_actions.addWidget(refresh_all_btn)
        left_layout.addLayout(source_actions)

        storage = download_manager.get_subscription_storage_info()
        db_name = os.path.basename(str(storage.get("db_path", "") or "")) or "history.db"
        storage_label = BodyLabel(tr("Saved locally", "本地保存", "ローカル保存") + f": {db_name}", self)
        _style_inline_label(storage_label)
        storage_label.setToolTip(
            tr(
                f"DB: {storage.get('db_path', '')}\nBackup: {storage.get('backup_path', '')}",
                f"数据库: {storage.get('db_path', '')}\n备份: {storage.get('backup_path', '')}",
                f"DB: {storage.get('db_path', '')}\nバックアップ: {storage.get('backup_path', '')}",
            )
        )
        source_meta_row = ResponsiveFlowLayout()
        source_meta_row.setSpacing(_ROW_SPACING)
        source_meta_row.addWidget(storage_label)
        self._source_search_edit = LineEdit(self)
        self._source_search_edit.setPlaceholderText(
            tr(
                "Filter author / username...",
                "筛选作者名 / username...",
                "作者名 / ユーザー名で絞り込み...",
            )
        )
        self._source_search_edit.setClearButtonEnabled(True)
        self._source_search_edit.setMinimumWidth(240)
        self._source_search_edit.textChanged.connect(self._apply_source_filters)
        source_meta_row.addWidget(self._source_search_edit)
        source_columns_btn = PrimaryPushButton(tr("Source Fields", "源字段", "購読元列"), self, FluentIcon.SETTING)
        _style_action_button(source_columns_btn, min_width=96)
        source_columns_btn.clicked.connect(self._configure_source_columns)
        source_meta_row.addWidget(source_columns_btn)
        left_layout.addLayout(source_meta_row)

        self._source_table = TableWidget(self)
        self._source_table.setColumnCount(11)
        self._source_table.setHorizontalHeaderLabels(
            [
                tr("Avatar", "头像", "アイコン"),
                tr("State", "状态", "状態"),
                tr("Type", "类型", "種類"),
                tr("Display Name", "名称（作者名）", "表示名"),
                tr("New", "新增", "新規"),
                tr("Missing", "未下载", "未保存"),
                tr("Items", "项目", "項目"),
                tr("Last Check", "上次刷新", "最終確認"),
                tr("Username", "名称（username）", "ユーザー名"),
                tr("Source URL", "来源URL", "元URL"),
                tr("Page", "主页", "ページ"),
            ]
        )
        self._source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._source_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._source_table.setAlternatingRowColors(True)
        self._source_table.setBorderVisible(True)
        self._source_table.setBorderRadius(8)
        self._source_table.verticalHeader().setVisible(False)
        self._source_table.verticalHeader().setDefaultSectionSize(56)
        self._source_table.setIconSize(QSize(40, 40))
        self._source_table.currentCellChanged.connect(lambda *_args: self._load_items(self._selected_source_id()))
        self._source_table.cellClicked.connect(self._on_source_cell_clicked)
        source_header = self._source_table.horizontalHeader()
        source_header.setHighlightSections(False)
        source_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        source_widths = {
            self._SRC_AVATAR: 52,
            self._SRC_STATE: 68,
            self._SRC_TYPE: 70,
            self._SRC_TITLE: 220,
            self._SRC_NEW: 52,
            self._SRC_UNDOWNLOADED: 72,
            self._SRC_ITEMS: 58,
            self._SRC_CHECKED: 150,
            self._SRC_KEY: 170,
            self._SRC_URL: 220,
            self._SRC_OPEN: 68,
        }
        restore_table_widths(self._source_table, "subscription_source_widths_v3", source_widths)
        connect_table_width_saver(self._source_table, "subscription_source_widths_v3")
        restore_table_columns(
            self._source_table,
            "subscription_source_table_v3",
            default_visible=[
                self._SRC_AVATAR,
                self._SRC_STATE,
                self._SRC_TITLE,
                self._SRC_KEY,
                self._SRC_NEW,
                self._SRC_UNDOWNLOADED,
                self._SRC_ITEMS,
                self._SRC_URL,
            ],
        )
        connect_table_column_saver(self._source_table, "subscription_source_table_v3")
        source_header.sectionResized.connect(lambda *_args: self._fit_source_table_last_column())
        source_header.sectionMoved.connect(lambda *_args: self._fit_source_table_last_column())
        left_layout.addWidget(self._source_table, stretch=1)
        QTimer.singleShot(0, self._fit_source_table_last_column)

        item_summary_row = QHBoxLayout()
        item_summary_row.setSpacing(8)
        item_summary_row.addWidget(SubtitleLabel(tr("Videos", "视频列表", "動画一覧"), self))
        self._summary_label = BodyLabel("", self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setMinimumHeight(_CONTROL_HEIGHT)
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        item_summary_row.addWidget(self._summary_label, stretch=1)
        item_controls_layout.addLayout(item_summary_row)

        item_actions_top = ResponsiveFlowLayout()
        item_actions_top.setSpacing(_ROW_SPACING)
        all_sources_btn = PrimaryPushButton(tr("Show All", "显示全部", "全て表示"), self, FluentIcon.HISTORY)
        _style_action_button(all_sources_btn, min_width=116)
        all_sources_btn.clicked.connect(lambda: self._load_items(None))
        item_actions_top.addWidget(all_sources_btn)

        self._mark_downloaded_btn = PrimaryPushButton(
            tr("Mark Downloaded (Moved)", "标为已下载（移走）", "保存済み（移動済み）"),
            self,
            FluentIcon.CHECKBOX,
        )
        self._mark_downloaded_btn.setToolTip(
            tr(
                "Select one or more videos, then mark them as already downloaded/moved in history.",
                "需要先选中右侧列表里的一个或多个视频；会同步写入历史库为已下载（移走）。",
                "右側リストで1件以上選択してから、履歴上で保存済み（移動済み）にします。",
            )
        )
        _style_action_button(self._mark_downloaded_btn, min_width=176)
        self._mark_downloaded_btn.clicked.connect(self._mark_selected_downloaded_moved)
        item_actions_top.addWidget(self._mark_downloaded_btn)

        self._restore_moved_btn = PrimaryPushButton(
            tr("Restore Moved", "还原已移走", "移動済み解除"),
            self,
            FluentIcon.RETURN,
        )
        self._restore_moved_btn.setToolTip(
            tr(
                "Select moved records, then remove their moved/downloaded marker from history.",
                "需要先选中已移走的视频；会从历史库移除对应的已下载标记。",
                "移動済みの動画を選択して、履歴の保存済みマークを解除します。",
            )
        )
        _style_action_button(self._restore_moved_btn, min_width=136)
        self._restore_moved_btn.clicked.connect(self._restore_selected_downloaded_moved)
        item_actions_top.addWidget(self._restore_moved_btn)
        item_controls_layout.addLayout(item_actions_top)

        item_actions_bottom = ResponsiveFlowLayout()
        item_actions_bottom.setSpacing(_ROW_SPACING)
        self._download_selected_btn = PrimaryPushButton(tr("Download Selected", "下载选中", "選択を保存"), self, FluentIcon.DOWNLOAD)
        self._download_selected_btn.setToolTip(
            tr("Select one or more videos before downloading.", "需要先选中右侧列表里的一个或多个视频。", "右側リストで1件以上選択してください。")
        )
        _style_action_button(self._download_selected_btn, min_width=126)
        self._download_selected_btn.clicked.connect(self._download_selected)
        item_actions_bottom.addWidget(self._download_selected_btn)

        download_new_btn = PrimaryPushButton(tr("Download New", "下载新增", "新規を保存"), self, FluentIcon.DOWNLOAD)
        _style_action_button(download_new_btn, min_width=126)
        download_new_btn.clicked.connect(self._download_new)
        item_actions_bottom.addWidget(download_new_btn)

        download_all_btn = PrimaryPushButton(tr("Download Visible", "下载当前列表", "表示分を保存"), self, FluentIcon.DOWNLOAD)
        _style_action_button(download_all_btn, min_width=146)
        download_all_btn.clicked.connect(self._download_visible)
        item_actions_bottom.addWidget(download_all_btn)
        item_controls_layout.addLayout(item_actions_bottom)

        download_options_row = ResponsiveFlowLayout()
        download_options_row.setSpacing(_ROW_SPACING)
        options_label = BodyLabel(tr("Download Mode", "下载设置", "保存設定"), self)
        _style_inline_label(options_label)
        download_options_row.addWidget(options_label)

        filter_btn = PrimaryPushButton(tr("Filter Rules", "筛选项", "フィルター条件"), self, FluentIcon.FILTER)
        filter_btn.setToolTip(
            tr(
                "Open the same filter dialog used by the download workbench.",
                "打开和下载工作台共用的筛选设置；保存后会同步影响新任务。",
                "ダウンロード画面と同じフィルター設定を開きます。",
            )
        )
        _style_action_button(filter_btn, min_width=108)
        filter_btn.clicked.connect(self._open_filter_dialog)
        download_options_row.addWidget(filter_btn)

        self._option_download_video_btn = self._make_download_option_button(
            tr("Download Video", "下载视频", "動画保存"),
            tr("Queue real video downloads. This is mutually exclusive with mark-only.", "下载真实视频文件；和仅标记已下载互斥。", "動画ファイルを保存します。マークのみとは排他です。"),
        )
        self._option_download_video_btn.clicked.connect(self._on_download_video_option_clicked)
        download_options_row.addWidget(self._option_download_video_btn)

        self._option_mark_downloaded_btn = self._make_download_option_button(
            tr("Mark Only", "仅标记已下载", "マークのみ"),
            tr("Do not download video; only fetch metadata/sidecars and mark as downloaded.", "不下载视频，只拉取元数据/附属文件并标记为已下载。", "動画を保存せず、メタデータ/関連ファイルのみ取得して保存済みにします。"),
        )
        self._option_mark_downloaded_btn.clicked.connect(self._on_mark_downloaded_option_clicked)
        download_options_row.addWidget(self._option_mark_downloaded_btn)

        self._option_download_thumb_btn = self._make_download_option_button(
            tr("Thumbnail", "下载封面", "サムネイル"),
            tr("Download thumbnail images when metadata is available.", "有元数据时下载封面图。", "メタデータ取得時にサムネイルを保存します。"),
        )
        self._option_download_thumb_btn.clicked.connect(self._on_download_thumb_option_clicked)
        download_options_row.addWidget(self._option_download_thumb_btn)

        self._option_collect_nfo_btn = self._make_download_option_button(
            "NFO",
            tr("Write Kodi/Jellyfin compatible NFO metadata.", "写出兼容 Kodi/Jellyfin 的 NFO 元数据。", "Kodi/Jellyfin互換のNFOを書き出します。"),
        )
        self._option_collect_nfo_btn.clicked.connect(self._on_collect_nfo_option_clicked)
        download_options_row.addWidget(self._option_collect_nfo_btn)
        item_controls_layout.addLayout(download_options_row)

        title_filter_row = ResponsiveFlowLayout()
        title_filter_row.setSpacing(_ROW_SPACING)
        title_filter_label = BodyLabel(tr("Title Keywords", "标题关键词", "タイトルキーワード"), self)
        _style_inline_label(title_filter_label)
        title_filter_row.addWidget(title_filter_label)
        self._title_include_edit = LineEdit(self)
        self._title_include_edit.setPlaceholderText(
            tr("Include any (comma separated)", "包含任一关键词（逗号分隔）", "いずれかを含む（カンマ区切り）")
        )
        self._title_include_edit.setClearButtonEnabled(True)
        self._title_include_edit.textChanged.connect(self._apply_item_filters)
        title_filter_row.addWidget(self._title_include_edit)
        self._title_exclude_edit = LineEdit(self)
        self._title_exclude_edit.setPlaceholderText(
            tr("Exclude any (comma separated)", "排除任一关键词（逗号分隔）", "いずれかを除外（カンマ区切り）")
        )
        self._title_exclude_edit.setClearButtonEnabled(True)
        self._title_exclude_edit.textChanged.connect(self._apply_item_filters)
        title_filter_row.addWidget(self._title_exclude_edit)
        item_controls_layout.addLayout(title_filter_row)

        item_filter_row = ResponsiveFlowLayout()
        item_filter_row.setSpacing(_ROW_SPACING)
        install_label = BodyLabel(tr("Download Status", "下载状态", "保存状態"), self)
        _style_inline_label(install_label)
        item_filter_row.addWidget(install_label)
        self._install_filter_combo = ComboBox(self)
        self._install_filter_combo.addItems(
            [
                tr("All", "全部", "全て"),
                tr("Ready", "可下载", "保存可能"),
                tr("Not Downloadable", "不可下载", "保存不可"),
                tr("Downloaded", "已下载", "保存済み"),
                tr("Moved", "已移走", "移動済み"),
                tr("Queued", "已入队", "キュー内"),
            ]
        )
        self._install_filter_combo.setFixedSize(150, _CONTROL_HEIGHT)
        self._install_filter_combo.currentIndexChanged.connect(self._apply_item_filters)
        item_filter_row.addWidget(self._install_filter_combo)

        new_label = BodyLabel(tr("New", "新增", "新規"), self)
        _style_inline_label(new_label)
        item_filter_row.addWidget(new_label)
        self._new_filter_combo = ComboBox(self)
        self._new_filter_combo.addItems(
            [
                tr("All", "全部", "全て"),
                tr("New Only", "仅新增", "新規のみ"),
                tr("Not New", "非新增", "新規以外"),
            ]
        )
        self._new_filter_combo.setFixedSize(120, _CONTROL_HEIGHT)
        self._new_filter_combo.currentIndexChanged.connect(self._apply_item_filters)
        item_filter_row.addWidget(self._new_filter_combo)

        sort_label = BodyLabel(tr("Sort", "排序", "並び順"), self)
        _style_inline_label(sort_label)
        item_filter_row.addWidget(sort_label)
        self._item_sort_combo = ComboBox(self)
        self._item_sort_combo.addItems(
            [
                tr("Date Desc", "时间倒序", "日付降順"),
                tr("Date Asc", "时间升序", "日付昇順"),
                tr("New First", "新增优先", "新規優先"),
                tr("Title A-Z", "标题 A-Z", "タイトル A-Z"),
            ]
        )
        self._item_sort_combo.setFixedSize(130, _CONTROL_HEIGHT)
        self._item_sort_combo.currentIndexChanged.connect(self._apply_item_filters)
        item_filter_row.addWidget(self._item_sort_combo)

        view_label = BodyLabel(tr("View", "视图", "表示"), self)
        _style_inline_label(view_label)
        item_filter_row.addWidget(view_label)
        self._item_view_combo = ComboBox(self)
        self._item_view_combo.addItems(
            [
                tr("List", "列表", "リスト"),
                tr("Covers", "封面", "カバー"),
            ]
        )
        self._item_view_combo.setFixedSize(100, _CONTROL_HEIGHT)
        self._item_view_combo.currentIndexChanged.connect(self._on_item_view_changed)
        item_filter_row.addWidget(self._item_view_combo)

        item_columns_btn = PrimaryPushButton(tr("Fields", "字段设置", "列設定"), self, FluentIcon.SETTING)
        _style_action_button(item_columns_btn, min_width=96)
        item_columns_btn.clicked.connect(self._configure_item_columns)
        item_filter_row.addWidget(item_columns_btn)
        item_controls_layout.addLayout(item_filter_row)

        self._item_table = TableWidget(self)
        self._item_table.setColumnCount(11)
        self._item_table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("Reason", "原因", "理由"),
                tr("New", "新增", "新規"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Published", "发布时间", "公開日"),
                "ID",
                tr("Source URL", "来源URL", "元URL"),
                tr("Page", "页面", "ページ"),
                tr("Folder", "文件夹", "フォルダー"),
                tr("File", "文件", "ファイル"),
            ]
        )
        self._item_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._item_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._item_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._item_table.setAlternatingRowColors(True)
        self._item_table.setBorderVisible(True)
        self._item_table.setBorderRadius(8)
        self._item_table.setWordWrap(False)
        self._item_table.verticalHeader().setVisible(False)
        self._item_table.verticalHeader().setDefaultSectionSize(38)
        self._item_table.itemDoubleClicked.connect(lambda item: self._open_item_from_cell(item, open_file=None))
        self._item_table.cellClicked.connect(self._on_item_cell_clicked)
        item_header = self._item_table.horizontalHeader()
        item_header.setHighlightSections(False)
        item_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        item_widths = {
            self._ITEM_STATE: 90,
            self._ITEM_REASON: 260,
            self._ITEM_NEW: 58,
            self._ITEM_TITLE: 660,
            self._ITEM_AUTHOR: 140,
            self._ITEM_PUBLISHED: 130,
            self._ITEM_ID: 130,
            self._ITEM_SOURCE_URL: 260,
            self._ITEM_URL: 68,
            self._ITEM_FOLDER: 68,
            self._ITEM_FILE: 68,
        }
        restore_table_widths(self._item_table, "subscription_item_widths_v2", item_widths)
        connect_table_width_saver(self._item_table, "subscription_item_widths_v2")
        restore_table_columns(self._item_table, "subscription_item_table_v2")
        connect_table_column_saver(self._item_table, "subscription_item_table_v2")
        self._item_table.selectionModel().selectionChanged.connect(lambda *_args: self._update_selection_actions())

        self._thumbnail_list = ResponsiveCoverList(self)
        self._thumbnail_list.setViewMode(QListView.ViewMode.IconMode)
        self._thumbnail_list.setResizeMode(QListView.ResizeMode.Adjust)
        self._thumbnail_list.setMovement(QListView.Movement.Static)
        self._thumbnail_list.setFlow(QListView.Flow.LeftToRight)
        self._thumbnail_list.setWrapping(True)
        self._thumbnail_list.setWordWrap(True)
        self._thumbnail_list.setUniformItemSizes(True)
        self._thumbnail_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._thumbnail_list.setIconSize(QSize(256, 144))
        self._thumbnail_list.setGridSize(QSize(286, 232))
        self._thumbnail_list.setSpacing(8)
        self._thumbnail_list.itemDoubleClicked.connect(self._on_thumbnail_item_activated)
        self._thumbnail_list.itemSelectionChanged.connect(self._update_selection_actions)
        self._thumbnail_list.resized.connect(self._update_thumbnail_grid)

        self._item_stack = QStackedWidget(self)
        self._item_stack.addWidget(self._item_table)
        self._item_stack.addWidget(self._thumbnail_list)
        item_content_layout.addWidget(self._item_stack, stretch=1)
        _apply_fluent_scrollbars(self._item_table)
        _apply_fluent_scrollbars(self._thumbnail_list)
        restore_splitter_sizes(self._item_content_splitter, "subscription_item_content_splitter_sizes", [390, 1000])
        connect_splitter_saver(self._item_content_splitter, "subscription_item_content_splitter_sizes")
        self._update_selection_actions()
        self._update_thumbnail_grid()

    def _on_splitter_moved(self, *_args):
        sizes = list(self._splitter.sizes())
        if self._source_panel_visible and self._items_panel_visible and all(size > 0 for size in sizes):
            self._last_splitter_sizes = sizes
        self._fit_source_table_last_column()
        self._update_thumbnail_grid()

    def _update_panel_toggle_buttons(self):
        if not hasattr(self, "_toggle_sources_btn"):
            return
        source_hidden = not self._source_panel_visible
        items_hidden = not self._items_panel_visible
        self._toggle_sources_btn.setIcon(FluentIcon.VIEW if source_hidden else FluentIcon.LEFT_ARROW)
        self._toggle_items_btn.setIcon(FluentIcon.VIEW if items_hidden else FluentIcon.RIGHT_ARROW)
        self._toggle_sources_btn.setToolTip(
            tr("Show subscription sources" if source_hidden else "Hide subscription sources",
               "显示订阅源" if source_hidden else "隐藏订阅源",
               "購読元を表示" if source_hidden else "購読元を隠す")
        )
        self._toggle_items_btn.setToolTip(
            tr("Show video list" if items_hidden else "Hide video list",
               "显示视频列表" if items_hidden else "隐藏视频列表",
               "動画一覧を表示" if items_hidden else "動画一覧を隠す")
        )

    def _set_panel_visible(self, index: int, visible: bool):
        if index == 0:
            if not visible and not self._items_panel_visible:
                return
            self._source_panel_visible = visible
            self._source_panel.setVisible(visible)
        else:
            if not visible and not self._source_panel_visible:
                return
            self._items_panel_visible = visible
            self._items_panel.setVisible(visible)

        if self._source_panel_visible and self._items_panel_visible:
            sizes = list(self._last_splitter_sizes)
            total = max(2, self._splitter.height() if self._splitter_is_vertical else self._splitter.width())
            if len(sizes) != 2 or sum(sizes) <= 0 or min(sizes) <= 0:
                sizes = [int(total * 0.4), int(total * 0.6)]
            self._splitter.setSizes(sizes)
        else:
            visible_index = 0 if self._source_panel_visible else 1
            visible_panel = self._source_panel if visible_index == 0 else self._items_panel
            visible_panel.setVisible(True)
            total = max(1, sum(self._splitter.sizes()))
            sizes = [0, 0]
            sizes[visible_index] = total
            self._splitter.setSizes(sizes)
        self._update_panel_toggle_buttons()
        self._fit_source_table_last_column()
        self._update_thumbnail_grid()

    def _toggle_source_panel(self):
        self._set_panel_visible(0, not self._source_panel_visible)

    def _toggle_item_panel(self):
        self._set_panel_visible(1, not self._items_panel_visible)

    def _fit_source_table_last_column(self):
        if not hasattr(self, "_source_table"):
            return
        header = self._source_table.horizontalHeader()
        visible_columns = [
            column for column in range(self._source_table.columnCount())
            if not self._source_table.isColumnHidden(column)
        ]
        if not visible_columns:
            return
        visible_columns.sort(key=header.visualIndex)
        last_column = visible_columns[-1]
        for column in visible_columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        # Keep the final visible field bound to the panel width. The splitter
        # therefore gives its extra space to the last source-table column.
        header.setSectionResizeMode(last_column, QHeaderView.ResizeMode.Stretch)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Horizontal splitters are useful on wide screens, but at compact widths
        # they force both panels to keep their full toolbar width. Stack panels
        # vertically instead so every control can wrap naturally.
        should_stack = self.width() < 1100
        if should_stack != self._splitter_is_vertical:
            self._splitter_is_vertical = should_stack
            self._splitter.setOrientation(
                Qt.Orientation.Vertical if should_stack else Qt.Orientation.Horizontal
            )
            if self._source_panel_visible and self._items_panel_visible:
                if should_stack:
                    height = max(1, self._splitter.height())
                    self._splitter.setSizes([max(250, int(height * 0.38)), max(300, int(height * 0.62))])
                else:
                    width = max(1, self._splitter.width())
                    self._splitter.setSizes([max(420, int(width * 0.40)), max(520, int(width * 0.60))])
            else:
                visible_index = 0 if self._source_panel_visible else 1
                total = max(1, self._splitter.height() if should_stack else self._splitter.width())
                sizes = [0, 0]
                sizes[visible_index] = total
                self._splitter.setSizes(sizes)
        self._fit_source_table_last_column()
        self._update_thumbnail_grid()

    def _update_thumbnail_grid(self):
        if not hasattr(self, "_thumbnail_list"):
            return
        viewport_width = self._thumbnail_list.viewport().width()
        if viewport_width <= 0:
            return
        spacing = 8
        target_card_width = 286
        columns = max(1, min(6, (viewport_width + spacing) // (target_card_width + spacing)))
        card_width = max(180, (viewport_width - spacing * (columns + 1)) // columns)
        image_width = max(140, card_width - 20)
        image_height = max(80, round(image_width * 9 / 16))
        self._thumbnail_list.setIconSize(QSize(image_width, image_height))
        self._thumbnail_list.setGridSize(QSize(card_width, image_height + 88))
        self._thumbnail_list.setSpacing(spacing)

    def _configure_source_columns(self):
        open_table_column_dialog(
            self._source_table,
            "subscription_source_table_v3",
            title=tr("Source Columns", "订阅源字段", "購読元列設定"),
            default_visible=[
                self._SRC_AVATAR,
                self._SRC_STATE,
                self._SRC_TITLE,
                self._SRC_KEY,
                self._SRC_NEW,
                self._SRC_UNDOWNLOADED,
                self._SRC_ITEMS,
                self._SRC_URL,
            ],
            parent=self,
        )
        self._fit_source_table_last_column()

    def _configure_item_columns(self):
        open_table_column_dialog(
            self._item_table,
            "subscription_item_table_v2",
            title=tr("Video Columns", "作品列表字段", "動画列設定"),
            parent=self,
        )

    def _load_sources(self):
        previous_source_id = self._selected_source_id()
        self._all_sources = download_manager.get_subscription_sources()
        self._apply_source_filters(preferred_source_id=previous_source_id)

    def _refresh_sources_keep_current_items(self):
        source_id = self._current_source_id
        self._all_sources = download_manager.get_subscription_sources()
        self._apply_source_filters(preferred_source_id=source_id)

    def _apply_source_filters(self, *_args, preferred_source_id: int | None = None):
        selected_source_id = preferred_source_id if preferred_source_id is not None else self._selected_source_id()
        query = self._source_search_edit.text().strip().casefold() if hasattr(self, "_source_search_edit") else ""
        sources = sorted(self._all_sources, key=_source_sort_key)
        if query:
            sources = [source for source in sources if query in _source_search_text(source)]
        self._sources = sources
        self._render_sources()
        if selected_source_id is not None and self._select_source_id(selected_source_id):
            self._load_items(selected_source_id)
            return
        if self._sources:
            first_source_id = int(self._sources[0].get("id", 0) or 0)
            self._source_table.blockSignals(True)
            self._source_table.setCurrentCell(0, self._SRC_STATE)
            self._source_table.blockSignals(False)
            self._load_items(first_source_id or None)
            return
        self._load_items(None)

    def _select_source_id(self, source_id: int) -> bool:
        selected_row = -1
        for row, source in enumerate(self._sources):
            if int(source.get("id", 0) or 0) == int(source_id):
                selected_row = row
                break
        if selected_row < 0:
            return False
        self._source_table.blockSignals(True)
        self._source_table.setCurrentCell(selected_row, self._SRC_STATE)
        self._source_table.blockSignals(False)
        return True

    def _render_sources(self):
        self._source_render_timer.stop()
        self._source_render_index = 0
        self._source_table.blockSignals(True)
        self._source_table.setUpdatesEnabled(False)
        self._source_table.clearContents()
        self._source_table.setRowCount(len(self._sources))
        self._source_table.setUpdatesEnabled(True)
        self._source_table.blockSignals(False)
        self._source_render_timer.start()
        self._start_avatar_worker_for_missing_sources()

    def _render_source_batch(self):
        start = self._source_render_index
        if start >= len(self._sources):
            self._source_render_timer.stop()
            return
        end = min(start + self._RENDER_BATCH_SIZE, len(self._sources))
        self._source_table.setUpdatesEnabled(False)
        try:
            for row in range(start, end):
                self._render_source_row(row, self._sources[row])
        finally:
            self._source_table.setUpdatesEnabled(True)
        self._source_render_index = end
        if end >= len(self._sources):
            self._source_render_timer.stop()

    def _render_source_row(self, row: int, source: dict[str, Any]):
        source_id = int(source.get("id", 0) or 0)
        enabled = bool(int(source.get("enabled", 1) or 0))
        source_url = _source_url(source)
        avatar_item = self._make_source_avatar_item(source_id, source)
        self._source_table.setItem(row, self._SRC_AVATAR, avatar_item)
        values = {
            self._SRC_STATE: tr("Enabled", "启用", "有効") if enabled else tr("Disabled", "停用", "無効"),
            self._SRC_TYPE: _source_type_label(str(source.get("source_type", "") or "")),
            self._SRC_TITLE: str(source.get("title", "") or ""),
            self._SRC_NEW: str(source.get("new_count", 0) or 0),
            self._SRC_UNDOWNLOADED: str(source.get("undownloaded_count", 0) or 0),
            self._SRC_ITEMS: str(source.get("item_count", 0) or 0),
            self._SRC_CHECKED: str(source.get("last_checked_at", "") or ""),
            self._SRC_KEY: str(source.get("source_key", "") or ""),
        }
        for col, value in values.items():
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            item.setToolTip(value)
            if col == self._SRC_STATE:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor("#107c10" if enabled else "#777777"))
            if col in (self._SRC_NEW, self._SRC_UNDOWNLOADED, self._SRC_ITEMS):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._source_table.setItem(row, col, item)
        source_url_item = QTableWidgetItem(source_url)
        source_url_item.setData(Qt.ItemDataRole.UserRole, source_id)
        source_url_item.setToolTip(source_url or tr("No source URL", "没有订阅源链接", "購読元URLがありません"))
        self._source_table.setItem(row, self._SRC_URL, source_url_item)
        self._set_action_item(
            self._source_table,
            row,
            self._SRC_OPEN,
            source_id,
            "open_source",
            tr("Open", "打开", "開く"),
            tr("Open source page", "打开订阅源页面", "購読元ページを開く"),
            bool(source_url),
            action_url=source_url,
        )

    def _make_source_avatar_item(self, source_id: int, source: dict[str, Any]) -> QTableWidgetItem:
        item = QTableWidgetItem("")
        item.setData(Qt.ItemDataRole.UserRole, source_id)
        title = str(source.get("title", "") or source.get("source_key", "") or "")
        avatar_path = str(source.get("avatar_path", "") or "")
        if avatar_path and os.path.isfile(avatar_path):
            pixmap = QPixmap(avatar_path)
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap))
                item.setToolTip(title)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                return item
        item.setText((title[:1] or "?").upper())
        item.setToolTip(title or tr("No avatar cached yet", "头像尚未缓存", "アバター未保存"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor("#777777"))
        return item

    def _start_avatar_worker_for_missing_sources(self):
        if self._avatar_worker and self._avatar_worker.isRunning():
            return
        source_ids: list[int] = []
        for source in self._sources:
            if str(source.get("source_type", "") or "") != "author":
                continue
            source_id = int(source.get("id", 0) or 0)
            if not source_id or source_id in self._avatar_requested_source_ids:
                continue
            avatar_path = str(source.get("avatar_path", "") or "")
            if avatar_path and os.path.isfile(avatar_path):
                continue
            source_ids.append(source_id)
        if not source_ids:
            return
        self._avatar_requested_source_ids.update(source_ids)
        self._avatar_worker = SubscriptionAvatarWorker(source_ids)
        self._avatar_worker.avatar_ready.connect(self._on_avatar_ready)
        self._avatar_worker.done.connect(self._on_avatar_worker_finished)
        self._avatar_worker.start()

    def _on_avatar_ready(self, source_id: int, avatar_url: str, avatar_path: str):
        for collection in (self._all_sources, self._sources):
            for source in collection:
                if int(source.get("id", 0) or 0) == int(source_id):
                    source["avatar_url"] = avatar_url
                    source["avatar_path"] = avatar_path
        for row, source in enumerate(self._sources):
            if int(source.get("id", 0) or 0) == int(source_id):
                self._render_source_row(row, source)
                break

    def _on_avatar_worker_finished(self):
        self._avatar_worker = None
        self._start_avatar_worker_for_missing_sources()

    def _load_items(self, source_id: int | None):
        self._current_source_id = source_id
        self._all_items = download_manager.get_subscription_items(source_id)
        self._apply_item_filters()

    def _apply_item_filters(self, *_args):
        self._sync_items_with_current_tasks()
        items = list(self._all_items)
        install_idx = self._install_filter_combo.currentIndex() if hasattr(self, "_install_filter_combo") else 0
        new_idx = self._new_filter_combo.currentIndex() if hasattr(self, "_new_filter_combo") else 0
        sort_idx = self._item_sort_combo.currentIndex() if hasattr(self, "_item_sort_combo") else 0
        include_terms = _split_title_keywords(
            self._title_include_edit.text() if hasattr(self, "_title_include_edit") else ""
        )
        exclude_terms = _split_title_keywords(
            self._title_exclude_edit.text() if hasattr(self, "_title_exclude_edit") else ""
        )

        if include_terms or exclude_terms:
            items = [
                item
                for item in items
                if _title_matches_keywords(
                    str(item.get("title", "") or item.get("video_id", "") or ""),
                    include_terms,
                    exclude_terms,
                )
            ]

        def is_downloaded(item: dict[str, Any]) -> bool:
            return bool(item.get("downloaded"))

        def file_exists(item: dict[str, Any]) -> bool:
            return bool(item.get("download_file_exists"))

        def unavailable(item: dict[str, Any]) -> bool:
            return _item_not_downloadable(item)

        if install_idx == 1:
            items = [item for item in items if not is_downloaded(item) and not item.get("queued") and not unavailable(item)]
        elif install_idx == 2:
            items = [item for item in items if unavailable(item)]
        elif install_idx == 3:
            items = [item for item in items if is_downloaded(item) and file_exists(item)]
        elif install_idx == 4:
            items = [item for item in items if is_downloaded(item) and not file_exists(item)]
        elif install_idx == 5:
            items = [item for item in items if item.get("queued")]

        if new_idx == 1:
            items = [item for item in items if int(item.get("is_new", 0) or 0)]
        elif new_idx == 2:
            items = [item for item in items if not int(item.get("is_new", 0) or 0)]

        if sort_idx == 1:
            items.sort(key=_item_date_key)
        elif sort_idx == 2:
            items.sort(key=_item_date_key, reverse=True)
            items.sort(key=lambda item: 0 if int(item.get("is_new", 0) or 0) else 1)
        elif sort_idx == 3:
            items.sort(key=lambda item: str(item.get("title", "") or item.get("video_id", "") or "").lower())
        else:
            items.sort(key=_item_date_key, reverse=True)

        self._visible_items = items
        self._render_items()

    def _render_items(self):
        self._item_render_timer.stop()
        self._item_render_index = 0
        new_count = downloaded_count = moved_count = queued_count = unavailable_count = 0
        for item_data in self._all_items:
            downloaded = bool(item_data.get("downloaded"))
            file_exists = bool(item_data.get("download_file_exists"))
            queued = bool(item_data.get("queued"))
            is_new = bool(int(item_data.get("is_new", 0) or 0))
            if is_new:
                new_count += 1
            if downloaded and file_exists:
                downloaded_count += 1
            if downloaded and not file_exists:
                moved_count += 1
            if queued:
                queued_count += 1
            if _item_not_downloadable(item_data):
                unavailable_count += 1
        self._summary_label.setText(
            tr(
                f"Visible: {len(self._visible_items)}/{len(self._all_items)} | new: {new_count} | downloaded: {downloaded_count} | moved: {moved_count} | queued: {queued_count} | unavailable: {unavailable_count}",
                f"当前显示: {len(self._visible_items)}/{len(self._all_items)} | 新增: {new_count} | 本地已下载: {downloaded_count} | 已移走: {moved_count} | 已在队列: {queued_count} | 不可下载: {unavailable_count}",
                f"表示: {len(self._visible_items)}/{len(self._all_items)} | 新規: {new_count} | 保存済み: {downloaded_count} | 移動済み: {moved_count} | キュー内: {queued_count} | 保存不可: {unavailable_count}",
            )
        )

        cover_mode = (
            hasattr(self, "_item_view_combo")
            and self._item_view_combo.currentIndex() == 1
        )
        if hasattr(self, "_item_stack"):
            self._item_stack.setCurrentIndex(1 if cover_mode else 0)
        if cover_mode:
            self._item_table.clearContents()
            self._item_table.setRowCount(0)
            self._render_thumbnail_items()
        else:
            self._thumbnail_list.clear()
            self._thumbnail_items_by_video_id.clear()
            self._item_table.setUpdatesEnabled(False)
            self._item_table.clearContents()
            self._item_table.setRowCount(len(self._visible_items))
            self._item_table.setUpdatesEnabled(True)
            self._item_render_timer.start()
        self._update_selection_actions()

    def _on_item_view_changed(self, _index: int):
        self._render_items()
        QTimer.singleShot(0, self._update_thumbnail_grid)

    def _render_thumbnail_items(self):
        self._update_thumbnail_grid()
        self._thumbnail_list.setUpdatesEnabled(False)
        self._thumbnail_list.clear()
        self._thumbnail_items_by_video_id.clear()
        placeholder = self._thumbnail_placeholder_icon()
        try:
            for item_data in self._visible_items:
                video_id = str(item_data.get("video_id", "") or "").strip()
                if not video_id:
                    continue
                title = str(item_data.get("title", "") or video_id)
                author = str(item_data.get("author", "") or item_data.get("source_title", "") or "")
                published = _date_only(str(item_data.get("published_at", "") or ""))
                state = _item_state_text(
                    downloaded=bool(item_data.get("downloaded")),
                    queued=bool(item_data.get("queued")),
                    file_exists=bool(item_data.get("download_file_exists")),
                    task_status=str(item_data.get("task_status", "") or ""),
                    download_state=str(item_data.get("download_state", "") or ""),
                    download_reason=str(item_data.get("download_reason", "") or ""),
                )
                meta = " · ".join(value for value in (author, published) if value)
                label = _ellipsize(title, 56)
                if meta:
                    label += f"\n{_ellipsize(meta, 44)}"
                label += f"\n{state}"
                path = str(item_data.get("thumbnail_path", "") or "")
                icon = self._thumbnail_icon(path) if path and os.path.isfile(path) else placeholder
                list_item = QListWidgetItem(icon, label)
                list_item.setData(Qt.ItemDataRole.UserRole, video_id)
                list_item.setToolTip(
                    tr(
                        f"{title}\nAuthor: {author}\nDouble-click to open",
                        f"{title}\n作者: {author}\n双击后按设置打开",
                        f"{title}\n作者: {author}\nダブルクリックで開く",
                    )
                )
                list_item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                self._thumbnail_list.addItem(list_item)
                self._thumbnail_items_by_video_id.setdefault(video_id, []).append(list_item)
        finally:
            self._thumbnail_list.setUpdatesEnabled(True)
        self._start_thumbnail_worker_for_visible_items()

    def _thumbnail_placeholder_icon(self) -> QIcon:
        pixmap = QPixmap(self._thumbnail_list.iconSize())
        pixmap.fill(QColor("#34373d" if isDarkTheme() else "#e8e8e8"))
        return QIcon(pixmap)

    def _thumbnail_icon(self, path: str) -> QIcon:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return self._thumbnail_placeholder_icon()
        size = self._thumbnail_list.iconSize()
        scaled = pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - size.width()) // 2)
        y = max(0, (scaled.height() - size.height()) // 2)
        return QIcon(scaled.copy(x, y, size.width(), size.height()))

    def _start_thumbnail_worker_for_visible_items(self):
        if self._thumbnail_worker and self._thumbnail_worker.isRunning():
            return
        requests: list[tuple[str, str]] = []
        for item_data in self._visible_items:
            video_id = str(item_data.get("video_id", "") or "").strip()
            thumbnail_url = str(item_data.get("thumbnail_url", "") or "").strip()
            thumbnail_path = str(item_data.get("thumbnail_path", "") or "").strip()
            if (
                not video_id
                or not thumbnail_url
                or (thumbnail_path and os.path.isfile(thumbnail_path))
                or video_id in self._thumbnail_requested_video_ids
            ):
                continue
            requests.append((video_id, thumbnail_url))
            if len(requests) >= 80:
                break
        if not requests:
            return
        self._thumbnail_requested_video_ids.update(video_id for video_id, _url in requests)
        self._thumbnail_worker = SubscriptionThumbnailWorker(requests)
        self._thumbnail_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumbnail_worker.done.connect(self._on_thumbnail_worker_finished)
        self._thumbnail_worker.start()

    def _on_thumbnail_ready(self, video_id: str, path: str):
        for collection in (self._all_items, self._visible_items):
            for item_data in collection:
                if str(item_data.get("video_id", "") or "") == video_id:
                    item_data["thumbnail_path"] = path
        icon = self._thumbnail_icon(path)
        for list_item in self._thumbnail_items_by_video_id.get(video_id, []):
            list_item.setIcon(icon)

    def _on_thumbnail_worker_finished(self):
        worker = self._thumbnail_worker
        self._thumbnail_worker = None
        if worker:
            worker.deleteLater()
        if (
            hasattr(self, "_item_view_combo")
            and self._item_view_combo.currentIndex() == 1
        ):
            self._start_thumbnail_worker_for_visible_items()

    def _render_item_batch(self):
        start = self._item_render_index
        if start >= len(self._visible_items):
            self._item_render_timer.stop()
            return
        end = min(start + self._RENDER_BATCH_SIZE, len(self._visible_items))
        self._item_table.setUpdatesEnabled(False)
        try:
            for row in range(start, end):
                self._render_item_row(row, self._visible_items[row])
        finally:
            self._item_table.setUpdatesEnabled(True)
        self._item_render_index = end
        if end >= len(self._visible_items):
            self._item_render_timer.stop()

    def _render_item_row(self, row: int, item_data: dict[str, Any]):
        video_id = str(item_data.get("video_id", "") or "")
        downloaded = bool(item_data.get("downloaded"))
        queued = bool(item_data.get("queued"))
        task_status = str(item_data.get("task_status", "") or "")
        download_reason = str(item_data.get("download_reason", "") or "")
        is_new = bool(int(item_data.get("is_new", 0) or 0))
        file_exists = bool(item_data.get("download_file_exists"))
        source_title = str(item_data.get("source_title", "") or "")
        source_url = str(item_data.get("source_url", "") or _video_url(video_id))
        state = _item_state_text(
            downloaded=downloaded,
            queued=queued,
            file_exists=file_exists,
            task_status=task_status,
            download_state=str(item_data.get("download_state", "") or ""),
            download_reason=download_reason,
        )
        values = [
            state,
            download_reason,
            tr("Yes", "是", "はい") if is_new else "",
            str(item_data.get("title", "") or video_id),
            str(item_data.get("author", "") or ""),
            _date_only(str(item_data.get("published_at", "") or "")),
            video_id,
        ]
        for col, value in enumerate(values):
            cell = QTableWidgetItem(value)
            cell.setData(Qt.ItemDataRole.UserRole, video_id)
            tooltip = value
            if col in (self._ITEM_TITLE, self._ITEM_AUTHOR):
                tooltip = tr(
                    f"{value}\nSource: {source_title}",
                    f"{value}\n来源: {source_title}",
                    f"{value}\n元: {source_title}",
                )
                if download_reason:
                    tooltip += f"\n{download_reason}"
            if col in (self._ITEM_STATE, self._ITEM_REASON) and download_reason:
                tooltip = download_reason
            cell.setToolTip(tooltip)
            if col in (self._ITEM_STATE, self._ITEM_NEW):
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == self._ITEM_STATE:
                cell.setForeground(
                    _state_color(
                        downloaded=downloaded,
                        queued=queued,
                        file_exists=file_exists,
                        task_status=task_status,
                        download_state=str(item_data.get("download_state", "") or ""),
                    )
                )
            elif col == self._ITEM_REASON and download_reason:
                cell.setForeground(QColor("#c42b1c"))
            elif col == self._ITEM_NEW and is_new:
                cell.setForeground(QColor("#c17d00"))
            self._item_table.setItem(row, col, cell)

        source_url_item = QTableWidgetItem(source_url)
        source_url_item.setData(Qt.ItemDataRole.UserRole, video_id)
        source_url_item.setToolTip(source_url or tr("No video URL", "没有视频链接", "動画URLがありません"))
        self._item_table.setItem(row, self._ITEM_SOURCE_URL, source_url_item)
        self._set_action_item(
            self._item_table,
            row,
            self._ITEM_URL,
            video_id,
            "open_url",
            tr("Page", "页面", "ページ"),
            tr("Open video page", "打开视频页", "動画ページを開く"),
            bool(video_id),
            action_url=_video_url(video_id),
        )
        self._set_action_item(
            self._item_table,
            row,
            self._ITEM_FOLDER,
            video_id,
            "open_folder",
            tr("Folder", "文件夹", "フォルダー"),
            tr("Open downloaded folder", "打开下载文件夹", "保存フォルダーを開く"),
            file_exists,
        )
        self._set_action_item(
            self._item_table,
            row,
            self._ITEM_FILE,
            video_id,
            "open_file",
            tr("File", "文件", "ファイル"),
            tr("Open downloaded file", "打开下载文件", "保存ファイルを開く"),
            file_exists,
        )

    def _set_action_item(
        self,
        table: TableWidget,
        row: int,
        column: int,
        entity_id: int | str,
        action: str,
        text: str,
        tooltip: str,
        enabled: bool,
        *,
        action_url: str = "",
    ):
        cell = QTableWidgetItem(text if enabled else "—")
        cell.setData(Qt.ItemDataRole.UserRole, entity_id)
        cell.setData(Qt.ItemDataRole.UserRole + 1, action if enabled else "")
        cell.setData(Qt.ItemDataRole.UserRole + 2, action_url if enabled else "")
        cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        cell.setToolTip(tooltip)
        cell.setForeground(QColor("#0078d4" if enabled else "#999999"))
        table.setItem(row, column, cell)

    def _selected_source_id(self) -> int | None:
        row = self._source_table.currentRow()
        if row < 0 or row >= len(self._sources):
            return None
        source_id = int(self._sources[row].get("id", 0) or 0)
        return source_id or None

    def _selected_video_ids(self) -> list[str]:
        ids: list[str] = []
        if hasattr(self, "_item_view_combo") and self._item_view_combo.currentIndex() == 1:
            for item in self._thumbnail_list.selectedItems():
                video_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if video_id and video_id not in ids:
                    ids.append(video_id)
            return ids
        for index in self._item_table.selectionModel().selectedRows():
            item = self._item_table.item(index.row(), self._ITEM_ID)
            if not item:
                continue
            video_id = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "").strip()
            if video_id and video_id not in ids:
                ids.append(video_id)
        return ids

    def _on_source_cell_clicked(self, row: int, column: int):
        if column != self._SRC_OPEN or row < 0 or row >= len(self._sources):
            return
        item = self._source_table.item(row, column)
        url = str(item.data(Qt.ItemDataRole.UserRole + 2) or "") if item else ""
        if url:
            _open_url(url)
        else:
            self._open_source_page(self._sources[row])

    def _on_thumbnail_item_activated(self, item: QListWidgetItem):
        video_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if video_id:
            self._activate_video_id(video_id)

    def _activate_video_id(self, video_id: str):
        item_data = next(
            (item for item in self._all_items if str(item.get("video_id", "") or "") == video_id),
            None,
        )
        if not item_data:
            _open_url(_video_url(video_id))
            return
        if bool(item_data.get("download_file_exists")):
            self._open_history_item(
                video_id,
                open_file=app_config.completed_task_click_action == "player",
            )
            return
        _open_url(str(item_data.get("source_url", "") or _video_url(video_id)))

    def _on_item_cell_clicked(self, row: int, column: int):
        if column not in (self._ITEM_URL, self._ITEM_FOLDER, self._ITEM_FILE):
            return
        item = self._item_table.item(row, column)
        if not item:
            return
        video_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        action = str(item.data(Qt.ItemDataRole.UserRole + 1) or "").strip()
        if not video_id or not action:
            return
        if action == "open_url":
            _open_url(str(item.data(Qt.ItemDataRole.UserRole + 2) or "") or _video_url(video_id))
        elif action == "open_folder":
            self._open_history_item(video_id, open_file=False)
        elif action == "open_file":
            self._open_history_item(video_id, open_file=True)

    def _open_item_from_cell(self, item: QTableWidgetItem, *, open_file: bool | None):
        video_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if video_id:
            if item.column() == self._ITEM_SOURCE_URL:
                return
            if item.column() == self._ITEM_URL:
                _open_url(str(item.data(Qt.ItemDataRole.UserRole + 2) or "") or _video_url(video_id))
            elif item.column() == self._ITEM_FOLDER:
                self._open_history_item(video_id, open_file=False)
            elif item.column() == self._ITEM_FILE:
                self._open_history_item(video_id, open_file=True)
            elif open_file is None:
                self._activate_video_id(video_id)
            else:
                self._open_history_item(video_id, open_file=open_file)

    def _open_source_page(self, source: dict[str, Any]):
        url = _source_url(source)
        if url:
            _open_url(url)

    def _open_history_item(self, video_id: str, *, open_file: bool):
        ok, message = download_manager.open_history_output(video_id, open_file=open_file)
        if ok:
            return
        InfoBar.warning(
            title=tr("Cannot open", "无法打开", "開けません"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _add_following_feed(self):
        download_manager.add_following_subscription()
        self._load_sources()

    def _add_source(self):
        text, ok = QInputDialog.getText(
            self,
            tr("Add Subscription", "添加订阅", "購読を追加"),
            tr(
                "Author username / author URL / playlist URL:",
                "作者用户名 / 作者主页链接 / 播放列表链接：",
                "作者ユーザー名 / 作者URL / プレイリストURL:",
            ),
        )
        if not ok or not text.strip():
            return
        kind, key = _detect_source_input(text.strip())
        if not key:
            self._show_error(tr("Invalid subscription input", "订阅输入无效", "購読入力が不正です"))
            return
        if kind == "playlist":
            source_id = download_manager.add_playlist_subscription(key)
        else:
            source_id = download_manager.add_author_subscription(key)
        self._load_sources()
        if source_id:
            self._select_source_id(source_id)
            self._load_items(source_id)
            self._start_refresh(source_id)
            InfoBar.success(
                title=tr("Subscription Added", "订阅已添加", "購読を追加しました"),
                content=tr("Refreshing this source now", "正在立即刷新该订阅源", "この購読元を更新しています"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _on_subscription_source_added(self, source_id: int):
        source_id = int(source_id or 0)
        if not source_id:
            return
        if hasattr(self, "_source_search_edit") and self._source_search_edit.text():
            self._source_search_edit.blockSignals(True)
            self._source_search_edit.clear()
            self._source_search_edit.blockSignals(False)
        self._all_sources = download_manager.get_subscription_sources()
        self._apply_source_filters(preferred_source_id=source_id)
        self._start_refresh(source_id)

    def _import_followed_authors(self):
        if self._import_worker and self._import_worker.isRunning():
            return
        self._import_worker = SubscriptionImportAuthorsWorker()
        self._import_worker.finished.connect(self._on_import_followed_finished)
        self._import_worker.start()

    def _on_import_followed_finished(self, result: dict):
        self._load_sources()
        imported = int(result.get("imported", 0) or 0)
        method = str(result.get("method", "") or "")
        error = str(result.get("error", "") or "")
        if error:
            InfoBar.warning(
                title=tr("Import Finished", "导入完成", "取込完了"),
                content=error,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return
        content = tr(
            f"Synced {imported} authors",
            f"已同步 {imported} 个作者",
            f"{imported} 人の作者を同期しました",
        )
        if method == "feed":
            content += tr(
                " (derived from subscribed videos)",
                "（从订阅视频流反推）",
                "（購読フィードから抽出）",
            )
        InfoBar.success(
            title=tr("Followed Authors Imported", "关注作者已导入", "フォロー作者を取込"),
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _refresh_selected(self):
        source_id = self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        self._start_refresh(source_id)

    def _refresh_all(self):
        self._start_refresh(None)

    def _start_refresh(self, source_id: int | None):
        if self._worker and self._worker.isRunning():
            return
        self._worker = SubscriptionRefreshWorker(source_id)
        self._worker.finished.connect(self._on_refresh_finished)
        self._worker.start()

    def _on_refresh_finished(self, summary: dict):
        self._refresh_sources_keep_current_items()
        errors = summary.get("errors") or []
        if errors:
            first = errors[0]
            InfoBar.warning(
                title=tr("Refresh Finished With Errors", "刷新完成但有错误", "一部更新失敗"),
                content=str(first.get("error", ""))[:180],
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        else:
            InfoBar.success(
                title=tr("Refresh Finished", "刷新完成", "更新完了"),
                content=tr(
                    f"New {summary.get('new', 0)}, downloaded locally {summary.get('downloaded', 0)}, unavailable {summary.get('unavailable', 0)}, total {summary.get('total', 0)}",
                    f"新增 {summary.get('new', 0)}，本地已下载 {summary.get('downloaded', 0)}，不可下载 {summary.get('unavailable', 0)}，累计 {summary.get('total', 0)}",
                    f"新規 {summary.get('new', 0)}、保存済み {summary.get('downloaded', 0)}、保存不可 {summary.get('unavailable', 0)}、合計 {summary.get('total', 0)}",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
        signal_bus.log_message.emit(
            tr(
                f"[Subscriptions] refresh done: new={summary.get('new', 0)}, downloaded={summary.get('downloaded', 0)}, unavailable={summary.get('unavailable', 0)}, total={summary.get('total', 0)}",
                f"[订阅] 刷新完成: 新增={summary.get('new', 0)}, 已下载={summary.get('downloaded', 0)}, 不可下载={summary.get('unavailable', 0)}, 总计={summary.get('total', 0)}",
                f"[購読] 更新完了: 新規={summary.get('new', 0)}, 保存済み={summary.get('downloaded', 0)}, 保存不可={summary.get('unavailable', 0)}, 合計={summary.get('total', 0)}",
            )
        )

    def _delete_selected_source(self):
        source_id = self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription first", "请先选择一个订阅", "購読を選択してください"))
            return
        source = next(
            (source for source in self._sources if int(source.get("id", 0) or 0) == source_id),
            None,
        )
        if not source:
            return
        display_name = (
            str(source.get("title", "") or "").strip()
            or str(source.get("source_key", "") or "").strip()
            or f"#{source_id}"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("Delete Subscription", "删除订阅", "購読を削除"))
        box.setText(
            tr(
                f'Delete subscription "{display_name}" and its cached video list?',
                f'确定删除订阅“{display_name}”及其缓存视频列表吗？',
                f'購読「{display_name}」と保存済み動画一覧を削除しますか？',
            )
        )
        box.setInformativeText(
            tr(
                "Downloaded files and history records will not be deleted.",
                "不会删除已下载文件和历史记录。",
                "保存済みファイルと履歴は削除されません。",
            )
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        download_manager.remove_subscription_source(source_id)
        self._current_source_id = None
        self._load_sources()
        InfoBar.success(
            title=tr("Subscription Deleted", "订阅已删除", "購読を削除しました"),
            content=display_name,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _toggle_selected_source(self):
        source_id = self._selected_source_id()
        if not source_id:
            return
        source = next((s for s in self._sources if int(s.get("id", 0) or 0) == source_id), None)
        if not source:
            return
        enabled = bool(int(source.get("enabled", 1) or 0))
        download_manager.set_subscription_enabled(source_id, not enabled)
        self._refresh_sources_keep_current_items()

    def _mark_selected_seen(self):
        ids = self._selected_video_ids()
        if not ids:
            ids = [str(item.get("video_id", "") or "") for item in self._visible_items if item.get("is_new")]
        download_manager.mark_subscription_items_seen(ids)
        self._refresh_sources_keep_current_items()

    def _mark_selected_downloaded_moved(self):
        ids = self._selected_video_ids()
        if not ids:
            self._show_error(tr("Select one or more videos first", "请先选中右侧列表里的一个或多个视频", "先に右側リストで動画を選択してください"))
            return
        marked = download_manager.mark_subscription_items_downloaded(ids)
        self._refresh_sources_keep_current_items()
        InfoBar.success(
            title=tr("Marked", "已标记", "マーク完了"),
            content=tr(
                f"Marked {marked} videos as downloaded/moved",
                f"已将 {marked} 个视频标记为已下载（移走）",
                f"{marked} 件を保存済み（移動済み）にしました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _restore_selected_downloaded_moved(self):
        ids = self._selected_video_ids()
        if not ids:
            self._show_error(tr("Select one or more moved videos first", "请先选中一个或多个已移走的视频", "先に移動済み動画を選択してください"))
            return
        restored = download_manager.restore_subscription_items_downloaded(ids)
        self._refresh_sources_keep_current_items()
        InfoBar.success(
            title=tr("Restored", "已还原", "解除完了"),
            content=tr(
                f"Restored {restored} moved records",
                f"已还原 {restored} 个已移走记录",
                f"移動済み記録を {restored} 件解除しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _download_selected(self):
        ids = self._selected_video_ids()
        if not ids:
            self._show_error(tr("Select one or more videos first", "请先选中右侧列表里的一个或多个视频", "先に右側リストで動画を選択してください"))
            return
        self._enqueue_ids(ids)

    def _update_selection_actions(self):
        count = len(self._selected_video_ids()) if hasattr(self, "_item_table") else 0
        has_selection = count > 0
        if hasattr(self, "_download_selected_btn"):
            self._download_selected_btn.setEnabled(has_selection)
            self._download_selected_btn.setText(
                tr(f"Download Selected ({count})", f"下载选中（{count}）", f"選択を保存（{count}）")
                if has_selection else tr("Download Selected", "下载选中", "選択を保存")
            )
        if hasattr(self, "_mark_downloaded_btn"):
            self._mark_downloaded_btn.setEnabled(has_selection)
            self._mark_downloaded_btn.setText(
                tr(f"Mark Downloaded ({count})", f"标为已下载（{count}）", f"保存済み（{count}）")
                if has_selection else tr("Mark Downloaded (Moved)", "标为已下载（移走）", "保存済み（移動済み）")
            )
        if hasattr(self, "_restore_moved_btn"):
            self._restore_moved_btn.setEnabled(has_selection)
            self._restore_moved_btn.setText(
                tr(f"Restore Moved ({count})", f"还原已移走（{count}）", f"移動済み解除（{count}）")
                if has_selection else tr("Restore Moved", "还原已移走", "移動済み解除")
            )

    def _open_filter_dialog(self):
        dlg = FilterDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            signal_bus.download_options_changed.emit()
            InfoBar.success(
                title=tr("Filter rules saved", "筛选条件已保存", "フィルター条件を保存しました"),
                content=tr(
                    "Subscription downloads will use the same filter rules as the download workbench.",
                    "订阅下载会使用和下载工作台相同的筛选规则。",
                    "購読保存にもダウンロード画面と同じフィルター条件を使います。",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _make_download_option_button(self, text: str, tooltip: str) -> PrimaryPushButton:
        button = PrimaryPushButton(text, self)
        button._base_text = text
        button.setCheckable(True)
        button.setMinimumWidth(126)
        button.setFixedHeight(_CONTROL_HEIGHT)
        button.setToolTip(tooltip)
        return button

    def _sync_download_option_buttons(self):
        if app_config.mark_submitted_as_downloaded and app_config.download_video_file:
            app_config.download_video_file = False
        if not app_config.mark_submitted_as_downloaded and not app_config.download_video_file:
            app_config.download_video_file = True
        if not hasattr(self, "_option_download_video_btn"):
            return
        self._syncing_download_options = True
        try:
            self._set_download_option_button_state(self._option_download_video_btn, app_config.download_video_file)
            self._set_download_option_button_state(self._option_mark_downloaded_btn, app_config.mark_submitted_as_downloaded)
            self._set_download_option_button_state(self._option_download_thumb_btn, app_config.download_thumbnail)
            self._set_download_option_button_state(self._option_collect_nfo_btn, app_config.collect_nfo_info)
        finally:
            self._syncing_download_options = False

    def _set_download_option_button_state(self, button: PrimaryPushButton, checked: bool):
        button.setChecked(checked)
        base_text = str(getattr(button, "_base_text", button.text()) or "")
        button.setText(f"{base_text}  {'On' if checked else 'Off'}")
        button.setStyleSheet(_OPTION_ON_STYLE if checked else _OPTION_OFF_STYLE)

    def _on_download_video_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.download_video_file = bool(checked)
        app_config.mark_submitted_as_downloaded = not bool(checked)
        signal_bus.download_options_changed.emit()

    def _on_mark_downloaded_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.mark_submitted_as_downloaded = bool(checked)
        app_config.download_video_file = not bool(checked)
        signal_bus.download_options_changed.emit()

    def _on_download_thumb_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.download_thumbnail = bool(checked)
        signal_bus.download_options_changed.emit()

    def _on_collect_nfo_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.collect_nfo_info = bool(checked)
        signal_bus.download_options_changed.emit()

    def _download_new(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if item.get("is_new") and not item.get("downloaded")
        ]
        self._enqueue_ids(ids)

    def _download_visible(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if not item.get("downloaded")
        ]
        self._enqueue_ids(ids)

    def _enqueue_ids(self, ids: list[str]):
        ids = [video_id for video_id in ids if video_id]
        if not ids:
            self._show_error(tr("No downloadable videos in current selection", "当前选择没有可下载视频", "保存可能な動画がありません"))
            return
        if self._enqueue_worker and self._enqueue_worker.isRunning():
            self._show_error(tr("Queue operation is still running", "队列操作仍在进行中", "キュー操作が実行中です"))
            return
        self._enqueue_worker = SubscriptionEnqueueWorker(ids)
        self._enqueue_worker.finished.connect(self._on_enqueue_finished)
        self._enqueue_worker.start()

    def _on_enqueue_finished(self, result: dict):
        queued = int(result.get("queued", 0) or 0)
        marked = int(result.get("marked", 0) or 0)
        thumbnail = int(result.get("thumbnail", 0) or 0)
        nfo = int(result.get("nfo", 0) or 0)
        failed = int(result.get("failed", 0) or 0)
        skipped_unavailable = int(result.get("skipped_unavailable", 0) or 0)
        mode = str(result.get("mode", "") or "")
        self._enqueue_worker = None
        self._refresh_sources_keep_current_items()
        if mode == "metadata":
            title = tr("Processed", "已处理", "処理完了")
            content = tr(
                f"Marked {marked}, thumbnails {thumbnail}, NFO {nfo}, failed {failed}, skipped unavailable {skipped_unavailable}",
                f"已标记 {marked}，封面 {thumbnail}，NFO {nfo}，失败 {failed}，跳过不可下载 {skipped_unavailable}",
                f"記録 {marked}、サムネイル {thumbnail}、NFO {nfo}、失敗 {failed}、保存不可スキップ {skipped_unavailable}",
            )
        elif mode == "empty" and skipped_unavailable:
            title = tr("Skipped", "已跳过", "スキップ")
            content = tr(
                f"Skipped {skipped_unavailable} unavailable videos",
                f"已跳过 {skipped_unavailable} 个不可下载视频",
                f"保存不可の動画を {skipped_unavailable} 件スキップしました",
            )
        else:
            title = tr("Added To Queue", "已加入队列", "キューに追加")
            content = tr(
                f"Queued {queued} videos, skipped unavailable {skipped_unavailable}",
                f"已加入 {queued} 个视频，跳过不可下载 {skipped_unavailable}",
                f"{queued} 件を追加、保存不可スキップ {skipped_unavailable}",
            )
        bar = InfoBar.warning if (mode == "empty" and skipped_unavailable) else InfoBar.success
        bar(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _show_error(self, msg: str):
        InfoBar.error(
            title=tr("Operation Failed", "操作失败", "操作失敗"),
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self,
        )

    def _on_task_status_changed(self, _task_id: str, _status_str: str):
        self._schedule_items_refresh_after_task_change()

    def _on_task_changed(self, *_args):
        self._schedule_items_refresh_after_task_change()

    def _on_tasks_changed(self, *_args):
        self._schedule_items_refresh_after_task_change()

    def _schedule_items_refresh_after_task_change(self):
        if self._items_refresh_pending:
            return
        self._items_refresh_pending = True
        QTimer.singleShot(150, self._refresh_visible_items_after_task_change)

    def _refresh_visible_items_after_task_change(self):
        self._items_refresh_pending = False
        self._load_items(self._current_source_id)

    def _sync_items_with_current_tasks(self):
        task_status_by_video_id = {
            task.video_id.lower(): task.status.value
            for task in download_manager.get_tasks()
            if task.video_id
        }
        for item in self._all_items:
            video_id = str(item.get("video_id", "") or "").lower()
            task_status = task_status_by_video_id.get(video_id, "") if video_id else ""
            item["task_status"] = task_status
            item["queued"] = bool(task_status)


def _split_title_keywords(value: str) -> list[str]:
    terms: list[str] = []
    for part in str(value or "").replace(";", ",").replace("\n", ",").split(","):
        term = part.strip().casefold()
        if term and term not in terms:
            terms.append(term)
    return terms


def _title_matches_keywords(title: str, include_terms: list[str], exclude_terms: list[str]) -> bool:
    haystack = str(title or "").casefold()
    if include_terms and not any(term in haystack for term in include_terms):
        return False
    return not any(term in haystack for term in exclude_terms)


def _ellipsize(value: str, limit: int) -> str:
    value = str(value or "")
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _source_type_label(source_type: str) -> str:
    if source_type == "feed":
        return tr("Feed", "账号流", "フィード")
    if source_type == "author":
        return tr("Author", "作者", "作者")
    if source_type == "playlist":
        return tr("Playlist", "播放列表", "リスト")
    return source_type


def _source_sort_key(source: dict[str, Any]) -> tuple[str, str, str]:
    title = str(source.get("title", "") or "").casefold()
    source_key = str(source.get("source_key", "") or "").casefold()
    source_type = str(source.get("source_type", "") or "").casefold()
    return (title or source_key, source_key, source_type)


def _source_search_text(source: dict[str, Any]) -> str:
    values = [
        source.get("title", ""),
        source.get("source_key", ""),
        source.get("source_type", ""),
        _source_type_label(str(source.get("source_type", "") or "")),
        _source_url(source),
    ]
    return " ".join(str(value or "") for value in values).casefold()


def _item_state_text(
    *,
    downloaded: bool,
    queued: bool,
    file_exists: bool,
    task_status: str = "",
    download_state: str = "",
    download_reason: str = "",
) -> str:
    if downloaded and file_exists:
        return tr("Downloaded", "已下载", "保存済み")
    if downloaded:
        return tr("Moved", "已移走", "移動済み")
    if download_state or download_reason:
        return tr("Not Downloadable", "不可下载", "保存不可")
    status = _task_status_from_value(task_status)
    if status:
        return STATUS_LABELS.get(status, status.value)
    if queued:
        return tr("Queued", "已入队", "キュー内")
    return tr("Ready", "可下载", "保存可能")


def _state_color(
    *,
    downloaded: bool,
    queued: bool,
    file_exists: bool,
    task_status: str = "",
    download_state: str = "",
) -> QColor:
    if downloaded and file_exists:
        return QColor("#107c10")
    if downloaded:
        return QColor("#c17d00")
    if download_state:
        return QColor("#c42b1c")
    status = _task_status_from_value(task_status)
    if status in (TaskStatus.DOWNLOADING, TaskStatus.COMPLETED):
        return QColor("#107c10")
    if status in (TaskStatus.RESOLVING, TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD):
        return QColor("#0078d4")
    if status in (TaskStatus.CANCELLING, TaskStatus.SKIPPED):
        return QColor("#c17d00")
    if status == TaskStatus.FAILED:
        return QColor("#c42b1c")
    if status == TaskStatus.CANCELLED:
        return QColor("#666666")
    if queued:
        return QColor("#0078d4")
    return QColor("#555555")


def _item_not_downloadable(item: dict[str, Any]) -> bool:
    return bool(str(item.get("download_state", "") or ""))


def _task_status_from_value(value: str) -> TaskStatus | None:
    try:
        return TaskStatus(str(value or ""))
    except ValueError:
        return None


def _item_date_key(item: dict[str, Any]) -> str:
    return str(
        item.get("published_at", "")
        or item.get("discovered_at", "")
        or item.get("updated_at", "")
        or ""
    )


def _extract_author_key(text: str) -> str:
    parsed = _parse_iwara_path(text)
    if parsed:
        parts = parsed
        for key in ("user", "profile"):
            if key in parts:
                idx = parts.index(key)
                if idx + 1 < len(parts):
                    return parts[idx + 1]
    return text.strip().strip("/")


def _extract_playlist_key(text: str) -> str:
    parsed = _parse_iwara_path(text)
    if parsed and "playlist" in parsed:
        idx = parsed.index("playlist")
        if idx + 1 < len(parsed):
            return parsed[idx + 1]
    return text.strip().strip("/")


def _detect_source_input(text: str) -> tuple[str, str]:
    lowered = text.strip().lower()
    for prefix in ("playlist:", "list:"):
        if lowered.startswith(prefix):
            return "playlist", text.split(":", 1)[1].strip().strip("/")
    parsed = _parse_iwara_path(text)
    if parsed and "playlist" in parsed:
        return "playlist", _extract_playlist_key(text)
    return "author", _extract_author_key(text)


def _source_url(source: dict[str, Any]) -> str:
    source_type = str(source.get("source_type", "") or "")
    key = str(source.get("source_key", "") or "").strip()
    if source_type == "author" and key:
        return f"https://www.iwara.tv/profile/{key}"
    if source_type == "playlist" and key:
        return f"https://www.iwara.tv/playlist/{key}"
    if source_type == "feed":
        return "https://www.iwara.tv/subscriptions"
    return ""


def _video_url(video_id: str) -> str:
    video_id = str(video_id or "").strip()
    return f"https://www.iwara.tv/video/{video_id}" if video_id else ""


def _open_url(url: str):
    if url:
        webbrowser.open(url)


def _parse_iwara_path(text: str) -> list[str] | None:
    if "iwara.tv" not in text:
        return None
    normalized = text if "://" in text else f"https://{text.lstrip('/')}"
    try:
        parsed = urlparse(normalized)
    except Exception:
        return None
    return [part for part in parsed.path.split("/") if part]


def _date_only(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10] if len(text) >= 10 else text
