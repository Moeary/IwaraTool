"""Choosing and launching the player for a video that exists on disk.

``builtin`` is handled by the UI (it needs a window); everything else is a
detached external process started here.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from typing import Callable

from ..config import app_config
from ..i18n import tr
from ..logging_setup import get_logger

logger = get_logger(__name__)

MODE_SYSTEM = "system"
MODE_BUILTIN = "builtin"
MODE_CUSTOM = "custom"
MODES = (MODE_SYSTEM, MODE_BUILTIN, MODE_CUSTOM)
FILE_PLACEHOLDER = "{file}"


def player_mode() -> str:
    mode = str(app_config.preview_player_mode or MODE_SYSTEM).strip().lower()
    return mode if mode in MODES else MODE_SYSTEM


def split_command(command: str, *, windows: bool | None = None) -> list[str]:
    """Split a user-entered command line, honouring quotes."""
    windows = (os.name == "nt") if windows is None else windows
    parts = shlex.split(command, posix=not windows)
    if windows:
        # non-POSIX shlex keeps the surrounding quotes.
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    return parts


def build_custom_command(command: str, file_path: str, *, windows: bool | None = None) -> list[str]:
    """``command`` with ``{file}`` replaced, or the file appended when absent."""
    parts = split_command(str(command or "").strip(), windows=windows)
    if not parts:
        return []
    if any(FILE_PLACEHOLDER in part for part in parts):
        return [part.replace(FILE_PLACEHOLDER, file_path) for part in parts]
    return [*parts, file_path]


def local_video_path(video_id: str, history=None) -> str:
    """Path of the downloaded file for ``video_id`` if it still exists."""
    if history is None:
        from .manager import download_manager

        history = download_manager.history
    video_id = str(video_id or "").strip()
    if not video_id:
        return ""
    record = history.get_record(video_id)
    path = str((record or {}).get("file_path", "") or "")
    return path if path and os.path.isfile(path) else ""


def open_local_video(
    path: str,
    *,
    mode: str | None = None,
    launcher: Callable[[list[str]], None] | None = None,
) -> tuple[bool, str]:
    """Open ``path`` in the system or custom player (``builtin`` falls back to system)."""
    if not path or not os.path.isfile(path):
        return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")
    mode = mode or player_mode()
    try:
        if mode == MODE_CUSTOM:
            command = build_custom_command(app_config.preview_player_command, path)
            if not command:
                return False, tr(
                    "No custom player command is set in Settings",
                    "设置中尚未填写自定义播放器命令",
                    "設定でカスタムプレイヤーのコマンドが未設定です",
                )
            (launcher or _spawn)(command)
            return True, ""
        _open_with_system(path)
        return True, ""
    except Exception as exc:
        logger.warning("Could not open %s", path, exc_info=True)
        return False, str(exc)


def _spawn(command: list[str]) -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def _open_with_system(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - user-initiated, local file
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", path])
    elif shutil.which("open"):
        subprocess.Popen(["open", path])
    else:
        raise OSError(
            tr(
                "System does not support auto-open",
                "系统不支持自动打开",
                "システムが自動オープンに対応していません",
            )
        )
