"""Load, filter and render one source's items, and follow download task changes."""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QTableWidgetItem

from ...i18n import tr
from .helpers import (
    _date_only,
    _item_date_key,
    _item_not_downloadable,
    _item_state_text,
    _state_color,
    _title_matcher,
    _video_url,
)
from ..theme import qcolor, summary_text
from .page_dependencies import download_manager


class SubscriptionItemListMixin:
    """Load, filter and render one source's items, and follow download task changes."""

    def _load_items(self, source_id: int | None):
        self._current_source_id = source_id
        self._all_items = download_manager.get_subscription_items(source_id)
        self._apply_item_filters()

    def _on_title_filter_mode_toggled(self, regex_mode: bool):
        self._title_filter_mode_btn.setText(
            tr("Regex", "正则模式", "正規表現")
            if regex_mode else tr("Simple", "简单搜索", "簡易検索")
        )
        self._title_filter_mode_btn.setToolTip(
            tr("Switch to simple search", "切换到简单搜索", "簡易検索に切替")
            if regex_mode else tr("Switch to regex mode", "切换到正则模式", "正規表現モードに切替")
        )
        if hasattr(self, "_title_search_edit"):
            self._title_search_edit.setPlaceholderText(
                tr(
                    "Search titles as you type…",
                    "输入标题关键词，实时筛选…",
                    "タイトルを入力して絞り込み…",
                )
                if not regex_mode else tr(
                    "Python regex, e.g. \\b\\d{2}-\\d{2}\\b",
                    "输入 Python 正则，例如 \\b\\d{2}-\\d{2}\\b",
                    "Python 正規表現。例: \\b\\d{2}-\\d{2}\\b",
                )
            )
        self._apply_item_filters()

    def _apply_item_filters(self, *_args):
        self._sync_items_with_current_tasks()
        items = list(self._all_items)
        install_idx = self._install_filter_combo.currentIndex() if hasattr(self, "_install_filter_combo") else 0
        new_idx = self._new_filter_combo.currentIndex() if hasattr(self, "_new_filter_combo") else 0
        sort_idx = self._item_sort_combo.currentIndex() if hasattr(self, "_item_sort_combo") else 0
        title_query = self._title_search_edit.text().strip() if hasattr(self, "_title_search_edit") else ""
        title_mode = 1 if getattr(self, "_title_filter_mode_btn", None) and self._title_filter_mode_btn.isChecked() else 0
        self._title_filter_error = ""
        if title_query:
            try:
                matcher = _title_matcher(title_query, regex_mode=title_mode == 1)
            except re.error as exc:
                self._title_filter_error = str(exc)
                items = []
            else:
                items = [
                    item
                    for item in items
                    if matcher(str(item.get("title", "") or item.get("video_id", "") or ""))
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
            summary_text(
                [
                    (tr("Visible", "当前显示", "表示"), f"{len(self._visible_items)}/{len(self._all_items)}"),
                    (tr("Pending", "待操作", "操作待ち"), len(self._pending_video_ids)),
                    (tr("New", "新增", "新規"), new_count),
                    (tr("Downloaded", "本地已下载", "保存済み"), downloaded_count),
                    (tr("Moved", "已移走", "移動済み"), moved_count),
                    (tr("Queued", "已在队列", "キュー内"), queued_count),
                    (tr("Unavailable", "不可下载", "保存不可"), unavailable_count),
                ]
            )
        )
        if self._title_filter_error:
            self._title_search_edit.setToolTip(
                tr(
                    f"Invalid regular expression: {self._title_filter_error}",
                    f"正则表达式无效：{self._title_filter_error}",
                    f"正規表現が無効です：{self._title_filter_error}",
                )
            )
            self._summary_label.setToolTip(
                tr(
                    f"Invalid regular expression: {self._title_filter_error}",
                    f"正则表达式无效：{self._title_filter_error}",
                    f"正規表現が無効です：{self._title_filter_error}",
                )
            )
        else:
            self._title_search_edit.setToolTip("")
            self._summary_label.setToolTip("")

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
                cell.setForeground(qcolor("danger"))
            elif col == self._ITEM_NEW and is_new:
                cell.setForeground(qcolor("warning"))
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

        if video_id in self._pending_video_ids:
            self._set_pending_row_background(row, True)

    def _on_task_status_changed(self, _task_id: str, _status_str: str):
        self._schedule_items_refresh_after_task_change()

    def _on_task_changed(self, *_args):
        self._schedule_items_refresh_after_task_change()

    def _on_tasks_changed(self, *_args):
        self._schedule_items_refresh_after_task_change()

    def _schedule_items_refresh_after_task_change(self):
        self._schedule_views_refresh()
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
