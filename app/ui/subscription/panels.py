"""Source / item panel splitter, panel toggles and table column fitting."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHeaderView

from qfluentwidgets import FluentIcon

from ...i18n import tr
from ..ui_state import fit_table_last_column, open_table_column_dialog


class SubscriptionPanelsMixin:
    """Source / item panel splitter, panel toggles and table column fitting."""

    def _on_splitter_moved(self, *_args):
        sizes = list(self._splitter.sizes())
        if self._source_panel_visible and self._items_panel_visible and all(size > 0 for size in sizes):
            self._last_splitter_sizes = sizes
        self._fit_source_table_last_column()
        self._fit_item_table_last_column()
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()

    def _update_panel_toggle_buttons(self):
        if not hasattr(self, "_toggle_sources_btn"):
            return
        source_hidden = not self._source_panel_visible
        items_hidden = not self._items_panel_visible
        vertical = bool(self._splitter_is_vertical)
        source_direction_icon = FluentIcon.UP if vertical else FluentIcon.LEFT_ARROW
        items_direction_icon = FluentIcon.DOWN if vertical else FluentIcon.RIGHT_ARROW
        self._toggle_sources_btn.setIcon(
            FluentIcon.VIEW if source_hidden else source_direction_icon
        )
        self._toggle_items_btn.setIcon(
            FluentIcon.VIEW if items_hidden else items_direction_icon
        )
        self._toggle_sources_btn.setToolTip(
            tr(
                (
                    "Show subscription sources"
                    if source_hidden
                    else "Hide the upper panel"
                    if vertical
                    else "Hide subscription sources"
                ),
                (
                    "显示订阅源"
                    if source_hidden
                    else "隐藏上方区域"
                    if vertical
                    else "隐藏订阅源"
                ),
                (
                    "購読元を表示"
                    if source_hidden
                    else "上側の領域を隠す"
                    if vertical
                    else "購読元を隠す"
                ),
            )
        )
        self._toggle_items_btn.setToolTip(
            tr(
                (
                    "Show video list"
                    if items_hidden
                    else "Hide the lower panel"
                    if vertical
                    else "Hide video list"
                ),
                (
                    "显示视频列表"
                    if items_hidden
                    else "隐藏下方区域"
                    if vertical
                    else "隐藏视频列表"
                ),
                (
                    "動画一覧を表示"
                    if items_hidden
                    else "下側の領域を隠す"
                    if vertical
                    else "動画一覧を隠す"
                ),
            )
        )

    def _set_panel_visible(self, index: int, visible: bool):
        if index == 0:
            if not visible and not self._items_panel_visible:
                return
            self._source_panel_visible = visible
            self._source_panel.setVisible(visible)
        else:
            if not visible and not self._source_panel_visible:
                return
            self._items_panel_visible = visible
            self._items_panel.setVisible(visible)

        if self._source_panel_visible and self._items_panel_visible:
            sizes = list(self._last_splitter_sizes)
            total = max(2, self._splitter.height() if self._splitter_is_vertical else self._splitter.width())
            if len(sizes) != 2 or sum(sizes) <= 0 or min(sizes) <= 0:
                sizes = [int(total * 0.4), int(total * 0.6)]
            self._splitter.setSizes(sizes)
        else:
            visible_index = 0 if self._source_panel_visible else 1
            visible_panel = self._source_panel if visible_index == 0 else self._items_panel
            visible_panel.setVisible(True)
            total = max(1, sum(self._splitter.sizes()))
            sizes = [0, 0]
            sizes[visible_index] = total
            self._splitter.setSizes(sizes)
        self._update_panel_toggle_buttons()
        self._fit_source_table_last_column()
        self._fit_item_table_last_column()
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()

    def _toggle_source_panel(self):
        self._set_panel_visible(0, not self._source_panel_visible)

    def _toggle_item_panel(self):
        self._set_panel_visible(1, not self._items_panel_visible)

    def _fit_source_table_last_column(self):
        if not hasattr(self, "_source_table"):
            return
        header = self._source_table.horizontalHeader()
        visible_columns = [
            column for column in range(self._source_table.columnCount())
            if not self._source_table.isColumnHidden(column)
        ]
        if not visible_columns:
            return
        visible_columns.sort(key=header.visualIndex)
        last_column = visible_columns[-1]
        for column in visible_columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        # Keep the final visible field bound to the panel width. The splitter
        # therefore gives its extra space to the last source-table column.
        header.setSectionResizeMode(last_column, QHeaderView.ResizeMode.Stretch)

    def _fit_item_table_last_column(self):
        if hasattr(self, "_item_table"):
            fit_table_last_column(self._item_table)

    def _fit_item_controls_height(self):
        """Grow the video-controls pane so its buttons are not cut in half.

        The pane stays user-resizable: a collapsed pane (size 0) is respected,
        and the video table always keeps at least ``_MIN_ITEM_TABLE_HEIGHT``.
        """
        splitter = getattr(self, "_item_content_splitter", None)
        scroll = getattr(self, "_item_controls_scroll", None)
        if splitter is None or scroll is None or not splitter.isVisible():
            return
        sizes = splitter.sizes()
        total = sum(sizes)
        if len(sizes) != 2 or total <= 0 or sizes[0] <= 0:
            return
        panel = scroll.widget()
        layout = panel.layout() if panel is not None else None
        width = scroll.viewport().width()
        if layout is None or width <= 0:
            return
        if layout.hasHeightForWidth():
            needed = layout.totalHeightForWidth(width)
        else:
            needed = layout.totalSizeHint().height()
        needed += 2
        deficit = needed + self._MIN_ITEM_TABLE_HEIGHT - total
        outer = self._splitter.sizes()
        if (
            deficit > 0
            and self._splitter_is_vertical
            and self._source_panel_visible
            and self._items_panel_visible
            and len(outer) == 2
            and outer[0] > self._MIN_STACKED_SOURCE_HEIGHT
            and not getattr(self, "_rebalancing_item_panel", False)
        ):
            # Stacked panels: borrow height from the source list so the video
            # controls are not cut in half, then fit again on the next pass.
            moved = min(deficit, outer[0] - self._MIN_STACKED_SOURCE_HEIGHT)
            self._rebalancing_item_panel = True
            self._splitter.setSizes([outer[0] - moved, outer[1] + moved])
            QTimer.singleShot(0, self._finish_item_panel_rebalance)
            return
        table_floor = min(self._MIN_ITEM_TABLE_HEIGHT, int(total * 0.35))
        needed = min(needed, max(0, total - table_floor))
        if sizes[0] < needed:
            splitter.setSizes([needed, total - needed])

    def _finish_item_panel_rebalance(self):
        self._fit_item_controls_height()
        self._rebalancing_item_panel = False

    def _configure_source_columns(self):
        open_table_column_dialog(
            self._source_table,
            "subscription_source_table_v5",
            title=tr("Source Columns", "订阅源字段", "購読元列設定"),
            default_visible=[
                self._SRC_AVATAR,
                self._SRC_STATE,
                self._SRC_TITLE,
                self._SRC_KEY,
                self._SRC_NEW,
                self._SRC_UNDOWNLOADED,
                self._SRC_ITEMS,
                self._SRC_CREATED,
                self._SRC_URL,
            ],
            parent=self,
        )
        self._fit_source_table_last_column()

    def _configure_item_columns(self):
        open_table_column_dialog(
            self._item_table,
            "subscription_item_table_v2",
            title=tr("Video Columns", "作品列表字段", "動画列設定"),
            parent=self,
        )
        self._fit_item_table_last_column()
