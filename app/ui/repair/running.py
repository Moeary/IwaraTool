"""Start, follow and stop a scan or repair run."""

from __future__ import annotations

import os
import time
from typing import Any

from ...config import app_config
from ...i18n import tr
from ..worker_lifecycle import stop_qthreads
from .worker import RepairWorker


class RepairRunningMixin:
    """Start, follow and stop a scan or repair run."""

    def _scan_folder(self):
        if self._worker is not None and self._worker.isRunning():
            return
        folder = os.path.abspath(os.path.expanduser(self._folder_edit.text().strip()))
        if not os.path.isdir(folder):
            self._show_warning(
                tr("Folder not found", "文件夹不存在", "フォルダーが見つかりません"),
                tr("Choose or drop a valid local folder first.", "请先选择或拖入有效的本地文件夹。", "有効なローカルフォルダーを選択またはドロップしてください。"),
            )
            return
        output_root = self._output_root()
        self._folder_edit.setText(folder)
        app_config.set_ui_value("repair_folder", folder)
        app_config.set_ui_value("repair_output_folder", self._output_edit.text().strip())
        app_config.set_ui_value("repair_filename_template", self._template())
        app_config.set_ui_value("repair_mode", "organize" if self._move_to_output() else "in_place")
        self._items.clear()
        self._items_by_path.clear()
        self._clear_table()
        self._repair_log_active = True
        self._last_feedback_log_at = 0.0
        self._scan_feedback = (0, 0, 0)
        self._status_label.setText(
            tr("Scanning files…", "正在扫描文件…", "ファイルを検索中…")
        )
        self._append_log(
            tr(
                f"[Repair] scanning ({self._mode_combo.currentText()}): {folder}" + (f" -> {output_root}" if output_root else ""),
                f"[修复] 开始扫描（{self._mode_combo.currentText()}）：{folder}" + (f" → {output_root}" if output_root else ""),
                f"[修復] 検索開始（{self._mode_combo.currentText()}）: {folder}" + (f" -> {output_root}" if output_root else ""),
            )
        )
        self._start_worker(
            RepairWorker(
                "scan",
                folder=folder,
                filename_template=self._template(),
                output_root=output_root,
                move_to_output=self._move_to_output(),
                parent=self,
            )
        )

    def _start_repair(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._items:
            self._show_warning(
                tr("Nothing to repair", "没有可修复的项目", "修復対象がありません"),
                tr("Scan a folder first.", "请先扫描文件夹。", "先にフォルダーを検索してください。"),
            )
            return
        app_config.set_ui_value("repair_filename_template", self._template())
        app_config.set_ui_value("repair_output_folder", self._output_edit.text().strip())
        app_config.set_ui_value("repair_mode", "organize" if self._move_to_output() else "in_place")
        self._repair_log_active = True
        self._append_log(
            tr(
                f"[Repair] starting {len(self._items)} files",
                f"[修复] 开始处理 {len(self._items)} 个文件",
                f"[修復] {len(self._items)} 件の処理を開始",
            )
        )
        self._start_worker(
            RepairWorker(
                "repair",
                items=list(self._items),
                options=self._options(),
                parent=self,
            )
        )

    def _start_worker(self, worker: RepairWorker):
        self._worker = worker
        self._worker_mode = worker.mode
        self._scan_btn.setEnabled(False)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        for control in (self._mode_combo, self._folder_edit, self._source_browse_btn,
                        self._template_edit, self._rule_combo, self._rename_check,
                        self._thumbnail_check, self._nfo_check,
                        self._download_video_check, self._history_check):
            control.setEnabled(False)
        self._update_mode_controls()
        if worker.mode == "scan":
            self._scan_feedback_timer.start()
        else:
            self._scan_feedback_timer.stop()
        worker.item_ready.connect(self._on_item_ready)
        worker.progress.connect(self._on_progress)
        worker.activity.connect(self._on_activity)
        worker.result_ready.connect(self._on_result)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _stop_worker(self):
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.requestInterruption()
        self._status_label.setText(tr("Stopping…", "正在停止…", "停止中…"))

    def _on_progress(self, index: int, total: int):
        if self._worker_mode == "scan":
            if index == total:
                self._status_label.setText(
                    tr(
                        f"Scan completed {index}/{total}",
                        f"扫描完成 {index}/{total}",
                        f"検索完了 {index}/{total}",
                    )
                )
            return
        self._status_label.setText(
            tr(f"Processing {index}/{total}", f"处理中 {index}/{total}", f"処理中 {index}/{total}")
        )

    def _on_activity(self, completed: int, total: int, active: int):
        if self._worker_mode != "scan":
            return
        self._scan_feedback = (max(0, completed), max(0, total), max(0, active))
        self._show_scan_feedback()

    def _on_scan_feedback_timer(self):
        if self._worker_mode != "scan":
            return
        self._show_scan_feedback()

    def _show_scan_feedback(self):
        completed, total, active = self._scan_feedback
        if total <= 0:
            return
        queued = max(0, total - completed - active)
        self._status_label.setText(
            tr(
                f"Scanning {completed}/{total} · active {active} · queued {queued}",
                f"扫描中 {completed}/{total} · 活跃 {active} · 排队 {queued}",
                f"検索中 {completed}/{total} · 実行中 {active} · 待機 {queued}",
            )
        )
        now = time.monotonic()
        if completed == total or now - self._last_feedback_log_at >= 1.0:
            self._last_feedback_log_at = now
            self._append_log(
                tr(
                    f"[Repair] scan progress: {completed}/{total}, active={active}, queued={queued}",
                    f"[修复] 扫描进度：{completed}/{total}，活跃请求 {active}，排队 {queued}",
                    f"[修復] 検索進捗: {completed}/{total}、実行中={active}、待機={queued}",
                )
            )

    def _on_item_ready(self, item: dict[str, Any]):
        path = str(item.get("path", "") or "")
        if not path:
            return
        self._items_by_path[path] = dict(item)
        self._items = list(self._items_by_path.values())
        self._render_item(item)
        self._update_summary()

    def _on_result(self, result: object):
        if self._worker_mode == "scan" and isinstance(result, list):
            self._items = [dict(item) for item in result if isinstance(item, dict)]
            self._items_by_path = {
                str(item.get("path", "") or ""): item
                for item in self._items
                if str(item.get("path", "") or "")
            }
            self._refresh_target_previews()
            self._render_all_items()
            ready = sum(1 for item in self._items if item.get("status") == "ready" and not item.get("target_conflict"))
            self._append_log(
                tr(
                    f"[Repair] scan complete: {ready}/{len(self._items)} ready",
                    f"[修复] 扫描完成：{ready}/{len(self._items)} 个可修复",
                    f"[修復] 検索完了: {ready}/{len(self._items)} 件が修復可能",
                )
            )
        elif self._worker_mode == "repair" and isinstance(result, dict):
            repaired_items = result.get("items", [])
            if isinstance(repaired_items, list):
                self._items = [dict(item) for item in repaired_items if isinstance(item, dict)]
                self._items_by_path = {
                    str(item.get("path", "") or ""): item
                    for item in self._items
                    if str(item.get("path", "") or "")
                }
                self._render_all_items()
            self._append_log(
                tr(
                    f"[Repair] finished: renamed={result.get('renamed', 0)}, NFO={result.get('nfo', 0)}, cover={result.get('thumbnail', 0)}, skipped={result.get('skipped', 0)}, failed={result.get('failed', 0)}",
                    f"[修复] 完成：重命名 {result.get('renamed', 0)}，NFO {result.get('nfo', 0)}，封面 {result.get('thumbnail', 0)}，跳过 {result.get('skipped', 0)}，失败 {result.get('failed', 0)}",
                    f"[修復] 完了: 名前変更={result.get('renamed', 0)}, NFO={result.get('nfo', 0)}, サムネイル={result.get('thumbnail', 0)}, スキップ={result.get('skipped', 0)}, 失敗={result.get('failed', 0)}",
                )
            )
        self._update_summary()

    def _on_worker_error(self, message: str):
        self._append_log(tr(f"[Repair] error: {message}", f"[修复] 出错：{message}", f"[修復] エラー: {message}"))
        self._show_warning(
            tr("Repair failed", "修复失败", "修復に失敗"),
            str(message),
        )

    def _on_worker_finished(self):
        self._scan_feedback_timer.stop()
        self._worker = None
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        for control in (self._mode_combo, self._folder_edit, self._source_browse_btn,
                        self._template_edit, self._rule_combo, self._rename_check,
                        self._thumbnail_check, self._nfo_check,
                        self._download_video_check, self._history_check):
            control.setEnabled(True)
        self._update_mode_controls()
        ready = sum(1 for item in self._items if item.get("status") == "ready" and not item.get("target_conflict"))
        self._start_btn.setEnabled(bool(self._items) and ready > 0)
        self._update_summary()
        self._repair_log_active = False

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        worker = self._worker
        if worker is None:
            return True
        return stop_qthreads([worker], timeout_ms=timeout_ms)
