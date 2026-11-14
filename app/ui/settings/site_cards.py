"""Settings card: which iwara site (iwara.tv / iwara.ai) the app browses.

Applies as soon as it changes, like the sidebar entry and Ctrl+Shift+S, so it
takes no part in "Save All Settings".
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, ComboBox, SubtitleLabel

from ...core import site as sites
from ...i18n import tr
from ...signal_bus import signal_bus


class SiteSettingsCard:
    def __init__(self, board, owner: QWidget):
        card = board.create_card("site")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel(tr("Site", "站点", "サイト"), card))
        layout.addWidget(
            BodyLabel(
                tr(
                    "The site Home, Search and Subscriptions show. Both share one account; pasted links "
                    "always open on their own site, and posts already on screen keep theirs. "
                    "The sidebar globe and Ctrl+Shift+S switch it too.",
                    "首页、搜索和订阅页浏览的站点。两站共用同一个账号；粘贴的链接始终按自身站点处理，"
                    "切换前已显示的作品仍按原站点打开。也可用侧栏的地球图标或 Ctrl+Shift+S 切换。",
                    "ホーム・検索・購読で表示するサイトです。アカウントは共通で、貼り付けたリンクは常にそのサイトで開き、"
                    "表示中の投稿も元のサイトのままです。サイドバーの地球アイコンや Ctrl+Shift+S でも切り替えられます。",
                ),
                card,
            )
        )
        row = QHBoxLayout()
        row.addWidget(BodyLabel(tr("Browse", "浏览站点", "表示するサイト"), card))
        row.addStretch()
        self._combo = ComboBox(card)
        for key, site in sites.SITES.items():
            self._combo.addItem(site.label)
            self._combo.setItemData(self._combo.count() - 1, key)
        self._combo.setFixedWidth(200)
        row.addWidget(self._combo)
        layout.addLayout(row)
        self._show(sites.configured_site())
        self._combo.currentIndexChanged.connect(self._on_changed)
        board.add_card("site", card)
        signal_bus.site_changed.connect(self._show)

    def _show(self, key: str):
        try:
            combo = self._combo
            combo.blockSignals(True)
        except RuntimeError:  # the settings page is gone
            try:
                signal_bus.site_changed.disconnect(self._show)
            except (RuntimeError, TypeError):
                pass
            return
        try:
            for index in range(combo.count()):
                if combo.itemData(index) == sites.normalize(key):
                    combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _on_changed(self, index: int):
        key = self._combo.itemData(index)
        if key:
            sites.set_configured_site(str(key))
