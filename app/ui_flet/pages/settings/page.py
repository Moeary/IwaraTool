"""Settings (docs/flet_ui_plan.md 7.11): categories, and the cards of the chosen one.

Wide windows show the category list beside the cards; phones list the
categories and open ``/settings/<category>``.  Every change saves at once,
so there is no "Save all" button.  ``/settings/home/rows`` customizes Home.
"""
from __future__ import annotations

import flet as ft

from ....config import app_config
from ....presentation.settings import catalog
from ... import layout, routes
from ...components.page_frame import PageFrame
from ...icons import AppIcon, NavIcon
from ...stores.app_state import use_shell
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space
from .cards import CARDS
from .home_rows import HomeRowsPage

NAV_WIDTH = 248

ICONS = {
    "general": NavIcon.SETTINGS,
    "home": NavIcon.HOME,
    "network": NavIcon.ACCOUNT,
    "downloads": NavIcon.DOWNLOADS,
    "search": NavIcon.SEARCH,
    "subscriptions": NavIcon.SUBSCRIPTIONS,
    "tools": NavIcon.TOOLS,
    "system": NavIcon.REPAIR,
}
DESKTOP_ONLY = {"tools", "shortcuts"}


def categories(desktop: bool) -> list[catalog.Category]:
    return [c for c in catalog.CATEGORIES if desktop or c.key not in DESKTOP_ONLY]


def _icon(key: str, selected: bool):
    if key == "shortcuts":
        return AppIcon.KEYBOARD
    return ICONS[key].pick(selected)


def cards_of(category: catalog.Category) -> tuple[str, ...]:
    return ("shortcuts",) if category.key == "shortcuts" else category.cards


@ft.component
def CategoryCards(key: str):
    category = catalog.category(key) or catalog.CATEGORIES[0]
    controls = [CARDS[name](key=f"card:{name}") for name in cards_of(category) if name in CARDS]
    return ft.ListView(expand=True, spacing=Space.MD, padding=ft.Padding.only(bottom=Space.XL), controls=controls)


@ft.component
def CategoryList(selected: str, on_pick):
    shell = use_shell()
    palette = palette_for(shell.page)
    two_pane = shell.state.layout.two_pane

    def item(c: catalog.Category) -> ft.Control:
        chosen = two_pane and c.key == selected
        return ft.Container(
            padding=Space.MD, border_radius=Radius.CONTROL,
            bgcolor=palette.primary_container if chosen else None,
            on_click=lambda _e, k=c.key: on_pick(k), tooltip=c.summary if two_pane else None,
            content=ft.Row(spacing=Space.MD, controls=[
                ft.Icon(_icon(c.key, chosen), size=Size.ICON_LG),
                ft.Column(spacing=Space.XXS, expand=True, controls=[
                    ft.Text(c.title, style=text_style(TYPE.label)),
                    *([] if two_pane else [ft.Text(c.summary, style=text_style(TYPE.meta, color=palette.on_surface_variant))]),
                ]),
            ]),
        )

    return ft.ListView(expand=True, spacing=Space.XXS, controls=[item(c) for c in categories(shell.state.layout.is_desktop)])


@ft.component
def SettingsPage(destination: str, path: str):
    shell = use_shell()
    route = routes.parse(path)
    parts = list(route.parts)
    if parts[:2] == ["home", "rows"]:
        return HomeRowsPage(key="home-rows")
    view = shell.state.layout
    remembered = str(app_config.get_ui_value(catalog.CATEGORY_KEY, "general") or "general")
    asked = catalog.category(parts[0]) if parts else None

    def pick(key: str) -> None:
        app_config.set_ui_value(catalog.CATEGORY_KEY, key)
        shell.open(routes.route(routes.SETTINGS, key))

    if not view.two_pane:
        if asked is None:
            return PageFrame(title=routes.label(routes.SETTINGS), body=CategoryList("", pick), max_width=layout.MAX_WIDTH_FORM)
        return PageFrame(title=asked.title, body=CategoryCards(asked.key, key=f"cards:{asked.key}"),
                         max_width=layout.MAX_WIDTH_FORM)
    current = asked or catalog.category(remembered) or catalog.CATEGORIES[0]
    if current.key in DESKTOP_ONLY and not view.is_desktop:
        current = catalog.CATEGORIES[0]
    palette = palette_for(shell.page)
    body = ft.Row(expand=True, spacing=Space.LG, vertical_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
        ft.Container(width=NAV_WIDTH, content=CategoryList(current.key, pick)),
        ft.VerticalDivider(width=1),
        ft.Column(expand=True, spacing=Space.SM, controls=[
            ft.Text(current.summary, style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            ft.Container(expand=True, content=ft.Row(alignment=ft.MainAxisAlignment.START, controls=[
                ft.Container(width=min(layout.MAX_WIDTH_FORM, max(320, view.content_width - NAV_WIDTH - Space.XXL * 2)),
                             content=CategoryCards(current.key, key=f"cards:{current.key}")),
            ], vertical_alignment=ft.CrossAxisAlignment.STRETCH)),
        ]),
    ])
    return PageFrame(title=routes.label(routes.SETTINGS), body=body, max_width=layout.MAX_WIDTH_LIST_DETAIL)
