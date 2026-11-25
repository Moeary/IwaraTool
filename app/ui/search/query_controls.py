"""Scope, sort and rating selectors and the collapsible query card."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget

from qfluentwidgets import ComboBox, FluentIcon

from ...config import app_config
from ...core.rating import UI_RATING_KEY, normalize_rating
from ...i18n import tr
from ...presentation.search import options as search_options
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
        source = str(self._source_combo.currentData() or "oreno3d")
        hint = search_options.scope_hint(scope, source)
        self._keyword_edit.setPlaceholderText(search_options.keyword_placeholder(scope))
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
                self._status_label.setText(search_options.enter_terms_text())
        self._sync_sort_options()

    def _sync_sort_options(self, *_args):
        """Only advertise sorts supported by the selected remote endpoint."""

        items = search_options.sort_choices(
            str(self._source_combo.currentData() or "oreno3d"),
            str(self._scope_combo.currentData() or "videos"),
            self._keyword_edit.text(),
        )
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
        # No focusIn arrives when the box already had focus, or when the page
        # switch handed it back first (OtherFocusReason, which deliberately
        # keeps the history closed), so open the history here.
        self._show_search_history_popup()

    def _set_scope_hint(self, text: str):
        """Context line under the query box; hidden whenever it has nothing to say."""

        self._scope_hint.setText(text)
        self._scope_hint.setVisible(bool(text))
