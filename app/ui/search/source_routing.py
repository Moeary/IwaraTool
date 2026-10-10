"""Choose the search source (Oreno3D or Iwara), by hand or from the keyword."""

from __future__ import annotations

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...config import app_config
from ...i18n import tr

from .keyword_language import is_chinese_keyword


class SearchSourceRoutingMixin:
    """Choose the search source (Oreno3D or Iwara), by hand or from the keyword."""

    def _scope_index(self, scope: str) -> int:
        for index in range(self._scope_combo.count()):
            if str(self._scope_combo.itemData(index) or "") == scope:
                return index
        return -1

    def _sync_scope_options_for_source(self, source: str):
        """Expose only scopes supported by the selected online source."""

        supported = {"videos", "tags"} if source == "oreno3d" else {
            "videos",
            "images",
            "authors",
            "tags",
            "playlists",
        }
        current_scope = str(self._scope_combo.currentData() or "videos")
        self._scope_combo.blockSignals(True)
        self._scope_combo.clear()
        for text, data in self._scope_items:
            if data in supported:
                self._add_combo_item(self._scope_combo, text, data)
        selected_scope = current_scope if current_scope in supported else "videos"
        selected_index = self._scope_index(selected_scope)
        if selected_index >= 0:
            self._scope_combo.setCurrentIndex(selected_index)
        self._scope_combo.blockSignals(False)

    def _on_source_combo_changed(self, *_args):
        self._source_auto_routed = False
        self._source_pinned = True
        self._on_source_changed()

    def _unpin_source(self, *_args):
        self._source_pinned = False

    def _switch_source(self, source: str) -> bool:
        """Select ``source`` without counting it as the user's own pick.

        The keyword (and the sort, where the new source offers it) comes along
        instead of that source's own draft.
        """

        if str(self._source_combo.currentData() or "") == source:
            return False
        keyword = self._keyword_edit.text()
        sort = str(self._sort_combo.currentData() or "date")
        self._source_combo.blockSignals(True)
        changed = self._set_combo_data(self._source_combo, source)
        self._source_combo.blockSignals(False)
        if changed:
            self._on_source_changed(trigger_search=False)
            self._keyword_edit.setText(keyword)
            self._sync_sort_options()
            self._sort_combo.blockSignals(True)
            self._set_combo_data(self._sort_combo, sort)
            self._sort_combo.blockSignals(False)
        return changed

    def _on_default_source_changed(self, source: str):
        """Settings chose another main search engine: start from it."""

        self._source_auto_routed = False
        self._source_pinned = False
        self._switch_source(source)

    def _route_source_for_keyword(self):
        """Chinese video keywords go to Oreno3D; the next other search goes back."""

        if self._author_video_target is not None or self._source_pinned:
            return
        if str(self._scope_combo.currentData() or "videos") != "videos":
            return
        source = str(self._source_combo.currentData() or "oreno3d")
        chinese = bool(app_config.search_chinese_via_oreno3d) and is_chinese_keyword(
            self._keyword_edit.text()
        )
        if chinese and source == "iwara":
            if self._switch_source("oreno3d"):
                self._source_auto_routed = True
                InfoBar.info(
                    title=tr("Searching Oreno3D", "已改用 Oreno3D 搜索", "Oreno3D で検索"),
                    content=tr(
                        "Chinese keywords are searched with Oreno3D (see Settings › Search).",
                        "中文关键词使用 Oreno3D 搜索（可在 设置 › 搜索 中关闭）。",
                        "中国語キーワードは Oreno3D で検索します（設定 › 検索）。",
                    ),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2500,
                    parent=self,
                )
        elif not chinese and self._source_auto_routed:
            self._source_auto_routed = False
            self._switch_source(app_config.search_default_source)

    def _on_source_changed(self, *_args, trigger_search: bool = True):
        self._clear_author_navigation()
        source = str(self._source_combo.currentData() or "oreno3d")
        self._sync_scope_options_for_source(source)
        self._rating_group.setVisible(source == "iwara")
        self._on_scope_changed(trigger_search=False)
        if trigger_search:
            self._schedule_auto_search()
