"""Data models for the download task state machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..i18n import tr


class TaskStatus(Enum):
    QUEUED_META = "queued_meta"        # 仅有元数据，未解析直链
    RESOLVING = "resolving"            # 正在解析真实下载直链
    QUEUED_DOWNLOAD = "queued_download"  # 已拿到直链，等待下载槽位
    DOWNLOADING = "downloading"        # 正在下载
    CANCELLING = "cancelling"          # 正在中断
    CANCELLED = "cancelled"            # 已中断
    SKIPPED = "skipped"                # 命中过滤条件，已跳过
    COMPLETED = "completed"            # 下载完成
    FAILED = "failed"                  # 下载失败


_STATUS_LABEL_TEXTS: dict[TaskStatus, tuple[str, str, str]] = {
    TaskStatus.QUEUED_META: ("Queued", "排队中", "待機中"),
    TaskStatus.RESOLVING: ("Resolving", "解析中", "解析中"),
    TaskStatus.QUEUED_DOWNLOAD: ("Waiting", "待下载", "待機"),
    TaskStatus.DOWNLOADING: ("Downloading", "下载中", "ダウンロード中"),
    TaskStatus.CANCELLING: ("Cancelling", "中断中", "中断中"),
    TaskStatus.CANCELLED: ("Cancelled", "已中断", "キャンセル済み"),
    TaskStatus.SKIPPED: ("Skipped", "已跳过", "スキップ"),
    TaskStatus.COMPLETED: ("Completed", "已完成", "完了"),
    TaskStatus.FAILED: ("Failed", "失败", "失敗"),
}


def status_label(status: TaskStatus) -> str:
    """Return a status label using the current UI language."""
    texts = _STATUS_LABEL_TEXTS.get(status)
    return tr(texts[0], texts[1], texts[2]) if texts else status.value


@dataclass
class DownloadTask:
    task_id: str
    url: str                  # 原始输入 URL
    video_id: str             # Iwara video ID
    title: str = ""
    author: str = ""
    thumbnail_url: str = ""
    status: TaskStatus = TaskStatus.QUEUED_META
    download_url: str = ""
    filename: str = ""
    file_path: str = ""
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed_str: str = ""
    error_msg: str = ""
    quality: str = ""         # 实际选择的画质
    published_at: str = ""    # API createdAt
    likes: int = 0
    views: int = 0
    slug: str = ""
    rating: str = ""
    duration: int = 0
    comments: int = 0
    tags_json: str = ""
    raw_json: str = ""
    file_url: str = ""
    file_id: str = ""
    thumbnail_index: int = 0
    thumbnail_path: str = ""
    cancel_requested: bool = False
    delete_temp_on_cancel: bool = False
    remove_after_cancel: bool = False
    cancel_origin: str = ""
    aria2_gid: str = ""
