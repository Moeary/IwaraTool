"""Rule, mode and folder controls of the repair page."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtWidgets import QFileDialog

from ...config import app_config
from ...core.rules import active_rule_id, normalize_rule_payload, rule_store
from ...i18n import tr


class RepairControlsMixin:
    """Rule, mode and folder controls of the repair page."""

    def _load_rules(self, *_args):
        current_id = active_rule_id()
        entries = rule_store.list_available()
        self._rule_payloads = [normalize_rule_payload(entry.get("payload")) for entry in entries]
        self._rule_combo.blockSignals(True)
        try:
            self._rule_combo.clear()
            self._rule_combo.addItems([
                str(entry.get("name", "") or tr("Unnamed rule", "未命名规则", "名前なしルール"))
                for entry in entries
            ])
            index = next(
                (idx for idx, entry in enumerate(entries) if str(entry.get("id", "")) == current_id),
                0,
            )
            self._rule_combo.setCurrentIndex(index)
        finally:
            self._rule_combo.blockSignals(False)
        self._apply_rule_template(self._rule_combo.currentIndex())

    def _apply_rule_template(self, index: int):
        if not 0 <= index < len(self._rule_payloads):
            return
        payload = self._rule_payloads[index]
        self._template_edit.setText(str(payload.get("filename_template", "") or ""))

    def _on_rule_changed(self, index: int):
        self._apply_rule_template(index)

    def _load_saved_state(self):
        # Older versions saved an output folder without recording intent to
        # move files. Only an explicitly saved organize mode restores moving.
        saved_mode = str(app_config.get_ui_value("repair_mode", "in_place") or "in_place")
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(1 if saved_mode == "organize" else 0)
        self._mode_combo.blockSignals(False)
        self._update_mode_controls()
        saved_folder = str(app_config.get_ui_value("repair_folder", "") or "")
        if saved_folder and os.path.isdir(saved_folder):
            self._folder_edit.setText(saved_folder)
        saved_output = str(app_config.get_ui_value("repair_output_folder", "") or "")
        if saved_output:
            self._output_edit.setText(saved_output)
        saved_template = str(app_config.get_ui_value("repair_filename_template", "") or "")
        if saved_template:
            self._template_edit.setText(saved_template)

    def _move_to_output(self) -> bool:
        return self._mode_combo.currentIndex() == 1

    def _on_mode_changed(self, *_args):
        app_config.set_ui_value("repair_mode", "organize" if self._move_to_output() else "in_place")
        self._update_mode_controls()
        self._refresh_target_previews()

    def _update_mode_controls(self):
        moving = self._move_to_output()
        busy = self._worker is not None
        self._output_edit.setEnabled(moving and not busy)
        self._output_browse_btn.setEnabled(moving and not busy)
        self._mode_hint.setText(
            tr(
                "Videos and their sidecars are moved into the output folder. Existing targets and batch conflicts are skipped.",
                "视频及附属文件会剪切到输出目录；已有目标与批次内冲突均跳过。",
                "動画と関連ファイルを出力フォルダーへ移動します。既存の変更先や一括処理内の競合はスキップします。",
            ) if moving else tr(
                "Keep every video's current folder and apply only the filename segment. Covers and NFO files follow; existing targets are skipped.",
                "保留每个视频的所在目录，只应用规则的文件名段。封面与 NFO 跟随重命名；已有目标会跳过。",
                "各動画の現在のフォルダーを保ち、ファイル名部分のみ適用します。サムネイルと NFO も名前変更し、既存の変更先はスキップします。",
            )
        )

    def _browse_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("Choose source folder", "选择视频文件夹", "動画フォルダーを選択"),
            self._folder_edit.text().strip() or os.getcwd(),
        )
        if selected:
            self._folder_edit.setText(selected)

    def _browse_output_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("Choose output folder", "选择输出文件夹", "出力フォルダーを選択"),
            self._output_edit.text().strip() or self._folder_edit.text().strip() or os.getcwd(),
        )
        if selected:
            self._output_edit.setText(selected)

    def _output_root(self) -> str:
        if not self._move_to_output():
            return ""
        custom = self._output_edit.text().strip()
        if custom:
            return os.path.abspath(os.path.expanduser(custom))
        source = self._folder_edit.text().strip()
        return os.path.abspath(os.path.expanduser(source)) if source else ""

    def _template(self) -> str:
        return self._template_edit.text().strip() or "{YYYY-MM-DD}_{title}_{id}.mp4"

    def _options(self) -> dict[str, Any]:
        return {
            "filename_template": self._template(),
            "output_root": self._output_root(),
            "move_to_output": self._move_to_output(),
            "rename": self._rename_check.isChecked(),
            "download_video": self._download_video_check.isChecked(),
            "download_thumbnail": self._thumbnail_check.isChecked(),
            "collect_nfo": self._nfo_check.isChecked(),
            "add_to_history": self._history_check.isChecked(),
        }
