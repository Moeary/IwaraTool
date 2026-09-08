"""Commands and context-menu actions for the subscription page."""
from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QDialog, QListWidgetItem, QTableWidgetItem

from qfluentwidgets import (
    Action,
    FluentIcon,
    InfoBar as _DefaultInfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    ProgressBar,
    RoundMenu,
    TableWidget,
    isDarkTheme as _default_is_dark_theme,
)

from ..config import app_config
from ..core.manager import download_manager as _default_download_manager
from ..i18n import tr
from ..signal_bus import signal_bus as _default_signal_bus
from .download_page import FilterDialog, option_button_style
from .subscription_components import (
    SubscriptionEnqueueWorker,
    SubscriptionImportAuthorsWorker,
    SubscriptionRefreshWorker,
    SubscriptionThumbnailWorker,
    _CONTROL_HEIGHT,
)
from .subscription_helpers import _detect_source_input, _open_url, _source_url, _video_url
from .ui_state import show_fluent_confirmation, show_fluent_text_input


class _PageDependencyProxy:
    def __init__(self, name: str, fallback: object):
        self.name = name
        self.fallback = fallback

    def __getattr__(self, attribute: str):
        page_module = sys.modules.get("app.ui.subscription_page")
        target = getattr(page_module, self.name, self.fallback)
        return getattr(target, attribute)


download_manager = _PageDependencyProxy("download_manager", _default_download_manager)
signal_bus = _PageDependencyProxy("signal_bus", _default_signal_bus)
InfoBar = _PageDependencyProxy("InfoBar", _DefaultInfoBar)


def _is_dark_theme() -> bool:
    page_module = sys.modules.get("app.ui.subscription_page")
    resolver = getattr(page_module, "isDarkTheme", _default_is_dark_theme)
    return bool(resolver())


