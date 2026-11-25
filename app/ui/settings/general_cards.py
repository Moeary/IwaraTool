"""Appearance, language, data location and quality cards."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PushButton,
    SubtitleLabel,
)

from ...config import app_config
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from ...presentation import preferences
from ..style.theme import apply_theme_mode


class SettingsGeneralCardsMixin:
    """Appearance, language, data location and quality cards."""

    def _build_appearance_card(self):
        appearance_card = self._settings_board.create_card("appearance")
        appearance_layout = QVBoxLayout(appearance_card)
        appearance_layout.setContentsMargins(20, 16, 20, 16)
        appearance_layout.setSpacing(10)
        appearance_layout.addWidget(SubtitleLabel(stext("appearance"), appearance_card))
        appearance_layout.addWidget(
            BodyLabel(
                stext("light_and_dark_modes_share_one"),
                appearance_card,
            )
        )
        theme_row = QHBoxLayout()
        theme_row.addWidget(BodyLabel(stext("theme"), appearance_card))
        theme_row.addStretch()
        self._theme_combo = ComboBox(appearance_card)
        self._theme_combo.addItem(stext("follow_system"))
        self._theme_combo.setItemData(0, "auto")
        self._theme_combo.addItem(stext("light"))
        self._theme_combo.setItemData(1, "light")
        self._theme_combo.addItem(stext("dark"))
        self._theme_combo.setItemData(2, "dark")
        self._theme_combo.setFixedWidth(180)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_mode_changed)
        theme_row.addWidget(self._theme_combo)
        appearance_layout.addLayout(theme_row)
        self._settings_board.add_card("appearance", appearance_card)

    def _build_language_card(self):
        lang_card = self._settings_board.create_card("language")
        lang_layout = QVBoxLayout(lang_card)
        lang_layout.setContentsMargins(20, 16, 20, 16)
        lang_layout.setSpacing(10)

        lang_layout.addWidget(SubtitleLabel(stext("interface_language"), lang_card))
        lang_layout.addWidget(
            BodyLabel(
                stext("default_is_chinese_language_switch_is"),
                lang_card,
            )
        )

        lang_row = QHBoxLayout()
        self._lang_combo = ComboBox(lang_card)
        self._lang_combo.addItems(["简体中文", "English", "日本語"])
        self._lang_combo.setFixedWidth(180)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()
        lang_layout.addLayout(lang_row)

        self._settings_board.add_card("language", lang_card)

    def _build_data_paths_card(self):
        data_card = self._settings_board.create_card("data_paths")
        data_layout = QVBoxLayout(data_card)
        data_layout.setContentsMargins(20, 16, 20, 16)
        data_layout.setSpacing(8)
        data_layout.addWidget(SubtitleLabel(stext("local_data_paths"), data_card))
        data_layout.addWidget(BodyLabel(f"{stext('data_dir')}: {app_config.app_data_dir}", data_card))
        data_layout.addWidget(BodyLabel(f"{stext('config_file')}: {app_config.config_path}", data_card))
        data_layout.addWidget(BodyLabel(f"{stext('history_db')}: {app_config.history_db_path}", data_card))
        open_data_btn = PushButton(
            stext("open_data_folder"),
            data_card,
            FluentIcon.FOLDER,
        )
        open_data_btn.clicked.connect(lambda: self._open_local_folder(app_config.app_data_dir))
        data_layout.addWidget(open_data_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._settings_board.add_card("data_paths", data_card)

    def _build_quality_card(self):
        quality_card = self._settings_board.create_card("quality")
        quality_layout = QVBoxLayout(quality_card)
        quality_layout.setContentsMargins(20, 16, 20, 16)
        quality_layout.setSpacing(10)

        quality_layout.addWidget(SubtitleLabel(stext("preferred_quality"), quality_card))
        quality_layout.addWidget(
            BodyLabel(
                stext("if_preferred_quality_is_unavailable_fallback"),
                quality_card,
            )
        )

        quality_row = QHBoxLayout()
        self._quality_combo = ComboBox(quality_card)
        self._quality_combo.addItems([stext("source"), "540p", "360p"])
        self._quality_combo.setFixedWidth(180)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()
        quality_layout.addLayout(quality_row)
        self._settings_board.add_card("quality", quality_card)

    def _on_quality_changed(self, idx: int):
        setting_values.set_quality(setting_values.QUALITIES[idx])

    def _on_language_changed(self, idx: int):
        if self._loading_settings:
            return
        preferences.set_language({1: "en", 2: "ja"}.get(idx, "zh"))

    def _on_theme_mode_changed(self, idx: int):
        if self._loading_settings:
            return
        apply_theme_mode(preferences.set_theme_mode(self._theme_combo.itemData(idx)))

    def _sync_theme_combo(self):
        mode = app_config.theme_mode
        index = next(
            (i for i in range(self._theme_combo.count()) if self._theme_combo.itemData(i) == mode),
            0,
        )
        self._theme_combo.blockSignals(True)
        self._theme_combo.setCurrentIndex(index)
        self._theme_combo.blockSignals(False)

    def _open_local_folder(self, path: str):
        path = str(path or "").strip()
        if not path or not os.path.isdir(path):
            InfoBar.warning(
                title=stext("folder_not_found"),
                content=path,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
