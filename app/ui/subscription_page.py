"""Subscription Interface — sources, update checks, and batch enqueue."""
from __future__ import annotations

import webbrowser
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QTimer, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    TableWidget,
    TitleLabel,
)

from ..core.manager import download_manager
from ..i18n import tr
from ..signal_bus import signal_bus
from .ui_state import (
    connect_splitter_saver,
    connect_table_width_saver,
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
        queued = download_manager.enqueue_subscription_items(self._video_ids)
        self.finished.emit({"queued": queued})


class SubscriptionInterface(QWidget):
    """Page for tracking subscription updates and queueing downloads."""

    _SRC_STATE = 0
    _SRC_TYPE = 1
    _SRC_TITLE = 2
    _SRC_NEW = 3
    _SRC_ITEMS = 4
    _SRC_CHECKED = 5
    _SRC_KEY = 6
    _SRC_OPEN = 7

    _ITEM_STATE = 0
    _ITEM_NEW = 1
    _ITEM_TITLE = 2
    _ITEM_AUTHOR = 3
    _ITEM_PUBLISHED = 4
    _ITEM_ID = 5
    _ITEM_URL = 6
    _ITEM_FOLDER = 7
    _ITEM_FILE = 8

    _RENDER_BATCH_SIZE = 80

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SubscriptionInterface")
        self._worker: SubscriptionRefreshWorker | None = None
        self._import_worker: SubscriptionImportAuthorsWorker | None = None
        self._enqueue_worker: SubscriptionEnqueueWorker | None = None
        self._sources: list[dict[str, Any]] = []
        self._visible_items: list[dict[str, Any]] = []
        self._source_render_index = 0
        self._item_render_index = 0
        self._items_refresh_pending = False
        self._source_render_timer = QTimer(self)
        self._source_render_timer.setInterval(0)
        self._source_render_timer.timeout.connect(self._render_source_batch)
        self._item_render_timer = QTimer(self)
        self._item_render_timer.setInterval(0)
        self._item_render_timer.timeout.connect(self._render_item_batch)
        self._build_ui()
        self._load_sources()
        signal_bus.task_status_changed.connect(self._on_task_status_changed)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 16)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("Subscriptions", "订阅页", "購読"), self))
        title_row.addStretch()
        refresh_all_btn = PrimaryPushButton(tr("Refresh All", "刷新全部", "全件更新"), self, FluentIcon.SYNC)
        refresh_all_btn.clicked.connect(self._refresh_all)
        title_row.addWidget(refresh_all_btn)
        root.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

        left_panel = QWidget(self)
        left_panel.setMinimumWidth(400)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        splitter.addWidget(left_panel)

        right_panel = QWidget(self)
        right_panel.setMinimumWidth(760)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        restore_splitter_sizes(splitter, "subscription_splitter_sizes", [800, 1200])
        connect_splitter_saver(splitter, "subscription_splitter_sizes")

        source_actions_1 = QHBoxLayout()
        import_authors_btn = PrimaryPushButton(tr("Import Followed", "导入关注作者", "フォローを取込"), self, FluentIcon.PEOPLE)
        import_authors_btn.clicked.connect(self._import_followed_authors)
        source_actions_1.addWidget(import_authors_btn)

        add_feed_btn = PrimaryPushButton(tr("Account Feed", "账号订阅流", "購読フィード"), self, FluentIcon.HISTORY)
        add_feed_btn.clicked.connect(self._add_following_feed)
        source_actions_1.addWidget(add_feed_btn)
        left_layout.addLayout(source_actions_1)

        source_actions_2 = QHBoxLayout()
        add_source_btn = PrimaryPushButton(tr("Add Author / Playlist", "添加作者/播放列表", "作者/リストを追加"), self, FluentIcon.PEOPLE)
        add_source_btn.clicked.connect(self._add_source)
        source_actions_2.addWidget(add_source_btn)
        left_layout.addLayout(source_actions_2)

        source_actions_3 = QHBoxLayout()
        refresh_selected_btn = PrimaryPushButton(tr("Refresh Selected", "刷新选中", "選択を更新"), self, FluentIcon.SYNC)
        refresh_selected_btn.clicked.connect(self._refresh_selected)
        source_actions_3.addWidget(refresh_selected_btn)

        toggle_btn = PrimaryPushButton(tr("Enable / Disable", "启用/停用", "有効/無効"), self, FluentIcon.CANCEL)
        toggle_btn.clicked.connect(self._toggle_selected_source)
        source_actions_3.addWidget(toggle_btn)
        left_layout.addLayout(source_actions_3)

        storage = download_manager.get_subscription_storage_info()
        storage_label = BodyLabel(tr("Saved locally", "本地保存", "ローカル保存") + ": subscriptions.db", self)
        storage_label.setToolTip(
            tr(
                f"DB: {storage.get('db_path', '')}\nBackup: {storage.get('backup_path', '')}",
                f"数据库: {storage.get('db_path', '')}\n备份: {storage.get('backup_path', '')}",
                f"DB: {storage.get('db_path', '')}\nバックアップ: {storage.get('backup_path', '')}",
            )
        )
        left_layout.addWidget(storage_label)

        self._source_table = TableWidget(self)
        self._source_table.setColumnCount(8)
        self._source_table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("Type", "类型", "種類"),
                tr("Title", "名称", "名前"),
                tr("New", "新增", "新規"),
                tr("Items", "项目", "項目"),
                tr("Last Check", "上次刷新", "最終確認"),
                tr("Key", "标识", "キー"),
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
        self._source_table.verticalHeader().setDefaultSectionSize(38)
        self._source_table.currentCellChanged.connect(lambda *_args: self._load_items(self._selected_source_id()))
        self._source_table.cellClicked.connect(self._on_source_cell_clicked)
        source_header = self._source_table.horizontalHeader()
        source_header.setHighlightSections(False)
        source_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        source_widths = {
            self._SRC_STATE: 68,
            self._SRC_TYPE: 70,
            self._SRC_TITLE: 230,
            self._SRC_NEW: 52,
            self._SRC_ITEMS: 58,
            self._SRC_CHECKED: 150,
            self._SRC_KEY: 260,
            self._SRC_OPEN: 68,
        }
        restore_table_widths(self._source_table, "subscription_source_widths", source_widths)
        connect_table_width_saver(self._source_table, "subscription_source_widths")
        self._source_table.setColumnHidden(self._SRC_CHECKED, True)
        self._source_table.setColumnHidden(self._SRC_KEY, True)
        left_layout.addWidget(self._source_table, stretch=1)

        item_actions = QHBoxLayout()
        self._summary_label = BodyLabel("", self)
        item_actions.addWidget(self._summary_label)
        item_actions.addStretch()

        all_sources_btn = PrimaryPushButton(tr("Show All", "显示全部", "全て表示"), self, FluentIcon.HISTORY)
        all_sources_btn.clicked.connect(lambda: self._load_items(None))
        item_actions.addWidget(all_sources_btn)

        mark_seen_btn = PrimaryPushButton(tr("Mark Seen", "标为已读", "既読にする"), self, FluentIcon.CHECKBOX)
        mark_seen_btn.clicked.connect(self._mark_selected_seen)
        item_actions.addWidget(mark_seen_btn)

        download_selected_btn = PrimaryPushButton(tr("Download Selected", "下载选中", "選択を保存"), self, FluentIcon.DOWNLOAD)
        download_selected_btn.clicked.connect(self._download_selected)
        item_actions.addWidget(download_selected_btn)

        download_new_btn = PrimaryPushButton(tr("Download New", "下载新增", "新規を保存"), self, FluentIcon.DOWNLOAD)
        download_new_btn.clicked.connect(self._download_new)
        item_actions.addWidget(download_new_btn)

        download_all_btn = PrimaryPushButton(tr("Download Visible", "下载当前列表", "表示分を保存"), self, FluentIcon.DOWNLOAD)
        download_all_btn.clicked.connect(self._download_visible)
        item_actions.addWidget(download_all_btn)
        right_layout.addLayout(item_actions)

        self._item_table = TableWidget(self)
        self._item_table.setColumnCount(9)
        self._item_table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("New", "新增", "新規"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Published", "发布时间", "公開日"),
                "ID",
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
        self._item_table.itemDoubleClicked.connect(lambda item: self._open_item_from_cell(item, open_file=True))
        self._item_table.cellClicked.connect(self._on_item_cell_clicked)
        item_header = self._item_table.horizontalHeader()
        item_header.setHighlightSections(False)
        item_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        item_widths = {
            self._ITEM_STATE: 90,
            self._ITEM_NEW: 58,
            self._ITEM_TITLE: 660,
            self._ITEM_AUTHOR: 140,
            self._ITEM_PUBLISHED: 130,
            self._ITEM_ID: 130,
            self._ITEM_URL: 68,
            self._ITEM_FOLDER: 68,
            self._ITEM_FILE: 68,
        }
        restore_table_widths(self._item_table, "subscription_item_widths", item_widths)
        connect_table_width_saver(self._item_table, "subscription_item_widths")
        right_layout.addWidget(self._item_table, stretch=1)

    def _load_sources(self):
        previous_source_id = self._selected_source_id()
        self._sources = download_manager.get_subscription_sources()
        self._render_sources()
        selected_row = -1
        if previous_source_id:
            for row, source in enumerate(self._sources):
                if int(source.get("id", 0) or 0) == previous_source_id:
                    selected_row = row
                    break
        if selected_row < 0 and self._sources:
            selected_row = 0
        if selected_row >= 0:
            self._source_table.blockSignals(True)
            self._source_table.setCurrentCell(selected_row, self._SRC_STATE)
            self._source_table.blockSignals(False)
            self._load_items(self._selected_source_id())
            return
        self._load_items(None)

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
        values = [
            tr("Enabled", "启用", "有効") if enabled else tr("Disabled", "停用", "無効"),
            _source_type_label(str(source.get("source_type", "") or "")),
            str(source.get("title", "") or ""),
            str(source.get("new_count", 0) or 0),
            str(source.get("item_count", 0) or 0),
            str(source.get("last_checked_at", "") or ""),
            str(source.get("source_key", "") or ""),
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            item.setToolTip(value)
            if col == self._SRC_STATE:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor("#107c10" if enabled else "#777777"))
            if col in (self._SRC_NEW, self._SRC_ITEMS):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._source_table.setItem(row, col, item)
        self._set_action_item(
            self._source_table,
            row,
            self._SRC_OPEN,
            source_id,
            "open_source",
            tr("Open", "打开", "開く"),
            tr("Open source page", "打开订阅源页面", "購読元ページを開く"),
            bool(_source_url(source)),
        )

    def _load_items(self, source_id: int | None):
        self._visible_items = download_manager.get_subscription_items(source_id)
        self._render_items()

    def _render_items(self):
        self._item_render_timer.stop()
        self._item_render_index = 0
        self._item_table.setUpdatesEnabled(False)
        self._item_table.clearContents()
        self._item_table.setRowCount(len(self._visible_items))
        new_count = downloaded_count = queued_count = 0
        for item_data in self._visible_items:
            downloaded = bool(item_data.get("downloaded"))
            queued = bool(item_data.get("queued"))
            is_new = bool(int(item_data.get("is_new", 0) or 0))
            if is_new:
                new_count += 1
            if downloaded:
                downloaded_count += 1
            if queued:
                queued_count += 1
        self._item_table.setUpdatesEnabled(True)
        self._summary_label.setText(
            tr(
                f"Visible: {len(self._visible_items)} | new: {new_count} | downloaded: {downloaded_count} | queued: {queued_count}",
                f"当前显示: {len(self._visible_items)} | 新增: {new_count} | 本地已下载: {downloaded_count} | 已在队列: {queued_count}",
                f"表示: {len(self._visible_items)} | 新規: {new_count} | 保存済み: {downloaded_count} | キュー内: {queued_count}",
            )
        )
        self._item_render_timer.start()

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
        is_new = bool(int(item_data.get("is_new", 0) or 0))
        file_exists = bool(item_data.get("download_file_exists"))
        source_title = str(item_data.get("source_title", "") or "")
        state = _item_state_text(downloaded=downloaded, queued=queued)
        values = [
            state,
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
            cell.setToolTip(tooltip)
            if col in (self._ITEM_STATE, self._ITEM_NEW):
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == self._ITEM_STATE:
                cell.setForeground(_state_color(downloaded=downloaded, queued=queued))
            elif col == self._ITEM_NEW and is_new:
                cell.setForeground(QColor("#c17d00"))
            self._item_table.setItem(row, col, cell)

        self._set_action_item(
            self._item_table,
            row,
            self._ITEM_URL,
            video_id,
            "open_url",
            tr("Page", "页面", "ページ"),
            tr("Open video page", "打开视频页", "動画ページを開く"),
            bool(video_id),
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
    ):
        cell = QTableWidgetItem(text if enabled else "—")
        cell.setData(Qt.ItemDataRole.UserRole, entity_id)
        cell.setData(Qt.ItemDataRole.UserRole + 1, action if enabled else "")
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
        self._open_source_page(self._sources[row])

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
            _open_url(_video_url(video_id))
        elif action == "open_folder":
            self._open_history_item(video_id, open_file=False)
        elif action == "open_file":
            self._open_history_item(video_id, open_file=True)

    def _open_item_from_cell(self, item: QTableWidgetItem, *, open_file: bool):
        video_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if video_id:
            if item.column() == self._ITEM_URL:
                _open_url(_video_url(video_id))
            elif item.column() == self._ITEM_FOLDER:
                self._open_history_item(video_id, open_file=False)
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
            download_manager.add_playlist_subscription(key)
        else:
            download_manager.add_author_subscription(key)
        self._load_sources()

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
        self._load_sources()
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
                    f"New {summary.get('new', 0)}, downloaded locally {summary.get('downloaded', 0)}, total {summary.get('total', 0)}",
                    f"新增 {summary.get('new', 0)}，本地已下载 {summary.get('downloaded', 0)}，累计 {summary.get('total', 0)}",
                    f"新規 {summary.get('new', 0)}、保存済み {summary.get('downloaded', 0)}、合計 {summary.get('total', 0)}",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
        signal_bus.log_message.emit(
            tr(
                f"[Subscriptions] refresh done: new={summary.get('new', 0)}, downloaded={summary.get('downloaded', 0)}, total={summary.get('total', 0)}",
                f"[订阅] 刷新完成: 新增={summary.get('new', 0)}, 已下载={summary.get('downloaded', 0)}, 总计={summary.get('total', 0)}",
                f"[購読] 更新完了: 新規={summary.get('new', 0)}, 保存済み={summary.get('downloaded', 0)}, 合計={summary.get('total', 0)}",
            )
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
        self._load_sources()

    def _mark_selected_seen(self):
        ids = self._selected_video_ids()
        if not ids:
            ids = [str(item.get("video_id", "") or "") for item in self._visible_items if item.get("is_new")]
        download_manager.mark_subscription_items_seen(ids)
        self._load_sources()

    def _download_selected(self):
        ids = self._selected_video_ids()
        if not ids:
            self._show_error(tr("Select videos first", "请先选择视频", "動画を選択してください"))
            return
        self._enqueue_ids(ids)

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
        self._enqueue_worker = None
        self._load_sources()
        InfoBar.success(
            title=tr("Added To Queue", "已加入队列", "キューに追加"),
            content=tr(
                f"Queued {queued} videos",
                f"已加入 {queued} 个视频",
                f"{queued} 件を追加しました",
            ),
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
        if self._items_refresh_pending:
            return
        self._items_refresh_pending = True
        QTimer.singleShot(800, self._refresh_visible_items_after_task_change)

    def _refresh_visible_items_after_task_change(self):
        self._items_refresh_pending = False
        self._load_items(self._selected_source_id())


def _source_type_label(source_type: str) -> str:
    if source_type == "feed":
        return tr("Feed", "账号流", "フィード")
    if source_type == "author":
        return tr("Author", "作者", "作者")
    if source_type == "playlist":
        return tr("Playlist", "播放列表", "リスト")
    return source_type


def _item_state_text(*, downloaded: bool, queued: bool) -> str:
    if downloaded:
        return tr("Downloaded", "已下载", "保存済み")
    if queued:
        return tr("Queued", "已入队", "キュー内")
    return tr("Ready", "可下载", "保存可能")


def _state_color(*, downloaded: bool, queued: bool) -> QColor:
    if downloaded:
        return QColor("#107c10")
    if queued:
        return QColor("#0078d4")
    return QColor("#555555")


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
