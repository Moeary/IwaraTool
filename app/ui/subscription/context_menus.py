"""Right-click menus for sources and items in every subscription view."""

from __future__ import annotations

from PySide6.QtCore import Qt

from qfluentwidgets import Action, FluentIcon, RoundMenu

from ...i18n import tr
from .helpers import _open_url, _source_url, _video_url
from .page_dependencies import download_manager, signal_bus


class SubscriptionContextMenusMixin:
    """Right-click menus for sources and items in every subscription view."""

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
        if len(video_ids) == 1:
            self._add_single_video_actions(menu, video_ids[0])
            menu.addSeparator()
        self._add_pending_toggle_action(menu, video_ids)
        menu.addSeparator()
        self._add_item_batch_actions(menu, video_ids)
        menu.exec(global_pos)

    def _add_single_video_actions(self, menu: RoundMenu, single: str):
        menu.addAction(
            Action(
                FluentIcon.VIEW,
                tr("View Details", "查看详情", "詳細を表示"),
                self,
                triggered=lambda _checked=False: signal_bus.media_detail_requested.emit("video", single),
            )
        )
        menu.addAction(
            Action(
                FluentIcon.PLAY,
                tr("Play", "播放", "再生"),
                self,
                triggered=lambda _checked=False: self._activate_video_id(single),
            )
        )
        menu.addAction(
            Action(
                FluentIcon.GLOBE,
                tr("Open in Browser", "在浏览器打开", "ブラウザーで開く"),
                self,
                triggered=lambda _checked=False: _open_url(_video_url(single)),
            )
        )

    def _add_pending_toggle_action(self, menu: RoundMenu, video_ids: list[str]):
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

    def _add_item_batch_actions(self, menu: RoundMenu, video_ids: list[str]):
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

    def _show_source_menu(self, source_id: int, global_pos):
        source = next((s for s in self._all_sources if int(s.get("id", 0) or 0) == int(source_id)), None)
        if source is None:
            return
        enabled = bool(int(source.get("enabled", 1) or 0))
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FluentIcon.VIEW, tr("View All", "查看全部", "すべて表示"), self, triggered=lambda: self._open_source_grid(source_id)))
        menu.addAction(Action(FluentIcon.SYNC, tr("Refresh", "刷新", "更新"), self, triggered=lambda: self._refresh_source_ids_from_view(source_id)))
        url = _source_url(source)
        if url:
            menu.addAction(Action(FluentIcon.GLOBE, tr("Open Page", "打开主页", "ページを開く"), self, triggered=lambda: _open_url(url)))
        if str(source.get("source_type", "") or "") == "author":
            key = str(source.get("source_key", "") or "")
            target = (key, str(source.get("title", "") or key), str(source.get("remote_id", "") or ""), str(source.get("avatar_url", "") or ""))
            menu.addAction(Action(FluentIcon.SEARCH, tr("Search Their Works", "在搜索页查看作品", "検索ページで作品を表示"), self, triggered=lambda: signal_bus.search_requested.emit({"author": target})))
        menu.addSeparator()
        menu.addAction(
            Action(
                FluentIcon.PAUSE if enabled else FluentIcon.PLAY,
                tr("Pause Updates", "停用此订阅", "更新を停止") if enabled else tr("Resume Updates", "启用此订阅", "更新を再開"),
                self,
                triggered=lambda: self._toggle_source_enabled(source_id, not enabled),
            )
        )
        menu.addAction(Action(FluentIcon.DELETE, tr("Delete Subscription", "删除订阅", "購読を削除"), self, triggered=lambda: self._delete_source_ids([source_id])))
        menu.exec(global_pos)

    def _toggle_source_enabled(self, source_id: int, enabled: bool):
        download_manager.set_subscription_enabled(int(source_id), bool(enabled))
        self._load_sources()

    def _show_video_menu(self, video, global_pos, selection: list[str] | None = None):
        """Context menu for a poster card (overview strip or source grid)."""

        video_id = video.video_id
        ids = list(selection) if selection and len(selection) > 1 else [video_id]
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FluentIcon.VIEW, tr("View Details", "查看详情", "詳細を表示"), self, triggered=lambda: self._show_video_detail(video)))
        menu.addAction(Action(FluentIcon.PLAY, tr("Play", "播放", "再生"), self, triggered=lambda: self._activate_video_id(video_id)))
        menu.addAction(Action(FluentIcon.GLOBE, tr("Open in Browser", "在浏览器打开", "ブラウザーで開く"), self, triggered=lambda: _open_url(_video_url(video_id))))
        menu.addSeparator()
        label = (
            tr("Download Selected", "下载选中", "選択を保存")
            if len(ids) > 1
            else tr("Download", "下载", "保存")
        )
        menu.addAction(Action(FluentIcon.DOWNLOAD, label, self, triggered=lambda: self._download_selected_with_rule(ids)))
        menu.addAction(
            Action(
                FluentIcon.ACCEPT,
                tr("Mark as Downloaded", "标记为已下载/已移走", "保存済み/移動済みにする"),
                self,
                triggered=lambda: self._mark_selected_downloaded_moved(ids),
            )
        )
        if video.author_username:
            author = (video.author_username, video.author_name or video.author_username, "", "")
            menu.addAction(Action(FluentIcon.PEOPLE, tr("Open Author Page", "打开作者页", "作者ページを開く"), self, triggered=lambda: signal_bus.author_page_requested.emit(author)))
            menu.addAction(Action(FluentIcon.SEARCH, tr("Search Author's Works", "查看作者作品", "作者の作品を表示"), self, triggered=lambda: signal_bus.search_requested.emit({"author": author})))
        menu.exec(global_pos)
