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

from ...config import app_config
from ...core.home.cache import DEFAULT_CACHE_MINUTES, HOME_CACHE_MINUTES_KEY, shared_cache
from ...core.home.feed import default_specs, load_specs, save_specs
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from ...signal_bus import signal_bus
from ..home.layout_dialog import HomeLayoutDialog
from ..style.theme import set_secondary_text
from ..common.fluent_dialogs import show_fluent_confirmation


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
            stext("home_rows"),
            stext("rows_can_be_the_newest_uploads"),
        )
        self._summary = CaptionLabel("", card)
        self._summary.setWordWrap(True)
        set_secondary_text(self._summary)
        layout.addWidget(self._summary)
        row = QHBoxLayout()
        edit = PrimaryPushButton(stext("customize_rows"), card, FluentIcon.EDIT)
        edit.clicked.connect(lambda: HomeLayoutDialog.edit(self._owner.window()))
        reset = PushButton(stext("reset_to_default"), card)
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
            stext("home_cache"),
            stext("rows_are_kept_on_disk_and"),
        )
        row = QHBoxLayout()
        row.addWidget(BodyLabel(
            stext("re_check_a_row_after_minutes"),
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
        clear = PushButton(stext("clear_home_cache"), card, FluentIcon.DELETE)
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
        try:
            self._summary.setText(
                stext("showing") + (" · ".join(shown) if shown else stext("nothing"))
            )
        except RuntimeError:
            # The settings page is gone (window closed); stop listening.
            try:
                signal_bus.home_layout_changed.disconnect(self._refresh_summary)
            except (RuntimeError, TypeError):
                pass

    def _on_minutes(self, value: int):
        if not self._loading:
            app_config.set_ui_value(HOME_CACHE_MINUTES_KEY, int(value))

    def _reset_layout(self):
        if show_fluent_confirmation(
            self._owner.window(),
            stext("reset_home"),
            stext("replace_your_home_rows_with_the"),
        ):
            save_specs(default_specs())
            signal_bus.home_layout_changed.emit()

    def _clear_cache(self):
        removed = shared_cache().clear()
        signal_bus.home_layout_changed.emit()  # rows reload from the site
        InfoBar.success(
            title=stext("home_cache_cleared"),
            content=setting_values.home_cache_cleared_text(removed),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2500,
            parent=self._owner.window(),
        )
