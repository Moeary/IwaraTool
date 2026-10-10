"""Load, sort, filter and render the subscription source table."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QTableWidgetItem

from qfluentwidgets import FluentIcon

from ...config import app_config
from ...i18n import tr
from .helpers import (
    _source_origin_color,
    _source_origin_label,
    _source_search_text,
    _source_sort_key,
    _source_type_label,
    _source_url,
)
from ..media_card import read_pixmap
from .components import SubscriptionAvatarWorker, _SOURCE_SORT_FIELDS
from .item_queries import is_feed_source
from ..theme import qcolor
from .page_dependencies import download_manager


class SubscriptionSourceListMixin:
    """Load, sort, filter and render the subscription source table."""

    def _on_source_sort_changed(self, index: int):
        if not hasattr(self, "_source_sort_combo"):
            return
        field = self._source_sort_combo.itemData(index)
        field = str(field or "title").strip().casefold()
        if field not in _SOURCE_SORT_FIELDS:
            field = "title"
        self._source_sort_field = field
        app_config.set_ui_value("subscription_source_sort_field_v1", field)
        self._apply_source_filters()

    def _toggle_source_sort_direction(self):
        self._source_sort_desc = not self._source_sort_desc
        app_config.set_ui_value(
            "subscription_source_sort_desc_v1",
            "1" if self._source_sort_desc else "0",
        )
        self._update_source_sort_direction_button()
        self._apply_source_filters()

    def _update_source_sort_direction_button(self):
        button = getattr(self, "_source_sort_direction_btn", None)
        if button is None:
            return
        button.setIcon(FluentIcon.DOWN if self._source_sort_desc else FluentIcon.UP)
        button.setToolTip(
            tr(
                "Descending order" if self._source_sort_desc else "Ascending order",
                "倒序排列" if self._source_sort_desc else "正序排列",
                "降順" if self._source_sort_desc else "昇順",
            )
        )

    def _fetch_sources(self) -> list[dict[str, Any]]:
        """Authors and playlists; the old account-feed source lives on Home now."""

        return [s for s in download_manager.get_subscription_sources() if not is_feed_source(s)]

    def _load_sources(self):
        previous_source_id = self._selected_source_id()
        self._all_sources = self._fetch_sources()
        self._apply_source_filters(preferred_source_id=previous_source_id)

    def _refresh_sources_keep_current_items(self):
        source_id = self._current_source_id
        self._all_sources = self._fetch_sources()
        self._apply_source_filters(preferred_source_id=source_id)

    def _apply_source_filters(self, *_args, preferred_source_id: int | None = None):
        selected_source_id = preferred_source_id if preferred_source_id is not None else self._selected_source_id()
        query = self._source_search_edit.text().strip().casefold() if hasattr(self, "_source_search_edit") else ""
        sources = sorted(
            self._all_sources,
            key=lambda source: _source_sort_key(
                source,
                getattr(self, "_source_sort_field", "title"),
            ),
            reverse=bool(getattr(self, "_source_sort_desc", False)),
        )
        if query:
            sources = [source for source in sources if query in _source_search_text(source)]
        self._sources = sources
        self._render_sources()
        self.sources_changed.emit()
        if selected_source_id is not None and self._select_source_id(selected_source_id):
            self._load_items(selected_source_id)
            return
        if self._sources:
            first_source_id = int(self._sources[0].get("id", 0) or 0)
            self._source_table.blockSignals(True)
            self._source_table.clearSelection()
            self._source_table.selectRow(0)
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
        self._source_table.clearSelection()
        self._source_table.selectRow(selected_row)
        self._source_table.setCurrentCell(selected_row, self._SRC_STATE)
        self._source_table.blockSignals(False)
        item = self._source_table.item(selected_row, self._SRC_STATE)
        if item is not None:
            self._source_table.scrollToItem(item)
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
            # Selecting a newly inserted source happens before its cells are
            # populated. Restore that selection once the batched render ends,
            # while respecting any selection made during rendering.
            if self._current_source_id is not None and self._selected_source_id() is None:
                self._select_source_id(self._current_source_id)

    def _render_source_row(self, row: int, source: dict[str, Any]):
        source_id = int(source.get("id", 0) or 0)
        source_url = _source_url(source)
        origin_label = _source_origin_label(source)
        avatar_item = self._make_source_avatar_item(source_id, source)
        self._source_table.setItem(row, self._SRC_AVATAR, avatar_item)
        values = {
            self._SRC_STATE: origin_label,
            self._SRC_TYPE: _source_type_label(str(source.get("source_type", "") or "")),
            self._SRC_TITLE: str(source.get("title", "") or ""),
            self._SRC_NEW: str(source.get("new_count", 0) or 0),
            self._SRC_UNDOWNLOADED: str(source.get("undownloaded_count", 0) or 0),
            self._SRC_ITEMS: str(source.get("item_count", 0) or 0),
            self._SRC_CHECKED: str(source.get("last_checked_at", "") or ""),
            self._SRC_CREATED: str(source.get("created_at", "") or ""),
            self._SRC_KEY: str(source.get("source_key", "") or ""),
        }
        for col, value in values.items():
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            item.setToolTip(value)
            if col == self._SRC_STATE:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(origin_label)
                item.setForeground(QColor(_source_origin_color(source)))
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
            pixmap = read_pixmap(avatar_path, 256)
            if not pixmap.isNull():
                size = self._source_table.iconSize()
                scaled = pixmap.scaled(
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                item.setIcon(QIcon(scaled))
                item.setToolTip(title)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                return item
        item.setText((title[:1] or "?").upper())
        font = item.font()
        font.setPointSize(max(14, font.pointSize() + 4))
        item.setFont(font)
        item.setToolTip(title or tr("No avatar cached yet", "头像尚未缓存", "アバター未保存"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(qcolor("text_secondary"))
        return item

    def _start_avatar_worker_for_missing_sources(self):
        if self._shutting_down:
            return
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
        self._avatar_worker.finished.connect(self._on_avatar_worker_finished)
        self._avatar_worker.start()

    def _on_avatar_ready(self, source_id: int, avatar_url: str, avatar_path: str):
        self._overview.set_avatar(source_id, avatar_path)
        self._source_view.set_avatar(source_id, avatar_path)
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
        worker = self._avatar_worker
        self._avatar_worker = None
        if worker is not None:
            worker.deleteLater()
        if self._shutting_down:
            return
        self._start_avatar_worker_for_missing_sources()