class SubscriptionActionsMixin:
    @staticmethod
    def _pending_item_brush() -> QBrush:
        return QBrush(QColor("#1f5f3d" if _is_dark_theme() else "#c6efce"))

    @staticmethod
    def _default_item_brush() -> QBrush:
        return QBrush(Qt.BrushStyle.NoBrush)

    def _set_pending_row_background(self, row: int, pending: bool):
        brush = self._pending_item_brush() if pending else self._default_item_brush()
        for column in range(self._item_table.columnCount()):
            item = self._item_table.item(row, column)
            if item:
                item.setBackground(brush)

    def _set_pending_visuals(self, video_ids: list[str], pending: bool):
        ids = {str(video_id).strip() for video_id in video_ids if str(video_id).strip()}
        if not ids:
            return
        for row in range(self._item_table.rowCount()):
            item = self._item_table.item(row, self._ITEM_ID)
            video_id = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "").strip() if item else ""
            if video_id in ids:
                self._set_pending_row_background(row, pending)
        brush = self._pending_item_brush() if pending else self._default_item_brush()
        for index in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() in ids:
                item.setBackground(brush)

    def _add_pending_video_ids(self, video_ids: list[str]):
        added = [
            str(video_id).strip()
            for video_id in video_ids
            if str(video_id).strip() and str(video_id).strip() not in self._pending_video_ids
        ]
        if not added:
            return
        self._pending_video_ids.update(added)
        self._set_pending_visuals(added, True)
        self._update_selection_actions()

    def _remove_pending_video_ids(self, video_ids: list[str]):
        removed = [
            str(video_id).strip()
            for video_id in video_ids
            if str(video_id).strip() in self._pending_video_ids
        ]
        for video_id in removed:
            self._pending_video_ids.discard(video_id)
        self._set_pending_visuals(removed, False)
        self._update_selection_actions()

    def _clear_pending_video_ids(self):
        if not self._pending_video_ids:
            return
        cleared = list(self._pending_video_ids)
        self._pending_video_ids.clear()
        self._set_pending_visuals(cleared, False)
        self._update_selection_actions()

    def _context_video_ids_from_table(self, pos) -> list[str]:
        row = self._item_table.rowAt(pos.y())
        if row < 0:
            return []
        id_item = self._item_table.item(row, self._ITEM_ID)
        video_id = str(id_item.data(Qt.ItemDataRole.UserRole) or id_item.text() or "").strip() if id_item else ""
        selected_ids = self._selected_video_ids()
        if not video_id:
            return []
        if video_id not in selected_ids:
            self._item_table.clearSelection()
            self._item_table.selectRow(row)
            self._item_table.setCurrentCell(row, self._ITEM_STATE)
            selected_ids = [video_id]
        return selected_ids

    def _show_item_table_context_menu(self, pos):
        video_ids = self._context_video_ids_from_table(pos)
        if not video_ids:
            return
        self._show_pending_context_menu(video_ids, self._item_table.viewport().mapToGlobal(pos))

    def _show_thumbnail_context_menu(self, pos):
        item = self._thumbnail_list.itemAt(pos)
        if not item:
            return
        video_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        selected_ids = self._selected_video_ids()
        if not video_id:
            return
        if video_id not in selected_ids:
            self._thumbnail_list.clearSelection()
            item.setSelected(True)
            selected_ids = [video_id]
        video_ids = selected_ids
        if video_ids:
            self._show_pending_context_menu(video_ids, self._thumbnail_list.viewport().mapToGlobal(pos))

    def _show_pending_context_menu(self, video_ids: list[str], global_pos):
        menu = RoundMenu(parent=self)
        pending = [video_id for video_id in video_ids if video_id in self._pending_video_ids]
        if len(pending) == len(video_ids):
            menu.addAction(
                Action(
                    FluentIcon.RETURN,
                    tr("Remove From Pending", "移出待操作", "操作待ちから外す"),
                    self,
                    triggered=lambda _checked=False: self._remove_pending_video_ids(video_ids),
                )
            )
        else:
            menu.addAction(
                Action(
                    FluentIcon.ACCEPT,
                    tr("Add To Pending", "加入待操作", "操作待ちに追加"),
                    self,
                    triggered=lambda _checked=False: self._add_pending_video_ids(video_ids),
                )
            )
        menu.addSeparator()
        menu.addAction(
            Action(
                FluentIcon.DOWNLOAD,
                tr("Download Selected", "直接下载选中", "選択した動画を保存"),
                self,
                triggered=lambda _checked=False: self._download_selected_with_rule(video_ids),
            )
        )
        menu.addAction(
            Action(
                FluentIcon.ACCEPT,
                tr(
                    "Mark Selected as Downloaded",
                    "标记选中为已下载/已移走",
                    "選択した動画を保存済み/移動済みにする",
                ),
                self,
                triggered=lambda _checked=False: self._mark_selected_downloaded_moved(video_ids),
            )
        )
        menu.addAction(
            Action(
                FluentIcon.RETURN,
                tr(
                    "Restore Selected Moved",
                    "还原选中的已移走记录",
                    "選択した移動済み記録を復元",
                ),
                self,
                triggered=lambda _checked=False: self._restore_selected_downloaded_moved(video_ids),
            )
        )
        if self._pending_video_ids:
            menu.addAction(
                Action(
                    FluentIcon.DELETE,
                    tr("Clear Pending", "清空待操作", "操作待ちをクリア"),
                    self,
                    triggered=self._clear_pending_video_ids,
                )
            )
        menu.exec(global_pos)

    def _show_source_table_context_menu(self, pos):
        source_ids = self._context_source_ids_from_table(pos)
        if not source_ids:
            return
        global_pos = self._source_table.viewport().mapToGlobal(pos)
        menu = RoundMenu(parent=self)
        menu.addAction(
            Action(
                FluentIcon.SYNC,
                tr("Refresh Selected", "刷新选中订阅", "選択した購読を更新"),
                self,
                triggered=lambda _checked=False, ids=list(source_ids): self._refresh_selected_sources(ids),
            )
        )
        menu.addAction(
            Action(
                FluentIcon.DELETE,
                tr("Delete Selected", "删除选中订阅", "選択した購読を削除"),
                self,
                triggered=lambda _checked=False, ids=list(source_ids): self._delete_source_ids(ids),
            )
        )
        menu.exec(global_pos)

    def _context_source_ids_from_table(self, pos) -> list[int]:
        row = self._source_table.rowAt(pos.y())
        if row < 0 or row >= len(self._sources):
            return []
        source_id = int(self._sources[row].get("id", 0) or 0)
        if not source_id:
            return []
        selected_ids = self._selected_source_ids()
        if source_id not in selected_ids:
            self._source_table.clearSelection()
            self._source_table.selectRow(row)
            self._source_table.setCurrentCell(row, self._SRC_STATE)
            selected_ids = [source_id]
        return selected_ids

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

    def _selected_source_ids(self) -> list[int]:
        ids: list[int] = []
        if not hasattr(self, "_source_table"):
            return ids
        for index in self._source_table.selectionModel().selectedRows():
            row = index.row()
            if row < 0 or row >= len(self._sources):
                continue
            source_id = int(self._sources[row].get("id", 0) or 0)
            if source_id and source_id not in ids:
                ids.append(source_id)
        return ids

    def _selected_source_ids_for_operation(self) -> list[int]:
        ids = self._selected_source_ids()
        if ids:
            return ids
        source_id = self._selected_source_id()
        return [source_id] if source_id else []

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

    def _operation_video_ids(self) -> list[str]:
        """Use explicitly staged videos first, then the transient UI selection."""
        if self._pending_video_ids:
            return list(self._pending_video_ids)
        return self._selected_video_ids()

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
        text, ok = show_fluent_text_input(
            self,
            tr("Add Subscription", "添加订阅", "購読を追加"),
            tr(
                "Author username / author URL / playlist URL:",
                "作者用户名 / 作者主页链接 / 播放列表链接：",
                "作者ユーザー名 / 作者URL / プレイリストURL:",
            ),
            accept_text=tr("Add", "添加", "追加"),
            cancel_text=tr("Cancel", "取消", "キャンセル"),
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
        InfoBar.success(
            title=tr("Followed Authors Imported", "关注作者已导入", "フォロー作者を取込"),
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _refresh_current_source(self):
        source_id = self._current_source_id or self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        self._start_refresh(source_id, ignore_disabled=True)

    def _refresh_selected_sources(self, source_ids: list[int] | None = None):
        ids = list(source_ids or self._selected_source_ids_for_operation())
        if not ids:
            self._show_error(
                tr(
                    "Select one or more subscription sources first",
                    "请先选择一个或多个订阅源",
                    "先に1つ以上の購読元を選択してください",
                )
            )
            return
        self._start_refresh(ids, ignore_disabled=True)

    def _cache_current_source_covers(self):
        source_id = self._current_source_id or self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        if self._shutting_down or self._cover_cache_worker is not None:
            return
        # Snapshot the full source, independently of filters and later navigation.
        items = download_manager.get_subscription_items(source_id)
        requests = list({
            str(item["video_id"]): str(item.get("thumbnail_url") or "")
            for item in items if item.get("video_id")
        }.items())
        if not requests:
            self._show_error(tr("Refresh the source list first", "请先刷新订阅源的视频列表", "先に購読元の一覧を更新してください"))
            return
        self._cover_cache_worker = SubscriptionThumbnailWorker(
            requests, concurrency=self._cover_download_concurrency(),
        )
        self._cover_cache_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._cover_cache_worker.finished.connect(self._on_source_cover_cache_finished)
        self._cover_cache_worker.start()
        InfoBar.info(
            title=tr("Caching Covers", "正在缓存封面", "カバーをキャッシュ中"),
            content=tr(
                "Caching all known videos in this source; existing covers are reused.",
                "正在缓存此订阅源已收录的全部视频封面，已有缓存会直接复用；无需下载视频。",
                "この購読元の登録済み動画のカバーを保存します。既存のキャッシュは再利用します。",
            ),
            duration=4000, parent=self,
        )

    def _on_source_cover_cache_finished(self):
        worker = self._cover_cache_worker
        self._cover_cache_worker = None
        if worker is None:
            return
        succeeded, failed = worker.succeeded, worker.failed
        worker.deleteLater()
        if self._shutting_down:
            return
        InfoBar.info(
            title=tr("Cover Cache Complete", "封面缓存完成", "カバーのキャッシュ完了"),
            content=tr(
                f"Available: {succeeded}; failed: {failed}. Run again to retry missing covers.",
                f"本地可用 {succeeded} 张，失败 {failed} 张。可再次执行以重试缺失封面。",
                f"利用可能: {succeeded}、失敗: {failed}。再実行で未取得分を再試行できます。",
            ),
            duration=5000, parent=self,
        )

    def _refresh_current_covers(self):
        source_id = self._current_source_id or self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        self._thumbnail_force_refresh_ids.clear()
        started = self._start_thumbnail_worker_for_visible_items(force=True)
        if started:
            InfoBar.info(
                title=tr("Refreshing Covers", "正在刷新封面", "カバーを更新中"),
                content=tr(
                    "The current source covers are being refreshed.",
                    "正在刷新当前订阅源的视频封面。",
                    "現在の購読元のカバーを更新しています。",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _refresh_all(self):
        self._start_refresh(None)

    def _show_selected_source_all_items(self):
        source_id = self._selected_source_id() or self._current_source_id
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        self._title_filter_mode_btn.setChecked(False)
        self._title_search_edit.clear()
        self._install_filter_combo.setCurrentIndex(0)
        self._new_filter_combo.setCurrentIndex(0)
        self._load_items(source_id)

    def _start_refresh(
        self,
        source_id: int | list[int] | None,
        *,
        ignore_disabled: bool = False,
    ):
        if self._worker and self._worker.isRunning():
            return
        self._set_refresh_actions_enabled(False)
        self._worker = SubscriptionRefreshWorker(source_id, ignore_disabled=ignore_disabled)
        self._worker.progress.connect(self._on_refresh_progress)
        self._worker.finished.connect(self._on_refresh_finished)
        self._worker.start()

    def _set_refresh_actions_enabled(self, enabled: bool):
        for button_name in ("_refresh_current_btn", "_refresh_all_btn"):
            button = getattr(self, button_name, None)
            if button:
                button.setEnabled(enabled)

    def _on_refresh_progress(self, progress: dict):
        total = max(1, int(progress.get("total", 1) or 1))
        index = max(1, min(total, int(progress.get("index", 1) or 1)))
        started = str(progress.get("stage", "") or "") == "started"
        title = str(progress.get("title", "") or "")
        if self._refresh_info_bar is None:
            self._refresh_info_bar = InfoBar.info(
                title=tr("Refreshing Subscriptions", "正在刷新订阅", "購読を更新中"),
                content="",
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                duration=-1,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            self._refresh_progress = ProgressBar(self._refresh_info_bar)
            self._refresh_progress.setFixedWidth(150)
            self._refresh_progress.setRange(0, total)
            self._refresh_info_bar.addWidget(self._refresh_progress)

        completed = index - 1 if started else index
        if self._refresh_progress:
            self._refresh_progress.setRange(0, total)
            self._refresh_progress.setValue(completed)
        content = tr(
            f"{index}/{total} · {'Refreshing' if started else 'Finished'} {title}",
            f"{index}/{total} · {'正在刷新' if started else '已完成'} {title}",
            f"{index}/{total} · {'更新中' if started else '完了'} {title}",
        )
        self._refresh_info_bar.content = content
        self._refresh_info_bar.contentLabel.setText(content)
        self._refresh_info_bar.adjustSize()

    def _clear_refresh_progress(self):
        info_bar = self._refresh_info_bar
        self._refresh_info_bar = None
        self._refresh_progress = None
        if info_bar:
            info_bar.close()

    def _on_refresh_finished(self, summary: dict):
        worker = self._worker
        self._worker = None
        self._clear_refresh_progress()
        self._set_refresh_actions_enabled(True)
        if worker:
            worker.deleteLater()
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
        self._delete_source_ids(self._selected_source_ids_for_operation())

    def _delete_source_ids(self, source_ids: list[int]):
        ids = list(dict.fromkeys(int(source_id) for source_id in source_ids if source_id))
        if not ids:
            self._show_error(
                tr(
                    "Select one or more subscriptions first",
                    "请先选择一个或多个订阅",
                    "先に1つ以上の購読を選択してください",
                )
            )
            return
        if self._worker and self._worker.isRunning():
            self._show_error(
                tr(
                    "Wait for the current refresh to finish first",
                    "请先等待当前刷新完成",
                    "現在の更新が完了するまでお待ちください",
                )
            )
            return
        source_by_id = {
            int(source.get("id", 0) or 0): source
            for source in self._sources
            if int(source.get("id", 0) or 0)
        }
        names = [
            (
                str(source_by_id.get(source_id, {}).get("title", "") or "").strip()
                or str(source_by_id.get(source_id, {}).get("source_key", "") or "").strip()
                or f"#{source_id}"
            )
            for source_id in ids
        ]
        if len(ids) == 1:
            question = tr(
                f'Delete subscription "{names[0]}" and its cached video list?',
                f'确定删除订阅“{names[0]}”及其缓存视频列表吗？',
                f'購読「{names[0]}」と保存済み動画一覧を削除しますか？',
            )
            content = names[0]
        else:
            preview = "、".join(names[:3])
            if len(names) > 3:
                preview += tr(" and more", "等", "ほか")
            question = tr(
                f"Delete {len(ids)} selected subscriptions and their cached video lists?",
                f"确定删除选中的 {len(ids)} 个订阅及其缓存视频列表吗？",
                f"選択した {len(ids)} 件の購読と保存済み動画一覧を削除しますか？",
            )
            content = tr(
                f"Selected: {preview}",
                f"选中：{preview}",
                f"選択: {preview}",
            )
        if not show_fluent_confirmation(
            self,
            tr("Delete Subscription", "删除订阅", "購読を削除"),
            question,
            informative=tr(
                "Downloaded files and history records will not be deleted.",
                "不会删除已下载文件和历史记录。",
                "保存済みファイルと履歴は削除されません。",
            ),
            yes_text=tr("Delete subscription", "删除订阅", "購読を削除"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        ):
            return
        remove_many = getattr(download_manager, "remove_subscription_sources", None)
        if callable(remove_many):
            remove_many(ids)
        else:
            for source_id in ids:
                download_manager.remove_subscription_source(source_id)
        if self._current_source_id in ids:
            self._current_source_id = None
        self._load_sources()
        InfoBar.success(
            title=tr("Subscription Deleted", "订阅已删除", "購読を削除しました"),
            content=content,
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
        ids = self._operation_video_ids()
        if not ids:
            ids = [str(item.get("video_id", "") or "") for item in self._visible_items if item.get("is_new")]
        download_manager.mark_subscription_items_seen(ids)
        self._refresh_sources_keep_current_items()

    def _mark_selected_downloaded_moved(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
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

    def _restore_selected_downloaded_moved(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
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

    def _download_selected(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
        if not ids:
            self._show_error(tr("Select one or more videos first", "请先选中右侧列表里的一个或多个视频", "先に右側リストで動画を選択してください"))
            return
        self._enqueue_ids(ids)

    def _update_selection_actions(self):
        count = len(self._operation_video_ids()) if hasattr(self, "_item_table") else 0
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
        state = tr("On", "开", "オン") if checked else tr("Off", "关", "オフ")
        button.setText(f"{base_text}  {state}")
        button.setStyleSheet(option_button_style(checked))

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

    def _apply_selected_rule_for_download(self):
        # Rules are independent from the list's browse filter. Reapplying here
        # keeps keyboard/automation calls consistent without rerendering rows.
        if hasattr(self, "_rule_picker"):
            self._rule_picker.apply_selected(show_notice=False)

    def _download_selected_with_rule(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
        self._apply_selected_rule_for_download()
        self._enqueue_ids(ids)

    def _download_new_with_rule(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if item.get("is_new") and not item.get("downloaded")
        ]
        self._apply_selected_rule_for_download()
        self._enqueue_ids(ids)

    def _download_visible_with_rule(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if not item.get("downloaded")
        ]
        self._apply_selected_rule_for_download()
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

    def _enqueue_ids(self, ids: list[str], *, rule_id: str = ""):
        ids = [video_id for video_id in ids if video_id]
        if not ids:
            self._show_error(tr("No downloadable videos in current selection", "当前选择没有可下载视频", "保存可能な動画がありません"))
            return
        if self._enqueue_worker and self._enqueue_worker.isRunning():
            self._show_error(tr("Queue operation is still running", "队列操作仍在进行中", "キュー操作が実行中です"))
            return
        selected_rule_id = str(
            rule_id
            or (self._rule_picker.selected_rule_id() if hasattr(self, "_rule_picker") else "")
        )
        self._enqueue_worker = SubscriptionEnqueueWorker(ids, rule_id=selected_rule_id)
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

