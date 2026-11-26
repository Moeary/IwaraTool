"""Whether search results are already downloaded, without a UI framework.

``query_download_statuses`` reads one history batch and checks only the file
paths of those rows; call it from a worker thread.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any
from urllib.parse import unquote, urlsplit

from ...core import site as sites
from ...core.download.locations import record_is_remote
from ...core.search.models import SearchVideo
from ...i18n import tr


class SearchDownloadStatus(Enum):
    DOWNLOADED = "downloaded"
    MOVED = "moved"
    REMOTE = "remote"  # saved by aria2 on the NAS / server, not on this machine
    NOT_DOWNLOADED = "not_downloaded"
    UNIDENTIFIED = "unidentified"


def iwara_history_id(video: SearchVideo) -> str:
    """Use a resolved Iwara identity, never an unresolved Oreno movie ID."""

    video_id = str(video.download_video_id or "").strip()
    if video_id and not video_id.startswith("oreno3d:"):
        return video_id
    raw = video.raw if isinstance(video.raw, dict) else {}
    for url in (video.iwara_url, raw.get("iwara_url")):
        try:
            parsed = urlsplit(str(url or ""))
            host = str(parsed.hostname or "").lower()
        except ValueError:
            continue
        parts = parsed.path.split("/")
        if (
            sites.site_of_host(host)
            and len(parts) >= 3
            and parts[1] == "video"
            and parts[2]
        ):
            return unquote(parts[2])
    if video.source_kind == "iwara" and not video.video_id.startswith("oreno3d:"):
        return str(video.video_id or "").strip()
    return ""


def download_status_text(status: SearchDownloadStatus) -> str:
    if status is SearchDownloadStatus.DOWNLOADED:
        return tr("Downloaded locally", "本地已下载", "ローカル保存済み")
    if status is SearchDownloadStatus.REMOTE:
        return tr("Downloaded to NAS", "已下载到远端", "リモート保存済み")
    if status is SearchDownloadStatus.MOVED:
        return tr("Moved or missing", "已移走", "移動済み・見つからない")
    if status is SearchDownloadStatus.NOT_DOWNLOADED:
        return tr("Not downloaded", "未下载", "未ダウンロード")
    return tr("Not identified yet", "尚未识别", "未識別")


def query_download_statuses(
    history: Any, video_ids: list[str], *, interrupted=None
) -> dict[str, SearchDownloadStatus]:
    """Read one history batch and check only the file paths in those rows."""

    ids = list(dict.fromkeys(str(value or "").strip() for value in video_ids if str(value or "").strip()))
    if not ids:
        return {}
    records = history.get_records(ids)
    statuses: dict[str, SearchDownloadStatus] = {}
    file_exists: dict[str, bool] = {}
    for video_id in ids:
        if interrupted is not None and interrupted():
            return {}
        record = records.get(video_id)
        if record is None:
            statuses[video_id] = SearchDownloadStatus.NOT_DOWNLOADED
            continue
        if record_is_remote(record):
            statuses[video_id] = SearchDownloadStatus.REMOTE
            continue
        file_path = str(record.get("file_path") or "").strip()
        if file_path not in file_exists:
            file_exists[file_path] = bool(file_path and os.path.isfile(file_path))
        statuses[video_id] = (
            SearchDownloadStatus.DOWNLOADED if file_exists[file_path] else SearchDownloadStatus.MOVED
        )
    return statuses
