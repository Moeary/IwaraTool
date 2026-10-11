"""Find a task's video already present in the download directory."""

from __future__ import annotations

import os
import re
import time

from ...config import app_config
from ..manager_state import EXISTING_FILE_INDEX_TTL_SECONDS as _EXISTING_FILE_INDEX_TTL_SECONDS


class ExistingFilesMixin:
    """Find a task's video already present in the download directory."""

    def _find_existing_local_file(self, video_id: str) -> str | None:
        """Try to find an already-downloaded file by video ID without per-task os.walk."""
        video_id = str(video_id or "").strip()
        if not video_id:
            return None
        record = self.history.get_record(video_id)
        if record:
            file_path = str(record.get("file_path", "") or "")
            if file_path and os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                return file_path
        index = self._get_existing_file_index()
        path = index.get(video_id.lower())
        if path and os.path.isfile(path):
            return path
        return None

    def _get_existing_file_index(self) -> dict[str, str]:
        root = app_config.download_dir
        if not os.path.isdir(root):
            return {}
        now = time.monotonic()
        with self._existing_file_index_lock:
            if (
                self._existing_file_index_root == root
                and self._existing_file_index_built_at > 0
                and now - self._existing_file_index_built_at < _EXISTING_FILE_INDEX_TTL_SECONDS
            ):
                return dict(self._existing_file_index)

            index: dict[str, str] = {}
            try:
                for dirpath, _, filenames in os.walk(root):
                    for name in filenames:
                        lower_name = name.lower()
                        if lower_name.endswith("_temp") or lower_name.endswith(".aria2"):
                            continue
                        if not lower_name.endswith(".mp4"):
                            continue
                        full = os.path.join(dirpath, name)
                        if not os.path.isfile(full) or os.path.getsize(full) <= 0:
                            continue
                        for token in re.findall(r"[A-Za-z0-9_-]{8,}", os.path.splitext(name)[0]):
                            index.setdefault(token.lower(), full)
            except Exception:
                return {}
            self._existing_file_index = dict(index)
            self._existing_file_index_root = root
            self._existing_file_index_built_at = now
            return dict(index)
