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
from ...i18n import tr
from ...signal_bus import signal_bus
from ..theme import apply_theme_mode, normalize_theme_mode


class SettingsGeneralCardsMixin:
    """Appearance, language, data location and quality cards."""

    def _build_appearance_card(self):
        appearance_card = self._settings_board.create_card("appearance")
        appearance_layout = QVBoxLayout(appearance_card)
        appearance_layout.setContentsMargins(20, 16, 20, 16)
        appearance_layout.setSpacing(10)
        appearance_layout.addWidget(SubtitleLabel(tr("Appearance", "外观", "外観"), appearance_card))
        appearance_layout.addWidget(
            BodyLabel(
                tr(
                    "Light and dark modes share one palette; the choice is remembered.",
                    "浅色与深色模式共用同一套配色，选择会被记住。",
                    "ライト/ダークは同じ配色を共有し、選択は保存されます。",
                ),
                appearance_card,
            )
        )
        theme_row = QHBoxLayout()
        theme_row.addWidget(BodyLabel(tr("Theme", "主题模式", "テーマ"), appearance_card))
        theme_row.addStretch()
        self._theme_combo = ComboBox(appearance_card)
        self._theme_combo.addItem(tr("Follow system", "跟随系统", "システムに合わせる"))
        self._theme_combo.setItemData(0, "auto")
        self._theme_combo.addItem(tr("Light", "浅色", "ライト"))
        self._theme_combo.setItemData(1, "light")
        self._theme_combo.addItem(tr("Dark", "深色", "ダーク"))
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

        lang_layout.addWidget(SubtitleLabel(tr("Interface Language", "界面语言", "表示言語"), lang_card))
        lang_layout.addWidget(
            BodyLabel(
                tr(
                    "Default is Chinese; language switch is applied immediately",
                    "默认中文；切换后立即生效",
                    "初期値は中国語です。言語切替は即時反映されます",
                ),
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
        data_layout.addWidget(SubtitleLabel(tr("Local Data Paths", "本地数据位置", "ローカルデータパス"), data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Data dir', '数据目录', 'データディレクトリ')}: {app_config.app_data_dir}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Config file', '配置文件', '設定ファイル')}: {app_config.config_path}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('History DB', '下载历史库', '履歴DB')}: {app_config.history_db_path}", data_card))
        open_data_btn = PushButton(
            tr("Open data folder", "打开数据目录", "データフォルダーを開く"),
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

        quality_layout.addWidget(SubtitleLabel(tr("Preferred Quality", "下载画质偏好", "優先画質"), quality_card))
        quality_layout.addWidget(
            BodyLabel(
                tr(
                    "If preferred quality is unavailable, fallback order is Source > 540 > 360",
                    "首选画质不存在时自动回退到更低分辨率：Source > 540 > 360",
                    "優先画質が無い場合は Source > 540 > 360 の順で自動フォールバックします",
                ),
                quality_card,
            )
        )

        quality_row = QHBoxLayout()
        self._quality_combo = ComboBox(quality_card)
        self._quality_combo.addItems([tr("Source", "原画", "オリジナル"), "540p", "360p"])
        self._quality_combo.setFixedWidth(180)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()
        quality_layout.addLayout(quality_row)
        self._settings_board.add_card("quality", quality_card)

    def _on_quality_changed(self, idx: int):
        q = ["Source", "540", "360"][idx]
        app_config.preferred_quality = q

    def _on_language_changed(self, idx: int):
        if self._loading_settings:
            return
        if idx == 1:
            app_config.ui_language = "en_US"
        elif idx == 2:
            app_config.ui_language = "ja_JP"
        else:
            app_config.ui_language = "zh_CN"
        signal_bus.language_changed.emit(app_config.ui_language)

    def _on_theme_mode_changed(self, idx: int):
        if self._loading_settings:
            return
        mode = normalize_theme_mode(self._theme_combo.itemData(idx))
        app_config.theme_mode = mode
        apply_theme_mode(mode)

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
                title=tr("Folder not found", "目录不存在", "フォルダーが見つかりません"),
                content=path,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
