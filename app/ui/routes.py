"""Destinations and routes of the Flet interface.

Framework-neutral.  A route is ``/<destination>[/<rest>]``; each destination
is one page of the shell with its own back stack (``presentation.navigation``).
Primary destinations sit in the bottom bar / upper rail.  On the desktop the
rail's foot holds two entries, Tools and Settings; Tools stands for rules,
repair and the upload tools (``TOOL_GROUP``, listed side by side in the Tools hub).
Phones show rules and settings in the account panel.  Repair and Tools are
desktop only.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..i18n import tr

HOME = "home"
SUBSCRIPTIONS = "subscriptions"
SEARCH = "search"
DOWNLOADS = "downloads"
LIBRARY = "library"
RULES = "rules"
REPAIR = "repair"
TOOLS = "tools"
SETTINGS = "settings"
# Routes that are not shell destinations: they open over a destination.
DETAIL = "detail"
AUTHOR = "author"

PRIMARY = (HOME, SUBSCRIPTIONS, SEARCH, DOWNLOADS, LIBRARY)
SECONDARY = (RULES, REPAIR, TOOLS, SETTINGS)
DESKTOP_ONLY = frozenset({REPAIR, TOOLS})
DESTINATIONS = PRIMARY + SECONDARY
# One "Tools" entry on the desktop: rules first, repair second, then the upload tools (maintainer, 2026-11-26).
TOOL_GROUP = (RULES, REPAIR, TOOLS)
FOOT = (TOOLS, SETTINGS)  # the rail's foot on the desktop

# Saved ``startup_page`` values (v1.2 names included) → destinations.
_STARTUP = {
    "home": HOME,
    "subscriptions": SUBSCRIPTIONS,
    "search": SEARCH,
    "download": DOWNLOADS,
    "downloads": DOWNLOADS,
    "history": LIBRARY,
    "library": LIBRARY,
    "rules": RULES,
    "repair": REPAIR,
    "tools": TOOLS,
    "settings": SETTINGS,
}


def label(destination: str) -> str:
    return {
        HOME: tr("Home", "首页", "ホーム"),
        SUBSCRIPTIONS: tr("Subscriptions", "订阅", "購読"),
        SEARCH: tr("Search", "搜索", "検索"),
        DOWNLOADS: tr("Downloads", "下载", "ダウンロード"),
        LIBRARY: tr("Library", "资料库", "ライブラリ"),
        RULES: tr("Rules", "规则", "ルール"),
        REPAIR: tr("Repair", "修复", "修復"),
        TOOLS: tr("Tools", "工具", "ツール"),
        SETTINGS: tr("Settings", "设置", "設定"),
        DETAIL: tr("Details", "详情", "詳細"),
        AUTHOR: tr("Author", "作者", "作者"),
    }.get(destination, destination)


def entries(*, desktop: bool) -> tuple[str, ...]:
    """Navigation entries in order: the primary five, then the foot (or rules and settings on phones)."""
    return PRIMARY + (FOOT if desktop else (RULES, SETTINGS))


def entry_for(destination: str, *, desktop: bool) -> str:
    """The entry that is marked while ``destination`` is shown."""
    return TOOLS if desktop and destination in TOOL_GROUP else destination


def short_label(destination: str) -> str:
    """Bottom-bar label: about 8 Latin characters fit one fifth of a 320 dp phone."""
    return {
        SUBSCRIPTIONS: tr("Subs", "订阅", "購読"),
        DOWNLOADS: tr("Queue", "下载", "保存"),
    }.get(destination) or label(destination)


def available(destinations: tuple[str, ...], *, desktop: bool) -> tuple[str, ...]:
    return tuple(d for d in destinations if desktop or d not in DESKTOP_ONLY)


def allowed(path: str, *, desktop: bool) -> bool:
    """False for a desktop-only destination (or a path inside one) on a phone."""
    return desktop or parse(path, fallback=HOME).destination not in DESKTOP_ONLY


def startup_destination(value: object, *, desktop: bool = True) -> str:
    destination = _STARTUP.get(str(value or "").strip().lower(), HOME)
    return destination if desktop or destination not in DESKTOP_ONLY else HOME


@dataclass(frozen=True, slots=True)
class Route:
    destination: str
    parts: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return "/" + "/".join((self.destination, *self.parts))

    @property
    def is_root(self) -> bool:
        return not self.parts


def parse(path: str, *, fallback: str = HOME) -> Route:
    """``/library/123`` → ``Route("library", ("123",))``; unknown roots fall back."""

    parts = tuple(part for part in str(path or "").split("?", 1)[0].split("/") if part)
    if not parts or parts[0] not in (*DESTINATIONS, DETAIL, AUTHOR):
        return Route(fallback)
    return Route(parts[0], parts[1:])


def route(destination: str, *parts: object) -> str:
    return Route(destination, tuple(str(part) for part in parts if str(part))).path
