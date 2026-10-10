"""Scope, sort and rating selectors and the collapsible query card."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget

from qfluentwidgets import ComboBox, FluentIcon

from ...config import app_config
from ...core.search import playlist_reference, profile_reference
from ...core.rating import UI_RATING_KEY, normalize_rating
from ...i18n import tr
from .page_dependencies import signal_bus


class SearchQueryControlsMixin:
    """Scope, sort and rating selectors and the collapsible query card."""

    @staticmethod
    def _add_combo_item(combo: ComboBox, text: str, data: str):
        combo.addItem(text)
        combo.setItemData(combo.count() - 1, data)

    def _toggle_search_controls(self):
        self._set_search_controls_collapsed(not self._search_controls_collapsed)

    def _set_search_controls_collapsed(self, collapsed: bool, *, persist: bool = True):
        self._search_controls_collapsed = bool(collapsed)
        self._query_card.setVisible(not self._search_controls_collapsed)
        self._rule_card.setVisible(not self._search_controls_collapsed)
        if self._search_controls_collapsed:
            self._hide_search_history_popup()
            if self._tag_popup is not None:
                self._tag_popup.hide()
            self._toggle_search_controls_btn.setIcon(FluentIcon.DOWN)
            self._toggle_search_controls_btn.setToolTip(
                tr("Show search controls", "展开搜索区", "検索欄を展開")
            )
        else:
            self._toggle_search_controls_btn.setIcon(FluentIcon.UP)
            self._toggle_search_controls_btn.setToolTip(
                tr("Hide search controls", "收起搜索区", "検索欄を折りたたむ")
            )
        if persist:
            app_config.set_ui_value(
                "search_controls_collapsed_v1",
                self._search_controls_collapsed,
            )
        QTimer.singleShot(0, self._resize_grid)

    @staticmethod
    def _make_combo(items: list[tuple[str, str]], parent: QWidget) -> ComboBox:
        combo = ComboBox(parent)
        for text, data in items:
            SearchQueryControlsMixin._add_combo_item(combo, text, data)
        combo.setCurrentIndex(0)
        return combo

    @staticmethod
    def _set_combo_data(combo: ComboBox, value: str) -> bool:
        target = str(value or "").strip()
        for index in range(combo.count()):
            if str(combo.itemData(index) or "") == target:
                combo.setCurrentIndex(index)
                return True
        return False

    def _on_scope_changed(self, *_args, trigger_search: bool = True):
        self._clear_author_navigation()
        self._switch_query_context()
        scope = str(self._scope_combo.currentData() or "videos")
        if scope == "authors":
            hint = tr(
                "Searches Iwara users by name. Use @username or a profile URL to open one exact account.",
                "按名称搜索 Iwara 用户；输入 @用户名 或个人主页链接可直接定位到该账号。",
                "名前でIwaraユーザーを検索します。@ユーザー名またはプロフィールURLで特定のアカウントを開けます。",
            )
            self._keyword_edit.setPlaceholderText(
                tr("Author name, @username or profile URL…", "输入作者名称、@用户名或主页链接…", "作者名、@ユーザー名、プロフィールURL…")
            )
        elif scope == "images":
            hint = tr(
                "Searches Iwara image posts; leave blank to browse them. Images can be viewed, but not queued for download.",
                "搜索 Iwara 图片作品，留空则浏览图片列表；图片可查看，但不能加入下载队列。",
                "Iwaraの画像投稿を検索します。空欄なら一覧を表示します。閲覧はできますが、ダウンロードキューには追加できません。",
            )
            self._keyword_edit.setPlaceholderText(
                tr("Image keywords (blank to browse)…", "输入图片关键词（留空浏览）…", "画像のキーワード（空欄で一覧）…")
            )
        elif scope == "tags":
            if str(self._source_combo.currentData() or "oreno3d") == "oreno3d":
                hint = tr(
                    "Mapped Iwara labels use typed numeric Oreno3D routes; unknown names fall back to keyword search. Multiple tags are matched by intersection. tag:<id>, origin:<id>, and character:<id> are also supported.",
                    "已匹配的 Iwara 标签会自动转为对应的 Oreno3D 数字路由；未知名称回退到关键词搜索。多个标签会取交集；也支持 tag:<id>、origin:<id>、character:<id>。",
                    "対応するIwaraラベルはOreno3Dの型付き数値ルートに変換し、未知名はキーワード検索に戻します。複数タグは共通結果を求めます。tag:<id>・origin:<id>・character:<id>にも対応します。",
                )
            else:
                hint = tr(
                    "Search for all specified tags. Select suggestions or enter exact translated names separated by commas; names are converted to Iwara tag IDs.",
                    "查找同时包含所选标签的视频。可选择候选或输入完整译名，用逗号分隔；提交时会转换为 Iwara 标签 ID。",
                    "指定したすべてのタグを含む動画を検索します。候補または正確な翻訳名をカンマで区切って入力すると、IwaraタグIDに変換します。",
                )
            self._keyword_edit.setPlaceholderText(
                tr(
                    "Type a tag, select a candidate, then add another…",
                    "输入标签，选择候选后可继续添加…",
                    "タグを入力し、候補選択後に続けて追加…",
                )
            )
        elif scope == "playlists":
            hint = tr(
                "Searches playlists by title. Paste a playlist ID or iwara.tv/playlist/... URL to list its videos; double-click a playlist card to open it here.",
                "按标题搜索播放列表；粘贴播放列表 ID 或 iwara.tv/playlist/... 链接可直接列出其中视频，双击播放列表卡片也可在此打开。",
                "タイトルでプレイリストを検索します。プレイリストIDまたはiwara.tv/playlist/... URLで動画一覧を表示し、カードをダブルクリックしてもここで開けます。",
            )
            self._keyword_edit.setPlaceholderText(
                tr("Playlist keywords, ID or URL…", "输入播放列表关键词、ID 或链接…", "プレイリストのキーワード、ID、URL…")
            )
        else:
            hint = tr(
                'Search text across Iwara. Keywords and tags are separate; use double quotes for an exact phrase. Leave blank to browse videos.',
                '在 Iwara 中搜索关键词，与标签搜索分开；精确短语可加双引号。留空则浏览视频列表。',
                'Iwara内のキーワード検索です。タグ検索とは別です。完全一致の語句は二重引用符で囲み、空欄なら動画一覧を表示します。',
            ) if str(self._source_combo.currentData() or "oreno3d") == "iwara" else tr(
                "Oreno3D is only a search bridge. Opening a result resolves and shows the final Iwara page.",
                "Oreno3D 仅作为搜索桥接；打开结果时会解析并展示最终的 Iwara 页面。",
                "Oreno3Dは検索ブリッジのみです。結果を開くとIwaraのページを表示します。",
            )
            self._keyword_edit.setPlaceholderText(
                tr(
                    "Keywords or titles…",
                    "输入关键词或标题…",
                    "キーワードまたはタイトル…",
                )
            )
        self._keyword_edit.setToolTip(hint)
        if scope != "tags" and self._tag_popup is not None:
            self._tag_popup.hide()
        self._sync_view_controls()
        if trigger_search:
            self._schedule_auto_search()

    def _switch_query_context(self):
        """Keep independent text/sort drafts for each source and search mode."""

        context = (
            str(self._source_combo.currentData() or "oreno3d"),
            str(self._scope_combo.currentData() or "videos"),
        )
        if context != self._query_context:
            if self._query_context is not None:
                self._query_drafts[self._query_context] = (
                    self._keyword_edit.text(), str(self._sort_combo.currentData() or "date"),
                )
            self._query_context = context
            text, sort = self._query_drafts.get(context, ("", "date"))
            self._keyword_edit.setText(text)
            self._sync_sort_options()
            self._sort_combo.blockSignals(True)
            self._set_combo_data(self._sort_combo, sort)
            self._sort_combo.blockSignals(False)
            self._hide_search_history_popup()
            if self._tag_popup is not None:
                self._tag_popup.hide()
            if self._auto_search_ready:
                self._interrupt_search_workers()
                self._active_search_request = None
                self._search_error = ""
                self._current_page = 0
                self._last_page = self._next_page = self._total = None
                self._all_videos.clear()
                self._all_authors.clear()
                self._all_playlists.clear()
                self._render_results()
                self._set_loading(False)
                self._status_label.setText(tr("Enter search terms", "请输入搜索条件", "検索条件を入力してください"))
        self._sync_sort_options()

    def _sync_sort_options(self, *_args):
        """Only advertise sorts supported by the selected remote endpoint."""

        iwara = str(self._source_combo.currentData() or "oreno3d") == "iwara"
        scope = str(self._scope_combo.currentData() or "videos")
        keyword = self._keyword_edit.text().strip()
        newest = (tr("Newest", "最新", "新着"), "date")
        relevance = (tr("Relevance", "相关度", "関連度"), "relevance")
        views = (tr("Most viewed", "最多人观看", "再生数最多"), "views")
        likes = (tr("Most liked", "喜欢最多", "いいね順"), "likes")
        if iwara and scope in {"images", "videos"} and keyword:
            # Native /search for videos and images accepts four orders.
            items = [newest, relevance, views, likes]
        elif iwara and (
            (scope == "authors" and not profile_reference(keyword))
            or (scope == "playlists" and not playlist_reference(keyword))
        ):
            # Native user and playlist search only accepts these two.
            items = [newest, relevance]
        else:
            items = [
                newest,
                (tr("Trending", "趋势", "トレンド"), "trending"),
                (tr("Popularity", "热度", "人気"), "popularity"),
                views,
                likes,
            ]
        if [self._sort_combo.itemData(i) for i in range(self._sort_combo.count())] == [key for _, key in items]:
            return
        previous = str(self._sort_combo.currentData() or "date")
        self._sort_combo.blockSignals(True)
        self._sort_combo.clear()
        for label, key in items:
            self._add_combo_item(self._sort_combo, label, key)
        if not self._set_combo_data(self._sort_combo, previous):
            self._sort_combo.setCurrentIndex(0)
        self._sort_combo.blockSignals(False)

    def _on_sort_changed(self, *_args):
        self._schedule_auto_search()

    def _selected_rating(self) -> str:
        return normalize_rating(self._rating_combo.currentData())

    def _on_rating_changed(self, *_args):
        rating = self._selected_rating()
        app_config.set_ui_value(UI_RATING_KEY, rating)
        signal_bus.content_rating_changed.emit(rating)
        self._schedule_auto_search()

    def _on_rating_broadcast(self, rating: str):
        """Follow a change made on the Home page without echoing it back."""

        rating = normalize_rating(rating)
        if rating == self._selected_rating():
            return
        self._rating_combo.blockSignals(True)
        self._set_combo_data(self._rating_combo, rating)
        self._rating_combo.blockSignals(False)
        self._schedule_auto_search()

    def _focus_keyword(self):
        if self._search_controls_collapsed:
            self._set_search_controls_collapsed(False)
        self._keyword_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._keyword_edit.selectAll()

    def _set_scope_hint(self, text: str):
        """Context line under the query box; hidden whenever it has nothing to say."""

        self._scope_hint.setText(text)
        self._scope_hint.setVisible(bool(text))
