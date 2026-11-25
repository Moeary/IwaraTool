"""What a result card shows, from any source (docs/flet_ui_plan.md 7.0 ``VideoCard``).

One shape for search, home, subscriptions, author pages and the library's
cover view, so every card reads the same: cover with duration, title (two
lines), an author line and one meta line.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core.search.models import IWARA_IMAGE_SOURCE_KIND, SearchAuthor, SearchPlaylist, SearchVideo
from ...core.search.normalize import small_cover_url
from ...i18n import tr
from ..formatting import _format_count, _format_duration
from ..search.download_status import iwara_history_id
from ..search.queueing import is_queueable

KIND_VIDEO = "video"
KIND_IMAGE = "image"
KIND_AUTHOR = "author"
KIND_PLAYLIST = "playlist"


@dataclass(frozen=True, slots=True)
class CardView:
    key: str  # unique within one result list: "video:<id>", "author:<id>"…
    kind: str
    title: str
    subtitle: str = ""  # the author line
    meta: str = ""  # views · likes · date
    duration: str = ""
    cover_kind: str = "video"  # image cache bucket
    cover_key: str = ""
    cover_url: str = ""
    cover_local: str = ""
    round_cover: bool = False  # avatars
    selectable: bool = True
    history_id: str = ""  # the Iwara video ID for download-status lookups ("" until known)
    tooltip: str = ""


def stats_text(views: int, likes: int, date: str = "") -> str:
    if not views and not likes:
        return date[:10]  # records without counts (stored subscription items): "0 views" would mislead
    stats = tr(
        f"{_format_count(views)} views · {_format_count(likes)} likes",
        f"{_format_count(views)} 播放 · {_format_count(likes)} 点赞",
        f"{_format_count(views)} 再生 · {_format_count(likes)} いいね",
    )
    return f"{stats} · {date[:10]}" if date else stats


def author_line(username: str, name: str = "") -> str:
    if name and username and name != username:
        return f"{name} @{username}"
    if username:
        return f"@{username}"
    return name or tr("Unknown author", "未知作者", "作者不明")


def video_card(video: SearchVideo) -> CardView:
    image = video.source_kind == IWARA_IMAGE_SOURCE_KIND
    return CardView(
        key=f"video:{video.video_id}",
        kind=KIND_IMAGE if image else KIND_VIDEO,
        title=video.title or video.video_id,
        subtitle=author_line(video.author_username, video.author_name),
        meta=stats_text(video.views, video.likes, video.published_at),
        duration=_format_duration(video.duration) if video.duration else "",
        cover_kind="video",
        cover_key=video.video_id,
        cover_url=small_cover_url(video.thumbnail_url),
        selectable=is_queueable({"kind": "video", "data": video}),
        history_id=iwara_history_id(video),
        tooltip=video.title,
    )


def author_card(author: SearchAuthor) -> CardView:
    count = int(author.video_count or 0)
    return CardView(
        key=f"author:{author.author_id}",
        kind=KIND_AUTHOR,
        title=author.name or author.username,
        subtitle=f"@{author.username}" if author.username else "",
        meta=tr(f"{count} videos", f"{count} 个视频", f"動画 {count} 件") if count else (author.joined_at or "")[:10],
        cover_kind="avatar",
        cover_key=author.author_id,
        cover_url=author.avatar_url,
        round_cover=True,
        selectable=False,
        tooltip=author.bio[:200] if author.bio else "",
    )


def playlist_card(playlist: SearchPlaylist) -> CardView:
    count = int(playlist.video_count or 0)
    return CardView(
        key=f"playlist:{playlist.playlist_id}",
        kind=KIND_PLAYLIST,
        title=playlist.title or playlist.playlist_id,
        subtitle=author_line(playlist.author_username, playlist.author_name),
        meta=tr(f"{count} videos", f"{count} 个视频", f"動画 {count} 件"),
        cover_kind="playlist",
        cover_key=playlist.playlist_id,
        cover_url=small_cover_url(playlist.thumbnail_url),
        selectable=True,
        tooltip=playlist.title,
    )


def card_for(item: Any) -> CardView | None:
    if isinstance(item, SearchVideo):
        return video_card(item)
    if isinstance(item, SearchAuthor):
        return author_card(item)
    if isinstance(item, SearchPlaylist):
        return playlist_card(item)
    return None
