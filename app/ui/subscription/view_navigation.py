"""Switch between overview, source grid, author view and table; back history support."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ...config import app_config
from ..author_view import clean_username
from ..navigation import navigate_back, record_navigation, restore_scroll
from .helpers import _source_url
from .page_dependencies import download_manager, signal_bus


class SubscriptionViewNavigationMixin:
    """Switch between overview, source grid, author view and table; back history support."""

    def _set_view_mode(self, mode: str, *, persist: bool = True):
        mode = "table" if mode == "table" else "overview"
        self._view_switch.blockSignals(True)
        self._view_switch.setCurrentItem(mode)
        self._view_switch.blockSignals(False)
        if mode == "table":
            self._view_stack.setCurrentWidget(self._table_page)
        else:
            # The overview reloads itself when it becomes visible; doing it here
            # as well built every row twice while the page was still hidden.
            self._view_stack.setCurrentWidget(self._overview)
        for button in (self._toggle_sources_btn, self._toggle_items_btn):
            button.setVisible(mode == "table")
        if persist:
            app_config.set_ui_value("subscription_view_mode_v1", mode)

    def _source_by_id(self, source_id: int) -> dict[str, Any] | None:
        return next((s for s in self._all_sources if int(s.get("id", 0) or 0) == int(source_id)), None)

    def _open_source_grid(self, source_id: int, *, record: bool = True):
        """Show one subscription's grid; ``record`` keeps where the user was for Back."""

        source = self._source_by_id(source_id)
        if source is None:
            return
        if record and not (
            self._view_stack.currentWidget() is self._source_view and self._source_view.source_id == int(source_id)
        ):
            record_navigation(self)
        self._source_view.open_source(source)
        self._view_stack.setCurrentWidget(self._source_view)

    def _show_list_view(self):
        """The overview or the table, whichever the user prefers."""

        list_view = self._overview if self._view_mode_is_overview() else self._table_page
        self._view_stack.setCurrentWidget(list_view)
        if list_view is self._overview:
            self._overview.reload_if_dirty()

    def _leave_page_view(self):
        """Back from a source/author page: to wherever the user came from, else the list."""

        if not navigate_back(self):
            self._show_list_view()

    def go_back_view(self):
        """Alt+Left: back from an opened subscription / author (to the list without history)."""

        if self._view_stack.currentWidget() in (self._source_view, self._author_view):
            self._leave_page_view()

    def draft_state(self):
        fields = {
            "sources": self._source_search_edit.text(),
            "titles": self._title_search_edit.text(),
        }
        return fields if any(fields.values()) else None

    def restore_draft(self, state):
        self._source_search_edit.setText(str(state.get("sources") or ""))
        self._title_search_edit.setText(str(state.get("titles") or ""))

    def nav_snapshot(self):
        current = self._view_stack.currentWidget()
        if current is self._source_view and self._source_view.source_id:
            return ("source", self._source_view.view_state())
        if current is self._author_view:
            return ("author", self._author_view.view_state())
        if current is self._table_page:
            return ("table", int(self._current_source_id or 0))
        return ("overview", self._overview._scroll.verticalScrollBar().value())

    def nav_restore(self, state):
        kind, data = state
        if kind == "source":
            source = self._source_by_id(data.source_id)
            if source is not None:
                self._source_view.restore_state(source, data)
                self._view_stack.setCurrentWidget(self._source_view)
                return
            kind, data = "overview", 0  # the subscription was deleted since
        if kind == "author":
            self._author_view.restore_state(data)
            self._view_stack.setCurrentWidget(self._author_view)
        elif kind == "table":
            self._set_view_mode("table")
            if data:
                self._select_source_id(int(data))
        else:
            self._set_view_mode("overview")
            self._overview.reload_if_dirty()
            restore_scroll(self._overview._scroll, int(data or 0))

    def select_all_in_view(self):
        if self._view_stack.currentWidget() is self._source_view:
            self._source_view._grid.select_all(True)
            return
        # Elsewhere the key keeps its usual meaning for the focused control.
        focus = QApplication.focusWidget()
        if focus is not None and hasattr(focus, "selectAll"):
            focus.selectAll()

    def clear_selection_in_view(self):
        if self._view_stack.currentWidget() is self._source_view:
            self._source_view._grid.clear_selection()
            return
        focus = QApplication.focusWidget()
        if focus is not None and hasattr(focus, "clearSelection"):
            focus.clearSelection()

    def dismiss_transient(self) -> bool:
        """Esc on the page: first drop the grid's selection, if there is one."""

        current = self._view_stack.currentWidget()
        grid = {
            self._source_view: self._source_view._grid,
            self._author_view: self._author_view._browse._grid,
        }.get(current)
        if grid is None or not grid.selected_videos():
            return False
        grid.clear_selection()
        return True

    def toggle_view_mode(self):
        self._set_view_mode("table" if self._view_mode_is_overview() else "overview")

    def focus_filter(self):
        current = self._view_stack.currentWidget()
        edit = {
            self._overview: getattr(self._overview, "_search", None),
            self._source_view: getattr(self._source_view, "_search", None),
            self._table_page: getattr(self, "_source_search_edit", None),
        }.get(current)
        if edit is not None:
            edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            edit.selectAll()

    def _view_mode_is_overview(self) -> bool:
        return str(app_config.get_ui_value("subscription_view_mode_v1", "overview") or "overview") != "table"

    def _close_source_grid(self):
        self._leave_page_view()

    def _close_author_view(self):
        self._leave_page_view()

    def show_source(self, source_id: int):
        """Bring one source to the front (the window has recorded where the user was)."""

        self._select_source_id(source_id)
        if self._view_stack.currentWidget() is not self._table_page:
            self._open_source_grid(source_id, record=False)

    def show_author(self, target, *, external: bool = False):
        """Open an author inside the app.

        A local subscription opens on its poster grid; anyone else gets a live
        author page with the subscribe/follow controls.  ``external`` means the
        window already recorded where the user came from.
        """

        username = clean_username(target[0]) if target else ""
        if not username:
            return
        source = download_manager.find_author_subscription(username)
        source_id = int((source or {}).get("id", 0) or 0)
        if source_id and any(int(s.get("id", 0) or 0) == source_id for s in self._all_sources):
            self._open_source_grid(source_id, record=not external)
            return
        if not external and not (
            self._view_stack.currentWidget() is self._author_view and self._author_view.username == username
        ):
            record_navigation(self)
        self._author_view.open_author(tuple(target))
        self._view_stack.setCurrentWidget(self._author_view)

    def _schedule_views_refresh(self, *_args):
        self._overview.mark_dirty()
        if not self._shutting_down:
            self._views_timer.start()

    def _refresh_views(self):
        if self._shutting_down:
            return
        current = self._view_stack.currentWidget()
        self._author_view.refresh_status()
        self._source_view.refresh_status()
        if current is self._source_view and self._source_view.source_id and not any(
            int(s.get("id", 0) or 0) == self._source_view.source_id for s in self._all_sources
        ):
            self._show_list_view()  # the subscription being viewed was deleted
            return
        if current is self._source_view:
            self._source_view.reload_if_visible()
        elif current is self._overview and self.isVisible():
            self._overview.reload()

    def _recent_items(self, per_source: int) -> dict[int, list[dict[str, Any]]]:
        try:
            try:
                recent = download_manager.get_subscription_recent_items(per_source, resolve_thumbnails=False)
            except TypeError:  # a manager without the option
                recent = download_manager.get_subscription_recent_items(per_source)
        except Exception:
            return {}
        return recent if isinstance(recent, dict) else {}

    def _cover_path(self, video_id: str, thumbnail_url: str = "") -> str:
        """The video's cover if it is already cached on disk (cheap, one file check)."""

        try:
            path = download_manager.subscription_cover_path(video_id, thumbnail_url)
        except Exception:
            return ""
        return path if isinstance(path, str) else ""

    def _source_items(self, source_id: int) -> list[dict[str, Any]]:
        items = download_manager.get_subscription_items(int(source_id))
        return list(items) if isinstance(items, list) else []

    def _source_page_url(self, source: dict[str, Any]) -> str:
        return _source_url(source)

    def _refresh_source_ids_from_view(self, source_id: int):
        self._start_refresh(int(source_id), ignore_disabled=True)

    def _show_video_detail(self, video):
        signal_bus.media_detail_requested.emit("video", video.video_id)
