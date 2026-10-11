"""Pure helpers over subscription items: state, filtering, sorting, download ids."""

from __future__ import annotations

from typing import Any

from ...core.search.models import SearchVideo
from .helpers import _item_date_key, _title_matcher


STATE_FILTERS = ("all", "ready", "new", "downloaded", "moved", "queued", "unavailable")


ITEM_SORTS = ("date_desc", "date_asc", "new_first", "title")


def is_feed_source(source: dict[str, Any]) -> bool:
    """The old "account feed" source; the Home page replaced it."""

    return str(source.get("source_type", "") or "") == "feed"


def item_state(item: dict[str, Any]) -> str:
    """One word for what the user cares about: unavailable/downloaded/moved/queued/new/''."""

    if str(item.get("download_state", "") or ""):
        return "unavailable"
    if item.get("downloaded"):
        return "downloaded" if item.get("download_file_exists") else "moved"
    if item.get("queued"):
        return "queued"
    if int(item.get("is_new", 0) or 0):
        return "new"
    return ""


def item_to_video(item: dict[str, Any], *, author_name: str = "") -> SearchVideo:
    """A poster-card record for a stored subscription item."""

    video_id = str(item.get("video_id", "") or "")
    author = str(item.get("author", "") or "")
    return SearchVideo(
        video_id=video_id,
        title=str(item.get("title", "") or video_id),
        author_username=author,
        author_name=author_name or author,
        published_at=str(item.get("published_at", "") or ""),
        thumbnail_url=str(item.get("thumbnail_url", "") or ""),
        source_url=str(item.get("source_url", "") or "") or f"https://www.iwara.tv/video/{video_id}",
        raw={"_state": item_state(item), "_thumbnail_path": str(item.get("thumbnail_path", "") or "")},
    )


def filter_items(items: list[dict[str, Any]], mode: str, query: str = "") -> list[dict[str, Any]]:
    result = list(items)
    query = str(query or "").strip()
    if query:
        matcher = _title_matcher(query, regex_mode=False)
        result = [i for i in result if matcher(str(i.get("title", "") or i.get("video_id", "") or ""))]
    if mode == "ready":
        result = [i for i in result if item_state(i) in {"", "new"}]
    elif mode in {"new", "downloaded", "moved", "queued", "unavailable"}:
        result = [i for i in result if item_state(i) == mode]
    return result


def sort_items(items: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    result = list(items)
    if mode == "date_asc":
        result.sort(key=_item_date_key)
    elif mode == "title":
        result.sort(key=lambda i: str(i.get("title", "") or i.get("video_id", "") or "").casefold())
    else:
        result.sort(key=_item_date_key, reverse=True)
        if mode == "new_first":
            result.sort(key=lambda i: 0 if int(i.get("is_new", 0) or 0) else 1)
    return result


def downloadable_ids(items: list[dict[str, Any]], *, only_new: bool = False) -> list[str]:
    """Ids still worth queueing: not on disk, not queued, not blocked."""

    ids: list[str] = []
    for item in items:
        state = item_state(item)
        if state in {"downloaded", "queued", "unavailable"}:
            continue
        if only_new and state != "new":
            continue
        video_id = str(item.get("video_id", "") or "")
        if video_id and video_id not in ids:
            ids.append(video_id)
    return ids
