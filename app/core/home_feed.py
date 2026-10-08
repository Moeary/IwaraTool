"""What the Home page shows: its sections, tabs and the API request each maps to.

The layout is user-editable: Settings (or the Home page itself) stores an ordered
list of :class:`HomeSectionSpec` records and :func:`home_sections` turns the
enabled ones into :class:`FeedSection` objects.  Kept free of Qt so the request
shapes can be unit-tested.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

from ..i18n import tr
from .rating import api_rating

HOME_PAGE_LIMIT = 24  # items fetched per section on the Home page
BROWSE_PAGE_LIMIT = 32  # items per page in the full "More" view

HOME_SECTIONS_KEY = "home_sections_v1"

MODE_SUBSCRIPTIONS = "subscriptions"  # the signed-in account's feed
MODE_BROWSE = "browse"  # /videos or /images by sort order
MODE_TAGS = "tags"  # /videos filtered by tags
MODE_KEYWORD = "keyword"  # /search text index
MODE_AUTHOR = "author"  # one author's latest works
SECTION_MODES = (MODE_SUBSCRIPTIONS, MODE_BROWSE, MODE_TAGS, MODE_KEYWORD, MODE_AUTHOR)

# Orders each request type accepts ("" in a browse section means "show sort tabs").
BROWSE_SORTS = ("date", "trending", "popularity", "views", "likes")
KEYWORD_SORTS = ("relevance", "date", "views", "likes")
MAX_SECTIONS = 20


@dataclass(frozen=True)
class FeedTab:
    id: str
    label: str
    kind: str  # "video" | "image"
    params: tuple[tuple[str, str], ...]
    needs_login: bool = False
    mode: str = MODE_BROWSE
    value: str = ""  # tags / keyword / username for the non-browse modes

    @property
    def sort(self) -> str:
        return dict(self.params).get("sort", "")

    def request_params(self, rating: object = "") -> dict[str, str]:
        """``/videos`` or ``/images`` query for this tab under a SFW/NSFW choice.

        The text ``/search`` index ignores ``rating``; for those tabs the caller
        filters the rows locally instead.
        """

        params = dict(self.params)
        wanted = api_rating(rating)
        if wanted and self.mode != MODE_KEYWORD:
            params["rating"] = wanted
        return params

    def search_request(self) -> dict[str, Any]:
        """The Search-page request that shows this tab in full ("more in search")."""

        if self.mode == MODE_AUTHOR:
            return {"author": (self.value, self.value, "", "")}
        request: dict[str, Any] = {"scope": "images" if self.kind == "image" else "videos"}
        if self.mode == MODE_TAGS:
            request["scope"] = "tags"
            request["keyword"] = self.value
        elif self.mode == MODE_KEYWORD:
            request["keyword"] = self.value
        if self.sort:
            request["sort"] = self.sort
        return request

    @property
    def opens_in_search(self) -> bool:
        """Everything but the account feed is browsable on the Search page."""

        return not self.needs_login


@dataclass(frozen=True)
class FeedSection:
    id: str
    title: str
    tabs: tuple[FeedTab, ...]

    def tab(self, tab_id: str) -> FeedTab:
        return next((tab for tab in self.tabs if tab.id == tab_id), self.tabs[0])


@dataclass(frozen=True)
class HomeSectionSpec:
    """One configured row of the Home page (what Settings stores)."""

    id: str
    mode: str = MODE_BROWSE
    content: str = "video"  # "video" | "image"
    title: str = ""  # empty = the localized default for the mode
    value: str = ""  # tags / keyword / username
    sort: str = ""  # empty = all sort tabs (browse mode only)
    enabled: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "mode": self.mode, "content": self.content, "title": self.title,
            "value": self.value, "sort": self.sort, "enabled": self.enabled,
        }

    @property
    def is_builtin(self) -> bool:
        return self.id in BUILTIN_IDS

    def display_title(self) -> str:
        title = self.title.strip()
        if title:
            return title
        defaults = {
            "latest": tr("Latest", "最新", "新着"),
            "subscriptions": tr("My subscriptions", "我的订阅", "購読中の更新"),
            "hot_videos": tr("Hot videos", "热门视频", "人気の動画"),
            "hot_images": tr("Hot images", "热门图片", "人気の画像"),
        }
        if self.id in defaults:
            return defaults[self.id]
        if self.mode == MODE_TAGS:
            return f"#{self.value}"
        if self.mode == MODE_AUTHOR:
            return f"@{self.value}"
        if self.mode == MODE_KEYWORD:
            return self.value
        if self.mode == MODE_SUBSCRIPTIONS:
            return defaults["subscriptions"]
        return tr("Hot images", "热门图片", "人気の画像") if self.content == "image" else tr("Hot videos", "热门视频", "人気の動画")


BUILTIN_IDS = ("latest", "subscriptions", "hot_videos", "hot_images")


def default_specs() -> list[HomeSectionSpec]:
    """Newest uploads, the account feed, then the hot lists."""

    return [
        HomeSectionSpec("latest", MODE_BROWSE, "video", sort="date"),
        HomeSectionSpec("subscriptions", MODE_SUBSCRIPTIONS, "video"),
        HomeSectionSpec("hot_videos", MODE_BROWSE, "video"),
        HomeSectionSpec("hot_images", MODE_BROWSE, "image"),
    ]


def new_section_id() -> str:
    return f"c-{uuid.uuid4().hex[:8]}"


def sanitize_spec(raw: Any) -> HomeSectionSpec | None:
    """A valid spec from loosely-typed stored data, or ``None`` if unusable."""

    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("mode") or MODE_BROWSE).strip().lower()
    if mode not in SECTION_MODES:
        return None
    section_id = str(raw.get("id") or "").strip()[:40] or new_section_id()
    content = "image" if str(raw.get("content") or "").strip().lower() == "image" else "video"
    value = " ".join(str(raw.get("value") or "").split())[:120]
    if mode == MODE_AUTHOR:
        value = value.lstrip("@").strip("/ ")
    if mode in {MODE_TAGS, MODE_KEYWORD, MODE_AUTHOR} and not value:
        return None  # a search row with nothing to search for cannot load
    if mode == MODE_TAGS:
        content = "video"  # image posts have no tag filter
    sort = str(raw.get("sort") or "").strip().lower()
    allowed = KEYWORD_SORTS if mode == MODE_KEYWORD else BROWSE_SORTS
    if sort not in allowed:
        sort = "" if mode == MODE_BROWSE else ("relevance" if mode == MODE_KEYWORD else "date")
    if mode == MODE_SUBSCRIPTIONS:
        sort = ""
    return HomeSectionSpec(
        id=section_id,
        mode=mode,
        content=content,
        title=" ".join(str(raw.get("title") or "").split())[:60],
        value=value,
        sort=sort,
        enabled=bool(raw.get("enabled", True)),
    )


def sanitize_specs(raw: Any) -> list[HomeSectionSpec]:
    specs: list[HomeSectionSpec] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        spec = sanitize_spec(item)
        if spec is None:
            continue
        if spec.id in seen:
            spec = replace(spec, id=new_section_id())
        seen.add(spec.id)
        specs.append(spec)
        if len(specs) >= MAX_SECTIONS:
            break
    return specs


def load_specs(store: Any = None) -> list[HomeSectionSpec]:
    """The saved layout; a missing or corrupt value yields :func:`default_specs`."""

    if store is None:
        from ..config import app_config as store
    stored = store.get_ui_value(HOME_SECTIONS_KEY, "")
    if not stored:
        return default_specs()
    try:
        data = json.loads(stored) if isinstance(stored, str) else stored
    except (TypeError, ValueError):
        return default_specs()
    if not isinstance(data, list):
        return default_specs()
    return sanitize_specs(data)


def save_specs(specs: list[HomeSectionSpec], store: Any = None) -> None:
    if store is None:
        from ..config import app_config as store
    store.set_ui_value(
        HOME_SECTIONS_KEY,
        json.dumps([spec.to_json() for spec in sanitize_specs([s.to_json() for s in specs])], ensure_ascii=False),
    )


def _sorted_tabs(kind: str) -> tuple[FeedTab, ...]:
    return (
        FeedTab("trending", tr("Trending", "趋势", "トレンド"), kind, (("sort", "trending"),)),
        FeedTab("popularity", tr("Popular", "热度", "人気"), kind, (("sort", "popularity"),)),
        FeedTab("date", tr("Newest", "最新", "新着"), kind, (("sort", "date"),)),
    )


def sort_label(sort: str) -> str:
    return {
        "date": tr("Newest", "最新", "新着"),
        "trending": tr("Trending", "趋势", "トレンド"),
        "popularity": tr("Popular", "热度", "人気"),
        "views": tr("Most viewed", "最多观看", "再生数順"),
        "likes": tr("Most liked", "最多喜欢", "いいね順"),
        "relevance": tr("Relevance", "相关度", "関連度"),
    }.get(sort, sort)


def section_from_spec(spec: HomeSectionSpec) -> FeedSection:
    title = spec.display_title()
    kind = spec.content
    if spec.mode == MODE_SUBSCRIPTIONS:
        subscribed = (("subscribed", "true"), ("sort", "date"))
        return FeedSection(spec.id, title, (
            FeedTab("videos", tr("Videos", "视频", "動画"), "video", subscribed, needs_login=True),
            FeedTab("images", tr("Images", "图片", "画像"), "image", subscribed, needs_login=True),
        ))
    if spec.mode == MODE_BROWSE:
        if spec.sort:
            return FeedSection(spec.id, title, (
                FeedTab("main", sort_label(spec.sort), kind, (("sort", spec.sort),)),
            ))
        return FeedSection(spec.id, title, _sorted_tabs(kind))
    sort = spec.sort or ("relevance" if spec.mode == MODE_KEYWORD else "date")
    if spec.mode == MODE_KEYWORD:
        params = (("query", spec.value), ("sort", sort))
    elif spec.mode == MODE_TAGS:
        params = (("tags", spec.value), ("sort", sort))
    else:
        params = (("sort", sort),)
    return FeedSection(spec.id, title, (
        FeedTab("main", sort_label(sort), kind, params, mode=spec.mode, value=spec.value),
    ))


def home_sections(specs: list[HomeSectionSpec] | None = None) -> tuple[FeedSection, ...]:
    """The Home page layout, top to bottom (built on demand so labels follow the language)."""

    if specs is None:
        specs = load_specs()
    return tuple(section_from_spec(spec) for spec in specs if spec.enabled)
