"""Open, preview and click search results."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QTableWidgetItem

from ...core.search import IWARA_IMAGE_SOURCE_KIND, SearchAuthor, SearchPlaylist, SearchVideo
from ...i18n import tr
from .result_authors import (
    _author_source_info,
    _author_subscription_target,
    _extract_iwara_video_id,
    _oreno3d_video_url,
)
from ..navigation import record_navigation
from .page_dependencies import signal_bus


class SearchOpeningMixin:
    """Open, preview and click search results."""

    def _open_selected(self):
        for value in self._selected_data():
            data = value.get("data")
            if isinstance(data, SearchVideo):
                self._open_video(data)
            elif isinstance(data, (SearchAuthor, SearchPlaylist)):
                url = str(data.source_url or "").strip()
                if url:
                    webbrowser.open(url)

    def _open_playlist_videos(self, playlist: SearchPlaylist):
        """List one playlist's videos in place of the playlist search results."""

        record_navigation(self)
        self._clear_author_navigation()
        self._set_combo_data(self._scope_combo, "playlists")
        self._keyword_edit.setText(playlist.source_url)
        self._start_search()

    @staticmethod
    def _preview_video_id(video: SearchVideo) -> str:
        """Iwara id to play, or empty when an Oreno3D result is not resolved yet."""

        return str(
            video.download_video_id
            or _extract_iwara_video_id(video.iwara_url)
            or (video.video_id if video.source_kind == "iwara" else "")
            or ""
        )

    def _preview_video(self, video: SearchVideo):
        """Play a local copy, or stream the video in the built-in player."""

        if not video.downloadable:
            self._open_video(video)  # image posts have nothing to play
            return
        video_id = self._preview_video_id(video)
        if video_id:
            signal_bus.video_preview_requested.emit(video_id, video.title, "")
            return
        if video.source_kind == "oreno3d":
            self._pending_preview_video_ids.add(video.video_id)
            self._start_oreno_link_resolution([video], priority=True, hydrate_metadata=False)
            self._status_label.setText(
                tr("Resolving the Iwara ID…", "正在解析 Iwara ID…", "Iwara IDを取得中…")
            )
            return
        self._open_video(video)

    def _preview_selected(self):
        for value in self._selected_data():
            data = value.get("data")
            if isinstance(data, SearchVideo):
                self._preview_video(data)

    def _open_video(self, video: SearchVideo):
        """Open the final Iwara page; Oreno3D is never used as a fallback URL."""

        if video.source_kind == "oreno3d":
            iwara_url = str(video.iwara_url or "").strip()
            if _extract_iwara_video_id(iwara_url):
                webbrowser.open(iwara_url)
                return
            self._pending_open_video_ids.add(video.video_id)
            # A click is a priority request.  It gets a dedicated one-item
            # worker and only resolves the ID, so the browser opens before the
            # rest of the page's metadata can finish loading.
            self._start_oreno_link_resolution(
                [video],
                priority=True,
                hydrate_metadata=False,
            )
            self._status_label.setText(
                tr(
                    "Resolving the Iwara ID…",
                    "正在解析 Iwara ID…",
                    "Iwara IDを取得中…",
                )
            )
            return
        url = str(video.iwara_url or video.source_url or "").strip()
        if url:
            webbrowser.open(url)

    def _open_oreno3d_video_page(self, video: SearchVideo):
        """Open the bridge source directly for diagnosing Oreno3D records."""

        url = _oreno3d_video_url(video)
        if not url:
            self._show_warning(
                tr(
                    "This result has no Oreno3D source URL",
                    "当前结果没有 Oreno3D 来源链接",
                    "この結果にはOreno3D元URLがありません",
                )
            )
            return
        webbrowser.open(url)

    def _open_author_page_for_result(self):
        """Open the author inside the app when an Iwara account can be identified.

        The in-app page shows whether the author is a local subscription or
        followed on the website and lets you subscribe.  Without a usable
        Iwara account this falls back to the source's own author page.
        """

        values = self._selected_data()
        if len(values) == 1:
            data = values[0].get("data")
            if isinstance(data, (SearchAuthor, SearchVideo)):
                target = self._author_navigation_target(data)
                if target:
                    signal_bus.author_page_requested.emit(target)
                    return
        self._open_source_author_page_for_result(prefer_app=True)

    def _open_source_author_page_for_result(self, *, prefer_app: bool = False):
        """Open the result's author page on its source site in the browser.

        ``prefer_app``: if resolving the source turns up an Iwara account, show
        the in-app author page instead of the website.
        """

        self._open_author_prefers_app = prefer_app

        values = self._selected_data()
        if len(values) != 1:
            self._show_warning(
                tr(
                    "Select one result to open its author page",
                    "请只选择一个结果后打开作者页",
                    "作者ページを開くには1件だけ選択してください",
                )
            )
            return
        data = values[0].get("data")
        if isinstance(data, SearchAuthor):
            url = str(data.source_url or "").strip()
            if url:
                webbrowser.open(url)
            return
        if not isinstance(data, SearchVideo):
            return
        source_url, source_origin = _author_source_info(data)
        if source_url:
            webbrowser.open(source_url)
            return
        if source_origin == "oreno3d":
            if data.video_id in self._pending_open_author_video_ids:
                return
            self._pending_open_author_video_ids.add(data.video_id)
            self._start_oreno_author_resolution(data)
            self._status_label.setText(
                tr(
                    "Resolving the Oreno3D author page…",
                    "正在解析 Oreno3D 作者页…",
                    "Oreno3D作者ページを解析中…",
                )
            )
            return
        target = _author_subscription_target(data)
        if target:
            webbrowser.open(f"https://www.iwara.tv/profile/{target[0]}")

    def _open_iwara_author_page_for_result(self):
        values = self._selected_data()
        if len(values) != 1:
            return
        data = values[0].get("data")
        target = self._author_navigation_target(data)
        if target:
            webbrowser.open(f"https://www.iwara.tv/profile/{target[0]}")

    @classmethod
    def _detail_target(cls, video: SearchVideo) -> tuple[str, str] | None:
        """``(kind, Iwara id)`` for the Home detail view, or ``None`` while unknown.

        A resolved Oreno3D card turns into ``source_kind == "iwara"`` but keeps
        its ``oreno3d:<n>`` card id; the Iwara id is the resolved one.
        """

        if video.source_kind == IWARA_IMAGE_SOURCE_KIND:
            return "image", video.video_id
        video_id = cls._preview_video_id(video)
        return ("video", video_id) if video_id else None

    def _show_media_detail(self, video: SearchVideo):
        target = self._detail_target(video)
        if target:
            signal_bus.media_detail_requested.emit(*target)

    def _perform_result_click(self, action: str, item):
        """One Settings click action on a result (``item``: grid item or the row's first cell)."""

        from ..click_dispatch import ACTION_DETAIL, ACTION_PLAY, ACTION_SELECT

        if action == ACTION_SELECT:
            self._toggle_result_selected(item)
            return
        value = item.data(self._DATA_ROLE)
        data = value.get("data") if isinstance(value, dict) else None
        if action == ACTION_DETAIL:
            self._open_result_detail(data)
        elif action == ACTION_PLAY:
            if isinstance(data, SearchVideo) and data.source_kind != IWARA_IMAGE_SOURCE_KIND:
                self._preview_video(data)
            else:
                self._open_result_detail(data)  # nothing to play: show it instead

    def _toggle_result_selected(self, item):
        if isinstance(item, QTableWidgetItem):
            table = item.tableWidget()
            if table is None:
                return
            flags = QItemSelectionModel.SelectionFlag.Toggle | QItemSelectionModel.SelectionFlag.Rows
            table.selectionModel().select(table.indexFromItem(item), flags)
        else:
            item.setSelected(not item.isSelected())
        self._sync_selection_buttons()

    def _open_result_detail(self, data):
        """The in-app page of a result: the post, the playlist's videos or the author."""

        if isinstance(data, SearchPlaylist):
            self._open_playlist_videos(data)
        elif isinstance(data, SearchAuthor):
            target = self._author_navigation_target(data)
            if target:
                signal_bus.author_page_requested.emit(target)
            elif str(data.source_url or "").strip():
                webbrowser.open(str(data.source_url).strip())
        elif isinstance(data, SearchVideo):
            target = self._detail_target(data)
            if target is None and data.source_kind == "oreno3d":
                # An Oreno3D card learns its Iwara id first.
                self._pending_detail_video_ids.add(data.video_id)
                self._start_oreno_link_resolution([data], priority=True, hydrate_metadata=False)
                self._status_label.setText(
                    tr("Resolving the Iwara ID…", "正在解析 Iwara ID…", "Iwara IDを取得中…")
                )
                return
            if target:
                signal_bus.media_detail_requested.emit(*target)

    def _activate_result(self, item):
        """Enter on a result: its detail page (mouse clicks follow the Settings actions)."""

        value = item.data(self._DATA_ROLE)
        if isinstance(item, QTableWidgetItem) and not isinstance(value, dict):
            table = item.tableWidget()
            first = table.item(item.row(), 0) if table is not None else None
            value = first.data(self._DATA_ROLE) if first is not None else None
        if isinstance(value, dict):
            self._open_result_detail(value.get("data"))
