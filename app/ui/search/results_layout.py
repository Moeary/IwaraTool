"""Grid / list switching, card sizing and selection of the result views."""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, QSize, QTimer
from PySide6.QtWidgets import QHeaderView

from ...config import app_config
from ...i18n import tr
from .grid_style import _grid_item_height
from ..ui_state import open_table_column_dialog
from ..media_card import CARD_WIDTH_MAX, CARD_WIDTH_MIN


_VIDEO_ICON_SIZE = QSize(260, 146)


_DEFAULT_GRID_HEIGHT = 238


_MAX_GRID_COLUMNS = 12


class SearchResultsLayoutMixin:
    """Grid / list switching, card sizing and selection of the result views."""

    def _grid_only_results(self) -> bool:
        """Author and playlist cards have no table columns to show."""

        scope = str(self._scope_combo.currentData() or "videos")
        return scope == "authors" or bool(self._all_playlists)

    def _is_list_view(self) -> bool:
        return (
            str(self._view_combo.currentData() or "grid") == "list"
            and not self._grid_only_results()
        )

    def _sync_view_controls(self):
        if not hasattr(self, "_view_combo"):
            return
        # Card-only results show the grid without overwriting the saved view,
        # so returning to video results restores the user's list preference.
        grid_only = self._grid_only_results()
        saved_view = str(app_config.get_ui_value("search_view_mode_v1", "grid") or "grid")
        self._view_combo.blockSignals(True)
        self._view_combo.setCurrentIndex(0 if grid_only or saved_view != "list" else 1)
        self._view_combo.blockSignals(False)
        self._view_combo.setEnabled(not grid_only)
        list_mode = self._is_list_view()
        grid_mode = not list_mode
        self._card_size_control.setVisible(grid_mode)
        self._result_fields_btn.setVisible(list_mode)
        if hasattr(self, "_results_stack"):
            self._results_stack.setCurrentIndex(1 if list_mode else 0)

    def _on_view_changed(self, _index: int):
        app_config.set_ui_value(
            "search_view_mode_v1",
            str(self._view_combo.currentData() or "grid"),
        )
        self._sync_view_controls()
        self._render_results()
        if not self._is_list_view():
            self._start_image_loading()

    def _on_card_size_changed(self, width: int):
        self._card_min_width = max(CARD_WIDTH_MIN, min(CARD_WIDTH_MAX, int(width)))
        self._resize_grid()
        self._schedule_grid_resize()

    def _grid_columns_for(self, available_width: int, spacing: int) -> int:
        """Cards per row for the chosen cover size: as many as fit at least that wide."""

        return max(1, min(_MAX_GRID_COLUMNS, (available_width + spacing) // (self._card_min_width + spacing)))

    def _configure_result_columns(self):
        open_table_column_dialog(
            self._results_table,
            "search_result_table_v2",
            title=tr("Search Result Fields", "搜索结果字段", "検索結果の列設定"),
            default_visible=[0, 1, 2, 3, 4, 5, 7, 8, 12],
            default_order=[0, 1, 2, 12, 3, 4, 5, 6, 7, 8, 9, 10, 11],
            parent=self,
        )
        self._fit_results_table_last_column()

    def _fit_results_table_last_column(self):
        if not hasattr(self, "_results_table"):
            return
        header = self._results_table.horizontalHeader()
        visible_columns = [
            column
            for column in range(self._results_table.columnCount())
            if not self._results_table.isColumnHidden(column)
        ]
        if not visible_columns:
            return
        visible_columns.sort(key=header.visualIndex)
        last_column = visible_columns[-1]
        for column in visible_columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(last_column, QHeaderView.ResizeMode.Stretch)

    def _toggle_view_mode(self):
        """Switch between the poster grid and the table (when the results allow it)."""

        if not self._view_combo.isEnabled():
            return
        self._view_combo.setCurrentIndex(0 if self._is_list_view() else 1)

    def _resize_grid(self):
        if not hasattr(self, "_results"):
            return
        width = self._results.viewport().width()
        if width <= 0:
            return
        spacing = 12
        # Keep a small fixed reserve for the vertical scrollbar and Qt's list
        # layout rounding.  Without it a 1208px viewport calculates 301px
        # cells, which makes IconMode wrap four requested columns into three as
        # soon as the scrollbar becomes visible.
        scrollbar_reserve = max(
            8,
            int(self._results.verticalScrollBar().sizeHint().width()) - 1,
        )
        available_width = max(1, width - scrollbar_reserve)
        columns = self._grid_columns_for(available_width, spacing)
        cell_width = max(
            40,
            (available_width - spacing * (columns - 1)) // columns,
        )
        # Let the cover occupy the card width.  The old 260px cap made a
        # one-column result look like a small preview floating in a large
        # blank card, and was especially obvious on wide displays.
        image_width = max(40, cell_width - 12)
        image_height = max(40, round(image_width * 9 / 16))
        grid_height = _grid_item_height(
            self._results,
            cell_width,
            QSize(image_width, image_height),
            fallback_lines=5 if self._all_authors or self._all_playlists else 6,
        )
        previous_icon_size = getattr(self, "_grid_icon_size", None)
        self._grid_icon_size = QSize(image_width, image_height)
        self._grid_item_size = QSize(cell_width, grid_height)
        updates_enabled = self._results.updatesEnabled()
        self._results.setUpdatesEnabled(False)
        try:
            self._results.setIconSize(self._grid_icon_size)
            if previous_icon_size != self._grid_icon_size:
                # QIcon only scales pixmaps down. Covers rendered for a smaller
                # cell would otherwise stay small and centered in a wide card.
                self._refresh_item_icons()
            self._results.setGridSize(self._grid_item_size)
            self._results.setSpacing(spacing)
            for index in range(self._results.count()):
                self._results.item(index).setSizeHint(self._grid_item_size)
            # Force IconMode to place every item before the next paint.  This
            # removes the transient three-cards-then-fill-in effect when the
            # column selector changes.
            self._results.doItemsLayout()
        finally:
            self._results.setUpdatesEnabled(updates_enabled)
        self._results.updateGeometry()
        if updates_enabled:
            self._results.viewport().update()

    def _schedule_grid_resize(self):
        """Repeat a grid pass after the parent layout has settled."""

        if self._grid_resize_pending:
            return
        self._grid_resize_pending = True
        QTimer.singleShot(0, self._run_scheduled_grid_resize)

    def _run_scheduled_grid_resize(self):
        self._grid_resize_pending = False
        self._resize_grid()

    def _result_view(self):
        return self._results_table if self._is_list_view() else self._results

    def _selected_keys(self) -> tuple[str, ...]:
        return tuple(
            str(value.get("key") or "") for value in self._selected_data() if value.get("key")
        )

    def _select_keys(self, keys: set[str]):
        if not keys:
            return
        if self._is_list_view():
            model = self._results_table.selectionModel()
            flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
            for row in range(self._results_table.rowCount()):
                item = self._results_table.item(row, 0)
                value = item.data(self._DATA_ROLE) if item else None
                if isinstance(value, dict) and value.get("key") in keys:
                    model.select(self._results_table.model().index(row, 0), flags)
        else:
            for key in keys:
                item = self._item_by_key.get(key)
                if item is not None:
                    item.setSelected(True)
        self._sync_selection_buttons()
