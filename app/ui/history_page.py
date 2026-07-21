"""History Center Interface — DB-backed downloaded file browser."""
from __future__ import annotations

import os
import webbrowser
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QTableWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    TableWidget,
    TitleLabel,
)

from ..config import app_config
from ..core.manager import download_manager
from ..core.models import TaskStatus
from ..i18n import tr
from ..signal_bus import signal_bus
from .ui_state import (
    ResponsiveFlowLayout,
    connect_table_column_saver,
    connect_table_width_saver,
    open_table_column_dialog,
    restore_table_columns,
    restore_table_widths,
)


class HistoryInterface(QWidget):
    """Downloaded history page backed by data/history.db."""

    _COL_STATE = 0
    _COL_TITLE = 1
    _COL_AUTHOR = 2
    _COL_QUALITY = 3
    _COL_PUBLISHED = 4
    _COL_LIKES = 5
    _COL_VIEWS = 6
    _COL_DOWNLOADED = 7
    _COL_ID = 8
    _COL_PATH = 9
    _COL_SOURCE_URL = 10
    _COL_OPEN_URL = 11
    _COL_OPEN_FOLDER = 12
    _COL_OPEN_FILE = 13
    _COL_RENAME = 14
    _COL_REMOVE = 15

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("HistoryInterface")
        self._all_records: list[dict[str, Any]] = []
        self._records_by_id: dict[str, dict[str, Any]] = {}
        self._sort_column = self._COL_DOWNLOADED
        self._sort_reverse = True

        self._build_ui()
        self._load_history()
        signal_bus.task_status_changed.connect(self._on_task_status_changed)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 16)
        root.setSpacing(12)

        title_row = ResponsiveFlowLayout()
        title_row.addWidget(TitleLabel(tr("History Center", "历史记录中心", "履歴センター"), self))

        refresh_btn = PrimaryPushButton(tr("Refresh", "刷新", "更新"), self, FluentIcon.SYNC)
        refresh_btn.setToolTip(
            tr(
                "Reload DB and refresh file states; no records are deleted",
                "重新读取数据库并刷新文件状态；不会删除任何记录",
                "DBを再読み込みし状態を更新します。履歴は削除しません",
            )
        )
        refresh_btn.clicked.connect(self._load_history)
        title_row.addWidget(refresh_btn)

        clean_btn = PrimaryPushButton(
            tr("Clean Moved", "清理已移走记录", "移動済みを削除"),
            self,
            FluentIcon.BROOM,
        )
        clean_btn.setToolTip(
            tr(
                "Delete DB records whose files are missing or outside the download folder",
                "删除数据库中已移走/不在下载文件夹的记录；不会删除本地文件",
                "ファイル不明または保存先外のDB履歴を削除します",
            )
        )
        clean_btn.clicked.connect(self._sync_with_download_folder)
        title_row.addWidget(clean_btn)

        columns_btn = PrimaryPushButton(tr("Fields", "字段设置", "列設定"), self, FluentIcon.SETTING)
        columns_btn.clicked.connect(self._configure_columns)
        title_row.addWidget(columns_btn)
        root.addLayout(title_row)

        self._db_label = BodyLabel(
            f"{tr('History DB', '历史数据库', '履歴DB')}: {app_config.history_db_path}",
            self,
        )
        root.addWidget(self._db_label)

        self._summary_label = BodyLabel("", self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setMinimumHeight(28)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self._summary_label)

        filter_row = ResponsiveFlowLayout()
        self._search_edit = LineEdit(self)
        self._search_edit.setPlaceholderText(
            tr(
                "Search table text...",
                "搜索表格内容...",
                "表の内容を検索...",
            )
        )
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self._search_edit)

        self._field_combo = ComboBox(self)
        self._field_combo.addItems(
            [
                tr("All Columns", "全部字段", "全列"),
                tr("State", "状态", "状態"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Quality", "画质", "画質"),
                tr("Published", "发布日期", "公開日"),
                "ID",
                tr("Source URL", "来源URL", "元URL"),
                tr("Path", "路径", "パス"),
            ]
        )
        self._field_combo.setFixedWidth(150)
        self._field_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._field_combo)

        self._state_combo = ComboBox(self)
        self._state_combo.addItems(
            [
                tr("All States", "全部状态", "全状態"),
                tr("OK", "正常", "正常"),
                tr("Moved", "已移走", "移動済み"),
            ]
        )
        self._state_combo.setFixedWidth(130)
        self._state_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._state_combo)
        root.addLayout(filter_row)

        self._table = TableWidget(self)
        self._table.setMinimumWidth(0)
        self._table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._table.setObjectName("historyTable")
        self._table.setColumnCount(16)
        self._table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Quality", "画质", "画質"),
                tr("Published", "发布日期", "公開日"),
                tr("Likes", "点赞", "いいね"),
                tr("Views", "播放", "再生"),
                tr("Downloaded At", "下载时间", "保存日時"),
                "ID",
                tr("File Path", "文件路径", "ファイルパス"),
                tr("Source URL", "来源URL", "元URL"),
                tr("Page", "页面", "ページ"),
                tr("Folder", "文件夹", "フォルダー"),
                tr("File", "文件", "ファイル"),
                tr("Rename", "重命名", "名前変更"),
                tr("Delete", "删记录", "削除"),
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        self._table.setWordWrap(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(42)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.itemDoubleClicked.connect(self._on_item_double_clicked)

        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(self._sort_column, Qt.SortOrder.DescendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        initial_widths = {
            self._COL_STATE: 62,
            self._COL_TITLE: 620,
            self._COL_AUTHOR: 110,
            self._COL_QUALITY: 80,
            self._COL_PUBLISHED: 112,
            self._COL_LIKES: 72,
            self._COL_VIEWS: 78,
            self._COL_DOWNLOADED: 150,
            self._COL_ID: 135,
            self._COL_PATH: 520,
            self._COL_SOURCE_URL: 260,
            self._COL_OPEN_URL: 58,
            self._COL_OPEN_FOLDER: 58,
            self._COL_OPEN_FILE: 58,
            self._COL_RENAME: 66,
            self._COL_REMOVE: 66,
        }
        restore_table_widths(self._table, "history_table_widths", initial_widths)
        connect_table_width_saver(self._table, "history_table_widths")
        restore_table_columns(self._table, "history_table")
        connect_table_column_saver(self._table, "history_table")
        root.addWidget(self._table, stretch=1)

    def _configure_columns(self):
        open_table_column_dialog(
            self._table,
            "history_table",
            title=tr("History Columns", "历史列表字段", "履歴列設定"),
            parent=self,
        )

    def _load_history(self):
        self._all_records = download_manager.get_history_records()
        self._records_by_id = {
            str(row.get("video_id", "") or ""): row
            for row in self._all_records
            if str(row.get("video_id", "") or "")
        }
        self._apply_filters()

    def _apply_filters(self, *_args):
        query = self._search_edit.text().strip().lower() if hasattr(self, "_search_edit") else ""
        field_idx = self._field_combo.currentIndex() if hasattr(self, "_field_combo") else 0
        state_idx = self._state_combo.currentIndex() if hasattr(self, "_state_combo") else 0

        visible_records: list[dict[str, Any]] = []
        ok_count = moved_count = 0
        for record in self._all_records:
            _, state_key, _ = self._record_state(record)
            if state_key == "ok":
                ok_count += 1
            else:
                moved_count += 1

            if state_idx == 1 and state_key != "ok":
                continue
            if state_idx == 2 and state_key != "moved":
                continue
            if query and query not in self._record_search_text(record, field_idx):
                continue
            visible_records.append(record)

        visible_records = self._sort_records(visible_records)
        self._render_table(visible_records)
        self._summary_label.setText(
            tr(
                f"Records: {len(self._all_records)} | visible: {len(visible_records)} | OK: {ok_count} | moved: {moved_count}",
                f"记录数: {len(self._all_records)} | 当前显示: {len(visible_records)} | 正常: {ok_count} | 已移走: {moved_count}",
                f"履歴: {len(self._all_records)} | 表示: {len(visible_records)} | 正常: {ok_count} | 移動済み: {moved_count}",
            )
        )

    def _render_table(self, records: list[dict[str, Any]]):
        self._table.setUpdatesEnabled(False)
        try:
            self._table.clearContents()
            self._table.setRowCount(len(records))
            for row_idx, record in enumerate(records):
                state, state_key, state_detail = self._record_state(record)
                video_id = str(record.get("video_id", "") or "")
                file_path = str(record.get("file_path", "") or "")
                file_exists = bool(file_path and os.path.isfile(file_path))
                values = [
                    state,
                    str(record.get("title", "") or video_id),
                    str(record.get("author", "") or ""),
                    str(record.get("quality", "") or ""),
                    _date_only(str(record.get("published_at", "") or "")),
                    str(record.get("likes", 0) or 0),
                    str(record.get("views", 0) or 0),
                    str(record.get("downloaded_at", "") or ""),
                    video_id,
                    file_path,
                ]

                for col_idx, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, video_id)
                    item.setToolTip(state_detail if col_idx == self._COL_STATE else value)
                    if col_idx == self._COL_STATE:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        item.setForeground(QColor("#107c10" if state_key == "ok" else "#c17d00"))
                    elif col_idx in (self._COL_LIKES, self._COL_VIEWS):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self._table.setItem(row_idx, col_idx, item)

                source_url = str(record.get("source_url", "") or _video_url(video_id))
                source_item = QTableWidgetItem(source_url)
                source_item.setData(Qt.ItemDataRole.UserRole, video_id)
                source_item.setToolTip(source_url or tr("No video URL", "没有视频链接", "動画URLがありません"))
                self._table.setItem(row_idx, self._COL_SOURCE_URL, source_item)
                self._set_action_item(
                    row_idx,
                    self._COL_OPEN_URL,
                    tr("Page", "页面", "ページ"),
                    tr("Open video page", "打开视频页", "動画ページを開く"),
                    "open_url",
                    bool(video_id),
                    video_id,
                    action_url=_video_url(video_id),
                )
                self._set_action_item(
                    row_idx,
                    self._COL_OPEN_FOLDER,
                    tr("Folder", "目录", "フォルダー"),
                    tr("Open folder", "打开文件夹", "フォルダーを開く"),
                    "folder",
                    file_exists,
                    video_id,
                )
                self._set_action_item(
                    row_idx,
                    self._COL_OPEN_FILE,
                    tr("File", "文件", "ファイル"),
                    tr("Open file", "打开文件", "ファイルを開く"),
                    "file",
                    file_exists,
                    video_id,
                )
                self._set_action_item(
                    row_idx,
                    self._COL_RENAME,
                    tr("Rename", "改名", "名前変更"),
                    tr("Rename file", "重命名文件", "ファイル名を変更"),
                    "rename",
                    file_exists,
                    video_id,
                )
                self._set_action_item(
                    row_idx,
                    self._COL_REMOVE,
                    tr("Delete", "删除", "削除"),
                    tr("Remove DB record", "删除数据库记录", "DB履歴を削除"),
                    "remove",
                    bool(video_id),
                    video_id,
                )
        finally:
            self._table.setUpdatesEnabled(True)

    def _on_cell_clicked(self, row: int, column: int):
        if column not in {
            self._COL_OPEN_URL,
            self._COL_OPEN_FOLDER,
            self._COL_OPEN_FILE,
            self._COL_RENAME,
            self._COL_REMOVE,
        }:
            return
        item = self._table.item(row, column)
        if not item:
            return
        action = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
        video_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not action or not video_id:
            return
        if action == "open_url":
            url = str(item.data(Qt.ItemDataRole.UserRole + 2) or "")
            self._open_video_url(video_id, url=url)
        elif action == "folder":
            self._open_record(video_id, open_file=False)
        elif action == "file":
            self._open_record(video_id, open_file=True)
        elif action == "rename":
            self._rename_record(video_id)
        elif action == "remove":
            self._remove_record(video_id)

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        if item.column() in {
            self._COL_SOURCE_URL,
            self._COL_OPEN_URL,
            self._COL_OPEN_FOLDER,
            self._COL_OPEN_FILE,
            self._COL_RENAME,
            self._COL_REMOVE,
        }:
            return
        self._open_selected(open_file=True)

    def _on_header_clicked(self, column: int):
        if column not in self._sortable_columns():
            self._restore_sort_indicator()
            QTimer.singleShot(0, self._restore_sort_indicator)
            return
        if column == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column in (
                self._COL_PUBLISHED,
                self._COL_LIKES,
                self._COL_VIEWS,
                self._COL_DOWNLOADED,
            )
        self._restore_sort_indicator()
        self._apply_filters()

    def _restore_sort_indicator(self):
        self._table.horizontalHeader().setSortIndicator(
            self._sort_column,
            Qt.SortOrder.DescendingOrder if self._sort_reverse else Qt.SortOrder.AscendingOrder,
        )

    def _sortable_columns(self) -> set[int]:
        return {
            self._COL_STATE,
            self._COL_TITLE,
            self._COL_AUTHOR,
            self._COL_QUALITY,
            self._COL_PUBLISHED,
            self._COL_LIKES,
            self._COL_VIEWS,
            self._COL_DOWNLOADED,
            self._COL_ID,
            self._COL_PATH,
            self._COL_SOURCE_URL,
        }

    def _sort_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def text(value: Any) -> str:
            return str(value or "").lower()

        def number(value: Any) -> int:
            try:
                return int(value or 0)
            except Exception:
                return 0

        def key(record: dict[str, Any]):
            state, state_key, _ = self._record_state(record)
            if self._sort_column == self._COL_STATE:
                return (0 if state_key == "ok" else 1, text(state))
            if self._sort_column == self._COL_TITLE:
                return text(record.get("title") or record.get("video_id"))
            if self._sort_column == self._COL_AUTHOR:
                return text(record.get("author"))
            if self._sort_column == self._COL_QUALITY:
                return text(record.get("quality"))
            if self._sort_column == self._COL_PUBLISHED:
                return text(record.get("published_at"))
            if self._sort_column == self._COL_LIKES:
                return number(record.get("likes"))
            if self._sort_column == self._COL_VIEWS:
                return number(record.get("views"))
            if self._sort_column == self._COL_DOWNLOADED:
                return text(record.get("downloaded_at"))
            if self._sort_column == self._COL_ID:
                return text(record.get("video_id"))
            if self._sort_column == self._COL_PATH:
                return text(record.get("file_path"))
            if self._sort_column == self._COL_SOURCE_URL:
                return text(record.get("source_url") or _video_url(record.get("video_id", "")))
            return text(record.get("downloaded_at"))

        return sorted(records, key=key, reverse=self._sort_reverse)

    def _set_action_item(
        self,
        row: int,
        column: int,
        text: str,
        tooltip: str,
        action: str,
        enabled: bool,
        video_id: str,
        *,
        action_url: str = "",
    ):
        item = QTableWidgetItem(text if enabled else "-")
        item.setData(Qt.ItemDataRole.UserRole, video_id)
        item.setToolTip(tooltip if enabled else "")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setData(Qt.ItemDataRole.UserRole + 2, action_url if enabled else "")
        if enabled:
            item.setData(Qt.ItemDataRole.UserRole + 1, action)
            item.setForeground(QColor("#0078d4"))
        else:
            item.setForeground(QColor("#9aa0a6"))
        self._table.setItem(row, column, item)

    def _record_state(self, record: dict[str, Any]) -> tuple[str, str, str]:
        file_path = str(record.get("file_path", "") or "")
        if not file_path or not os.path.isfile(file_path):
            return (
                tr("Moved", "已移走", "移動済み"),
                "moved",
                tr("The stored file path no longer exists", "数据库中的文件路径已不存在", "保存されたファイルパスが存在しません"),
            )

        try:
            root = os.path.abspath(app_config.download_dir)
            file_abs = os.path.abspath(file_path)
            inside_root = os.path.commonpath([root, file_abs]) == root
        except Exception:
            inside_root = False

        if inside_root:
            return (
                tr("OK", "正常", "正常"),
                "ok",
                tr("File exists in the current download folder", "文件位于当前下载文件夹内", "現在の保存先内にファイルがあります"),
            )
        return (
            tr("Moved", "已移走", "移動済み"),
            "moved",
            tr("File exists, but is outside the current download folder", "文件存在，但不在当前下载文件夹内", "ファイルは存在しますが現在の保存先外です"),
        )

    def _record_search_text(self, record: dict[str, Any], field_idx: int) -> str:
        state, _, state_detail = self._record_state(record)
        fields = {
            1: [state, state_detail],
            2: [record.get("title", "")],
            3: [record.get("author", "")],
            4: [record.get("quality", "")],
            5: [record.get("published_at", ""), record.get("downloaded_at", "")],
            6: [record.get("video_id", "")],
            7: [record.get("source_url", ""), record.get("video_id", "")],
            8: [record.get("file_path", "")],
        }
        if field_idx in fields:
            values = fields[field_idx]
        else:
            values = [
                state,
                state_detail,
                record.get("title", ""),
                record.get("author", ""),
                record.get("quality", ""),
                record.get("published_at", ""),
                record.get("downloaded_at", ""),
                record.get("likes", ""),
                record.get("views", ""),
                record.get("video_id", ""),
                record.get("source_url", ""),
                record.get("file_path", ""),
            ]
        return " ".join(str(v or "") for v in values).lower()

    def _selected_video_id(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, self._COL_ID) or self._table.item(row, self._COL_STATE)
        if not item:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "")

    def _open_selected(self, *, open_file: bool):
        video_id = self._selected_video_id()
        if video_id:
            self._open_record(video_id, open_file=open_file)

    def _open_record(self, video_id: str, *, open_file: bool):
        ok, msg = download_manager.open_history_output(video_id, open_file=open_file)
        if not ok:
            self._show_error(msg)

    def _open_video_url(self, video_id: str, *, url: str = ""):
        record = self._records_by_id.get(video_id, {})
        url = str(url or record.get("source_url", "") or _video_url(video_id))
        if url:
            webbrowser.open(url)

    def _rename_record(self, video_id: str):
        record = self._records_by_id.get(video_id)
        if not record:
            self._show_error(tr("History record does not exist", "历史记录不存在", "履歴が存在しません"))
            return

        file_path = str(record.get("file_path", "") or "")
        if not file_path or not os.path.isfile(file_path):
            self._show_error(tr("File does not exist", "文件不存在", "ファイルが存在しません"))
            return

        old_name = os.path.basename(file_path)
        new_name, ok = QInputDialog.getText(
            self,
            tr("Rename File", "重命名文件", "ファイル名を変更"),
            tr("New file name:", "新的文件名：", "新しいファイル名:"),
            text=old_name,
        )
        if not ok or not new_name.strip():
            return

        renamed, msg = download_manager.rename_history_file(video_id, new_name)
        if not renamed:
            self._show_error(msg)
            return

        self._load_history()
        InfoBar.success(
            title=tr("Renamed", "已重命名", "名前変更完了"),
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _remove_record(self, video_id: str):
        record = self._records_by_id.get(video_id)
        if not record:
            self._show_error(tr("History record does not exist", "历史记录不存在", "履歴が存在しません"))
            return

        title = str(record.get("title", "") or video_id)
        box = QMessageBox(self)
        box.setWindowTitle(tr("Remove Record", "删除记录", "履歴を削除"))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            tr(
                f"Remove this DB record? The local file will not be deleted.\n{title}",
                f"删除这条数据库记录？本地文件不会被删除。\n{title}",
                f"このDB履歴を削除しますか？ローカルファイルは削除されません。\n{title}",
            )
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        download_manager.remove_history_record(video_id)
        self._load_history()

    def _sync_with_download_folder(self):
        moved_records = [
            row
            for row in self._all_records
            if self._record_state(row)[1] == "moved"
        ]
        if not moved_records:
            InfoBar.success(
                title=tr("Nothing To Clean", "无需清理", "削除対象なし"),
                content=tr("No moved records found", "没有发现已移走记录", "移動済み履歴はありません"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        box = QMessageBox(self)
        box.setWindowTitle(tr("Clean Moved Records", "清理已移走记录", "移動済み履歴を削除"))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            tr(
                f"This will delete {len(moved_records)} DB records for files that are missing or outside the current download folder.",
                f"将删除 {len(moved_records)} 条文件缺失或不在当前下载文件夹内的数据库记录。",
                f"不明または保存先外のDB履歴 {len(moved_records)} 件を削除します。",
            )
        )
        box.setInformativeText(
            tr(
                "Local files will not be deleted, but these videos will no longer count as previously saved in history.",
                "不会删除本地文件，但这些视频之后不再被历史库视为已保存；如果后续做作者订阅/去重，可能会重新入队。",
                "ローカルファイルは削除されませんが、履歴上は保存済み扱いではなくなります。",
            )
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        stats = download_manager.sync_history_with_download_folder()
        self._load_history()
        InfoBar.success(
            title=tr("Cleanup Finished", "清理完成", "削除完了"),
            content=tr(
                f"Kept {stats['kept']}, removed {stats['removed']}",
                f"保留 {stats['kept']} 条，删除数据库记录 {stats['removed']} 条",
                f"保持 {stats['kept']} 件、DB履歴を {stats['removed']} 件削除",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _show_error(self, msg: str):
        InfoBar.error(
            title=tr("Operation Failed", "操作失败", "操作失敗"),
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4500,
            parent=self,
        )

    def _on_task_status_changed(self, _task_id: str, status_str: str):
        if status_str == TaskStatus.COMPLETED.value:
            self._load_history()


def _date_only(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10] if len(text) >= 10 else text


def _video_url(video_id: str) -> str:
    video_id = str(video_id or "").strip()
    return f"https://www.iwara.tv/video/{video_id}" if video_id else ""
