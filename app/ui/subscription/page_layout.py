"""Build the subscription page widgets: view stack, source panel and item panel."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from qfluentwidgets import CardWidget, SegmentedWidget, ToolButton

from ...config import app_config
from ...i18n import tr
from ..author_view import AuthorView
from ..chrome import PageHeader
from .components import (
    _CONTROL_HEIGHT,
    _FluentContentSplitter,
    _ROW_SPACING,
    _apply_fluent_scrollbars,
    _style_content_splitter,
)
from .views import SourceItemsView, SubscriptionOverview
from ..theme import PAGE_MARGINS
from ..ui_state import connect_splitter_saver, fit_table_last_column, restore_splitter_sizes
from .item_panel_layout import SubscriptionItemPanelLayoutMixin
from .source_panel_layout import SubscriptionSourcePanelLayoutMixin


class SubscriptionPageLayoutMixin(
    SubscriptionSourcePanelLayoutMixin,
    SubscriptionItemPanelLayoutMixin,
):
    """Build the subscription page widgets: view stack, source panel and item panel."""

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)
        self._build_page_header(root)
        self._build_view_stack(root)
        left_layout, right_panel, right_layout = self._build_table_panels()
        item_controls_layout, item_content_layout = self._build_item_content_splitter(right_panel, right_layout)
        self._restore_table_splitter()
        self._build_panel_toggles()
        self._build_source_actions(left_layout)
        self._build_source_meta_row(left_layout)
        self._build_source_table(left_layout)
        self._build_item_summary_row(item_controls_layout)
        self._build_item_action_menus(item_controls_layout)
        self._build_download_rule_row(item_controls_layout)
        self._build_title_filter_row(item_controls_layout)
        self._build_item_filter_row(item_controls_layout)
        self._build_item_table()
        self._build_thumbnail_list()
        self._build_item_stack(item_content_layout)
        self._apply_initial_state()

    def _build_page_header(self, root: QVBoxLayout):
        # Same header as Home: title on the left, the page's switches on the right.
        self._page_header = PageHeader(tr("Subscriptions", "订阅页", "購読"), self)
        self._view_switch = SegmentedWidget(self)
        self._view_switch.addItem("overview", tr("Overview", "总览", "概要"), lambda: self._set_view_mode("overview"))
        self._view_switch.addItem("table", tr("Table", "经典表格", "表形式"), lambda: self._set_view_mode("table"))
        self._page_header.tools.addWidget(self._view_switch)
        root.addWidget(self._page_header)

    def _build_view_stack(self, root: QVBoxLayout):
        splitter = _FluentContentSplitter(Qt.Orientation.Horizontal, self)
        self._splitter = splitter
        self._splitter_is_vertical = False
        self._source_panel_visible = True
        self._items_panel_visible = True
        self._last_splitter_sizes = [1000, 1000]
        splitter.splitterMoved.connect(self._on_splitter_moved)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        _style_content_splitter(splitter)
        # The table view is one page of a stack next to the overview and the
        # per-source grid; its widgets and logic are unchanged.
        self._view_stack = QStackedWidget(self)
        self._table_page = QWidget(self._view_stack)
        table_layout = QVBoxLayout(self._table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(splitter, stretch=1)
        self._overview = SubscriptionOverview(self, self._covers, self._view_stack)
        self._source_view = SourceItemsView(self, self._covers, self._view_stack)
        self._view_stack.addWidget(self._overview)
        self._author_view = AuthorView(self._view_stack)
        self._view_stack.addWidget(self._source_view)
        self._view_stack.addWidget(self._author_view)
        self._view_stack.addWidget(self._table_page)
        self._overview.source_opened.connect(self._open_source_grid)
        self._source_view.back_requested.connect(self._close_source_grid)
        self._author_view.back_requested.connect(self._close_author_view)
        self._author_view.open_subscription_requested.connect(self._open_source_grid)
        root.addWidget(self._view_stack, stretch=1)

    def _build_table_panels(self):
        splitter = self._splitter
        left_panel = CardWidget(self)
        self._source_panel = left_panel
        left_panel.setObjectName("SubscriptionSourcesCard")
        left_panel.setMinimumWidth(0)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.setSpacing(_ROW_SPACING)
        splitter.addWidget(left_panel)

        right_panel = CardWidget(self)
        self._items_panel = right_panel
        right_panel.setObjectName("SubscriptionItemsCard")
        right_panel.setMinimumWidth(0)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(_ROW_SPACING)
        splitter.addWidget(right_panel)
        return left_layout, right_panel, right_layout

    def _build_item_content_splitter(self, right_panel: QWidget, right_layout: QVBoxLayout):
        self._item_content_splitter = _FluentContentSplitter(Qt.Orientation.Vertical, right_panel)
        self._item_content_splitter.setObjectName("SubscriptionItemContentSplitter")
        self._item_content_splitter.setChildrenCollapsible(True)
        self._item_content_splitter.setHandleWidth(10)
        self._item_content_splitter.setToolTip(
            tr(
                "Drag this handle to resize or collapse the video controls.",
                "拖动此分隔条调整或隐藏视频控制区。",
                "このハンドルをドラッグして動画操作領域を調整・折りたたみます。",
            )
        )
        _style_content_splitter(self._item_content_splitter)
        item_controls_scroll = QScrollArea(self._item_content_splitter)
        self._item_controls_scroll = item_controls_scroll
        item_controls_scroll.setObjectName("SubscriptionItemControlsScroll")
        item_controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        item_controls_scroll.setWidgetResizable(True)
        item_controls_scroll.setMinimumSize(0, 0)
        item_controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        item_controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        item_controls_scroll.setStyleSheet(
            "QScrollArea#SubscriptionItemControlsScroll { background: transparent; border: none; }"
            "QScrollArea#SubscriptionItemControlsScroll > QWidget > QWidget { background: transparent; }"
        )
        item_controls_scroll.viewport().setAutoFillBackground(False)
        _apply_fluent_scrollbars(item_controls_scroll)
        item_controls_panel = QWidget()
        item_controls_panel.setObjectName("SubscriptionItemControlsPanel")
        item_controls_panel.setMinimumHeight(0)
        item_controls_layout = QVBoxLayout(item_controls_panel)
        item_controls_layout.setContentsMargins(0, 0, 0, 0)
        item_controls_layout.setSpacing(_ROW_SPACING)
        item_controls_scroll.setWidget(item_controls_panel)
        item_content_panel = QWidget(self._item_content_splitter)
        item_content_panel.setObjectName("SubscriptionItemContentPanel")
        item_content_panel.setMinimumHeight(0)
        item_content_layout = QVBoxLayout(item_content_panel)
        item_content_layout.setContentsMargins(0, 0, 0, 0)
        item_content_layout.setSpacing(0)
        self._item_content_splitter.addWidget(item_controls_scroll)
        self._item_content_splitter.addWidget(item_content_panel)
        self._item_content_splitter.setStretchFactor(0, 0)
        self._item_content_splitter.setStretchFactor(1, 1)
        right_layout.addWidget(self._item_content_splitter, stretch=1)
        return item_controls_layout, item_content_layout

    def _restore_table_splitter(self):
        splitter = self._splitter
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        restore_splitter_sizes(splitter, "subscription_splitter_sizes_v2", [1000, 1000])
        connect_splitter_saver(splitter, "subscription_splitter_sizes_v2")
        self._last_splitter_sizes = list(splitter.sizes()) or [1000, 1000]

    def _build_panel_toggles(self):
        self._toggle_sources_btn = ToolButton(self)
        self._toggle_items_btn = ToolButton(self)
        for button in (self._toggle_sources_btn, self._toggle_items_btn):
            button.setFixedSize(40, _CONTROL_HEIGHT)
        self._toggle_sources_btn.clicked.connect(self._toggle_source_panel)
        self._toggle_items_btn.clicked.connect(self._toggle_item_panel)
        self._page_header.tools.addWidget(self._toggle_sources_btn)
        self._page_header.tools.addWidget(self._toggle_items_btn)
        self._update_panel_toggle_buttons()

    def _apply_initial_state(self):
        self._update_selection_actions()
        self._sync_item_grid_controls()
        self._update_thumbnail_grid()
        fit_table_last_column(self._item_table)
        mode = str(app_config.get_ui_value("subscription_view_mode_v1", "overview") or "overview")
        self._set_view_mode("table" if mode == "table" else "overview", persist=False)
