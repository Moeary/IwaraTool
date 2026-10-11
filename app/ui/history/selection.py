"""Selection, clicks and the right-click menu of the history table."""

from __future__ import annotations

import os

from PySide6.QtWidgets import QTableWidgetItem

from qfluentwidgets import Action, FluentIcon, RoundMenu

from ...i18n import tr
from ...signal_bus import signal_bus
from .page_dependencies import download_manager
from .record_state import _video_url


class HistorySelectionMixin:
    """Selection, clicks and the right-click menu of the history table."""

    def _update_action_state(self):
        if hasattr(self, "_delete_selected_btn"):
            self._delete_selected_btn.setEnabled(bool(self._selected_video_ids()))

    def _selected_video_ids(self) -> list[str]:
        if not hasattr(self, "_table"):
            return []
        if self._view_mode != "list":
            return self._cover_grid.selected_video_ids()

        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        video_ids: list[str] = []
        for row in rows:
            video_id = self._video_id_at(row)
            if video_id and video_id not in video_ids:
                video_ids.append(video_id)

        if video_ids:
            return video_ids

        video_id = self._video_id_at(self._table.currentRow())
        return [video_id] if video_id else []

    def _video_id_at(self, row: int) -> str:
        """The video id behind a table row (read from the record, not stored on 16 cells)."""

        if 0 <= row < len(self._render_records):
            return str(self._render_records[row].get("video_id", "") or "").strip()
        return ""

    def _show_context_menu(self, position):
        item = self._table.itemAt(position)
        if item is not None:
            selected_rows = {index.row() for index in self._table.selectionModel().selectedRows()}
            if item.row() not in selected_rows:
                self._table.clearSelection()
                self._table.selectRow(item.row())
                self._table.setCurrentCell(item.row(), self._COL_ID)
        self._exec_record_menu(self._table.viewport().mapToGlobal(position))

    def _exec_record_menu(self, global_position):
        """The right-click menu for the selected records, in either view."""

        video_ids = self._selected_video_ids()
        if not video_ids:
            return

        records = [self._records_by_id.get(video_id, {}) for video_id in video_ids]
        moved_ids = [
            video_id
            for video_id, record in zip(video_ids, records)
            if record and self._record_state(record)[1] == "moved"
        ]
        menu = RoundMenu(parent=self)

        if len(video_ids) == 1:
            self._add_single_record_actions(menu, video_ids[0], records[0])

        if menu.actions():
            menu.addSeparator()
        if moved_ids:
            menu.addAction(
                Action(
                    FluentIcon.BROOM,
                    tr(
                        f"Clean selected moved records ({len(moved_ids)})",
                        f"清理选中的失效记录（{len(moved_ids)}）",
                        f"選択した移動済み履歴を削除（{len(moved_ids)}）",
                    ),
                    self,
                    triggered=lambda _checked=False, ids=tuple(moved_ids): self._clean_selected_records(ids),
                )
            )
        self._add_watched_actions(menu, video_ids, records)
        menu.addAction(
            Action(
                FluentIcon.DELETE,
                tr(
                    f"Delete selected records ({len(video_ids)})",
                    f"删除选中的历史记录（{len(video_ids)}）",
                    f"選択した履歴を削除（{len(video_ids)}）",
                ),
                self,
                triggered=lambda _checked=False, ids=tuple(video_ids): self._remove_records(ids),
            )
        )
        menu.exec(global_position)

    def _add_watched_actions(self, menu: RoundMenu, video_ids: list[str], records: list[dict]):
        unwatched = [vid for vid, record in zip(video_ids, records) if not str(record.get("watched_at", "") or "")]
        watched = [vid for vid in video_ids if vid not in unwatched]
        if unwatched:
            menu.addAction(Action(
                FluentIcon.ACCEPT,
                tr(f"Mark as watched ({len(unwatched)})", f"标记为已观看（{len(unwatched)}）", f"視聴済みにする（{len(unwatched)}）"),
                self,
                triggered=lambda _checked=False, ids=tuple(unwatched): download_manager.set_videos_watched(list(ids), True),
            ))
        if watched:
            menu.addAction(Action(
                FluentIcon.HIDE,
                tr(f"Mark as unwatched ({len(watched)})", f"标记为未观看（{len(watched)}）", f"未視聴にする（{len(watched)}）"),
                self,
                triggered=lambda _checked=False, ids=tuple(watched): download_manager.set_videos_watched(list(ids), False),
            ))

    def _add_single_record_actions(self, menu: RoundMenu, video_id: str, record: dict):
        file_path = str(record.get("file_path", "") or "")
        file_exists = bool(file_path and os.path.isfile(file_path))
        menu.addAction(
            Action(
                FluentIcon.HISTORY,
                tr("Open video page", "打开视频页面", "動画ページを開く"),
                self,
                triggered=lambda _checked=False, item_id=video_id: self._open_video_url(item_id),
            )
        )
        if file_exists:
            menu.addAction(
                Action(
                    FluentIcon.FOLDER,
                    tr("Open folder", "打开文件夹", "フォルダーを開く"),
                    self,
                    triggered=lambda _checked=False, item_id=video_id: self._open_record(
                        item_id, open_file=False
                    ),
                )
            )
            menu.addAction(
                Action(
                    FluentIcon.DOCUMENT,
                    tr("Open file", "打开文件", "ファイルを開く"),
                    self,
                    triggered=lambda _checked=False, item_id=video_id: self._open_record(
                        item_id, open_file=True
                    ),
                )
            )
            menu.addAction(
                Action(
                    FluentIcon.SETTING,
                    tr("Rename file", "重命名文件", "ファイル名を変更"),
                    self,
                    triggered=lambda _checked=False, item_id=video_id: self._rename_record(item_id),
                )
            )

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
        if not item or item.text() == "-":  # "-" marks an action that is not available for this row
            return
        action = self._ACTION_BY_COLUMN.get(column, "")
        video_id = self._video_id_at(row)
        if not action or not video_id:
            return
        if action == "open_url":
            self._open_video_url(video_id, url=_video_url(video_id))
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
        self._preview_selected()

    def _preview_selected(self):
        video_id = self._selected_video_id()
        if video_id:
            title = str(self._records_by_id.get(video_id, {}).get("title", "") or "")
            signal_bus.video_preview_requested.emit(video_id, title, "")

    def _selected_video_id(self) -> str:
        selected = self._selected_video_ids()
        return selected[0] if selected else ""
