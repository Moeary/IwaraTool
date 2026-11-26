"""Rule, mode and folder controls of the repair page."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtWidgets import QFileDialog

from ...core.rules import active_rule_id, normalize_rule_payload, rule_store
from ...presentation.repair import page as repair_page
from ...presentation.repair.texts import repair_text


class RepairControlsMixin:
    """Rule, mode and folder controls of the repair page."""

    def _load_rules(self, *_args):
        current_id = active_rule_id()
        entries = rule_store.list_available()
        self._rule_payloads = [normalize_rule_payload(entry.get("payload")) for entry in entries]
        self._rule_combo.blockSignals(True)
        try:
            self._rule_combo.clear()
            self._rule_combo.addItems([repair_page.rule_name(entry) for entry in entries])
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

    def _on_preset_chosen(self, index: int):
        if index > 0:
            self._template_edit.setText(self._template_presets[index - 1].template)

    def _sync_preset_combo(self, text: str):
        text = str(text or "").strip()
        index = next((i + 1 for i, preset in enumerate(self._template_presets) if preset.template == text), 0)
        self._preset_combo.blockSignals(True)
        self._preset_combo.setCurrentIndex(index)
        self._preset_combo.blockSignals(False)

    def _load_saved_state(self):
        # Older versions saved an output folder without recording intent to
        # move files. Only an explicitly saved organize mode restores moving.
        saved = repair_page.saved_form()
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(1 if saved.move_to_output else 0)
        self._mode_combo.blockSignals(False)
        self._update_mode_controls()
        if saved.folder:
            self._folder_edit.setText(saved.folder)
        if saved.output:
            self._output_edit.setText(saved.output)
        if saved.template:
            self._template_edit.setText(saved.template)

    def _move_to_output(self) -> bool:
        return self._mode_combo.currentIndex() == 1

    def _on_mode_changed(self, *_args):
        repair_page.save_mode(self._form().mode)
        self._update_mode_controls()
        self._refresh_target_previews()

    def _update_mode_controls(self):
        moving = self._move_to_output()
        busy = self._worker is not None
        self._output_edit.setEnabled(moving and not busy)
        self._output_browse_btn.setEnabled(moving and not busy)
        self._mode_hint.setText(repair_page.mode_hint(self._form().mode))

    def _browse_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            repair_text("choose_source_folder"),
            self._folder_edit.text().strip() or os.getcwd(),
        )
        if selected:
            self._folder_edit.setText(selected)

    def _browse_output_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            repair_text("choose_output_folder"),
            self._output_edit.text().strip() or self._folder_edit.text().strip() or os.getcwd(),
        )
        if selected:
            self._output_edit.setText(selected)

    def _form(self) -> repair_page.RepairForm:
        return repair_page.RepairForm(
            folder=self._folder_edit.text(),
            mode=repair_page.MODE_ORGANIZE if self._move_to_output() else repair_page.MODE_IN_PLACE,
            output=self._output_edit.text(),
            template=self._template_edit.text(),
            rename=self._rename_check.isChecked(),
            download_video=self._download_video_check.isChecked(),
            download_thumbnail=self._thumbnail_check.isChecked(),
            collect_nfo=self._nfo_check.isChecked(),
            add_to_history=self._history_check.isChecked(),
        )

    def _output_root(self) -> str:
        return self._form().output_root

    def _template(self) -> str:
        return self._form().effective_template

    def _options(self) -> dict[str, Any]:
        return self._form().options()
