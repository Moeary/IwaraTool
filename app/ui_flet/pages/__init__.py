"""Pages of the Flet interface, one per shell destination.

``page_for(destination)`` returns the component that renders a destination;
destinations not ported yet show ``PlaceholderPage``.
"""
from __future__ import annotations

from collections.abc import Callable

from .placeholder import PlaceholderPage

_PAGES: dict[str, Callable] = {}


def register(destination: str, component: Callable) -> None:
    _PAGES[destination] = component


def page_for(destination: str) -> Callable:
    if not _PAGES:
        _register_ported()
    return _PAGES.get(destination, PlaceholderPage)


def _register_ported() -> None:
    from .. import routes
    from .author.page import AuthorPage
    from .detail.page import DetailPage
    from .downloads.page import DownloadsPage
    from .home.page import HomePage
    from .library.page import LibraryPage
    from .rules.page import RulesPage
    from .search.page import SearchPage
    from .settings.page import SettingsPage
    from .subscriptions.page import SubscriptionsPage

    register(routes.AUTHOR, AuthorPage)
    register(routes.DETAIL, DetailPage)
    register(routes.DOWNLOADS, DownloadsPage)
    register(routes.HOME, HomePage)
    register(routes.LIBRARY, LibraryPage)
    register(routes.RULES, RulesPage)
    register(routes.SEARCH, SearchPage)
    register(routes.SETTINGS, SettingsPage)
    register(routes.SUBSCRIPTIONS, SubscriptionsPage)
