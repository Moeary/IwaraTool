"""Root of the Flet interface: the adaptive shell around the current page (docs/flet_ui_plan.md 6.3).

``mount(page)`` creates the window's ``Shell``, wires page events and renders
``App``.  The navigation follows ``LayoutState.nav_mode``: bottom bar on
phones, a collapsed rail when the height is compact, a labelled rail on
medium and expanded widths, and an extended rail from large widths on.
"""
from __future__ import annotations

import flet as ft

from . import layout
from .components.account_panel import AccountPanelHost
from .components.navigation import build_bar, build_rail
from .pages import page_for
from .shell import Shell
from .stores.app_state import ShellContext, subscribe


@ft.component
def CurrentPage(shell: Shell):
    state = shell.state
    subscribe(state)
    page = page_for(state.destination)
    # Keyed by destination: each destination keeps its own component state.
    return ft.Container(expand=True, key=state.destination, content=page(state.destination, state.path))


@ft.component
def App(shell: Shell):
    state = shell.state
    subscribe(state)

    def body() -> ft.Control:
        mode = state.layout.nav_mode
        current = CurrentPage(shell)
        if mode == layout.NAV_BAR:
            content = ft.Column(expand=True, spacing=0, controls=[current, build_bar(shell)])
        else:
            content = ft.Row(
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[build_rail(shell), ft.VerticalDivider(width=1), current],
            )
        return ft.SafeArea(expand=True, content=ft.Stack(expand=True, controls=[content, AccountPanelHost()]))

    return ShellContext(shell, body)


def mount(page: ft.Page, *, start: str | None = None, theme_override: str = "") -> Shell:
    shell = Shell(page, start=start, theme_override=theme_override)
    page.on_resize = shell.on_resize
    page.on_keyboard_event = shell.on_keyboard
    shell.connect()
    shell.apply_theme()
    page.render(App, shell)
    return shell
