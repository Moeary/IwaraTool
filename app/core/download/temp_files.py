"""Temporary download files: find, clean up after retries and clear on request."""

from __future__ import annotations

import os

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..models import DownloadTask


class TempFilesMixin:
    """Temporary download files: find, clean up after retries and clear on request."""

    def _task_temp_candidates(self, task: DownloadTask) -> list[str]:
        candidates: list[str] = []
        if task.file_path:
            for suffix in ("_temp", ".tmp"):
                temp_path = f"{task.file_path}{suffix}"
                candidates.append(temp_path)
                candidates.append(f"{temp_path}.aria2")
        return list(dict.fromkeys(candidates))

    def _remove_task_temp_files(self, task: DownloadTask) -> tuple[int, int]:
        removed = 0
        failed = 0
        for temp_path in self._task_temp_candidates(task):
            if not os.path.exists(temp_path):
                continue
            try:
                os.remove(temp_path)
                removed += 1
            except Exception:
                failed += 1
        return removed, failed

    def _log_retry_temp_cleanup(self, title: str, file_path: str) -> tuple[int, int]:
        removed, failed = self._clear_retry_temp_files(file_path)
        if not removed and not failed:
            return removed, failed
        signal_bus.log_message.emit(
            tr(
                f"[Retry] Cleaned temp cache for \"{title}\": removed {removed}, failed {failed}",
                f"[重试] 已清理《{title}》对应临时缓存: 删除 {removed} 个，失败 {failed} 个",
                f"[再試行] 「{title}」の一時キャッシュを削除: 削除 {removed} / 失敗 {failed}",
            )
        )
        return removed, failed

    @staticmethod
    def _clear_retry_temp_files(file_path: str) -> tuple[int, int]:
        if not file_path:
            return 0, 0
        candidates: list[str] = []
        for suffix in ("_temp", ".tmp"):
            temp_path = f"{file_path}{suffix}"
            candidates.append(temp_path)
            candidates.append(f"{temp_path}.aria2")

        removed = 0
        failed = 0
        for temp_path in dict.fromkeys(candidates):
            if not os.path.isfile(temp_path):
                continue
            try:
                os.remove(temp_path)
                removed += 1
            except Exception:
                failed += 1
        return removed, failed

    def clear_temp_files(self) -> tuple[int, int]:
        """Delete all *_temp files under download directory.

        Returns:
            (removed_count, failed_count)
        """
        root = app_config.download_dir
        if not os.path.isdir(root):
            return 0, 0

        removed = 0
        failed = 0
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                if not name.endswith("_temp"):
                    continue
                temp_file = os.path.join(dirpath, name)
                try:
                    os.remove(temp_file)
                    removed += 1
                except Exception:
                    failed += 1
                    continue
                sidecar = f"{temp_file}.aria2"
                if os.path.exists(sidecar):
                    try:
                        os.remove(sidecar)
                    except Exception:
                        failed += 1
        return removed, failed
