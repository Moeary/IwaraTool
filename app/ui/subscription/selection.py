"""Selection in the source and item tables, and opening what is selected."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem, QTableWidgetItem

from qfluentwidgets import InfoBarPosition, TableWidget

from ...i18n import tr
from .helpers import _open_url, _source_url, _video_url
from .page_dependencies import InfoBar, download_manager, signal_bus
from ..theme import link_color


class SubscriptionSelectionMixin:
    """Selection in the source and item tables, and opening what is selected."""

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
        cell.setForeground(link_color(enabled))
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
        title = str((item_data or {}).get("title", "") or "")
        signal_bus.video_preview_requested.emit(video_id, title, "")

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
