"""Render search results as grid cards and table rows."""

from __future__ import annotations


from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QListWidgetItem, QTableWidgetItem

from ...core.search import SearchAuthor, SearchPlaylist, SearchVideo
from ...i18n import tr
from .download_status import iwara_history_id
from .widgets import _format_count, _format_duration, _short_text
from ..theme import qcolor

from .results_layout import _DEFAULT_GRID_HEIGHT


class SearchResultItemsMixin:
    """Render search results as grid cards and table rows."""

    def _render_results(self):
        self._request_download_status_refresh()
        self._results.setUpdatesEnabled(False)
        try:
            self._results.clear()
            self._results_table.clearContents()
            self._results_table.setRowCount(0)
            self._item_by_key.clear()
            scope = str(self._scope_combo.currentData() or "videos")
            list_mode = self._is_list_view()
            if scope == "authors":
                for author in self._all_authors:
                    self._add_author_item(author)
            elif self._all_playlists:
                for playlist in self._all_playlists:
                    self._add_playlist_item(playlist)
            elif list_mode:
                self._render_video_table()
            else:
                for video in self._all_videos:
                    self._add_video_item(video)
            self._sync_view_controls()
            self._resize_grid()
            self._fit_results_table_last_column()
            self._sync_selection_buttons()
        finally:
            self._results.setUpdatesEnabled(True)
            self._results.doItemsLayout()
            self._results.viewport().update()
        self._schedule_grid_resize()

    def _render_video_table(self):
        self._results_table.setUpdatesEnabled(False)
        try:
            self._results_table.setRowCount(len(self._all_videos))
            for row, video in enumerate(self._all_videos):
                values = [
                    self._result_field_value(video, key)
                    for key in self._RESULT_COLUMN_KEYS
                ]
                data = {
                    "kind": "video",
                    "key": f"video:{video.video_id}",
                    "data": video,
                }
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(self._DATA_ROLE, data)
                    item.setToolTip(value or "—")
                    if column in {3, 4, 5}:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self._results_table.setItem(row, column, item)
        finally:
            self._results_table.setUpdatesEnabled(True)

    def _update_video_presentation(self, video: SearchVideo):
        """Update one hydrated result without rebuilding the whole page."""

        self._request_download_status_refresh()
        key = f"video:{video.video_id}"
        data = {"kind": "video", "key": key, "data": video}
        if self._is_list_view():
            for row in range(self._results_table.rowCount()):
                first_item = self._results_table.item(row, 0)
                first_data = first_item.data(self._DATA_ROLE) if first_item else None
                if not isinstance(first_data, dict) or first_data.get("key") != key:
                    continue
                values = [self._result_field_value(video, field) for field in self._RESULT_COLUMN_KEYS]
                for column, value in enumerate(values):
                    item = self._results_table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self._results_table.setItem(row, column, item)
                    item.setText(value)
                    item.setData(self._DATA_ROLE, data)
                    item.setToolTip(value or "—")
                self._sync_selection_buttons()
                return

        item = self._item_by_key.get(key)
        if item is None:
            return
        item.setData(self._DATA_ROLE, data)
        item.setText(self._video_card_text(video))
        item.setToolTip(
            f"{video.title}\n"
            f"{self._download_status_text(video)}\n"
            f"Iwara: {video.iwara_url or 'resolving…'}\n"
            f"Source: {video.raw.get('oreno3d_url') or video.source_url}"
        )
        item.setSizeHint(getattr(self, "_grid_item_size", QSize(300, _DEFAULT_GRID_HEIGHT)))
        if key in self._image_path_by_key:
            item.setIcon(self._image_icon(self._image_path_by_key[key]))
        self._resize_grid()
        self._sync_selection_buttons()

    def _update_download_status_presentation(self, video_ids: set[str]):
        """Refresh local state without rebuilding rows or losing selection."""

        if not video_ids:
            return
        if self._is_list_view():
            column = self._RESULT_COLUMN_KEYS.index("download_status")
            for row in range(self._results_table.rowCount()):
                first_item = self._results_table.item(row, 0)
                data = first_item.data(self._DATA_ROLE) if first_item else None
                video = data.get("data") if isinstance(data, dict) else None
                if not isinstance(video, SearchVideo) or iwara_history_id(video) not in video_ids:
                    continue
                item = self._results_table.item(row, column)
                if item is not None:
                    text = self._download_status_text(video)
                    item.setText(text)
                    item.setToolTip(text)
            return
        self._results.setUpdatesEnabled(False)
        try:
            for video in self._all_videos:
                if iwara_history_id(video) not in video_ids:
                    continue
                item = self._item_by_key.get(f"video:{video.video_id}")
                if item is None:
                    continue
                item.setText(self._video_card_text(video))
                item.setToolTip(
                    f"{video.title}\n"
                    f"{self._download_status_text(video)}\n"
                    f"Iwara: {video.iwara_url or 'resolving…'}\n"
                    f"Source: {video.raw.get('oreno3d_url') or video.source_url}"
                )
            self._resize_grid()
        finally:
            self._results.setUpdatesEnabled(True)
            self._results.viewport().update()

    def _result_field_value(self, video: SearchVideo, key: str) -> str:
        if key == "download_status":
            return self._download_status_text(video)
        if key == "iwara_id":
            if video.download_video_id:
                return video.download_video_id
            return "…" if video.source_kind == "oreno3d" else "—"
        if key == "title":
            return video.title or video.video_id
        if key == "author":
            return video.author_name or video.author_username or "—"
        if key == "views":
            return _format_count(video.views)
        if key == "likes":
            return _format_count(video.likes)
        if key == "comments":
            if video.comments or video.source_kind != "oreno3d":
                return _format_count(video.comments)
            return "—"
        if key == "duration":
            return _format_duration(video.duration) if video.duration else "—"
        if key == "published":
            return video.published_at[:19] if video.published_at else "—"
        if key == "tags":
            return ", ".join(video.tags) or "—"
        if key == "iwara_url":
            if video.iwara_url:
                return video.iwara_url
            if video.download_video_id:
                return f"https://www.iwara.tv/video/{video.download_video_id}"
            return "解析中…" if video.source_kind == "oreno3d" else "—"
        if key == "source_url":
            return video.source_url or "—"
        if key == "source":
            return tr("Oreno3D", "Oreno3D", "Oreno3D") if video.source_kind == "oreno3d" else "Iwara"
        return "—"

    def _video_card_text(self, video: SearchVideo) -> str:
        date_text = video.published_at[:10] if video.published_at else "—"
        author = f"@{video.author_username}" if video.author_username else tr("Unknown author", "未知作者", "作者不明")
        stats = tr(
            f"{_format_count(video.views)} views · {_format_count(video.likes)} likes",
            f"{_format_count(video.views)} 次观看 · {_format_count(video.likes)} 喜欢",
            f"{_format_count(video.views)} 再生 · {_format_count(video.likes)} いいね",
        )
        duration = _format_duration(video.duration) if video.duration else "—"
        return (
            f"{_short_text(video.title, 46)}\n"
            f"{author}\n"
            f"{stats}\n"
            f"{duration}\n"
            f"{date_text}\n"
            f"{self._download_status_text(video)}"
        )

    def _add_video_item(self, video: SearchVideo):
        key = f"video:{video.video_id}"
        item = QListWidgetItem(self._placeholder_icon("video"), self._video_card_text(video))
        item.setData(self._DATA_ROLE, {"kind": "video", "key": key, "data": video})
        item.setForeground(qcolor("text"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        item.setToolTip(
            f"{video.title}\n"
            f"{self._download_status_text(video)}\n"
            f"Iwara: {video.iwara_url or 'resolving…'}\n"
            f"Source: {video.source_url}"
        )
        item.setSizeHint(getattr(self, "_grid_item_size", QSize(300, _DEFAULT_GRID_HEIGHT)))
        if key in self._image_path_by_key:
            item.setIcon(self._image_icon(self._image_path_by_key[key]))
        self._results.addItem(item)
        self._item_by_key[key] = item

    def _add_author_item(self, author: SearchAuthor):
        key = f"author:{author.author_id}"
        if author.video_count or not author.joined_at:
            detail = tr(
                f"{_format_count(author.video_count)} videos",
                f"{_format_count(author.video_count)} 个视频",
                f"動画 {_format_count(author.video_count)} 件",
            )
        else:
            # Native user search rows carry no work count; show when the
            # account was created instead of a misleading "0 videos".
            joined = author.joined_at[:10]
            detail = tr(f"Joined {joined}", f"注册于 {joined}", f"登録日 {joined}")
        text = f"{_short_text(author.name or author.username, 42)}\n@{author.username}\n{detail}"
        if author.bio:
            text += f"\n{_short_text(author.bio, 64)}"
        item = QListWidgetItem(self._placeholder_icon("author"), text)
        item.setData(self._DATA_ROLE, {"kind": "author", "key": key, "data": author})
        item.setForeground(qcolor("text"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        item.setToolTip(f"{author.username}\n{author.source_url}")
        item.setSizeHint(QSize(300, _DEFAULT_GRID_HEIGHT))
        if key in self._image_path_by_key:
            item.setIcon(self._image_icon(self._image_path_by_key[key]))
        self._results.addItem(item)
        self._item_by_key[key] = item

    def _add_playlist_item(self, playlist: SearchPlaylist):
        key = f"playlist:{playlist.playlist_id}"
        count = tr(
            f"{_format_count(playlist.video_count)} videos",
            f"{_format_count(playlist.video_count)} 个视频",
            f"動画 {_format_count(playlist.video_count)} 件",
        )
        author = (
            f"@{playlist.author_username}"
            if playlist.author_username
            else tr("Unknown author", "未知作者", "作者不明")
        )
        text = f"{_short_text(playlist.title, 46)}\n{author}\n{count}"
        item = QListWidgetItem(self._placeholder_icon("playlist"), text)
        item.setData(self._DATA_ROLE, {"kind": "playlist", "key": key, "data": playlist})
        item.setForeground(qcolor("text"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        item.setToolTip(
            f"{playlist.title}\n{playlist.source_url}\n"
            + tr(
                "Double-click to list its videos here",
                "双击在此列出其中的视频",
                "ダブルクリックでここに動画一覧を表示",
            )
        )
        item.setSizeHint(getattr(self, "_grid_item_size", QSize(300, _DEFAULT_GRID_HEIGHT)))
        if key in self._image_path_by_key:
            item.setIcon(self._image_icon(self._image_path_by_key[key]))
        self._results.addItem(item)
        self._item_by_key[key] = item
