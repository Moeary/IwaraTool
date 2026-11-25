"""Search engine, limits, history and Oreno3D bridge cards."""

from __future__ import annotations


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
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from ...signal_bus import signal_bus
from .tag_dictionary_worker import TagDictionaryUpdateWorker


class SettingsSearchCardsMixin:
    """Search engine, limits, history and Oreno3D bridge cards."""

    def _build_search_engine_card(self):
        engine_card = self._settings_board.create_card("search_engine")
        engine_layout = QVBoxLayout(engine_card)
        engine_layout.setContentsMargins(20, 16, 20, 16)
        engine_layout.setSpacing(10)
        engine_layout.addWidget(
            SubtitleLabel(stext("search_engine"), engine_card)
        )
        engine_row = QHBoxLayout()
        engine_row.addWidget(
            BodyLabel(stext("main_search_engine"), engine_card)
        )
        engine_row.addStretch()
        self._search_source_combo = ComboBox(engine_card)
        self._search_source_combo.addItem(stext("iwara_live_api"))
        self._search_source_combo.setItemData(0, "iwara")
        self._search_source_combo.addItem(
            stext("oreno3d_online_search")
        )
        self._search_source_combo.setItemData(1, "oreno3d")
        self._search_source_combo.setFixedWidth(260)
        self._search_source_combo.currentIndexChanged.connect(self._on_search_source_changed)
        engine_row.addWidget(self._search_source_combo)
        engine_layout.addLayout(engine_row)

        chinese_row = QHBoxLayout()
        chinese_row.addWidget(
            BodyLabel(
                stext("search_chinese_keywords_with_oreno3d"),
                engine_card,
            )
        )
        chinese_row.addStretch()
        self._search_chinese_oreno_switch = SwitchButton(engine_card)
        self._search_chinese_oreno_switch.checkedChanged.connect(self._on_search_chinese_oreno_toggle)
        chinese_row.addWidget(self._search_chinese_oreno_switch)
        engine_layout.addLayout(chinese_row)
        chinese_hint = BodyLabel(
            stext("iwara_s_own_search_rarely_matches"),
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
        search_header.addWidget(SubtitleLabel(stext("search_download_limit"), search_card))
        search_header.addStretch()
        self._search_limit_switch = SwitchButton(search_card)
        self._search_limit_switch.checkedChanged.connect(self._on_search_limit_toggle)
        search_header.addWidget(self._search_limit_switch)
        search_layout.addLayout(search_header)

        search_row = QHBoxLayout()
        search_row.addWidget(BodyLabel(stext("max_videos"), search_card))
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
            SubtitleLabel(stext("search_history"), history_card)
        )
        history_layout.addWidget(
            BodyLabel(
                stext("keep_recent_search_conditions_available_from"),
                history_card,
            )
        )
        history_row = QHBoxLayout()
        history_row.addWidget(
            BodyLabel(stext("remembered_searches"), history_card)
        )
        self._search_history_limit_spin = SpinBox(history_card)
        self._search_history_limit_spin.setRange(1, 100)
        self._search_history_limit_spin.setFixedWidth(132)
        self._search_history_limit_spin.valueChanged.connect(self._on_search_history_limit_changed)
        history_row.addWidget(self._search_history_limit_spin)
        history_row.addWidget(
            BodyLabel(stext("items_1_100"), history_card)
        )
        history_row.addStretch()
        history_layout.addLayout(history_row)

        auto_search_row = QHBoxLayout()
        auto_search_row.addWidget(
            BodyLabel(
                stext("search_when_source_scope_or_sort"),
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
                stext("oreno3d_iwara_search_loading"),
                search_resolve_card,
            )
        )
        tag_dictionary_row = QHBoxLayout()
        tag_dictionary_row.addWidget(
            BodyLabel(
                stext("refresh_localized_search_tags"),
                search_resolve_card,
            )
        )
        tag_dictionary_row.addStretch()
        self._tag_dictionary_status = BodyLabel("", search_resolve_card)
        tag_dictionary_row.addWidget(self._tag_dictionary_status)
        self._update_tags_btn = PushButton(
            stext("update_tags"),
            search_resolve_card,
            FluentIcon.SYNC,
        )
        self._update_tags_btn.clicked.connect(self._update_tag_dictionary)
        tag_dictionary_row.addWidget(self._update_tags_btn)
        search_resolve_layout.addLayout(tag_dictionary_row)

        resolve_mode_row = QHBoxLayout()
        resolve_mode_row.addWidget(
            BodyLabel(
                stext("resolve_timing"),
                search_resolve_card,
            )
        )
        self._search_resolution_mode_combo = ComboBox(search_resolve_card)
        self._search_resolution_mode_combo.addItem(
            stext("background_pre_resolve")
        )
        self._search_resolution_mode_combo.setItemData(0, "eager")
        self._search_resolution_mode_combo.addItem(
            stext("on_click_or_download")
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
                stext("oreno3d_id_concurrency"),
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
        setting_values.set_search_source(str(self._search_source_combo.itemData(index) or "iwara"))

    def _on_search_chinese_oreno_toggle(self, checked: bool):
        if self._loading_settings:
            return
        app_config.search_chinese_via_oreno3d = bool(checked)

    def _on_search_limit_toggle(self, checked: bool):
        app_config.search_limit_enabled = checked
        self._search_limit_edit.setEnabled(checked)

    def _on_search_limit_input_finished(self):
        value = setting_values.set_search_limit_count(self._search_limit_edit.text())
        self._search_limit_edit.setText(str(value))

    def _on_search_history_limit_changed(self, value: int):
        if self._loading_settings:
            return
        setting_values.set_search_history_limit(value)

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
            stext("updating")
        )
        worker.result_ready.connect(self._on_tag_dictionary_result)
        worker.finished.connect(
            lambda worker=worker: self._cleanup_tag_dictionary_worker(worker)
        )
        worker.start()

    def _on_tag_dictionary_result(self, result: object):
        ok, status, title, log = setting_values.tag_dictionary_result(result)
        self._tag_dictionary_status.setText(status if ok else stext("update_failed"))
        (InfoBar.success if ok else InfoBar.warning)(
            title=title,
            content=status if ok else log.removeprefix("[Tags] "),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500 if ok else 5000,
            parent=self,
        )
        signal_bus.log_message.emit(log)

    def _cleanup_tag_dictionary_worker(self, worker: TagDictionaryUpdateWorker):
        if self._tag_dictionary_worker is worker:
            self._tag_dictionary_worker = None
            self._update_tags_btn.setEnabled(True)
        worker.deleteLater()

    def _on_search_resolution_mode_changed(self, _index: int):
        if self._loading_settings:
            return
        setting_values.set_resolution_mode(str(self._search_resolution_mode_combo.currentData() or "eager"))

    def _on_search_resolution_workers_changed(self, value: int):
        if self._loading_settings:
            return
        setting_values.set_resolution_workers(value)

    def _on_cover_download_workers_changed(self, value: int):
        if self._loading_settings:
            return
        setting_values.set_cover_workers(value)
