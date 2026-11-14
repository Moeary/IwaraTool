"""Find a task's video already present in the download directory."""

from __future__ import annotations

import os
import re
import time

from ...config import app_config
from ..rules import rule_store
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
            skipped_dirs = self._temporary_output_dirs(root)
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    if skipped_dirs:
                        dirnames[:] = [
                            d for d in dirnames
                            if os.path.normcase(os.path.abspath(os.path.join(dirpath, d))) not in skipped_dirs
                        ]
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

    def _temporary_output_dirs(self, root: str) -> set[str]:
        """Fixed folders of rules that keep no history (``temp_video/...``).

        Files there are temporary copies: they must not make a later download
        of the same video look like it is already here.
        """

        dirs: set[str] = set()
        for rule in rule_store.list_available():
            payload = rule["payload"]
            if payload.get("record_to_history", True):
                continue
            prefix: list[str] = []
            for segment in str(payload.get("filename_template") or "").replace("\\", "/").split("/")[:-1]:
                if not segment.strip() or "{" in segment:
                    break
                prefix.append(self._sanitize_path_segment(segment))
            if prefix:
                dirs.add(os.path.normcase(os.path.abspath(os.path.join(root, *prefix))))
        return dirs
