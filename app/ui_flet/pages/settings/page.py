"""Settings (docs/flet_ui_plan.md 7.11): groups by purpose, and the cards of the chosen one.

Wide windows show the group list beside the cards ("About" at its foot);
phones list the groups and open ``/settings/<group>``.  The groups are the new
interface's own (``presentation/settings/groups.py``); old links that name a
Classic category or a card still land on the right group.  Every change saves at once,
so there is no "Save all" button.  ``/settings/home/rows`` customizes Home.
"""
from __future__ import annotations

import flet as ft

from ....config import app_config
from ....presentation.settings import groups
from ... import layout, routes
from ...components.page_frame import PageFrame
from ...icons import NavIcon, SettingsIcon
from ...stores.app_state import use_shell
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space
from .cards import CARDS
from .home_rows import HomeRowsPage

NAV_WIDTH = 248

ICONS = {
    "appearance": SettingsIcon.APPEARANCE,
    "browsing": SettingsIcon.BROWSING,
    "account": NavIcon.ACCOUNT,
    "downloads": NavIcon.DOWNLOADS,
    "search": NavIcon.SEARCH,
    "subscriptions": NavIcon.SUBSCRIPTIONS,
    "system": SettingsIcon.STORAGE,
    "shortcuts": SettingsIcon.KEYBOARD,
    "about": SettingsIcon.ABOUT,
}
DESKTOP_CARDS = {"ffmpeg", "window"}  # cards that only mean something on the desktop


def cards_of(group: groups.Group, *, desktop: bool) -> tuple[str, ...]:
    return tuple(c for c in group.cards if desktop or c not in DESKTOP_CARDS)


@ft.component
def GroupCards(key: str):
    shell = use_shell()
    desktop = shell.state.layout.is_desktop
    group = groups.find(key, desktop=desktop) or groups.groups()[0]
    controls = [CARDS[name](key=f"card:{name}") for name in cards_of(group, desktop=desktop) if name in CARDS]
    return ft.ListView(expand=True, spacing=Space.MD, padding=ft.Padding.only(bottom=Space.XL), controls=controls)


@ft.component
def GroupList(selected: str, on_pick):
    shell = use_shell()
    palette = palette_for(shell.page)
    two_pane = shell.state.layout.two_pane

    def item(g: groups.Group) -> ft.Control:
        chosen = two_pane and g.key == selected
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.SM + 2), border_radius=Radius.CONTROL,
            bgcolor=palette.primary_container if chosen else None,
            on_click=lambda _e, k=g.key: on_pick(k), tooltip=g.summary if two_pane else None,
            content=ft.Row(spacing=Space.MD, controls=[
                ft.Icon(ICONS[g.key].pick(chosen), size=Size.ICON),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(g.title, style=text_style(TYPE.label)),
                    *([] if two_pane else [ft.Text(g.summary, style=text_style(TYPE.meta, color=palette.on_surface_variant))]),
                ]),
            ]),
        )

    every = groups.visible(desktop=shell.state.layout.is_desktop)
    # "About" sits apart at the foot of the list.
    return ft.Column(expand=True, spacing=Space.XXS, controls=[
        ft.ListView(expand=True, spacing=Space.XXS, controls=[item(g) for g in every if g.key != "about"]),
        ft.Divider(height=1),
        *[item(g) for g in every if g.key == "about"],
    ])


@ft.component
def SettingsPage(destination: str, path: str):
    shell = use_shell()
    route = routes.parse(path)
    parts = list(route.parts)
    if parts[:2] == ["home", "rows"]:
        return HomeRowsPage(key="home-rows")
    view = shell.state.layout
    desktop = view.is_desktop
    remembered, set_remembered = ft.use_state(
        lambda: str(app_config.get_ui_value(groups.GROUP_KEY, "appearance") or "appearance"))
    asked = groups.find(parts[0], desktop=desktop) if parts else None

    def pick(key: str) -> None:
        app_config.set_ui_value(groups.GROUP_KEY, key)
        if not view.two_pane:
            shell.open(routes.route(routes.SETTINGS, key))
            return
        # Side by side, picking a group is not a step: stay at /settings and show the group.
        set_remembered(key)
        shell.navigator.show(routes.route(routes.SETTINGS))

    if not view.two_pane:
        if asked is None:
            return PageFrame(title=routes.label(routes.SETTINGS), body=GroupList("", pick), max_width=layout.MAX_WIDTH_FORM)
        return PageFrame(title=asked.title, body=GroupCards(asked.key, key=f"cards:{asked.key}"),
                         max_width=layout.MAX_WIDTH_FORM)
    current = asked or groups.find(remembered, desktop=desktop) or groups.groups()[0]
    palette = palette_for(shell.page)
    body = ft.Row(expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
        ft.Container(width=NAV_WIDTH, content=GroupList(current.key, pick)),
        ft.VerticalDivider(width=Space.XL, thickness=1),
        ft.Column(expand=True, spacing=Space.SM, controls=[
            ft.Text(current.title, style=text_style(TYPE.section_title_compact)),
            ft.Text(current.summary, style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            ft.Container(expand=True, content=ft.Row(alignment=ft.MainAxisAlignment.START, controls=[
                ft.Container(width=min(layout.MAX_WIDTH_FORM, max(320, view.content_width - NAV_WIDTH - Space.XXL * 2)),
                             content=GroupCards(current.key, key=f"cards:{current.key}")),
            ], vertical_alignment=ft.CrossAxisAlignment.STRETCH)),
        ]),
    ])
    return PageFrame(title=routes.label(routes.SETTINGS), body=body, max_width=layout.MAX_WIDTH_LIST_DETAIL)
