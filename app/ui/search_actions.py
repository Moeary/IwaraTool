"""User actions for search results, kept separate from page layout."""
from __future__ import annotations

import sys
import webbrowser as _default_webbrowser
from typing import Any

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QTableWidgetItem

from qfluentwidgets import (
    Action,
    FluentIcon,
    InfoBar as _DefaultInfoBar,
    InfoBarPosition,
    RoundMenu,
)

from ..core.manager import download_manager as _default_download_manager
from ..core.search import (
    IWARA_IMAGE_SOURCE_KIND,
    SearchAuthor,
    SearchPlaylist,
    SearchVideo,
    normalize_video,
)
from ..i18n import tr
from ..signal_bus import signal_bus as _default_signal_bus
from .search_widgets import (
    _author_source_info,
    _author_subscription_target,
    _extract_iwara_video_id,
    _oreno3d_video_url,
)
from .navigation import record_navigation
from .worker_lifecycle import on_finished
from .search_workers import (
    SearchAuthorProfileWorker,
    SearchIwaraAuthorWorker,
    SearchOrenoAuthorWorker,
    SearchQueueResolveWorker,
)


class _PageDependencyProxy:
    def __init__(self, name: str, fallback: object):
        self.name = name
        self.fallback = fallback

    def __getattr__(self, attribute: str):
        page_module = sys.modules.get("app.ui.search_page")
        target = getattr(page_module, self.name, self.fallback)
        return getattr(target, attribute)


download_manager = _PageDependencyProxy("download_manager", _default_download_manager)
signal_bus = _PageDependencyProxy("signal_bus", _default_signal_bus)
InfoBar = _PageDependencyProxy("InfoBar", _DefaultInfoBar)
webbrowser = _PageDependencyProxy("webbrowser", _default_webbrowser)


