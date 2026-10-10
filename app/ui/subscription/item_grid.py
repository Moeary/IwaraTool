"""Cover-grid view of a source's items and its thumbnail loading."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QListWidgetItem

from ...config import app_config
from ...i18n import tr
from .helpers import _date_only, _ellipsize, _item_state_text
from ..media.card_size import CARD_WIDTH_MIN
from ..media.pixmaps import PixmapLRU, read_pixmap
from .components import (
    SubscriptionThumbnailWorker,
    _DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    _MAX_COVER_DOWNLOAD_CONCURRENCY,
    _MAX_SUBSCRIPTION_GRID_COLUMNS,
    _grid_text_height,
)
from ..theme import qcolor


class SubscriptionItemGridMixin:
    """Cover-grid view of a source's items and its thumbnail loading."""

    def _schedule_thumbnail_grid_update(self):
        """Run one more layout pass after Qt has committed the new viewport size."""

        if self._thumbnail_grid_resize_pending:
            return
        self._thumbnail_grid_resize_pending = True
        QTimer.singleShot(0, self._run_scheduled_thumbnail_grid_update)

    def _run_scheduled_thumbnail_grid_update(self):
        self._thumbnail_grid_resize_pending = False
        self._update_thumbnail_grid()

    def _update_thumbnail_grid(self):
        if not hasattr(self, "_thumbnail_list"):
            return
        viewport_width = self._thumbnail_list.viewport().width()
        if viewport_width <= 0:
            return
        spacing = 8
        scrollbar_reserve = max(
            8,
            int(self._thumbnail_list.verticalScrollBar().sizeHint().width()) - 1,
        )
        available_width = max(1, viewport_width - scrollbar_reserve)
        # As many columns as fit at the chosen cover size.
        columns = max(
            1,
            min(
                _MAX_SUBSCRIPTION_GRID_COLUMNS,
                (available_width + spacing) // (self._item_card_min_width + spacing),
            ),
        )
        card_width = max(
            40,
            (available_width - spacing * (columns - 1)) // columns,
        )
        # The cover is the visual body of a card.  Match it to the item cell so
        # every column shares one exact left edge; the 1px allowance is for the
        # selection border rather than an arbitrary visual inset.
        image_width = max(32, card_width - 2)
        image_height = max(32, round(image_width * 9 / 16))
        text_height = _grid_text_height(
            self._thumbnail_list,
            max(40, card_width - 2),
            fallback_lines=4,
        )
        grid_size = QSize(card_width, image_height + text_height + 14)
        updates_enabled = self._thumbnail_list.updatesEnabled()
        self._thumbnail_list.setUpdatesEnabled(False)
        try:
            icon_size = QSize(image_width, image_height)
            size_changed = self._thumbnail_list.iconSize() != icon_size
            self._thumbnail_list.setIconSize(icon_size)
            if size_changed:
                # QIcon never scales a pixmap up, so covers rendered for a
                # narrower cell must be rebuilt or they stay small and centered.
                self._refresh_thumbnail_icons()
            self._thumbnail_list.setGridSize(grid_size)
            self._thumbnail_list.setSpacing(spacing)
            for index in range(self._thumbnail_list.count()):
                self._thumbnail_list.item(index).setSizeHint(grid_size)
            # QListWidget may defer IconMode placement until the next paint.
            # Force it now so a column-count change never shows a partial card.
            self._thumbnail_list.doItemsLayout()
        finally:
            self._thumbnail_list.setUpdatesEnabled(updates_enabled)
        if updates_enabled:
            self._thumbnail_list.viewport().update()

    def _on_item_view_changed(self, _index: int):
        self._sync_item_grid_controls()
        self._render_items()
        QTimer.singleShot(0, self._update_thumbnail_grid)

    def _sync_item_grid_controls(self):
        cover_mode = (
            hasattr(self, "_item_view_combo")
            and self._item_view_combo.currentIndex() == 1
        )
        if hasattr(self, "_item_card_size"):
            self._item_card_size.setVisible(cover_mode)
        if hasattr(self, "_item_columns_btn"):
            self._item_columns_btn.setVisible(not cover_mode)

    def _on_item_card_size_changed(self, width: int):
        self._item_card_min_width = max(CARD_WIDTH_MIN, int(int(width) * 0.8))
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()

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
                label = _ellipsize(title, 56)
                if author:
                    label += f"\n{_ellipsize(author, 44)}"
                if published:
                    label += f"\n{published}"
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
                if video_id in self._pending_video_ids:
                    list_item.setBackground(self._pending_item_brush())
                self._thumbnail_items_by_video_id.setdefault(video_id, []).append(list_item)
        finally:
            self._thumbnail_list.setUpdatesEnabled(True)
        # Recalculate after the real captions are present; long titles may
        # wrap to an extra line and should determine the row height.
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()
        self._start_thumbnail_worker_for_visible_items()

    def _refresh_thumbnail_icons(self):
        """Re-render every loaded cover at the list's current icon size."""

        paths = {
            str(entry.get("video_id", "")): str(entry.get("thumbnail_path", "") or "")
            for entry in self._visible_items
        }
        placeholder = self._thumbnail_placeholder_icon()
        for video_id, list_items in self._thumbnail_items_by_video_id.items():
            path = paths.get(video_id, "")
            icon = self._thumbnail_icon(path) if path and os.path.isfile(path) else placeholder
            for list_item in list_items:
                list_item.setIcon(icon)

    def _thumbnail_placeholder_icon(self) -> QIcon:
        pixmap = QPixmap(self._thumbnail_list.iconSize())
        pixmap.fill(qcolor("placeholder"))
        return QIcon(pixmap)

    def _thumbnail_icon(self, path: str) -> QIcon:
        # Decoded covers (at the size shown, not the file's) are kept up to a byte
        # budget so a grid resize can re-scale them cheaply.
        size = self._thumbnail_list.iconSize()
        want = max(240, round(size.width() * 1.25))
        cache = self.__dict__.setdefault("_thumbnail_pixmaps", PixmapLRU(48 * 1024 * 1024))
        small = self.__dict__.setdefault("_thumbnail_small_sources", set())
        pixmap = cache.get(path)
        if pixmap is None or (pixmap.width() < want and path not in small):
            pixmap = read_pixmap(path, want)
            if not pixmap.isNull():
                if pixmap.width() < want:
                    small.add(path)  # the file itself is that small
                cache[path] = pixmap
        if pixmap is None or pixmap.isNull():
            return self._thumbnail_placeholder_icon()
        scaled = pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - size.width()) // 2)
        y = max(0, (scaled.height() - size.height()) // 2)
        return QIcon(scaled.copy(x, y, size.width(), size.height()))

    @staticmethod
    def _cover_download_concurrency() -> int:
        try:
            value = int(
                app_config.get_ui_value(
                    "cover_download_workers_v1",
                    _DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
                )
                or _DEFAULT_COVER_DOWNLOAD_CONCURRENCY
            )
        except (TypeError, ValueError):
            value = _DEFAULT_COVER_DOWNLOAD_CONCURRENCY
        return max(1, min(_MAX_COVER_DOWNLOAD_CONCURRENCY, value))

    def _start_thumbnail_worker_for_visible_items(self, *, force: bool = False) -> bool:
        if self._shutting_down:
            return False
        if self._thumbnail_worker and self._thumbnail_worker.isRunning():
            if force:
                self._thumbnail_force_refresh_pending = True
            return True
        requests: list[tuple[str, str]] = []
        for item_data in self._visible_items:
            video_id = str(item_data.get("video_id", "") or "").strip()
            thumbnail_url = str(item_data.get("thumbnail_url", "") or "").strip()
            thumbnail_path = str(item_data.get("thumbnail_path", "") or "").strip()
            if (
                not video_id
                or (not force and thumbnail_path and os.path.isfile(thumbnail_path))
                or (not force and video_id in self._thumbnail_requested_video_ids)
                or (force and video_id in self._thumbnail_force_refresh_ids)
            ):
                continue
            requests.append((video_id, thumbnail_url))
            if len(requests) >= 80:
                break
        if not requests:
            if force:
                self._thumbnail_force_refresh_ids.clear()
            return False
        self._thumbnail_requested_video_ids.update(video_id for video_id, _url in requests)
        if force:
            self._thumbnail_force_refresh_ids.update(video_id for video_id, _url in requests)
        self._thumbnail_worker = SubscriptionThumbnailWorker(
            requests,
            force=force,
            concurrency=self._cover_download_concurrency(),
        )
        self._thumbnail_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumbnail_worker.finished.connect(self._on_thumbnail_worker_finished)
        self._thumbnail_worker.start()
        return True

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
        force_batch = bool(worker and getattr(worker, "_force", False))
        if worker:
            worker.deleteLater()
        force_refresh = self._thumbnail_force_refresh_pending
        self._thumbnail_force_refresh_pending = False
        if force_refresh or force_batch:
            if not self._start_thumbnail_worker_for_visible_items(force=True):
                self._thumbnail_force_refresh_ids.clear()
        elif (
            hasattr(self, "_item_view_combo")
            and self._item_view_combo.currentIndex() == 1
        ):
            self._start_thumbnail_worker_for_visible_items()
