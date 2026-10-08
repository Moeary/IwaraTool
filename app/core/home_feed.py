"""What the Home page shows: its sections, tabs and the API request each maps to.

Kept free of Qt so the request shapes can be unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..i18n import tr
from .rating import api_rating

HOME_PAGE_LIMIT = 24  # items fetched per section on the Home page
BROWSE_PAGE_LIMIT = 32  # items per page in the full "More" view


@dataclass(frozen=True)
class FeedTab:
    id: str
    label: str
    kind: str  # "video" | "image"
    params: tuple[tuple[str, str], ...]
    needs_login: bool = False

    def request_params(self, rating: object = "") -> dict[str, str]:
        """``/videos`` or ``/images`` query for this tab under a SFW/NSFW choice."""

        params = dict(self.params)
        wanted = api_rating(rating)
        if wanted:
            params["rating"] = wanted
        return params


@dataclass(frozen=True)
class FeedSection:
    id: str
    title: str
    tabs: tuple[FeedTab, ...]

    def tab(self, tab_id: str) -> FeedTab:
        return next((tab for tab in self.tabs if tab.id == tab_id), self.tabs[0])


def _sorted_tabs(kind: str) -> tuple[FeedTab, ...]:
    return (
        FeedTab("trending", tr("Trending", "趋势", "トレンド"), kind, (("sort", "trending"),)),
        FeedTab("popularity", tr("Popular", "热度", "人気"), kind, (("sort", "popularity"),)),
        FeedTab("date", tr("Newest", "最新", "新着"), kind, (("sort", "date"),)),
    )


def home_sections() -> tuple[FeedSection, ...]:
    """The Home page layout, top to bottom (built on demand so labels follow the language)."""

    subscribed = (("subscribed", "true"), ("sort", "date"))
    return (
        FeedSection(
            "subscriptions",
            tr("My subscriptions", "我的订阅", "購読中の更新"),
            (
                FeedTab("videos", tr("Videos", "视频", "動画"), "video", subscribed, needs_login=True),
                FeedTab("images", tr("Images", "图片", "画像"), "image", subscribed, needs_login=True),
            ),
        ),
        FeedSection("hot_videos", tr("Hot videos", "热门视频", "人気の動画"), _sorted_tabs("video")),
        FeedSection("hot_images", tr("Hot images", "热门图片", "人気の画像"), _sorted_tabs("image")),
    )
