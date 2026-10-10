"""Right-click menu of search results."""

from __future__ import annotations

from qfluentwidgets import Action, FluentIcon, RoundMenu

from ...core.search import SearchPlaylist, SearchVideo
from ...i18n import tr
from .result_authors import _author_source_info, _oreno3d_video_url


def _separate(menu: RoundMenu):
    if menu.actions():
        menu.addSeparator()


class SearchContextMenuMixin:
    """Right-click menu of search results."""

    def _show_context_menu(self, position):
        target = self._select_context_menu_target(position)
        values = self._selected_data()
        if not values:
            return
        global_position = target.viewport().mapToGlobal(position)
        menu = RoundMenu(parent=self)
        video_values = [value for value in values if value.get("kind") == "video"]
        author_values = [value for value in values if value.get("kind") == "author"]
        playlist_values = [value for value in values if value.get("kind") == "playlist"]
        self._add_result_menu_actions(menu, values, video_values, author_values, playlist_values)
        if len(values) == 1 and (video_values or author_values):
            self._add_author_menu_actions(menu, values[0])
        self._add_author_favorite_menu_action(menu, values, video_values, author_values)
        _separate(menu)
        menu.addAction(
            Action(
                FluentIcon.VIEW,
                tr("Open page", "打开页面", "ページを開く"),
                self,
                triggered=self._open_selected,
            )
        )
        menu.exec(global_position)

    def _select_context_menu_target(self, position):
        """Select the result under the cursor and return the view it is in."""

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
        return target

    def _add_result_menu_actions(self, menu, values, video_values, author_values, playlist_values):
        single_video = len(video_values) == 1 and len(values) == 1
        if single_video:
            menu.addAction(
                Action(
                    FluentIcon.PLAY,
                    tr("Play / Preview", "播放 / 预览", "再生 / プレビュー"),
                    self,
                    triggered=self._preview_selected,
                )
            )
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
        if single_video and not author_values:
            selected_video = video_values[0].get("data")
            if isinstance(selected_video, SearchVideo) and _oreno3d_video_url(selected_video):
                _separate(menu)
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

    def _add_author_menu_actions(self, menu, value):
        """Author page / works / subscribe entries for one selected video or author."""

        _separate(menu)
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
        selected_data = value.get("data")
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

    def _add_author_favorite_menu_action(self, menu, values, video_values, author_values):
        author_target = self._selected_author_subscription_target(values)
        if author_target:
            source_url, source_origin = _author_source_info(values[0].get("data")) if len(values) == 1 else ("", "")
            _separate(menu)
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
                _separate(menu)
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
            _separate(menu)
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
