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
    return _PAGES.get(destination, PlaceholderPage)
