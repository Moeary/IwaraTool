"""Which selected search results can be queued, and what to say about the rest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.search.models import IWARA_IMAGE_SOURCE_KIND, SearchPlaylist, SearchVideo
from ...i18n import tr
from ..downloads.staging import video_payload


def is_queueable(value: dict[str, Any]) -> bool:
    """Videos and whole playlists can be queued; image posts cannot."""

    data = value.get("data")
    if isinstance(data, SearchPlaylist):
        return True
    return isinstance(data, SearchVideo) and (data.source_kind in {"iwara", "oreno3d"} or bool(data.download_video_id))


@dataclass(slots=True)
class QueueSelection:
    videos: list[SearchVideo] = field(default_factory=list)
    playlists: list[SearchPlaylist] = field(default_factory=list)
    warning: str = ""  # set when nothing can be queued

    @property
    def needs_resolution(self) -> bool:
        """Oreno3D cards must be resolved to Iwara IDs first."""
        return any(video.source_kind == "oreno3d" for video in self.videos)

    @property
    def playlist_urls(self) -> list[str]:
        """Playlists are expanded into their videos inside the preview."""
        return [playlist.source_url for playlist in self.playlists if playlist.source_url]

    def known_videos(self) -> dict[str, SearchVideo]:
        return {video.download_video_id or video.video_id: video for video in self.videos}

    def video_ids(self) -> list[str]:
        return [video.download_video_id or video.video_id for video in self.videos]


def queue_selection(selected: list[dict[str, Any]]) -> QueueSelection:
    """Split ``{"kind", "data"}`` selections into queueable videos and playlists."""

    selection = QueueSelection()
    for value in selected:
        data = value.get("data")
        if not is_queueable(value):
            continue
        if isinstance(data, SearchPlaylist):
            selection.playlists.append(data)
        elif value.get("kind") == "video" and isinstance(data, SearchVideo):
            selection.videos.append(data)
    if not selection.videos and not selection.playlists:
        images = any(
            isinstance(value.get("data"), SearchVideo) and value["data"].source_kind == IWARA_IMAGE_SOURCE_KIND
            for value in selected
        )
        selection.warning = (
            tr(
                "Image posts cannot be downloaded; open them in the browser instead.",
                "图片作品暂不支持下载，请在浏览器中打开查看。",
                "画像投稿はダウンロードできません。ブラウザーで開いてください。",
            )
            if images
            else tr("Select at least one video", "请至少选择一个视频", "動画を1件以上選択してください")
        )
    return selection


def clean_ids(ids: list[str]) -> list[str]:
    return [str(video_id or "").strip() for video_id in ids if str(video_id or "").strip()]


def queue_payloads(ids: list[str], known: dict[str, SearchVideo]) -> list[dict[str, Any]]:
    return [video_payload(known[video_id], video_id) if video_id in known else {"id": video_id, "title": video_id} for video_id in ids]


def resolving_text() -> str:
    return tr("Resolving selected Oreno3D links…", "正在解析选中的 Oreno3D 下载链接…", "選択したOreno3Dリンクを解決中…")


def no_link_message(errors: list[str]) -> str:
    message = tr(
        "No selected item has an Iwara download link.",
        "选中的项目没有可用的 Iwara 下载链接。",
        "選択した項目にIwaraダウンロードリンクがありません。",
    )
    return f"{message} {errors[0]}" if errors else message


def skipped_message(skipped: int) -> str:
    return tr(
        f"Skipped {skipped} item(s) without an Iwara download link",
        f"已跳过 {skipped} 个无下载链接的项目",
        f"Iwara のリンクがない {skipped} 件をスキップしました",
    )


def resolve_failed_message() -> str:
    return tr("Could not resolve selected links", "无法解析选中的链接", "選択したリンクを解決できません")


def queue_label() -> str:
    return tr("Search results", "搜索结果", "検索結果")
