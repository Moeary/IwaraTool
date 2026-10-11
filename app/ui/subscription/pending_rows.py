"""Highlight items that were just sent to the download queue."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor

from .page_dependencies import isDarkTheme


class SubscriptionPendingRowsMixin:
    """Highlight items that were just sent to the download queue."""

    @staticmethod
    def _pending_item_brush() -> QBrush:
        return QBrush(QColor("#1f5f3d" if isDarkTheme() else "#c6efce"))

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
