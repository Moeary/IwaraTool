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


# Provides the ``Shell`` controller (state + actions) to every component below the root.
ShellContext = ft.create_context(None)


def subscribe(state: AppState) -> None:
    """Re-render the calling component when ``state`` changes.

    The context carries the ``Shell`` (state plus actions), which is not
    observable itself, so components subscribe to its state explicitly.
    """

    ft.use_state(state)


def use_shell():
    """The ``Shell`` of the window this component renders in (and subscribe to its state)."""

    shell = ft.use_context(ShellContext)
    if shell is not None:
        subscribe(shell.state)
    return shell
