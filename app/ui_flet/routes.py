"""Destinations and routes of the Flet interface (docs/flet_ui_plan.md 4.4, 6.3).

Framework-neutral.  A route is ``/<destination>[/<rest>]``; each destination
is one page of the shell with its own back stack (``presentation.navigation``).
Primary destinations sit in the bottom bar / upper rail; secondary ones in
the lower rail on wide windows and in the account menu on phones.  Repair and
Tools are desktop only (8.3).
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

# The PySide6 ``startup_page`` values → destinations.
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


def available(destinations: tuple[str, ...], *, desktop: bool) -> tuple[str, ...]:
    return tuple(d for d in destinations if desktop or d not in DESKTOP_ONLY)


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
