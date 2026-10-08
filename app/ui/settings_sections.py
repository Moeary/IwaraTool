"""Settings navigation: category list and the card pages it switches between."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    ListWidget,
    ScrollArea,
    SubtitleLabel,
)

from ..config import app_config
from ..i18n import tr
from .theme import set_secondary_text


def settings_categories() -> tuple[tuple[str, FluentIcon, str, str, tuple[str, ...]], ...]:
    """Settings grouped by what they control: (key, icon, title, summary, cards).

    Built on demand so the labels follow the language chosen at startup.
    """

    return (
        (
            "general",
            FluentIcon.SETTING,
            tr("General", "通用", "一般"),
            tr(
                "Appearance, language, updates and where local data is stored.",
                "外观、语言、更新检查与本地数据位置。",
                "外観、言語、更新確認、ローカルデータの保存先。",
            ),
            ("appearance", "language", "updates", "data_paths"),
        ),
        (
            "network",
            FluentIcon.GLOBE,
            tr("Account & Network", "账号与网络", "アカウントとネットワーク"),
            tr(
                "Iwara sign-in and the proxies used for API requests and downloads.",
                "Iwara 账号登录，以及 API 请求与下载使用的代理。",
                "Iwaraへのログインと、API・ダウンロードに使うプロキシ。",
            ),
            ("account", "proxy"),
        ),
        (
            "downloads",
            FluentIcon.DOWNLOAD,
            tr("Downloads", "下载", "ダウンロード"),
            tr(
                "Save location, quality, concurrency, speed and schedule, and aria2.",
                "保存位置、画质、并发、限速与分时，以及 aria2。",
                "保存先、画質、同時実行数、速度と時間帯、aria2。",
            ),
            ("download_dir", "quality", "behavior", "concurrency", "download_policy", "aria2"),
        ),
        (
            "search",
            FluentIcon.SEARCH,
            tr("Search", "搜索", "検索"),
            tr(
                "Search history, download limits, tag dictionary and Oreno3D resolution.",
                "搜索历史、搜索下载上限、标签词典与 Oreno3D 解析。",
                "検索履歴、ダウンロード上限、タグ辞書、Oreno3D の解決。",
            ),
            ("search_history", "search_limit", "search_bridge"),
        ),
        (
            "subscriptions",
            FluentIcon.SYNC,
            tr("Subscriptions & Refresh", "订阅与刷新", "購読と更新"),
            tr(
                "Automatic subscription refresh, subscribe prompts and cover loading.",
                "订阅自动刷新、下载时的订阅提示与封面加载性能。",
                "購読の自動更新、ダウンロード時の購読確認、カバー読み込み性能。",
            ),
            ("subscription_automation", "subscription_prompt", "cover_performance"),
        ),
        (
            "system",
            FluentIcon.DEVELOPER_TOOLS,
            tr("System & Maintenance", "系统与维护", "システムと保守"),
            tr(
                "Tray and startup behaviour, request pacing, backups and logs.",
                "托盘与开机启动、请求节奏、备份与日志。",
                "トレイと起動、リクエスト間隔、バックアップとログ。",
            ),
            ("window", "request_policy", "maintenance"),
        ),
    )


class SettingsSections(QWidget):
    """Category navigation on the left, one scrollable card page per category."""

    _CATEGORY_KEY = "settings_category_v1"
    _NAV_WIDTH = 216

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsSections")
        self._categories = settings_categories()
        self._category_by_card = {
            card_key: category[0]
            for category in self._categories
            for card_key in category[4]
        }
        self._category_index = {category[0]: index for index, category in enumerate(self._categories)}
        self._page_layouts: dict[str, QVBoxLayout] = {}
        self._page_contents: dict[str, QWidget] = {}
        self._added_cards: dict[str, list[str]] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)

        self.nav_column = QVBoxLayout()
        self.nav_column.setContentsMargins(0, 0, 0, 0)
        self.nav_column.setSpacing(12)
        self._nav = ListWidget(self)
        self._nav.setObjectName("settingsCategoryNav")
        self._nav.setFixedWidth(self._NAV_WIDTH)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.nav_column.addWidget(self._nav, 1)
        row.addLayout(self.nav_column)

        self._stack = QStackedWidget(self)
        row.addWidget(self._stack, 1)

        for key, icon, title, summary, _cards in self._categories:
            item = QListWidgetItem(icon.icon(), title)
            item.setSizeHint(QSize(self._NAV_WIDTH - 8, 44))
            item.setToolTip(summary)
            self._nav.addItem(item)
            self._stack.addWidget(self._build_page(key, title, summary))

        self._nav.currentRowChanged.connect(self._on_category_changed)
        saved = str(app_config.get_ui_value(self._CATEGORY_KEY, "general") or "general")
        self._nav.setCurrentRow(self._category_index.get(saved, 0))

    def _build_page(self, key: str, title: str, summary: str) -> ScrollArea:
        scroll = ScrollArea(self._stack)
        scroll.setObjectName(f"settingsPage_{key}")
        # A native QScrollArea viewport otherwise keeps its light palette and
        # paints an opaque white page over FluentWindow's Mica/dark background.
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea#settingsPage_{key} {{ background: transparent; border: none; }}"
            f"QScrollArea#settingsPage_{key} > QWidget > QWidget {{ background: transparent; }}"
        )
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget(scroll)
        content.setAutoFillBackground(False)
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        # Room on the right keeps cards clear of the overlay scrollbar.
        layout.setContentsMargins(0, 0, 12, 12)
        layout.setSpacing(16)
        layout.addWidget(SubtitleLabel(title, content))
        summary_label = CaptionLabel(summary, content)
        summary_label.setWordWrap(True)
        set_secondary_text(summary_label)
        layout.addWidget(summary_label)
        layout.addStretch(1)
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        self._page_layouts[key] = layout
        self._page_contents[key] = content
        return scroll

    def _on_category_changed(self, row: int):
        if not 0 <= row < len(self._categories):
            return
        self._stack.setCurrentIndex(row)
        app_config.set_ui_value(self._CATEGORY_KEY, self._categories[row][0])

    def current_category(self) -> str:
        row = self._nav.currentRow()
        return self._categories[row][0] if 0 <= row < len(self._categories) else ""

    def select_category(self, key: str):
        index = self._category_index.get(str(key or ""))
        if index is not None:
            self._nav.setCurrentRow(index)

    def _category_for(self, card_key: str) -> str:
        # Unknown cards land in General rather than disappearing.
        return self._category_by_card.get(card_key, self._categories[0][0])

    def create_card(self, card_key: str) -> CardWidget:
        card = CardWidget(self._page_contents[self._category_for(card_key)])
        card.setProperty("settingsCardKey", str(card_key))
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return card

    def add_card(self, card_key: str, card: CardWidget):
        card_key = str(card_key).strip()
        if not card_key:
            return
        # Long descriptions must wrap instead of widening the page.
        for label in card.findChildren(BodyLabel):
            label.setWordWrap(True)
            label.setMinimumWidth(0)
        # Labels placed directly in the card's column are descriptions; those
        # nested in rows name a control and keep the primary text color.
        card_layout = card.layout()
        if card_layout is not None:
            for index in range(card_layout.count()):
                widget = card_layout.itemAt(index).widget()
                if type(widget) is BodyLabel:
                    set_secondary_text(widget)
            self._let_row_labels_grow(card_layout)
        category = self._category_for(card_key)
        order = self._categories[self._category_index[category]][4]
        rank = order.index(card_key) if card_key in order else len(order)
        added = self._added_cards.setdefault(category, [])
        # Cards follow the category's declared order, after the page title and
        # summary and before the trailing stretch that keeps them top-aligned.
        position = 2 + sum(
            1 for key in added if (order.index(key) if key in order else len(order)) <= rank
        )
        added.append(card_key)
        self._page_layouts[category].insertWidget(position, card)

    @classmethod
    def _let_row_labels_grow(cls, layout):
        """Give a row's leading name label the spare width; controls go right.

        A wrapped label's size hint is deliberately narrow; without a stretch
        factor the trailing addStretch() spacer took the space instead. Only
        the first label is stretched: unit or separator labels after a control
        ("1-100", "—") would otherwise split the controls across the middle.
        """
        for index in range(layout.count()):
            item = layout.itemAt(index)
            child_layout = item.layout()
            if child_layout is not None:
                if isinstance(child_layout, QHBoxLayout):
                    first = child_layout.itemAt(0).widget() if child_layout.count() else None
                    if type(first) is BodyLabel:
                        for child_index in range(child_layout.count()):
                            child_layout.setStretch(child_index, 1 if child_index == 0 else 0)
                cls._let_row_labels_grow(child_layout)
            elif item.widget() is not None and item.widget().layout() is not None:
                cls._let_row_labels_grow(item.widget().layout())
