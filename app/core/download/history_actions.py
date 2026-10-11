"""Download history records and opening / renaming downloaded files."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..models import TaskStatus


class HistoryActionsMixin:
    """Download history records and opening / renaming downloaded files."""

    def get_history_records(self) -> list[dict[str, Any]]:
        return self.history.list_records()

    def sync_history_with_download_folder(self) -> dict[str, int]:
        return self.history.sync_with_download_folder(app_config.download_dir)

    def remove_history_record(self, video_id: str):
        self.history.remove(video_id)

    def remove_history_records(self, video_ids: list[str]) -> int:
        return self.history.remove_many(video_ids)

    def open_history_output(
        self, video_id: str, *, open_file: bool = False
    ) -> tuple[bool, str]:
        record = self.history.get_record(video_id)
        if not record:
            return False, tr("History record does not exist", "历史记录不存在", "履歴が存在しません")

        file_path = str(record.get("file_path", "") or "")
        if not file_path or not os.path.exists(file_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        if open_file:
            return self._open_video_file(
                file_path, video_id=video_id, title=str(record.get("title", "") or "")
            )
        return self._open_system_path(os.path.dirname(file_path))

    def set_videos_watched(self, video_ids: list[str], watched: bool = True) -> int:
        """Mark downloaded videos as played (or not) for the history's "unwatched" view."""
        return self.history.set_watched(video_ids, watched)

    def _open_video_file(self, path: str, *, video_id: str = "", title: str = "") -> tuple[bool, str]:
        """Open a downloaded video in the player chosen in Settings."""
        from ..video_player import MODE_BUILTIN, open_local_video, player_mode

        if player_mode() == MODE_BUILTIN:
            # The preview window marks it watched when it plays the local file.
            signal_bus.video_preview_requested.emit(video_id, title, path)
            return True, ""
        ok, message = open_local_video(path)
        if ok and video_id:
            self.set_videos_watched([video_id])
        return ok, message

    def rename_history_file(
        self, video_id: str, new_filename: str
    ) -> tuple[bool, str]:
        record = self.history.get_record(video_id)
        if not record:
            return False, tr("History record does not exist", "历史记录不存在", "履歴が存在しません")

        old_path = str(record.get("file_path", "") or "")
        if not old_path or not os.path.isfile(old_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        old_dir = os.path.dirname(old_path)
        old_ext = os.path.splitext(old_path)[1] or ".mp4"
        cleaned_name = self._sanitize_path_segment(new_filename.strip())
        if cleaned_name in ("", "_"):
            return False, tr("Invalid file name", "文件名无效", "ファイル名が不正です")
        if not os.path.splitext(cleaned_name)[1]:
            cleaned_name += old_ext

        new_path = os.path.join(old_dir, cleaned_name)
        if os.path.abspath(new_path) == os.path.abspath(old_path):
            return False, tr("File name is unchanged", "文件名没有变化", "ファイル名は変更されていません")
        if os.path.exists(new_path):
            return False, tr("Target file already exists", "目标文件已存在", "変更先ファイルは既に存在します")

        old_stem = os.path.splitext(old_path)[0]
        new_stem = os.path.splitext(new_path)[0]
        sidecars: list[tuple[str, str]] = []

        old_thumbnail = str(record.get("thumbnail_path", "") or "")
        if old_thumbnail and os.path.isfile(old_thumbnail):
            thumb_ext = os.path.splitext(old_thumbnail)[1] or ".jpg"
            sidecars.append((old_thumbnail, new_stem + thumb_ext))

        nfo_path = old_stem + ".nfo"
        if os.path.isfile(nfo_path):
            sidecars.append((nfo_path, new_stem + ".nfo"))

        for _, target in sidecars:
            if os.path.exists(target):
                return False, tr(
                    f"Sidecar target already exists: {target}",
                    f"同名附属文件已存在: {target}",
                    f"関連ファイルの変更先が既に存在します: {target}",
                )

        new_thumbnail = old_thumbnail
        try:
            os.rename(old_path, new_path)
            for source, target in sidecars:
                os.rename(source, target)
                if source == old_thumbnail:
                    new_thumbnail = target
        except Exception as exc:
            return False, str(exc)

        self.history.update_file_paths(
            video_id,
            file_path=new_path,
            thumbnail_path=new_thumbnail if os.path.exists(new_thumbnail) else "",
        )
        signal_bus.log_message.emit(
            tr(
                f"[History] Renamed file: {old_path} -> {new_path}",
                f"[历史] 已重命名文件: {old_path} -> {new_path}",
                f"[履歴] ファイル名を変更しました: {old_path} -> {new_path}",
            )
        )
        return True, new_path

    def open_task_output(
        self, task_id: str, *, open_file: bool | None = None
    ) -> tuple[bool, str]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False, tr("Task does not exist", "任务不存在", "タスクが存在しません")
            status = task.status
            file_path = task.file_path
            title = task.title or task.video_id
            video_id = task.video_id

        if status != TaskStatus.COMPLETED:
            return False, tr(
                "Only completed tasks can be opened",
                "仅支持已完成任务",
                "完了タスクのみ開けます",
            )
        if not file_path or not os.path.exists(file_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        action = ""
        if open_file is None:
            action = str(app_config.completed_task_click_action or "folder").lower()
            open_file = action == "player"
        target = file_path if open_file else os.path.dirname(file_path)
        if not target:
            return False, tr("No openable path", "无可打开路径", "開けるパスがありません")

        if open_file:
            ok, message = self._open_video_file(file_path, video_id=video_id, title=title)
        else:
            ok, message = self._open_system_path(target)
        if not ok:
            return False, message

        if action == "player":
            action_text = tr("Player", "播放器", "プレイヤー")
        else:
            action_text = tr("File", "文件", "ファイル") if open_file else tr("Folder", "文件夹", "フォルダー")
        signal_bus.log_message.emit(
            tr(
                f"[Open] \"{title}\" -> {action_text}",
                f"[打开] 《{title}》 → {action_text}",
                f"[開く] 「{title}」 -> {action_text}",
            )
        )
        return True, ""

    def _open_system_path(self, target: str) -> tuple[bool, str]:
        if not target:
            return False, tr("No openable path", "无可打开路径", "開けるパスがありません")
        try:
            if os.name == "nt":
                os.startfile(target)
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", target])
            elif shutil.which("open"):
                subprocess.Popen(["open", target])
            else:
                return False, tr(
                    "System does not support auto-open",
                    "系统不支持自动打开",
                    "システムが自動オープンに対応していません",
                )
        except Exception as exc:
            return False, str(exc)
        return True, ""