class SearchActionsMixin:
    def _sync_selection_buttons(self):
        selected_values = self._selected_data()
        selected = bool(selected_values)
        queueable = any(self._is_queueable(value) for value in selected_values)
        resolving = self._queue_resolve_worker is not None and self._queue_resolve_worker.isRunning()
        self._queue_selected_btn.setEnabled(selected and queueable and not resolving)
        self._open_selected_btn.setEnabled(selected)

    @staticmethod
    def _is_queueable(value: dict[str, Any]) -> bool:
        """Videos and whole playlists can be queued; image posts cannot."""

        data = value.get("data")
        if isinstance(data, SearchPlaylist):
            return True
        return isinstance(data, SearchVideo) and (
            data.source_kind in {"iwara", "oreno3d"} or bool(data.download_video_id)
        )

    def _selected_data(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = []
        if self._is_list_view():
            for index in self._results_table.selectionModel().selectedRows():
                item = self._results_table.item(index.row(), 0)
                value = item.data(self._DATA_ROLE) if item else None
                if isinstance(value, dict):
                    data.append(value)
        else:
            for item in self._results.selectedItems():
                value = item.data(self._DATA_ROLE)
                if isinstance(value, dict):
                    data.append(value)
        return data

    def _queue_selected(self):
        videos: list[SearchVideo] = []
        playlists: list[SearchPlaylist] = []
        for value in self._selected_data():
            data = value.get("data")
            if not self._is_queueable(value):
                continue
            if isinstance(data, SearchPlaylist):
                playlists.append(data)
            elif value.get("kind") == "video" and isinstance(data, SearchVideo):
                videos.append(data)
        if not videos and not playlists:
            if any(
                isinstance(value.get("data"), SearchVideo)
                and value["data"].source_kind == IWARA_IMAGE_SOURCE_KIND
                for value in self._selected_data()
            ):
                self._show_warning(tr(
                    "Image posts cannot be downloaded; open them in the browser instead.",
                    "图片作品暂不支持下载，请在浏览器中打开查看。",
                    "画像投稿はダウンロードできません。ブラウザーで開いてください。",
                ))
                return
            self._show_warning(tr("Select at least one video", "请至少选择一个视频", "動画を1件以上選択してください"))
            return

        rule_id = self._rule_picker.selected_rule_id()
        self._rule_picker.apply_selected(show_notice=False)
        if playlists:
            # The manager expands each playlist URL into its videos, exactly
            # as when the URL is pasted on the download page.
            for playlist in playlists:
                download_manager.add_url(playlist.source_url, rule_id=rule_id)
            InfoBar.success(
                title=tr("Added to queue", "已加入队列", "キューに追加"),
                content=tr(
                    f"Fetching videos from {len(playlists)} playlist(s)",
                    f"正在获取 {len(playlists)} 个播放列表中的视频",
                    f"{len(playlists)} 件のプレイリストから動画を取得中",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            if not videos:
                return

        if any(video.source_kind == "oreno3d" for video in videos):
            if self._queue_resolve_worker is not None and self._queue_resolve_worker.isRunning():
                return
            worker = SearchQueueResolveWorker(
                videos,
                concurrency=self._search_resolution_concurrency(),
            )
            self._queue_resolve_worker = worker
            self._queue_resolve_rule_id = rule_id
            self._queue_selected_btn.setEnabled(False)
            self._status_label.setText(
                tr(
                    "Resolving selected Oreno3D links…",
                    "正在解析选中的 Oreno3D 下载链接…",
                    "選択したOreno3Dリンクを解決中…",
                )
            )
            worker.result_ready.connect(self._on_queue_resolved)
            on_finished(worker, self, self._cleanup_queue_resolve_worker)
            worker.start()
            return

        self._enqueue_video_ids(
            [video.download_video_id or video.video_id for video in videos],
            rule_id=rule_id,
        )

    def _enqueue_video_ids(
        self,
        ids: list[str],
        *,
        skipped: int = 0,
        errors: list[str] | None = None,
        rule_id: str = "",
    ):
        ids = [str(video_id or "").strip() for video_id in ids if str(video_id or "").strip()]
        errors = errors or []
        if not ids:
            message = tr(
                "No selected item has an Iwara download link.",
                "选中的项目没有可用的 Iwara 下载链接。",
                "選択した項目にIwaraダウンロードリンクがありません。",
            )
            if errors:
                message += f" {errors[0]}"
            self._show_warning(message)
            self._sync_selection_buttons()
            return
        selected_rule_id = str(rule_id or self._rule_picker.selected_rule_id())
        accepted = download_manager.enqueue_video_ids(
            ids,
            source_label=tr("Search", "搜索", "検索"),
            rule_id=selected_rule_id,
        )
        suffix = tr(
            f"; skipped {skipped} item(s)" if skipped else "",
            f"；已跳过 {skipped} 个无下载链接的项目" if skipped else "",
            f"；{skipped}件をスキップ" if skipped else "",
        )
        InfoBar.success(
            title=tr("Added to queue", "已加入队列", "キューに追加"),
            content=tr(
                f"Accepted {accepted} video(s){suffix}",
                f"已接受 {accepted} 个视频{suffix}",
                f"{accepted} 件を追加しました{suffix}",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
        if errors:
            self._show_warning(errors[0])

    def _on_queue_resolved(self, result: object):
        if not isinstance(result, dict):
            self._show_warning(tr("Could not resolve selected links", "无法解析选中的链接", "選択したリンクを解決できません"))
            return
        self._enqueue_video_ids(
            list(result.get("ids") or []),
            skipped=int(result.get("skipped") or 0),
            errors=[str(error) for error in result.get("errors") or []],
            rule_id=self._queue_resolve_rule_id,
        )
        self._sync_selection_buttons()

    def _cleanup_queue_resolve_worker(self, worker: SearchQueueResolveWorker):
        if self._queue_resolve_worker is worker:
            self._queue_resolve_worker = None
            self._queue_resolve_rule_id = ""
        worker.deleteLater()
        self._sync_selection_buttons()

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

        from .click_dispatch import ACTION_DETAIL, ACTION_PLAY, ACTION_SELECT

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

    def _show_context_menu(self, position):
        if self._is_list_view():
            target = self._results_table
            item = target.itemAt(position)
            if item is not None:
                target.clearSelection()
                target.selectRow(item.row())
        else:
            target = self._results
            item = target.itemAt(position)
            if item is not None and not item.isSelected():
                target.clearSelection()
                item.setSelected(True)
        values = self._selected_data()
        if not values:
            return
        global_position = target.viewport().mapToGlobal(position)
        menu = RoundMenu(parent=self)
        video_values = [value for value in values if value.get("kind") == "video"]
        author_values = [value for value in values if value.get("kind") == "author"]
        playlist_values = [value for value in values if value.get("kind") == "playlist"]
        if len(video_values) == 1 and len(values) == 1:
            menu.addAction(
                Action(
                    FluentIcon.PLAY,
                    tr("Play / Preview", "播放 / 预览", "再生 / プレビュー"),
                    self,
                    triggered=self._preview_selected,
                )
            )
        if len(video_values) == 1 and len(values) == 1:
            detail_video = video_values[0].get("data")
            if isinstance(detail_video, SearchVideo) and self._detail_target(detail_video):
                menu.addAction(
                    Action(
                        FluentIcon.VIEW,
                        tr("View details", "查看详情", "詳細を表示"),
                        self,
                        triggered=lambda _checked=False, video=detail_video: self._show_media_detail(video),
                    )
                )
        if any(self._is_queueable(value) for value in values):
            menu.addAction(
                Action(
                    FluentIcon.DOWNLOAD,
                    tr("Add to download queue", "加入下载队列", "ダウンロードキューに追加"),
                    self,
                    triggered=self._queue_selected,
                )
            )
        if len(values) == 1 and len(playlist_values) == 1:
            playlist = playlist_values[0].get("data")
            if isinstance(playlist, SearchPlaylist):
                menu.addAction(
                    Action(
                        FluentIcon.VIDEO,
                        tr("View playlist videos", "查看播放列表视频", "プレイリストの動画を表示"),
                        self,
                        triggered=lambda _checked=False, playlist=playlist: self._open_playlist_videos(playlist),
                    )
                )
        if len(values) == 1 and len(video_values) == 1 and not author_values:
            selected_video = video_values[0].get("data")
            if isinstance(selected_video, SearchVideo) and _oreno3d_video_url(selected_video):
                if menu.actions():
                    menu.addSeparator()
                menu.addAction(
                    Action(
                        FluentIcon.VIEW,
                        tr(
                            "Open Oreno3D video page",
                            "打开 Oreno3D 视频页",
                            "Oreno3D動画ページを開く",
                        ),
                        self,
                        triggered=lambda _checked=False, video=selected_video: self._open_oreno3d_video_page(video),
                    )
                )
        if len(values) == 1 and (video_values or author_values):
            if menu.actions():
                menu.addSeparator()
            menu.addAction(
                Action(
                    FluentIcon.PEOPLE,
                    tr("Open author page", "打开作者页", "作者ページを開く"),
                    self,
                    triggered=lambda _checked=False: self._open_author_page_for_result(),
                )
            )
            menu.addAction(
                Action(
                    FluentIcon.GLOBE,
                    tr("Open author page in browser", "在浏览器打开作者页", "作者ページをブラウザーで開く"),
                    self,
                    triggered=lambda _checked=False: self._open_source_author_page_for_result(),
                )
            )
            menu.addAction(
                Action(
                    FluentIcon.SEARCH,
                    tr("View author's works", "查看作者作品", "作者の作品を表示"),
                    self,
                    triggered=lambda _checked=False: self._view_selected_author_works(),
                )
            )
            menu.addAction(
                Action(
                    FluentIcon.PEOPLE,
                    tr("Add subscription and go", "加入订阅并前往", "購読に追加して移動"),
                    self,
                    triggered=lambda _checked=False: self._subscribe_selected_author_and_go(),
                )
            )
            selected_data = values[0].get("data")
            if isinstance(selected_data, SearchVideo):
                target = self._author_navigation_target(selected_data)
                source_url, source_origin = _author_source_info(selected_data)
                if target and source_origin == "oreno3d" and source_url:
                    menu.addAction(
                        Action(
                            FluentIcon.VIEW,
                            tr("Open Iwara author page", "打开 Iwara 作者页", "Iwara作者ページを開く"),
                            self,
                            triggered=lambda _checked=False: self._open_iwara_author_page_for_result(),
                        )
                    )
        author_target = self._selected_author_subscription_target(values)
        if author_target:
            source_url, source_origin = _author_source_info(values[0].get("data")) if len(values) == 1 else ("", "")
            if menu.actions():
                menu.addSeparator()
            menu.addAction(
                Action(
                    FluentIcon.PEOPLE,
                    tr("Favorite author / add subscription", "收藏作者 / 加入订阅", "作者をお気に入り／購読に追加"),
                    self,
                    triggered=lambda _checked=False, target=author_target, url=source_url, origin=source_origin: self._subscribe_to_author(
                        target, source_url=url, source_origin=origin
                    ),
                )
            )
        elif len(video_values) == 1 and not author_values:
            video = video_values[0].get("data")
            if isinstance(video, SearchVideo) and self._needs_iwara_author_hydration(video):
                if menu.actions():
                    menu.addSeparator()
                menu.addAction(
                    Action(
                        FluentIcon.PEOPLE,
                        tr(
                            "Find author and favorite",
                            "解析作者后收藏",
                            "作者を解析してお気に入りに追加",
                        ),
                        self,
                        triggered=lambda _checked=False, video=video: self._resolve_and_subscribe_author(video),
                    )
                )
        elif video_values or author_values:
            # Never leave a right-click without an explanation.  A few remote
            # search records only contain a display name, or have not finished
            # hydrating their author metadata yet; in that case expose a
            # disabled-looking action that tells the user how to proceed.
            if menu.actions():
                menu.addSeparator()
            menu.addAction(
                Action(
                    FluentIcon.PEOPLE,
                    tr("Favorite author (select one result)", "收藏作者（请只选择一个结果）", "作者をお気に入りに追加（1件を選択）"),
                    self,
                    triggered=lambda _checked=False: self._show_warning(
                        tr(
                            "The selected result has no usable author name. Select one result after its author details load.",
                            "当前结果没有可用的作者名；请等待作者信息加载后只选择一个结果。",
                            "選択結果に利用できる作者名がありません。作者情報の読込後に1件だけ選択してください。",
                        )
                    ),
                )
            )
        if menu.actions():
            menu.addSeparator()
        menu.addAction(
            Action(
                FluentIcon.VIEW,
                tr("Open page", "打开页面", "ページを開く"),
                self,
                triggered=self._open_selected,
            )
        )
        menu.exec(global_position)

    def _selected_author_subscription_target(
        self,
        values: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str, str] | None:
        """Return one author target when the current selection is unambiguous."""

        targets: dict[str, tuple[str, str, str, str]] = {}
        for value in values if values is not None else self._selected_data():
            if value.get("kind") not in {"video", "author"}:
                continue
            target = self._author_navigation_target(value.get("data"))
            if target:
                targets.setdefault(target[0].casefold(), target)
        return next(iter(targets.values())) if len(targets) == 1 else None

    @staticmethod
    def _author_navigation_target(
        value: SearchAuthor | SearchVideo | object,
    ) -> tuple[str, str, str, str] | None:
        """Use verified bridge metadata and distinguish card keys from user IDs."""

        raw = value.raw if isinstance(value, (SearchAuthor, SearchVideo)) and isinstance(value.raw, dict) else {}
        if isinstance(value, SearchVideo) and (
            value.source_kind == "oreno3d" or raw.get("oreno3d_url")
        ):
            mapped = raw.get("oreno_iwara_author")
            has_mapping = isinstance(mapped, dict) and bool(
                mapped.get("username") or mapped.get("slug") or mapped.get("profile_url")
            )
            if not has_mapping and raw.get("_iwara_metadata_loaded") is not True:
                return None
            if not has_mapping:
                user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
                author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
                if not any(
                    str(candidate.get(key) or "").strip()
                    for candidate in (user, author, raw)
                    for key in ("username", "slug", "handle", "author_username")
                ):
                    return None
        target = _author_subscription_target(value)
        if target and isinstance(value, SearchAuthor):
            raw_user = raw.get("user") if isinstance(raw.get("user"), dict) else raw
            real_id = str(raw_user.get("id") or raw_user.get("userId") or raw_user.get("user_id") or "").strip()
            if real_id:
                target = (target[0], target[1], real_id, target[3])
            elif target[2].casefold() == target[0].casefold():
                # normalize_author uses username as a stable card key when
                # a compact result lacks user.id; resolve the profile first.
                target = (target[0], target[1], "", target[3])
        return target

    def _clear_author_navigation(self):
        """Exit author browsing and invalidate pending navigation requests."""

        had_target = getattr(self, "_author_video_target", None) is not None
        self._author_video_target = None
        self._cancel_pending_author_navigation()
        if had_target:
            self._keyword_edit.setPlaceholderText(
                tr("Keywords or titles…", "输入关键词或标题…", "キーワードまたはタイトル…")
            )
            self._set_scope_hint("")

    def _cancel_pending_author_navigation(self):
        self._author_action_generation = getattr(self, "_author_action_generation", 0) + 1
        getattr(self, "_pending_author_actions", {}).clear()
        for worker in getattr(self, "_author_profile_workers", []):
            worker.requestInterruption()

    def _view_selected_author_works(self):
        self._request_selected_author_action("view")

    def _subscribe_selected_author_and_go(self):
        self._request_selected_author_action("subscribe_and_go")

    def _request_selected_author_action(self, action: str):
        values = self._selected_data()
        if len(values) != 1:
            self._show_warning(
                tr("Select one result for this author action", "请只选择一个结果后操作作者", "作者の操作には1件だけ選択してください")
            )
            return
        data = values[0].get("data")
        if not isinstance(data, (SearchAuthor, SearchVideo)):
            return
        self._cancel_pending_author_navigation()
        target = self._author_navigation_target(data)
        if target:
            source_url, source_origin = _author_source_info(data)
            self._run_author_action(target, action, source_url=source_url, source_origin=source_origin)
            return
        if not isinstance(data, SearchVideo):
            self._show_warning(tr("This result has no Iwara account", "当前结果没有 Iwara 账号信息", "この結果にはIwaraアカウント情報がありません"))
            return
        self._pending_author_actions[data.video_id] = action
        self._status_label.setText(
            tr("Resolving the Iwara author…", "正在解析 Iwara 作者…", "Iwara 作者を解析中…")
        )
        raw = data.raw if isinstance(data.raw, dict) else {}
        if data.source_kind == "oreno3d" or raw.get("oreno3d_url"):
            self._start_oreno_author_resolution(data)
        else:
            self._start_iwara_author_hydration(data)

    def _run_author_action(
        self,
        target: tuple[str, str, str, str],
        action: str,
        *,
        source_url: str = "",
        source_origin: str = "",
    ):
        if action == "subscribe_and_go":
            self._subscribe_to_author(
                target,
                source_url=source_url,
                source_origin=source_origin,
                navigate=True,
            )
        elif action == "view":
            if target[2]:
                self._show_author_works_target(target)
            else:
                self._start_author_profile_resolution(target)

    def _finish_pending_author_action(self, video: SearchVideo):
        action = getattr(self, "_pending_author_actions", {}).pop(video.video_id, "")
        if not action:
            return
        target = self._author_navigation_target(video)
        if target:
            source_url, source_origin = _author_source_info(video)
            self._run_author_action(target, action, source_url=source_url, source_origin=source_origin)
            return
        source_url, _source_origin = _author_source_info(video)
        message = tr(
            "Could not identify an Iwara account for this author.",
            "无法确定该作者的 Iwara 账号。",
            "この作者のIwaraアカウントを特定できません。",
        )
        if source_url:
            message += tr(
                " You can still use ‘Open author page in browser’ to browse the Oreno3D source.",
                " 仍可使用右键菜单“在浏览器打开作者页”浏览 Oreno3D 来源。",
                " 右クリックメニューの「作者ページをブラウザーで開く」からOreno3Dの元ページを閲覧できます。",
            )
        reason = str(video.raw.get("oreno3d_author_error") or "")
        if reason:
            message += f"\n{reason}"
        self._status_label.setText(message)
        self._show_warning(message)

    def _start_author_profile_resolution(self, target: tuple[str, str, str, str]):
        if any(
            worker.isRunning()
            and not worker.isInterruptionRequested()
            and worker.generation == self._generation
            and worker.action_generation == getattr(self, "_author_action_generation", 0)
            and worker.target[0].casefold() == target[0].casefold()
            for worker in self._author_profile_workers
        ):
            return
        worker = SearchAuthorProfileWorker(
            self._generation,
            getattr(self, "_author_action_generation", 0),
            target,
        )
        self._author_profile_workers.append(worker)
        worker.result_ready.connect(self._on_author_profile_result)
        on_finished(worker, self, self._cleanup_author_profile_worker)
        self._status_label.setText(
            tr(f"Resolving @{target[0]}’s Iwara user ID…", f"正在解析 @{target[0]} 的 Iwara 用户 ID…", f"@{target[0]} のIwaraユーザーIDを取得中…")
        )
        worker.start()

    def _on_author_profile_result(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        if int(result.get("action_generation", -1)) != getattr(self, "_author_action_generation", 0):
            return
        target = self._author_navigation_target(result.get("author"))
        if target and target[2]:
            self._show_author_works_target(target)
            return
        message = str(result.get("error") or tr("Could not resolve the Iwara user ID", "无法解析 Iwara 用户 ID", "IwaraユーザーIDを取得できません"))
        self._status_label.setText(message)
        self._show_warning(message)

    def _cleanup_author_profile_worker(self, worker: SearchAuthorProfileWorker):
        if worker in self._author_profile_workers:
            self._author_profile_workers.remove(worker)
        worker.deleteLater()

    def _show_author_works_target(self, target: tuple[str, str, str, str], *, record: bool = True):
        """Start a user-scoped video search; Back returns to the search it replaced.

        ``record`` is False when the window has already recorded the session.
        """

        if record:
            record_navigation(self)
        self._auto_search_timer.stop()
        self._set_combo_data(self._source_combo, "iwara")
        self._set_combo_data(self._scope_combo, "videos")
        self._keyword_edit.clear()
        self._auto_search_timer.stop()
        self._enter_author_mode(target)
        self._start_search()

    def _enter_author_mode(self, target: tuple[str, str, str, str]):
        self._author_video_target = target
        self._keyword_edit.setPlaceholderText(
            tr(f"@{target[0]}’s works; type keywords for a new search…", f"@{target[0]} 的作品；输入关键词开始新搜索…", f"@{target[0]} の作品。キーワード入力で新しい検索…")
        )
        self._set_scope_hint(
            tr(
                f"Showing @{target[0]}’s works. Enter keywords or change the source or scope to return to general search.",
                f"正在查看 @{target[0]} 的作品。输入关键词或切换数据源、搜索类型可返回普通搜索。",
                f"@{target[0]} の作品を表示中。キーワード入力やソース・検索対象の変更で通常の検索に戻ります。",
            )
        )

    @staticmethod
    def _needs_iwara_author_hydration(video: SearchVideo) -> bool:
        raw = video.raw if isinstance(video.raw, dict) else {}
        if (video.source_kind == "oreno3d" or raw.get("oreno3d_url")) and raw.get("_iwara_metadata_loaded") is not True:
            return True
        return bool(
            (video.download_video_id or video.video_id)
            and raw.get("_iwara_metadata_loaded") is not True
            and not _author_subscription_target(video)
        )

    def _resolve_and_subscribe_author(self, video: SearchVideo):
        if video.video_id in self._pending_author_subscription_video_ids:
            self._show_warning(
                tr(
                    "Iwara author resolution is already running",
                    "Iwara 作者解析已在进行中",
                    "Iwara 作者の解析は既に実行中です",
                )
            )
            return
        self._pending_author_subscription_video_ids.add(video.video_id)
        if video.source_kind == "oreno3d" or video.raw.get("oreno3d_url"):
            self._start_oreno_author_resolution(video)
            self._status_label.setText(
                tr(
                    "Finding the durable Oreno3D author page…",
                    "正在查找可长期访问的 Oreno3D 作者页…",
                    "永続的なOreno3D作者ページを検索中…",
                )
            )
            return
        iwara_id = (
            video.download_video_id
            or _extract_iwara_video_id(video.iwara_url)
            or (video.video_id if video.source_kind == "iwara" else "")
        )
        if iwara_id:
            if not video.download_video_id:
                video.download_video_id = iwara_id
                video.iwara_url = video.iwara_url or f"https://www.iwara.tv/video/{iwara_id}"
            self._start_iwara_author_hydration(video)
        elif video.source_kind == "oreno3d":
            self._start_oreno_link_resolution(
                [video],
                priority=True,
                hydrate_metadata=True,
            )
        else:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            self._show_warning(
                tr(
                    "This result has no Iwara video ID to resolve",
                    "当前结果没有可解析的 Iwara 视频 ID",
                    "この結果には解析可能な Iwara 動画 ID がありません",
                )
            )
            return
        self._status_label.setText(
            tr(
                "Resolving the Iwara author…",
                "正在解析 Iwara 作者…",
                "Iwara 作者を解析中…",
            )
        )

    def _start_oreno_author_resolution(self, video: SearchVideo):
        if any(
            worker.isRunning()
            and not worker.isInterruptionRequested()
            and worker.generation == self._generation
            and worker.video_id == video.video_id
            for worker in self._oreno_author_workers
        ):
            return
        worker = SearchOrenoAuthorWorker(self._generation, video)
        self._oreno_author_workers.append(worker)
        worker.result_ready.connect(self._on_oreno_author_result)
        on_finished(worker, self, self._cleanup_oreno_author_worker)
        worker.start()

    def _on_oreno_author_result(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        video_id = str(result.get("video_id") or "").strip()
        video = next((candidate for candidate in self._all_videos if candidate.video_id == video_id), None)
        if video is None:
            return
        resolved = result.get("result")
        resolved = dict(resolved) if isinstance(resolved, dict) else {}
        raw = video.raw if isinstance(video.raw, dict) else {}
        video.raw = raw
        for key in ("oreno_author_url", "oreno_author_name", "oreno_author_id"):
            value = str(resolved.get(key) or "").strip()
            if value:
                raw[key.replace("oreno_", "oreno3d_")] = value
        iwara_author = resolved.get("iwara_author")
        if isinstance(iwara_author, dict) and iwara_author:
            raw["oreno_iwara_author"] = dict(iwara_author)
            raw.pop("oreno3d_author_error", None)
        elif result.get("error") and not raw.get("oreno_iwara_author"):
            raw["oreno3d_author_error"] = str(result["error"])
        if raw.get("oreno3d_author_url"):
            video.raw["oreno3d_author_url"] = str(raw["oreno3d_author_url"])
        self._update_video_presentation(video)
        self._start_image_loading()

        target = self._author_navigation_target(video)
        if video.video_id in self._pending_author_subscription_video_ids:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            if target:
                source_url, source_origin = _author_source_info(video)
                if source_url:
                    self._subscribe_to_author(
                        target,
                        source_url=source_url,
                        source_origin=source_origin,
                    )
                else:
                    self._subscribe_to_author(target)
            else:
                self._show_warning(
                    tr(
                        "No surviving Iwara author could be found from this Oreno3D author page",
                        "无法从该 Oreno3D 作者页找到仍可用的 Iwara 作者",
                        "このOreno3D作者ページから有効なIwara作者を特定できません",
                    )
                )
        self._finish_pending_author_action(video)
        if video.video_id in self._pending_open_author_video_ids:
            self._pending_open_author_video_ids.discard(video.video_id)
            source_url, _source_origin = _author_source_info(video)
            if target and getattr(self, "_open_author_prefers_app", False):
                signal_bus.author_page_requested.emit(target)  # found an Iwara account: stay in the app
            elif source_url:
                webbrowser.open(source_url)
        if result.get("error") and not raw.get("oreno3d_author_url"):
            self._status_label.setText(str(result.get("error")))

    def _cleanup_oreno_author_worker(self, worker: SearchOrenoAuthorWorker):
        if worker in self._oreno_author_workers:
            self._oreno_author_workers.remove(worker)
        worker.deleteLater()

    def _start_iwara_author_hydration(self, video: SearchVideo):
        iwara_id = str(
            video.download_video_id
            or _extract_iwara_video_id(video.iwara_url)
            or (video.video_id if video.source_kind == "iwara" else "")
            or ""
        ).strip()
        if not iwara_id:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            self._finish_pending_author_action(video)
            return
        video.download_video_id = iwara_id
        if any(
            worker.isRunning()
            and not worker.isInterruptionRequested()
            and worker.generation == self._generation
            and worker.video_id == iwara_id
            for worker in self._iwara_author_workers
        ):
            return
        worker = SearchIwaraAuthorWorker(self._generation, iwara_id)
        self._iwara_author_workers.append(worker)
        worker.result_ready.connect(self._on_iwara_author_result)
        on_finished(worker, self, self._cleanup_iwara_author_worker)
        worker.start()

    def _on_iwara_author_result(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        iwara_id = str(result.get("video_id") or "").strip()
        video = next(
            (
                candidate
                for candidate in self._all_videos
                if candidate.download_video_id == iwara_id
            ),
            None,
        )
        if video is None:
            return
        metadata = result.get("metadata")
        if isinstance(metadata, dict) and metadata:
            self._apply_iwara_metadata(video, metadata)
            self._update_video_presentation(video)
            self._start_image_loading()
        target = self._author_navigation_target(video)
        if video.video_id in self._pending_author_subscription_video_ids:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            if target:
                source_url, source_origin = _author_source_info(video)
                if source_url:
                    self._subscribe_to_author(
                        target,
                        source_url=source_url,
                        source_origin=source_origin,
                    )
                else:
                    self._subscribe_to_author(target)
            else:
                self._show_warning(
                    tr(
                        "Iwara video metadata did not contain an author",
                        "Iwara 视频详情中没有作者信息",
                        "Iwara 動画詳細に作者情報がありません",
                    )
                )
        self._finish_pending_author_action(video)
        if result.get("error") and not target:
            self._status_label.setText(str(result.get("error")))

    def _cleanup_iwara_author_worker(self, worker: SearchIwaraAuthorWorker):
        if worker in self._iwara_author_workers:
            self._iwara_author_workers.remove(worker)
        worker.deleteLater()

    def _apply_iwara_metadata(self, video: SearchVideo, metadata: dict[str, Any]):
        normalized = normalize_video(metadata)
        if normalized is not None:
            for field_name in (
                "title",
                "author_username",
                "author_name",
                "published_at",
                "likes",
                "views",
                "duration",
                "comments",
                "rating",
                "tags",
                "origins",
                "characters",
                "thumbnail_url",
                "slug",
            ):
                setattr(video, field_name, getattr(normalized, field_name))
        video.raw.update(metadata)
        video.raw["_iwara_metadata_loaded"] = normalized is not None
        video.raw["iwara_id"] = video.download_video_id
        video.raw["iwara_url"] = video.iwara_url

    def _subscribe_to_author(
        self,
        target: tuple[str, str, str, str],
        *,
        source_url: str = "",
        source_origin: str = "",
        navigate: bool = False,
    ):
        username, title, remote_id, avatar_url = target
        kwargs: dict[str, str] = {
            "title": title,
            "remote_id": remote_id,
            "avatar_url": avatar_url,
        }
        if str(source_url or "").strip():
            kwargs["source_url"] = str(source_url).strip()
        if str(source_origin or "").strip():
            kwargs["source_origin"] = str(source_origin).strip()
        try:
            source_id = download_manager.add_author_subscription(
                username,
                **kwargs,
            )
        except TypeError as exc:
            # Keep lightweight manager fakes and older integrations usable.
            if not any(name in str(exc) for name in ("title", "remote_id", "avatar_url", "source_url", "source_origin")):
                self._report_author_subscription_failure(username, exc)
                return
            try:
                source_id = download_manager.add_author_subscription(username)
            except Exception as fallback_exc:
                self._report_author_subscription_failure(username, fallback_exc)
                return
        except Exception as exc:  # database/network integrations should never fail silently
            self._report_author_subscription_failure(username, exc)
            return
        source_id = int(source_id or 0)
        if not source_id:
            self._report_author_subscription_failure(username, None)
            return
        try:
            signal_bus.subscription_source_added.emit(int(source_id))
        except Exception as exc:
            # A refresh listener must not hide a successful database insert or
            # suppress the confirmation shown to the user.
            signal_bus.log_message.emit(f"Subscription refresh notification failed: {exc}")
        signal_bus.log_message.emit(f"Author subscription added: @{username}")
        InfoBar.success(
            title=tr("Author added", "作者已加入订阅", "作者を購読に追加しました"),
            content=tr(
                f"@{username} is now in your local subscriptions",
                f"@{username} 已加入本地订阅，可在订阅页查看可下载内容",
                f"@{username} をローカル購読に追加しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self,
        )
        if navigate:
            signal_bus.subscription_source_requested.emit(source_id)
        return source_id

    def _report_author_subscription_failure(self, username: str, error: Exception | None):
        detail = str(error or "").strip()
        content = tr(
            f"Could not add @{username} to subscriptions",
            f"无法将 @{username} 加入订阅",
            f"@{username} を購読に追加できません",
        )
        if detail:
            content += f": {detail}"
        signal_bus.log_message.emit(f"Author subscription failed: @{username} {detail}".strip())
        self._show_warning(content)

    def _search_selected_author(self):
        self._view_selected_author_works()

    def _reset_filters(self):
        self._clear_author_navigation()
        self._query_drafts.clear()
        self._query_context = None
        self._active_search_request = None
        self._search_error = ""
        self._hide_search_history_popup()
        self._keyword_edit.clear()
        self._source_combo.setCurrentIndex(0)
        self._scope_combo.setCurrentIndex(0)
        self._sort_combo.setCurrentIndex(0)
        self._results.clearSelection()
        self._results_table.clearSelection()
        for worker in self._oreno_link_workers:
            worker.requestInterruption()
        for worker in self._iwara_author_workers:
            worker.requestInterruption()
        for worker in self._oreno_author_workers:
            worker.requestInterruption()
        self._pending_author_subscription_video_ids.clear()
        self._pending_open_video_ids.clear()
        self._pending_preview_video_ids.clear()
        self._pending_detail_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        self._current_page = 0
        self._last_page = None
        self._next_page = None
        self._total = None
        self._all_videos.clear()
        self._all_authors.clear()
        self._all_playlists.clear()
        self._image_path_by_key.clear()
        self._image_pending_keys.clear()
        self._render_results()
        self._set_loading(False)
        self._status_label.setText(
            tr(
                "Enter a query or search the latest videos",
                "输入条件后开始搜索，也可以直接查看最新视频",
                "条件を入力して検索してください",
            )
        )

