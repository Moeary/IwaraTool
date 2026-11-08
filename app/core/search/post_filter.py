"""Post-search filtering and re-sorting of loaded results.

Iwara's search API cannot restrict likes, views, duration and the like before
the query runs, so these conditions are applied locally to the results that
have already arrived, and stay in force while the user pages through them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .filtering import parse_date
from .models import SearchVideo

POST_FILTER_SORTS = (
    "server",
    "likes",
    "views",
    "like_ratio",
    "duration_desc",
    "duration_asc",
    "date_desc",
    "date_asc",
)
PUBLISHED_WITHIN_DAYS = (1, 7, 30, 90, 365)


@dataclass(frozen=True, slots=True)
class PostFilter:
    """Conditions applied to loaded results; ``None`` means no limit."""

    min_likes: int | None = None
    max_likes: int | None = None
    min_views: int | None = None
    max_views: int | None = None
    min_duration_minutes: float | None = None
    max_duration_minutes: float | None = None
    published_within_days: int | None = None
    min_like_ratio_percent: float | None = None
    exclude_tags: tuple[str, ...] = ()
    sort: str = "server"

    def condition_count(self) -> int:
        """How many filter conditions are set (the sort is not a condition)."""

        values = (
            self.min_likes, self.max_likes, self.min_views, self.max_views,
            self.min_duration_minutes, self.max_duration_minutes,
            self.published_within_days, self.min_like_ratio_percent,
        )
        return sum(value is not None for value in values) + (1 if self.exclude_tags else 0)

    def is_active(self) -> bool:
        return self.condition_count() > 0 or self.sort != "server"

    def validation_error(self) -> str:
        """English key of the first inconsistent range, or ``""``."""

        for low, high, name in (
            (self.min_likes, self.max_likes, "likes"),
            (self.min_views, self.max_views, "views"),
            (self.min_duration_minutes, self.max_duration_minutes, "duration"),
        ):
            if low is not None and high is not None and low > high:
                return name
        return ""


def metadata_pending(video: SearchVideo) -> bool:
    """Oreno3D cards carry no Iwara statistics until they are resolved."""

    return video.source_kind == "oreno3d" and not video.raw.get("_iwara_metadata_loaded")


def like_ratio(video: SearchVideo) -> float:
    return video.likes / video.views if video.views > 0 else 0.0


def _within(value: float, low: float | None, high: float | None) -> bool:
    return (low is None or value >= low) and (high is None or value <= high)


def matches(video: SearchVideo, post_filter: PostFilter, *, today: date | None = None) -> bool:
    if metadata_pending(video) and post_filter.condition_count():
        return False  # unknown statistics must not pass as zero (or as a match)
    if not _within(video.likes, post_filter.min_likes, post_filter.max_likes):
        return False
    if not _within(video.views, post_filter.min_views, post_filter.max_views):
        return False
    minutes = video.duration / 60
    if not _within(minutes, post_filter.min_duration_minutes, post_filter.max_duration_minutes):
        return False
    if post_filter.min_like_ratio_percent is not None:
        if like_ratio(video) * 100 < post_filter.min_like_ratio_percent:
            return False
    if post_filter.published_within_days is not None:
        published = parse_date(video.published_at)
        earliest = (today or date.today()) - timedelta(days=post_filter.published_within_days)
        if published is None or published < earliest:
            return False
    if post_filter.exclude_tags:
        tags = [tag.casefold() for tag in video.tags]
        if any(term.casefold() in tag for term in post_filter.exclude_tags for tag in tags):
            return False
    return True


def _sort_key(sort: str):
    if sort == "likes":
        return lambda video: video.likes, True
    if sort == "views":
        return lambda video: video.views, True
    if sort == "like_ratio":
        return lambda video: (like_ratio(video), video.likes), True
    if sort == "duration_desc":
        return lambda video: video.duration, True
    if sort == "duration_asc":
        return lambda video: video.duration, False
    if sort == "date_desc":
        return lambda video: video.published_at, True
    if sort == "date_asc":
        return lambda video: video.published_at, False
    return None, False


def apply_post_filter(
    videos: list[SearchVideo], post_filter: PostFilter, *, today: date | None = None
) -> list[SearchVideo]:
    """Filter, then re-sort; ties keep the server's order (stable sort)."""

    if not post_filter.is_active():
        return list(videos)
    result = [video for video in videos if matches(video, post_filter, today=today)]
    key, reverse = _sort_key(post_filter.sort)
    if key is not None:
        # Results still waiting for statistics go last instead of ranking as zero.
        known = [video for video in result if not metadata_pending(video)]
        pending = [video for video in result if metadata_pending(video)]
        result = sorted(known, key=key, reverse=reverse) + pending
    return result
