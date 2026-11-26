"""Phones: a modal navigation drawer (Material 3) beside the bottom bar.

The bar holds the five primary destinations.  The drawer lists every
destination plus the settings groups, so a setting is two taps away from any
page.  It opens from the menu button in the page header (``PageFrame``) or a
swipe from the left edge, and closes on the scrim, a swipe back to the left,
Back, or any navigation.  It is drawn in the shell's stack rather than as
``View.drawer``: the declarative root owns the view, and this keeps the drawer
an ordinary component that follows ``state.drawer_open``.
"""
from __future__ import annotations

import flet as ft

from ...i18n import tr
from ...presentation.settings import groups
from .. import routes
from ..icons import NavIcon
from ..stores.app_state import use_shell
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space
from .cover import Cover
from .navigation import DESTINATION_ICONS, SETTINGS_GROUP_ICONS, current_entry

MAX_WIDTH = 360  # Material's modal drawer is at most 360 dp, leaving 56 dp of scrim on a phone
EDGE = Space.LG  # width of the left-edge strip a swipe opens the drawer from
FLING = 300  # px/s: a slower drag counts by distance instead
SCRIM_OPACITY = 0.32


def drawer_width(window_width: float) -> float:
    return max(0.0, min(MAX_WIDTH, window_width - 56))


def _item(palette, icon, label: str, *, selected: bool, on_click) -> ft.Control:
    color = palette.on_surface if selected else palette.on_surface_variant
    return ft.Container(
        height=Size.NAV_DRAWER_ITEM,
        border_radius=Radius.FULL,
        bgcolor=palette.primary_container if selected else None,
        padding=ft.Padding.symmetric(horizontal=Space.LG),
        on_click=on_click,
        content=ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Icon(icon, size=Size.ICON, color=color),
            ft.Text(label, style=text_style(TYPE.label, color=color), expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, weight=ft.FontWeight.W_700 if selected else None),
        ]),
    )


def _heading(text: str, palette) -> ft.Control:
    return ft.Container(padding=ft.Padding.only(left=Space.LG, top=Space.MD, bottom=Space.XS),
                        content=ft.Text(text, style=text_style(TYPE.meta, color=palette.on_surface_variant)))


def _account(shell, palette) -> ft.Control:
    state = shell.state
    logged_in = state.logged_in
    profile = state.profile if logged_in else {}
    avatar: ft.Control = (
        Cover("avatar", profile["id"], profile.get("avatar_url", ""), Size.AVATAR, aspect=1, round=True,
              icon=NavIcon.ACCOUNT.active, key=f"drawer-avatar:{profile['id']}")
        if profile.get("id") else ft.Icon(NavIcon.ACCOUNT.pick(logged_in), size=Size.AVATAR)
    )
    name = profile.get("name") or (tr("Signed in", "已登录", "ログイン済み") if logged_in else tr("Not signed in", "未登录", "未ログイン"))
    hint = tr("Account and preferences", "账号与偏好", "アカウントと設定")

    def open_account(_e) -> None:
        shell.close_drawer()
        shell.open_account()

    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Space.LG, vertical=Space.MD), border_radius=Radius.CARD,
        on_click=open_account, tooltip=hint,
        content=ft.Row(spacing=Space.MD, controls=[
            avatar,
            ft.Column(spacing=0, expand=True, controls=[
                ft.Text(name, style=text_style(TYPE.subtitle), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(hint, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ]),
        ]),
    )


def drawer_content(shell) -> ft.Control:
    palette = palette_for(shell.page)
    state = shell.state
    desktop = state.layout.is_desktop
    current = current_entry(shell)

    def go(destination: str):
        return lambda _e: shell.select_entry(destination)

    def setting(key: str):
        def open_group(_e) -> None:
            shell.close_drawer()
            shell.open(routes.route(routes.SETTINGS, key))
        return open_group

    group_shown = routes.parse(state.path).parts[:1] if state.destination == routes.SETTINGS else ()
    entries = routes.entries(desktop=desktop)
    places = [_item(palette, DESTINATION_ICONS[d].pick(d == current), routes.label(d), selected=d == current,
                    on_click=go(d)) for d in entries if d != routes.SETTINGS]
    settings = [_item(palette, SETTINGS_GROUP_ICONS[g.key].pick(g.key in group_shown), g.title,
                      selected=g.key in group_shown, on_click=setting(g.key))
                for g in groups.visible(desktop=desktop) if g.key in SETTINGS_GROUP_ICONS]
    return ft.ListView(
        expand=True, spacing=Space.XXS, padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.MD),
        controls=[
            _account(shell, palette),
            ft.Divider(height=Space.MD),
            *places,
            ft.Divider(height=Space.MD),
            _heading(routes.label(routes.SETTINGS), palette),
            *settings,
        ],
    )


@ft.component
def NavDrawerHost():
    """The scrim, the sheet and the edge strip; nothing to see while closed."""

    shell = use_shell()
    state = shell.state
    palette = palette_for(shell.page)
    width = drawer_width(state.layout.width)
    shown = state.drawer_open

    def opened_by_swipe(e: ft.DragEndEvent) -> None:
        if (e.velocity.x if e.velocity else 0) > FLING or e.local_position.x > EDGE * 4:
            shell.open_drawer()

    def closed_by_swipe(e: ft.DragEndEvent) -> None:
        if (e.velocity.x if e.velocity else 0) < -FLING:
            shell.close_drawer()

    edge = ft.GestureDetector(
        left=0, top=0, bottom=0, width=EDGE,
        on_horizontal_drag_end=opened_by_swipe,
        content=ft.Container(bgcolor=ft.Colors.TRANSPARENT),  # painted, so the strip takes the drag
    )
    if not shown:
        return ft.Stack(expand=True, controls=[edge]) if not shell.secondary_page() else ft.Container(width=0, height=0)
    scrim = ft.Container(expand=True, bgcolor=palette.scrim, opacity=SCRIM_OPACITY, on_click=lambda _e: shell.close_drawer())
    sheet = ft.GestureDetector(
        left=0, top=0, bottom=0, width=width,
        on_horizontal_drag_end=closed_by_swipe,
        content=ft.Container(
            expand=True,
            bgcolor=palette.container,
            border_radius=ft.BorderRadius.only(top_right=Radius.DIALOG, bottom_right=Radius.DIALOG),
            content=ft.SafeArea(expand=True, content=drawer_content(shell)),
        ),
    )
    return ft.Stack(expand=True, controls=[scrim, sheet])
