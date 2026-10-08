"""SFW / NSFW content rating shared by the Home and Search pages.

Iwara tags every video and image post as ``general`` (safe for work) or
``ecchi`` (adult).  ``/videos`` and ``/images`` accept ``rating=<value>`` and
filter on the server; omitting it returns both.  The text ``/search`` endpoint
ignores the parameter, so its results are filtered locally instead.
"""
from __future__ import annotations

from typing import Iterable, TypeVar

from ..i18n import tr

RATING_ALL = "all"
RATING_GENERAL = "general"  # SFW
RATING_ECCHI = "ecchi"  # NSFW (R-18)
RATINGS = (RATING_ALL, RATING_GENERAL, RATING_ECCHI)

UI_RATING_KEY = "content_rating_v1"

T = TypeVar("T")


def normalize_rating(value: object) -> str:
    """Canonical rating for user input; unknown text means no filter."""

    text = str(value or "").strip().casefold()
    aliases = {
        "sfw": RATING_GENERAL,
        "safe": RATING_GENERAL,
        "nsfw": RATING_ECCHI,
        "r18": RATING_ECCHI,
        "r-18": RATING_ECCHI,
        "adult": RATING_ECCHI,
    }
    text = aliases.get(text, text)
    return text if text in RATINGS else RATING_ALL


def api_rating(value: object) -> str:
    """The ``rating`` query value for a selection, ``""`` meaning "both"."""

    rating = normalize_rating(value)
    return "" if rating == RATING_ALL else rating


def rating_label(value: object) -> str:
    rating = normalize_rating(value)
    if rating == RATING_GENERAL:
        return tr("SFW", "SFW", "SFW")
    if rating == RATING_ECCHI:
        return tr("NSFW", "NSFW", "NSFW")
    return tr("All", "全部", "すべて")


def rating_options() -> list[tuple[str, str]]:
    """``(label, value)`` pairs for a selector, in display order."""

    return [(rating_label(value), value) for value in RATINGS]


def is_adult(rating: object) -> bool:
    return str(rating or "").strip().casefold() == RATING_ECCHI


def matches_rating(item_rating: object, selected: object) -> bool:
    """Whether an item with ``item_rating`` passes the ``selected`` filter.

    Items without a rating (Oreno3D cards, odd payloads) are kept for "All"
    and dropped by a specific filter, because their rating is unknown.
    """

    wanted = api_rating(selected)
    if not wanted:
        return True
    return str(item_rating or "").strip().casefold() == wanted


def filter_by_rating(items: Iterable[T], selected: object, *, attr: str = "rating") -> list[T]:
    """Keep the items whose ``attr`` matches the selected rating."""

    items = list(items)
    if not api_rating(selected):
        return items
    return [item for item in items if matches_rating(getattr(item, attr, ""), selected)]
