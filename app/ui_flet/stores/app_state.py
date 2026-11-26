"""App-wide state the shell and every page read (docs/flet_ui_plan.md 4.2).

An observable dataclass: assigning a field re-renders the components that
read it.  Only the event loop writes it (``services/events.py`` hops
signal_bus events over first).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import flet as ft

from ..layout import LayoutState
from ..routes import HOME, route


@ft.observable
@dataclass
class AppState:
    layout: LayoutState = field(default_factory=lambda: LayoutState(1280, 800))
    destination: str = HOME
    path: str = field(default_factory=lambda: route(HOME))
    can_back: bool = False
    language: str = "zh"
    theme_mode: str = "auto"
    rating: str = "all"
    site: str = "tv"
    logged_in: bool = False
    active_downloads: int = 0
    staged_downloads: int = 0
    account_open: bool = False
    cover_size: int = 224  # the shared cover width (presentation/media/card_size.py)


# Provides the ``Shell`` controller (state + actions) to every component below the root.
ShellContext = ft.create_context(None)


def subscribe(state: AppState) -> None:
    """Re-render the calling component when ``state`` changes.

    The context carries the ``Shell`` (state plus actions), which is not
    observable itself, so components subscribe to its state explicitly.
    """

    ft.use_state(state)


_MOUNTED: list = []  # the shell of this process's window (desktop and phone apps have exactly one)


def register_shell(shell) -> None:
    """Called by ``app.mount``: the fallback for ``use_shell`` below."""
    _MOUNTED[:] = [shell]


def use_shell():
    """The ``Shell`` of the window this component renders in (and subscribe to its state).

    Flet 1.1.3 loses the context for a component that re-renders on its own
    inside a memoized parent that was skipped (the parent component object is
    replaced and its contexts cleared; ``VideoGrid`` and the library rows are
    memoized), so the window's registered shell stands in.
    """

    shell = ft.use_context(ShellContext)
    if shell is None and _MOUNTED:
        shell = _MOUNTED[0]
    if shell is not None:
        subscribe(shell.state)
    return shell
