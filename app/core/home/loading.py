"""Load Home rows, post details and comments: the work behind the Home workers.

Each function opens its own API client (``manager.create_worker_api_client``)
so independent sections load concurrently instead of queueing behind the
shared, lock-guarded session, and always closes it.  They block, so both
interfaces call them off the UI thread (a ``ManagedThread`` in PySide6,
``asyncio.to_thread`` in Flet).

``manager`` is the download manager (passed in so tests can replace it).
``should_stop`` is asked between the stages of a multi-request load; by
default it follows the current ``CancelToken`` (or QThread interruption).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..net_policy import cancellation_requested
from ..rating import filter_by_rating
from ..search.models import SearchVideo
from ..search.normalize import normalize_image, normalize_video
from .feed import MODE_AUTHOR, MODE_BROWSE, MODE_KEYWORD, MODE_TAGS


@dataclass(slots=True)
class FeedResult:
    token: int = 0
    items: list[SearchVideo] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)  # raw API rows, kept for the cache
    total: int | None = None
    has_more: bool = False
    page: int = 0
    error: str = ""


def item_kind(kind: str) -> str:
    return "image" if kind == "image" else "video"


def normalize_items(kind: str, rows: list[dict]) -> list[SearchVideo]:
    """Cards for API rows; old imported image posts with no files left are dropped.

    Their cover is the site's grey "broken file" placeholder and the post has
    nothing to show, which is common in the all-time popular lists.
    """

    if kind == "image":
        rows = [row for row in rows if int(row.get("numImages") or 0) > 0]
    normalize = normalize_image if kind == "image" else normalize_video
    return [item for item in map(normalize, rows) if item is not None]


def items_from_rows(kind: str, rows: list[dict], mode: str = MODE_BROWSE, rating: str = "") -> list[SearchVideo]:
    """Cards for cached or fresh rows; text search results are rated locally."""

    items = normalize_items(kind, rows)
    if mode == MODE_KEYWORD:
        items = filter_by_rating(items, rating)  # /search ignores the rating parameter
    return items


_AUTHOR_IDS: dict[str, str] = {}  # lower-case username -> user id, for this session


def author_user_id(username: str, client) -> tuple[str, str]:
    """``(user id, error)`` for an author's username, remembered per session."""

    key = str(username or "").strip().lstrip("@").casefold()
    if key in _AUTHOR_IDS:
        return _AUTHOR_IDS[key], ""
    user_id, error = client.get_user_id(key)
    if user_id:
        _AUTHOR_IDS[key] = user_id
        return user_id, ""
    return "", error or "author not found"


def fetch_feed_rows(
    manager: Any,
    client: Any,
    kind: str,
    params: dict[str, str],
    page: int,
    limit: int,
    *,
    mode: str = MODE_BROWSE,
    value: str = "",
) -> tuple[list[dict], int | None, bool, str]:
    """``(rows, total, has_more, error)`` for one page of a Home row."""

    kind = item_kind(kind)
    params = dict(params)
    if mode == MODE_TAGS:
        tags = manager.tag_dictionary.resolve_query(params.get("tags", ""))
        if not tags:
            return [], None, False, "no tags"
        params["tags"] = ",".join(tags)
    elif mode == MODE_KEYWORD:
        return client.search_page("images" if kind == "image" else "videos", params, page=page, limit=limit)
    elif mode == MODE_AUTHOR:
        user_id, error = author_user_id(value, client)
        if not user_id:
            return [], None, False, error
        params["user"] = user_id
    return manager.get_home_page(kind, params, page=page, limit=limit, api_client=client)


def load_feed_page(
    manager: Any,
    kind: str,
    params: dict[str, str],
    page: int,
    limit: int,
    *,
    mode: str = MODE_BROWSE,
    value: str = "",
    rating: str = "",
    token: int = 0,
) -> FeedResult:
    """One page of a Home row (``/videos``, ``/images`` or ``/search``); errors land in ``error``."""

    kind = item_kind(kind)
    page = max(0, int(page))
    result = FeedResult(token=token, page=page)
    client = None
    try:
        client = manager.create_worker_api_client()
        rows, total, has_more, error = fetch_feed_rows(
            manager, client, kind, params, page, int(limit), mode=mode, value=value,
        )
        result.rows = list(rows) if not error else []
        result.items = items_from_rows(kind, rows, mode, rating)
        result.total, result.has_more, result.error = total, has_more, error
    except Exception as exc:
        result.error = str(exc)
    finally:
        manager.close_worker_api_client(client)
    return result


def load_detail(
    manager: Any,
    kind: str,
    item_id: str,
    *,
    on_info: Callable[[dict | None, str], None],
    on_related: Callable[[list[SearchVideo]], None],
    on_comments: Callable[[list[dict], int | None, str], None],
    should_stop: Callable[[], bool] = cancellation_requested,
) -> None:
    """A post's full record, then its related items, then the first comments.

    Each stage is reported as soon as it arrives; nothing more is fetched once
    ``should_stop()`` is true or the record could not be loaded.
    """

    kind = item_kind(kind)
    client = None
    try:
        client = manager.create_worker_api_client()
        if kind == "image":
            info, error = manager.get_image_info(item_id, api_client=client)
        else:
            info, error = client.get_video_info(item_id)
        if should_stop():
            return
        on_info(info, error)
        if info is None:
            return
        rows, _err = manager.get_related_items(kind, item_id, api_client=client)
        if should_stop():
            return
        on_related(normalize_items(kind, rows))
        comments, total, comment_error = manager.get_item_comments(kind, item_id, page=0, api_client=client)
        if not should_stop():
            on_comments(comments, total, comment_error)
    except Exception as exc:
        if not should_stop():
            on_info(None, str(exc))
    finally:
        manager.close_worker_api_client(client)


def load_comments(
    manager: Any, kind: str, item_id: str, *, page: int = 0, parent: str = ""
) -> tuple[list[dict], int | None, str]:
    """``(rows, total, error)``: a page of comments, or the replies to ``parent``."""

    client = None
    rows: list[dict] = []
    total: int | None = None
    error = ""
    try:
        client = manager.create_worker_api_client()
        rows, total, error = manager.get_item_comments(
            item_kind(kind), item_id, page=page, parent=parent, api_client=client,
        )
    except Exception as exc:
        error = str(exc)
    finally:
        manager.close_worker_api_client(client)
    return rows, total, error


def call_with_client(manager: Any, call: Callable[[Any], Any]) -> tuple[Any, str]:
    """``(result, error)`` of ``call(client)`` on a private API client."""

    client = None
    try:
        client = manager.create_worker_api_client()
        return call(client), ""
    except Exception as exc:
        return None, str(exc)
    finally:
        manager.close_worker_api_client(client)
