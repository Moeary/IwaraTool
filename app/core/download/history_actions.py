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
from .locations import (
    STORAGE_REMOTE,
    FileLocation,
    record_is_remote,
    resolve_record_location,
)


def remote_unreachable_message(location: FileLocation, *, for_folder: bool = False) -> str:
    """Why a NAS copy cannot be opened from this machine, and what to set up."""

    if for_folder:
        return tr(
            f"The file is on the aria2 host ({location.remote_path}). Set its local mount folder in Settings > Download > aria2 to open it here.",
            f"文件保存在 aria2 远端（{location.remote_path}）。在「设置 > 下载 > aria2」填写本机挂载路径后即可在本机打开文件夹。",
            f"ファイルは aria2 ホスト上にあります（{location.remote_path}）。設定 > ダウンロード > aria2 でマウント先を指定すると開けます。",
        )
    return tr(
        f"The file is on the aria2 host ({location.remote_path}) and is not reachable from here. Set the local mount folder or the HTTP address in Settings > Download > aria2.",
        f"文件保存在 aria2 远端（{location.remote_path}），本机无法直接访问。请在「设置 > 下载 > aria2」填写本机挂载路径或 HTTP 访问地址。",
        f"ファイルは aria2 ホスト上にあり（{location.remote_path}）、ここからは開けません。設定 > ダウンロード > aria2 でマウント先か HTTP アドレスを指定してください。",
    )


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

    def history_location(self, video_id: str) -> FileLocation:
        """Where the recorded copy of ``video_id`` is (local file, NAS, or nowhere)."""
        return resolve_record_location(self.history.get_record(video_id))

    def open_history_output(
        self, video_id: str, *, open_file: bool = False
    ) -> tuple[bool, str]:
        record = self.history.get_record(video_id)
        if not record:
            return False, tr("History record does not exist", "历史记录不存在", "履歴が存在しません")
        return self._open_location(
            resolve_record_location(record),
            open_file=open_file,
            video_id=video_id,
            title=str(record.get("title", "") or ""),
        )

    def _open_location(
        self, location: FileLocation, *, open_file: bool, video_id: str, title: str,
    ) -> tuple[bool, str]:
        if open_file and location.playable_target:
            return self._open_video_file(location.playable_target, video_id=video_id, title=title)
        if not open_file and location.local_path:
            return self._open_system_path(os.path.dirname(location.local_path))
        if location.is_remote:
            return False, remote_unreachable_message(location, for_folder=not open_file)
        return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

    def set_videos_watched(self, video_ids: list[str], watched: bool = True) -> int:
        """Mark downloaded videos as played (or not) for the history's "unwatched" view."""
        return self.history.set_watched(video_ids, watched)

    def _open_video_file(self, path: str, *, video_id: str = "", title: str = "") -> tuple[bool, str]:
        """Open a downloaded video (a local path or a NAS copy's URL) in the player chosen in Settings."""
        from ..playback.video_player import open_local_video, opens_externally

        if not opens_externally(path):
            # The preview window marks it watched when it plays the file.
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

        if record_is_remote(record):
            return False, tr(
                "Files on the aria2 host cannot be renamed here",
                "远端 aria2 主机上的文件无法在这里重命名",
                "aria2 ホスト上のファイルはここで名前を変更できません",
            )
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
                    f"同名附属文件已存在：{target}",
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
            remote_path = task.remote_path if task.storage == STORAGE_REMOTE else ""

        if status != TaskStatus.COMPLETED:
            return False, tr(
                "Only completed tasks can be opened",
                "仅支持已完成任务",
                "完了タスクのみ開けます",
            )
        action = ""
        if open_file is None:
            action = str(app_config.completed_task_click_action or "folder").lower()
            open_file = action == "player"

        if remote_path:
            ok, message = self._open_location(
                resolve_record_location({"storage": STORAGE_REMOTE, "remote_path": remote_path}),
                open_file=open_file, video_id=video_id, title=title,
            )
        else:
            if not file_path or not os.path.exists(file_path):
                return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")
            if open_file:
                ok, message = self._open_video_file(file_path, video_id=video_id, title=title)
            else:
                ok, message = self._open_system_path(os.path.dirname(file_path))
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
