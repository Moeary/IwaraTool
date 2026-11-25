"""Settings card: which iwara site (iwara.tv / iwara.ai) the app browses.

Applies as soon as it changes, like the sidebar entry and Ctrl+Shift+S, so it
takes no part in "Save All Settings".
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, ComboBox, SubtitleLabel

from ...core import site as sites
from ...presentation.settings.texts import stext
from ...signal_bus import signal_bus


class SiteSettingsCard:
    def __init__(self, board, owner: QWidget):
        card = board.create_card("site")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel(stext("site"), card))
        layout.addWidget(
            BodyLabel(
                stext("the_site_home_search_and_subscriptions"),
                card,
            )
        )
        row = QHBoxLayout()
        row.addWidget(BodyLabel(stext("browse_2"), card))
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
