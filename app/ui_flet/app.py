"""Root of the Flet interface: the adaptive shell around the current page (docs/flet_ui_plan.md 6.3).

``mount(page)`` creates the window's ``Shell``, wires page events and renders
``App``.  The navigation follows ``LayoutState.nav_mode``: bottom bar on
phones, a collapsed rail when the height is compact, a labelled rail on
medium and expanded widths, and an extended rail from large widths on.
"""
from __future__ import annotations

import flet as ft

from . import layout, routes
from .components.account_panel import AccountPanelHost
from .components.nav_drawer import NavDrawerHost
from .components.navigation import build_bar, build_rail
from .components.split_handle import SplitHandle
from .components.swipe_back import SwipeBack
from .lifecycle import install_back_handler, install_pause_handler
from .pages import page_for
from .shell import Shell
from .stores.app_state import ShellContext, register_shell, subscribe


@ft.component
def CurrentPage(shell: Shell):
    state = shell.state
    subscribe(state)
    route = routes.parse(state.path, fallback=state.destination)
    if route.destination in (routes.DETAIL, routes.AUTHOR):
        # A post or an author opened over the destination it came from.
        page = page_for(route.destination)
        return ft.Container(expand=True, key=route.path, content=page(state.destination, state.path))
    page = page_for(state.destination)
    # Keyed by destination: each destination keeps its own component state.
    return ft.Container(expand=True, key=state.destination, content=page(state.destination, state.path))


def _rail_edge(shell: Shell) -> ft.Control:
    """Desktop: drag the rail's edge to widen or narrow it.  Elsewhere a plain rule."""
    view = shell.state.layout
    if view.rail_handle <= 1:
        return ft.VerticalDivider(width=1)
    return SplitHandle(shell.drag_rail, shell.end_rail_drag, width=view.rail_handle)


@ft.component
def App(shell: Shell):
    state = shell.state
    subscribe(state)

    def body() -> ft.Control:
        mode = state.layout.nav_mode
        current = CurrentPage(shell)
        if shell.caps.desktop:
            current = SwipeBack(current, shell.back)  # two fingers to the right on the trackpad
        if mode == layout.NAV_BAR:
            bar = [] if shell.secondary_page() else [build_bar(shell)]
            content = ft.Column(expand=True, spacing=0, controls=[current, *bar])
        else:
            content = ft.Row(
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[build_rail(shell), _rail_edge(shell), current],
            )
        drawer = [NavDrawerHost()] if mode == layout.NAV_BAR else []
        return ft.SafeArea(expand=True, content=ft.Stack(expand=True, controls=[content, *drawer, AccountPanelHost()]))

    return ShellContext(shell, body)


def mount(page: ft.Page, *, start: str | None = None, theme_override: str = "") -> Shell:
    shell = Shell(page, start=start, theme_override=theme_override)
    register_shell(shell)
    page.on_resize = shell.on_resize
    page.on_keyboard_event = shell.on_keyboard
    shell.connect()
    shell.apply_theme()
    page.render(App, shell)
    install_back_handler(page, shell.back)
    install_pause_handler(page, on_resume=shell.offer_copied_link)
    return shell
