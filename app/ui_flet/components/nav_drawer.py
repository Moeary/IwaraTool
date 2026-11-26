"""Phones and narrow windows: the desktop sidebar as a modal drawer beside the bottom bar.

Laid out like the extended rail: "IwaraTool" and the content rating at the top,
the five primary destinations, and pinned to the foot the same entries as the
rail's foot (Tools and Settings; Rules and Settings on a phone, where Tools is
desktop-only) above the account entry.  It opens from the menu button in the
page header (``PageFrame``) or a swipe from the left edge, and closes on the
scrim, a swipe back to the left, Back, or any navigation.  It is drawn in the
shell's stack rather than as ``View.drawer``: the declarative root owns the
view, and this keeps the drawer an ordinary component following
``state.drawer_open``.
"""
from __future__ import annotations

import flet as ft

from ...i18n import tr
from .. import routes
from ..icons import NavIcon
from ..stores.app_state import use_shell
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space
from .cover import Cover
from .navigation import current_entry, nav_icon, rating_button

MAX_WIDTH = Size.NAV_RAIL_EXTENDED + Space.XXL  # about the desktop's extended rail, a little roomier for touch
EDGE = Space.LG  # width of the left-edge strip a swipe opens the drawer from
FLING = 300  # px/s: a slower drag counts by distance instead
SCRIM_OPACITY = 0.32


def drawer_width(window_width: float) -> float:
    return max(0.0, min(MAX_WIDTH, window_width - 56))


def _item(shell, palette, destination: str, *, selected: bool) -> ft.Control:
    """As the extended rail draws an entry: the indicator pill around the icon, the label beside it."""
    color = palette.on_surface if selected else palette.on_surface_variant
    pill = ft.Container(width=Size.NAV_INDICATOR_WIDTH, height=Size.NAV_INDICATOR_HEIGHT, border_radius=Radius.FULL,
                        bgcolor=palette.primary_container if selected else None, alignment=ft.Alignment.CENTER,
                        content=nav_icon(shell, destination, active=selected))
    return ft.Container(
        height=Size.NAV_DRAWER_ITEM,
        border_radius=Radius.FULL,
        padding=ft.Padding.symmetric(horizontal=Space.XS),
        on_click=lambda _e: shell.select_entry(destination),
        content=ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            pill,
            ft.Text(routes.label(destination), style=text_style(TYPE.label, color=color), expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, weight=ft.FontWeight.W_700 if selected else None),
        ]),
    )


def _account(shell, palette) -> ft.Control:
    """The account entry at the very foot, as on the desktop rail; it opens the account panel."""
    state = shell.state
    logged_in = state.logged_in
    profile = state.profile if logged_in else {}
    avatar: ft.Control = (
        Cover("avatar", profile["id"], profile.get("avatar_url", ""), Size.NAV_INDICATOR_HEIGHT, aspect=1, round=True,
              icon=NavIcon.ACCOUNT.active, key=f"drawer-avatar:{profile['id']}")
        if profile.get("id") else ft.Icon(NavIcon.ACCOUNT.pick(logged_in), size=Size.ICON_NAV, color=palette.primary)
    )
    name = profile.get("name") or (tr("Signed in", "已登录", "ログイン済み") if logged_in else tr("Not signed in", "未登录", "未ログイン"))

    def open_account(_e) -> None:
        shell.close_drawer()
        shell.open_account()

    return ft.Container(
        height=Size.NAV_DRAWER_ITEM, border_radius=Radius.FULL, padding=ft.Padding.symmetric(horizontal=Space.XS),
        on_click=open_account, tooltip=tr("Account and preferences", "账号与偏好", "アカウントと設定"),
        content=ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Container(width=Size.NAV_INDICATOR_WIDTH, alignment=ft.Alignment.CENTER, content=avatar),
            ft.Text(name, style=text_style(TYPE.label, color=palette.primary), expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
        ]),
    )


def drawer_content(shell) -> ft.Control:
    palette = palette_for(shell.page)
    entries = routes.entries(desktop=shell.state.layout.is_desktop)
    current = current_entry(shell)
    primary = [d for d in entries if d in routes.PRIMARY]
    foot = [d for d in entries if d not in routes.PRIMARY]
    header = ft.Container(
        padding=ft.Padding.only(left=Space.SM, top=Space.SM, bottom=Space.SM),
        content=ft.Row(spacing=Space.SM, controls=[ft.Text("IwaraTool", style=text_style(TYPE.subtitle)),
                                                   rating_button(shell)]),
    )
    return ft.Container(
        expand=True,
        padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.MD),
        content=ft.Column(expand=True, spacing=Space.XXS, controls=[
            header,
            ft.ListView(expand=True, spacing=Space.XXS, controls=[
                _item(shell, palette, d, selected=d == current) for d in primary]),
            *[_item(shell, palette, d, selected=d == current) for d in foot],
            _account(shell, palette),
        ]),
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
