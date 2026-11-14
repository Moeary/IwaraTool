"""iwara.tv and iwara.ai: which site a request, link or record belongs to.

Both sites share one API (``api.iwara.tv``) and image host; the API tells them
apart only by the ``X-Site`` header. Listings (search, a creator's videos,
the subscription feed) silently return the other site's content under the
wrong header, while asking for a single post on the wrong site answers
``301 {"message": "errors.differentSite", "siteId": "iwara_ai"}``. Every post
also carries its ``siteId`` ("iwara" or "iwara_ai").

The app has one *current* site, chosen by the user, that browsing follows.
Work that belongs to a specific site (a pasted iwara.ai link, a subscription
added on iwara.ai) runs inside ``site_scope(site)``, which overrides the
current site for the calling thread only.
"""

from __future__ import annotations

import contextvars
import functools
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urlparse

SITE_TV = "tv"
SITE_AI = "ai"


@dataclass(frozen=True)
class Site:
    key: str
    host: str  # web host, sent as X-Site
    site_id: str  # the API's siteId
    label: str

    @property
    def origin(self) -> str:
        return f"https://{self.host}"


SITES: dict[str, Site] = {
    SITE_TV: Site(SITE_TV, "www.iwara.tv", "iwara", "iwara.tv"),
    SITE_AI: Site(SITE_AI, "www.iwara.ai", "iwara_ai", "iwara.ai"),
}
_BY_SITE_ID = {site.site_id: site.key for site in SITES.values()}

_scoped: contextvars.ContextVar[str] = contextvars.ContextVar("iwara_site", default="")


def normalize(key: object) -> str:
    """A known site key; anything else is iwara.tv (the original site)."""

    text = str(key or "").strip().lower()
    if text in SITES:
        return text
    return _BY_SITE_ID.get(text, SITE_TV)


def configured_site() -> str:
    from ..config import app_config

    return normalize(app_config.iwara_site)


def set_configured_site(key: str) -> bool:
    """Make ``key`` the site browsing follows; announces it with ``site_changed``."""

    from ..config import app_config
    from ..signal_bus import signal_bus

    new = normalize(key)
    if new == configured_site():
        return False
    app_config.iwara_site = new
    signal_bus.site_changed.emit(new)
    return True


def current_site() -> str:
    """The site this thread is working for: a ``site_scope`` or the user's choice."""

    return _scoped.get() or configured_site()


def site(key: str | None = None) -> Site:
    return SITES[normalize(key) if key else current_site()]


def other(key: str) -> str:
    return SITE_AI if normalize(key) == SITE_TV else SITE_TV


@contextmanager
def site_scope(key: str | None) -> Iterator[str]:
    """Run the block for ``key`` (no-op for ``None`` / ``""``)."""

    if not key:
        yield current_site()
        return
    token = _scoped.set(normalize(key))
    try:
        yield _scoped.get()
    finally:
        _scoped.reset(token)


def scoped_to_link(func):
    """Run ``method(self, link, ...)`` for the site ``link`` points at.

    A bare id or a link without an iwara host keeps the current site.
    """

    @functools.wraps(func)
    def wrapper(self, link, *args, **kwargs):
        with site_scope(site_of_url(str(link or ""))):
            return func(self, link, *args, **kwargs)

    return wrapper


def api_headers(key: str | None = None) -> dict[str, str]:
    current = site(key)
    return {"Origin": current.origin, "Referer": f"{current.origin}/", "X-Site": current.host}


# ── Links ────────────────────────────────────────────────────────────────────


def site_of_host(host: str) -> str:
    """``tv`` / ``ai`` for an iwara host, ``""`` for anything else."""

    host = str(host or "").strip().lower().split(":")[0]
    if host == "iwara.ai" or host.endswith(".iwara.ai"):
        return SITE_AI
    if host == "iwara.tv" or host.endswith(".iwara.tv"):
        return SITE_TV
    return ""


def site_of_url(url: str) -> str:
    """The site a web link points at; ``""`` when it is not an iwara.tv / iwara.ai link.

    API and file hosts (``api.iwara.tv``, ``i.iwara.tv``) are shared, so they
    say nothing about the site and give ``""`` as well.
    """

    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        host = (urlparse(text).netloc or "").lower()
    except ValueError:
        return ""
    if host.split(":")[0] in ("api.iwara.tv", "apiq.iwara.tv", "i.iwara.tv", "files.iwara.tv", "filesq.iwara.tv"):
        return ""
    return site_of_host(host)


def is_iwara_url(url: str) -> bool:
    text = str(url or "").strip()
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        return bool(site_of_host(urlparse(text).netloc))
    except ValueError:
        return False


def site_of_item(item: Any, default: str | None = None) -> str:
    """The site of an API post / normalized record (``siteId``, ``site`` or its link)."""

    if isinstance(item, dict):
        site_id = str(item.get("siteId") or item.get("site_id") or "").strip()
        if site_id in _BY_SITE_ID:
            return _BY_SITE_ID[site_id]
        explicit = str(item.get("site") or "").strip().lower()
        if explicit in SITES:
            return explicit
        for key in ("source_url", "url", "page_url", "iwara_url"):
            found = site_of_url(str(item.get(key) or ""))
            if found:
                return found
    else:
        for attribute in ("site", "source_url", "iwara_url", "url"):
            value = str(getattr(item, attribute, "") or "")
            if attribute == "site" and value in SITES:
                return value
            found = site_of_url(value) if attribute != "site" else ""
            if found:
                return found
    return normalize(default) if default else current_site()


def web_url(kind: str, item_id: str, key: str | None = None) -> str:
    """``https://www.iwara.{tv,ai}/{kind}/{id}`` (``""`` without an id)."""

    item_id = str(item_id or "").strip()
    if not item_id:
        return ""
    return f"{site(key).origin}/{kind}/{quote(item_id, safe='')}"


def video_url(video_id: str, key: str | None = None) -> str:
    return web_url("video", video_id, key)


def profile_url(username: str, key: str | None = None) -> str:
    return web_url("profile", username, key)


def home_url(key: str | None = None) -> str:
    return f"{site(key).origin}/"


def different_site(data: Any) -> str:
    """The right site from an ``errors.differentSite`` body, else ``""``."""

    if isinstance(data, dict) and str(data.get("message") or "") == "errors.differentSite":
        return _BY_SITE_ID.get(str(data.get("siteId") or ""), "")
    return ""
