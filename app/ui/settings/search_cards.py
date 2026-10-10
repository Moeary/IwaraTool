"""Search engine, limits, history and Oreno3D bridge cards."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PushButton,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
)

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..tag_dictionary_worker import TagDictionaryUpdateWorker


class SettingsSearchCardsMixin:
    """Search engine, limits, history and Oreno3D bridge cards."""

    def _build_search_engine_card(self):
        engine_card = self._settings_board.create_card("search_engine")
        engine_layout = QVBoxLayout(engine_card)
        engine_layout.setContentsMargins(20, 16, 20, 16)
        engine_layout.setSpacing(10)
        engine_layout.addWidget(
            SubtitleLabel(tr("Search Engine", "搜索引擎", "検索エンジン"), engine_card)
        )
        engine_row = QHBoxLayout()
        engine_row.addWidget(
            BodyLabel(tr("Main search engine", "主搜索引擎", "メイン検索エンジン"), engine_card)
        )
        engine_row.addStretch()
        self._search_source_combo = ComboBox(engine_card)
        self._search_source_combo.addItem(tr("Iwara live API", "Iwara 实时 API", "IwaraライブAPI"))
        self._search_source_combo.setItemData(0, "iwara")
        self._search_source_combo.addItem(
            tr("Oreno3D online search", "Oreno3D 在线搜索", "Oreno3Dオンライン検索")
        )
        self._search_source_combo.setItemData(1, "oreno3d")
        self._search_source_combo.setFixedWidth(260)
        self._search_source_combo.currentIndexChanged.connect(self._on_search_source_changed)
        engine_row.addWidget(self._search_source_combo)
        engine_layout.addLayout(engine_row)

        chinese_row = QHBoxLayout()
        chinese_row.addWidget(
            BodyLabel(
                tr(
                    "Search Chinese keywords with Oreno3D",
                    "中文关键词使用 Oreno3D 搜索",
                    "中国語キーワードは Oreno3D で検索",
                ),
                engine_card,
            )
        )
        chinese_row.addStretch()
        self._search_chinese_oreno_switch = SwitchButton(engine_card)
        self._search_chinese_oreno_switch.checkedChanged.connect(self._on_search_chinese_oreno_toggle)
        chinese_row.addWidget(self._search_chinese_oreno_switch)
        engine_layout.addLayout(chinese_row)
        chinese_hint = BodyLabel(
            tr(
                "Iwara's own search rarely matches Chinese titles. Video searches whose keyword contains "
                "Chinese characters switch to Oreno3D automatically; the next search without them returns "
                "to the main engine.",
                "Iwara 自带搜索很难匹配中文标题。关键词含中文字符的视频搜索会自动改用 Oreno3D，"
                "之后不含中文的搜索会切回主搜索引擎。",
                "Iwara 自体の検索は中国語タイトルにほとんど一致しません。キーワードに中国語の文字を含む"
                "動画検索は自動的に Oreno3D を使い、含まない次の検索でメインのエンジンに戻ります。",
            ),
            engine_card,
        )
        chinese_hint.setWordWrap(True)
        engine_layout.addWidget(chinese_hint)
        self._settings_board.add_card("search_engine", engine_card)

    def _build_search_limit_card(self):
        search_card = self._settings_board.create_card("search_limit")
        search_layout = QVBoxLayout(search_card)
        search_layout.setContentsMargins(20, 16, 20, 16)
        search_layout.setSpacing(10)

        search_header = QHBoxLayout()
        search_header.addWidget(SubtitleLabel(tr("Search Download Limit", "搜索下载上限", "検索ダウンロード上限"), search_card))
        search_header.addStretch()
        self._search_limit_switch = SwitchButton(search_card)
        self._search_limit_switch.checkedChanged.connect(self._on_search_limit_toggle)
        search_header.addWidget(self._search_limit_switch)
        search_layout.addLayout(search_header)

        search_row = QHBoxLayout()
        search_row.addWidget(BodyLabel(tr("Max videos", "最大视频数", "最大動画数"), search_card))
        self._search_limit_edit = LineEdit(search_card)
        self._search_limit_edit.setFixedWidth(100)
        self._search_limit_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._search_limit_edit.setValidator(QIntValidator(1, 5000, self._search_limit_edit))
        self._search_limit_edit.setPlaceholderText("1-5000")
        self._search_limit_edit.editingFinished.connect(self._on_search_limit_input_finished)
        search_row.addWidget(self._search_limit_edit)
        search_row.addStretch()
        search_layout.addLayout(search_row)
        self._settings_board.add_card("search_limit", search_card)

    def _build_search_history_card(self):
        history_card = self._settings_board.create_card("search_history")
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(20, 16, 20, 16)
        history_layout.setSpacing(10)
        history_layout.addWidget(
            SubtitleLabel(tr("Search History", "搜索历史", "検索履歴"), history_card)
        )
        history_layout.addWidget(
            BodyLabel(
                tr(
                    "Keep recent search conditions available from the search page. Older entries are removed automatically.",
                    "在搜索页保留最近使用的搜索条件，超出数量后会自动移除较早记录。",
                    "検索ページで最近の検索条件を保持します。上限を超えた古い履歴は自動的に削除されます。",
                ),
                history_card,
            )
        )
        history_row = QHBoxLayout()
        history_row.addWidget(
            BodyLabel(tr("Remembered searches", "保留搜索数", "保存する検索数"), history_card)
        )
        self._search_history_limit_spin = SpinBox(history_card)
        self._search_history_limit_spin.setRange(1, 100)
        self._search_history_limit_spin.setFixedWidth(132)
        self._search_history_limit_spin.valueChanged.connect(self._on_search_history_limit_changed)
        history_row.addWidget(self._search_history_limit_spin)
        history_row.addWidget(
            BodyLabel(tr("items 1-100", "条数 1-100", "件数 1～100"), history_card)
        )
        history_row.addStretch()
        history_layout.addLayout(history_row)

        auto_search_row = QHBoxLayout()
        auto_search_row.addWidget(
            BodyLabel(
                tr(
                    "Search when source, scope, or sort changes",
                    "切换数据源、搜索类型或排序时自动搜索",
                    "ソース・検索対象・並び順の変更時に自動検索",
                ),
                history_card,
            )
        )
        auto_search_row.addStretch()
        self._search_auto_search_switch = SwitchButton(history_card)
        self._search_auto_search_switch.checkedChanged.connect(
            self._on_search_auto_search_toggle
        )
        auto_search_row.addWidget(self._search_auto_search_switch)
        history_layout.addLayout(auto_search_row)
        self._settings_board.add_card("search_history", history_card)

    def _build_search_bridge_card(self):
        search_resolve_card = self._settings_board.create_card("search_bridge")
        search_resolve_layout = QVBoxLayout(search_resolve_card)
        search_resolve_layout.setContentsMargins(20, 16, 20, 16)
        search_resolve_layout.setSpacing(10)
        search_resolve_layout.addWidget(
            SubtitleLabel(
                tr(
                    "Oreno3D / Iwara Search Loading",
                    "Oreno3D / Iwara 搜索加载",
                    "Oreno3D / Iwara 検索読み込み",
                ),
                search_resolve_card,
            )
        )
        tag_dictionary_row = QHBoxLayout()
        tag_dictionary_row.addWidget(
            BodyLabel(
                tr(
                    "Refresh localized search tags",
                    "刷新多语言搜索标签",
                    "多言語検索タグを更新",
                ),
                search_resolve_card,
            )
        )
        tag_dictionary_row.addStretch()
        self._tag_dictionary_status = BodyLabel("", search_resolve_card)
        tag_dictionary_row.addWidget(self._tag_dictionary_status)
        self._update_tags_btn = PushButton(
            tr("Update tags", "更新标签", "タグを更新"),
            search_resolve_card,
            FluentIcon.SYNC,
        )
        self._update_tags_btn.clicked.connect(self._update_tag_dictionary)
        tag_dictionary_row.addWidget(self._update_tags_btn)
        search_resolve_layout.addLayout(tag_dictionary_row)

        resolve_mode_row = QHBoxLayout()
        resolve_mode_row.addWidget(
            BodyLabel(
                tr("Resolve timing", "解析时机", "解決タイミング"),
                search_resolve_card,
            )
        )
        self._search_resolution_mode_combo = ComboBox(search_resolve_card)
        self._search_resolution_mode_combo.addItem(
            tr("Background pre-resolve", "后台预解析", "バックグラウンドで事前解決")
        )
        self._search_resolution_mode_combo.setItemData(0, "eager")
        self._search_resolution_mode_combo.addItem(
            tr("On click or download", "点击或下载时解析", "クリックまたはダウンロード時に解決")
        )
        self._search_resolution_mode_combo.setItemData(1, "on_demand")
        self._search_resolution_mode_combo.setFixedWidth(260)
        self._search_resolution_mode_combo.currentIndexChanged.connect(
            self._on_search_resolution_mode_changed
        )
        resolve_mode_row.addWidget(self._search_resolution_mode_combo)
        resolve_mode_row.addStretch()
        search_resolve_layout.addLayout(resolve_mode_row)

        resolve_workers_row = QHBoxLayout()
        resolve_workers_row.addWidget(
            BodyLabel(
                tr("Oreno3D ID concurrency", "Oreno3D ID 并发数", "Oreno3D ID 同時実行数"),
                search_resolve_card,
            )
        )
        self._search_resolution_workers_spin = SpinBox(search_resolve_card)
        self._search_resolution_workers_spin.setRange(1, 8)
        self._search_resolution_workers_spin.setFixedWidth(132)
        self._search_resolution_workers_spin.valueChanged.connect(
            self._on_search_resolution_workers_changed
        )
        resolve_workers_row.addWidget(self._search_resolution_workers_spin)
        resolve_workers_row.addStretch()
        search_resolve_layout.addLayout(resolve_workers_row)
        self._settings_board.add_card("search_bridge", search_resolve_card)

    def _on_search_source_changed(self, index: int):
        if self._loading_settings:
            return
        source = str(self._search_source_combo.itemData(index) or "iwara")
        if source == app_config.search_default_source:
            return
        app_config.search_default_source = source
        signal_bus.search_source_changed.emit(app_config.search_default_source)

    def _on_search_chinese_oreno_toggle(self, checked: bool):
        if self._loading_settings:
            return
        app_config.search_chinese_via_oreno3d = bool(checked)

    def _on_search_limit_toggle(self, checked: bool):
        app_config.search_limit_enabled = checked
        self._search_limit_edit.setEnabled(checked)

    def _on_search_limit_input_finished(self):
        text = self._search_limit_edit.text().strip()
        if not text:
            self._search_limit_edit.setText(str(max(1, app_config.search_limit_count)))
            return
        try:
            value = int(text)
        except ValueError:
            self._search_limit_edit.setText(str(max(1, app_config.search_limit_count)))
            return
        value = max(1, min(5000, value))
        self._search_limit_edit.setText(str(value))
        app_config.search_limit_count = value

    def _on_search_history_limit_changed(self, value: int):
        if self._loading_settings:
            return
        limit = max(1, min(100, int(value)))
        app_config.search_history_limit = limit
        # Apply a lower limit immediately so a later increase does not bring
        # back entries the user already asked us to discard.
        raw = app_config.get_ui_value("search_history_v1", "[]")
        try:
            history = json.loads(str(raw or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            history = []
        if isinstance(history, list) and len(history) > limit:
            app_config.set_ui_value(
                "search_history_v1",
                json.dumps(history[:limit], ensure_ascii=False, separators=(",", ":")),
            )

    def _on_search_auto_search_toggle(self, checked: bool):
        if self._loading_settings:
            return
        app_config.search_auto_search_enabled = bool(checked)

    def _update_tag_dictionary(self):
        if self._tag_dictionary_worker is not None and self._tag_dictionary_worker.isRunning():
            return
        worker = TagDictionaryUpdateWorker(self)
        self._tag_dictionary_worker = worker
        self._update_tags_btn.setEnabled(False)
        self._tag_dictionary_status.setText(
            tr(
                "Updating…",
                "更新中…",
                "更新中…",
            )
        )
        worker.result_ready.connect(self._on_tag_dictionary_result)
        worker.finished.connect(
            lambda worker=worker: self._cleanup_tag_dictionary_worker(worker)
        )
        worker.start()

    def _on_tag_dictionary_result(self, result: object):
        count, error = (
            result if isinstance(result, tuple) and len(result) == 2 else (0, "")
        )
        if error:
            message = tr(
                f"Tag dictionary update failed: {error}",
                f"标签词典更新失败：{error}",
                f"タグ辞書の更新に失敗：{error}",
            )
            self._tag_dictionary_status.setText(
                tr("Update failed", "更新失败", "更新失敗")
            )
            InfoBar.warning(
                title=tr("Tag update failed", "标签更新失败", "タグ更新失敗"),
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            signal_bus.log_message.emit(f"[Tags] {message}")
            return

        self._tag_dictionary_status.setText(
            tr(
                f"Loaded {count} tags",
                f"已加载 {count} 个标签",
                f"{count} 件のタグを読み込みました",
            )
        )
        InfoBar.success(
            title=tr("Tags updated", "标签已更新", "タグを更新しました"),
            content=self._tag_dictionary_status.text(),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )
        signal_bus.log_message.emit(
            tr(
                f"[Tags] Loaded {count} localized tags",
                f"[标签] 已加载 {count} 个多语言标签",
                f"[タグ] 多言語タグを{count}件読み込みました",
            )
        )

    def _cleanup_tag_dictionary_worker(self, worker: TagDictionaryUpdateWorker):
        if self._tag_dictionary_worker is worker:
            self._tag_dictionary_worker = None
            self._update_tags_btn.setEnabled(True)
        worker.deleteLater()

    def _on_search_resolution_mode_changed(self, _index: int):
        if self._loading_settings:
            return
        mode = str(self._search_resolution_mode_combo.currentData() or "eager")
        if mode not in {"eager", "on_demand"}:
            mode = "eager"
        app_config.set_ui_value("search_iwara_resolution_mode_v1", mode)

    def _on_search_resolution_workers_changed(self, value: int):
        if self._loading_settings:
            return
        value = max(1, min(8, int(value)))
        app_config.set_ui_value("search_iwara_resolution_workers_v1", value)

    def _on_cover_download_workers_changed(self, value: int):
        if self._loading_settings:
            return
        value = max(1, min(16, int(value)))
        app_config.set_ui_value("cover_download_workers_v1", value)
