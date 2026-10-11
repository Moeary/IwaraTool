"""Filter and sort search results locally."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..rating import filter_by_rating
from .models import SearchFilters, SearchVideo


def split_search_terms(value: str) -> tuple[str, ...]:
    """Split comma/space separated tags or search terms consistently."""

    parts = re.split(r"[,，;；|\s]+", str(value or ""))
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        term = part.strip().lstrip("#")
        if not term:
            continue
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def parse_date(value: Any) -> date | None:
    """Parse the common Iwara ISO timestamp/date forms."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _contains_terms(video: SearchVideo, terms: tuple[str, ...], mode: str = "all") -> bool:
    if not terms:
        return True
    haystack = " ".join(
        (
            video.title,
            video.author_username,
            video.author_name,
            video.video_id,
            *video.tags,
            *video.origins,
            *video.characters,
        )
    ).casefold()
    checks = [term.casefold() in haystack for term in terms]
    return any(checks) if mode == "any" else all(checks)


def filter_videos(videos: list[SearchVideo], filters: SearchFilters) -> list[SearchVideo]:
    """Apply enhanced local filtering to normalized videos."""

    keyword_terms = split_search_terms(filters.keyword)
    include_tags = {tag.casefold() for tag in filters.include_tags if tag}
    exclude_tags = {tag.casefold() for tag in filters.exclude_tags if tag}
    include_tags.update(tag.casefold() for tag in filters.tag_all if tag)
    tag_any = {tag.casefold() for tag in filters.tag_any if tag}
    exclude_tags.update(tag.casefold() for tag in filters.tag_not if tag)
    author_terms = {term.casefold() for term in filters.author_any if term}
    if filters.author.strip():
        author_terms.add(filters.author.strip().casefold())
    author_not = {term.casefold() for term in filters.author_not if term}
    origin_any = {term.casefold() for term in filters.origin_any if term}
    origin_not = {term.casefold() for term in filters.origin_not if term}
    character_any = {term.casefold() for term in filters.character_any if term}
    character_not = {term.casefold() for term in filters.character_not if term}
    result: list[SearchVideo] = []
    for video in filter_by_rating(videos, filters.rating):
        video_tags = {tag.casefold() for tag in video.tags}
        origins = {value.casefold() for value in video.origins}
        characters = {value.casefold() for value in video.characters}
        author_text = (video.author_username + " " + video.author_name).casefold()
        if not _contains_terms(video, keyword_terms, filters.title_mode):
            continue
        if author_terms and not any(term in author_text for term in author_terms):
            continue
        if any(term in author_text for term in author_not):
            continue
        if origin_any and not any(term in " ".join(origins) for term in origin_any):
            continue
        if any(term in " ".join(origins) for term in origin_not):
            continue
        if character_any and not any(term in " ".join(characters) for term in character_any):
            continue
        if any(term in " ".join(characters) for term in character_not):
            continue
        if include_tags and not all(
            any(term in tag for tag in video_tags) for term in include_tags
        ):
            continue
        if tag_any and not any(
            term in tag for term in tag_any for tag in video_tags
        ):
            continue
        if any(term in tag for term in exclude_tags for tag in video_tags):
            continue
        if filters.min_views is not None and video.views < filters.min_views:
            continue
        if filters.max_views is not None and video.views > filters.max_views:
            continue
        if filters.min_likes is not None and video.likes < filters.min_likes:
            continue
        if filters.max_likes is not None and video.likes > filters.max_likes:
            continue
        if filters.min_duration is not None and video.duration < filters.min_duration:
            continue
        if filters.max_duration is not None and video.duration > filters.max_duration:
            continue
        published = parse_date(video.published_at)
        if filters.date_from and (published is None or published < filters.date_from):
            continue
        if filters.date_to and (published is None or published > filters.date_to):
            continue
        result.append(video)
    return result


def sort_videos(videos: list[SearchVideo], sort: str = "date") -> list[SearchVideo]:
    """Apply a stable local fallback sort after server filtering."""

    mode = str(sort or "date").casefold()
    if mode in {"views", "view", "most_viewed"}:
        key = lambda video: (video.views, video.likes, video.published_at)
    elif mode in {"likes", "like", "favorite", "favorites"}:
        key = lambda video: (video.likes, video.views, video.published_at)
    elif mode in {"popular", "popularity", "trending"}:
        key = lambda video: (video.views + video.likes * 20, video.published_at)
    else:
        key = lambda video: video.published_at
    return sorted(videos, key=key, reverse=True)
