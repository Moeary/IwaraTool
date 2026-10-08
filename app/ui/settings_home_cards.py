"""Settings cards for the Home page: which rows it shows and how they cache.

Both cards apply immediately, like the other self-applying cards, so they take
no part in the page-wide "Save All Settings" flow.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    SubtitleLabel,
)

from ..config import app_config
from ..core.home_cache import DEFAULT_CACHE_MINUTES, HOME_CACHE_MINUTES_KEY, shared_cache
from ..core.home_feed import default_specs, load_specs, save_specs
from ..i18n import tr
from ..signal_bus import signal_bus
from .home_layout_dialog import HomeLayoutDialog
from .theme import set_secondary_text
from .ui_state import show_fluent_confirmation


class HomeSettingsCards:
    """Builds the Home cards into a ``SettingsSections`` board."""

    def __init__(self, board, owner: QWidget):
        self._owner = owner
        self._loading = True
        self._build_layout_card(board)
        self._build_cache_card(board)
        self.load()
        signal_bus.home_layout_changed.connect(self._refresh_summary)

    @staticmethod
    def _card_layout(card, title: str, description: str = "") -> QVBoxLayout:
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel(title, card))
        if description:
            layout.addWidget(BodyLabel(description, card))
        return layout

    def _build_layout_card(self, board):
        card = board.create_card("home_layout")
        layout = self._card_layout(
            card,
            tr("Home rows", "首页栏目", "ホームの欄"),
            tr(
                "Rows can be the newest uploads, your subscription feed, hot lists, or any tag, "
                "keyword or author search from the Search page.",
                "栏目可以是最新上传、账号订阅流、热门榜单，或搜索页里能搜到的任何标签、关键词、作者。",
                "欄には、新着・購読フィード・人気リスト、または検索ページで使えるタグ/キーワード/作者を指定できます。",
            ),
        )
        self._summary = CaptionLabel("", card)
        self._summary.setWordWrap(True)
        set_secondary_text(self._summary)
        layout.addWidget(self._summary)
        row = QHBoxLayout()
        edit = PrimaryPushButton(tr("Customize rows…", "自定义栏目…", "欄をカスタマイズ…"), card, FluentIcon.EDIT)
        edit.clicked.connect(lambda: HomeLayoutDialog.edit(self._owner.window()))
        reset = PushButton(tr("Reset to default", "恢复默认", "初期設定に戻す"), card)
        reset.clicked.connect(self._reset_layout)
        row.addWidget(edit)
        row.addWidget(reset)
        row.addStretch(1)
        layout.addLayout(row)
        board.add_card("home_layout", card)

    def _build_cache_card(self, board):
        card = board.create_card("home_cache")
        layout = self._card_layout(
            card,
            tr("Home cache", "首页缓存", "ホームのキャッシュ"),
            tr(
                "Rows are kept on disk and shown instantly. A row only asks the site again once it is "
                "older than this, and the cards change only if its posts did.",
                "各栏目缓存在本地并即时显示；只有超过下面的时长才会重新向网站检查，且仅在内容有变化时才更新卡片。",
                "各欄はディスクに保存されすぐ表示されます。下の時間を過ぎたときだけサイトに再確認し、内容が変わった場合のみ更新します。",
            ),
        )
        row = QHBoxLayout()
        row.addWidget(BodyLabel(
            tr(
                "Re-check a row after (minutes, 0 = only when I refresh)",
                "多少分钟后重新检查（0 = 仅手动刷新）",
                "再確認までの分数（0 = 手動のみ）",
            ),
            card,
        ))
        row.addStretch(1)
        self._minutes = SpinBox(card)
        self._minutes.setRange(0, 24 * 60)
        self._minutes.setFixedWidth(132)
        self._minutes.valueChanged.connect(self._on_minutes)
        row.addWidget(self._minutes)
        layout.addLayout(row)
        clear_row = QHBoxLayout()
        clear = PushButton(tr("Clear Home cache", "清除首页缓存", "ホームのキャッシュを消去"), card, FluentIcon.DELETE)
        clear.clicked.connect(self._clear_cache)
        clear_row.addWidget(clear)
        clear_row.addStretch(1)
        layout.addLayout(clear_row)
        board.add_card("home_cache", card)

    # ── state ────────────────────────────────────────────────────────────────

    def load(self):
        self._loading = True
        try:
            minutes = int(app_config.get_ui_value(HOME_CACHE_MINUTES_KEY, DEFAULT_CACHE_MINUTES))
        except (TypeError, ValueError):
            minutes = DEFAULT_CACHE_MINUTES
        self._minutes.setValue(max(0, minutes))
        self._loading = False
        self._refresh_summary()

    def _refresh_summary(self):
        shown = [spec.display_title() for spec in load_specs() if spec.enabled]
        self._summary.setText(
            tr("Showing: ", "当前显示：", "表示中: ") + (" · ".join(shown) if shown else tr("nothing", "无", "なし"))
        )

    def _on_minutes(self, value: int):
        if not self._loading:
            app_config.set_ui_value(HOME_CACHE_MINUTES_KEY, int(value))

    def _reset_layout(self):
        if show_fluent_confirmation(
            self._owner.window(),
            tr("Reset Home", "恢复默认首页", "ホームを初期化"),
            tr("Replace your Home rows with the default ones?", "用默认栏目替换当前首页栏目？", "ホームの欄を初期の欄に置き換えますか？"),
        ):
            save_specs(default_specs())
            signal_bus.home_layout_changed.emit()

    def _clear_cache(self):
        removed = shared_cache().clear()
        signal_bus.home_layout_changed.emit()  # rows reload from the site
        InfoBar.success(
            title=tr("Home cache cleared", "首页缓存已清除", "ホームのキャッシュを消去しました"),
            content=tr(f"{removed} cached row(s) removed", f"已移除 {removed} 条缓存", f"{removed} 件を削除しました"),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2500,
            parent=self._owner.window(),
        )
