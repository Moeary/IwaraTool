"""Rail and bottom bar of the shell (docs/flet_ui_plan.md 6.3).

Plain builders called while the shell renders; they read the ``Shell`` and
call back into it.  Destinations missing from a bar (secondary ones on
phones, everything but the primary five in the collapsed rail) are reached
from the account panel.
"""
from __future__ import annotations

import flet as ft

from ...i18n import tr
from ...presentation.downloads.task_statuses import badge_text
from .. import layout, routes
from ..icons import IconPair, NavIcon, StateIcon
from ..theme import text_style
from ..tokens import TYPE, Size, Space

DESTINATION_ICONS: dict[str, IconPair] = {
    routes.HOME: NavIcon.HOME,
    routes.SUBSCRIPTIONS: NavIcon.SUBSCRIPTIONS,
    routes.SEARCH: NavIcon.SEARCH,
    routes.DOWNLOADS: NavIcon.DOWNLOADS,
    routes.LIBRARY: NavIcon.LIBRARY,
    routes.RULES: NavIcon.RULES,
    routes.REPAIR: NavIcon.REPAIR,
    routes.TOOLS: NavIcon.TOOLS,
    routes.SETTINGS: NavIcon.SETTINGS,
}


def nav_items(shell) -> tuple[str, ...]:
    """Destinations the bar or rail shows for the current layout."""

    state = shell.state.layout
    if state.nav_mode in (layout.NAV_BAR, layout.NAV_RAIL_COLLAPSED):
        return routes.PRIMARY
    everything = routes.available(routes.DESTINATIONS, desktop=state.is_desktop)
    # Too short for the secondary group (841×673 with labels): it moves to the account panel.
    return everything if state.rail_fits(len(everything)) else routes.PRIMARY


def overflow_items(shell) -> tuple[str, ...]:
    """Destinations only the account panel offers."""

    shown = set(nav_items(shell))
    return tuple(d for d in shell.visible_destinations() if d not in shown)


def _icon(shell, destination: str, *, active: bool) -> ft.Control:
    pair = DESTINATION_ICONS[destination]
    badge = None
    if destination == routes.DOWNLOADS:
        if shell.state.active_downloads:
            badge = ft.Badge(label=badge_text(shell.state.active_downloads))
        elif shell.state.staged_downloads:
            badge = ft.Badge(small_size=Space.SM)
    return ft.Icon(pair.pick(active), size=Size.ICON_NAV, badge=badge)


def _select_handler(shell, items: tuple[str, ...]):
    def on_change(event: ft.Event) -> None:
        index = event.control.selected_index
        if index is not None and 0 <= index < len(items):
            shell.select(items[index])

    return on_change


def build_bar(shell) -> ft.NavigationBar:
    items = nav_items(shell)
    current = shell.state.destination
    return ft.NavigationBar(
        selected_index=items.index(current) if current in items else None,
        label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
        on_change=_select_handler(shell, items),
        destinations=[
            ft.NavigationBarDestination(
                icon=_icon(shell, d, active=False),
                selected_icon=_icon(shell, d, active=True),
                label=routes.short_label(d),
            )
            for d in items
        ],
    )


def account_button(shell, *, size: float = Size.AVATAR) -> ft.Control:
    logged_in = shell.state.logged_in
    return ft.IconButton(
        icon=NavIcon.ACCOUNT.pick(logged_in),
        icon_size=Size.ICON_LG,
        width=size,
        height=size,
        tooltip=tr("Account and preferences", "账号与偏好", "アカウントと設定"),
        on_click=lambda _e: shell.open_account(),
    )


def _account_row(shell) -> ft.Control:
    """The extended rail's account entry: icon plus the sign-in state, full width."""

    logged_in = shell.state.logged_in
    return ft.Container(
        width=Size.NAV_RAIL_EXTENDED - 2 * Space.MD,
        content=ft.TextButton(
            icon=NavIcon.ACCOUNT.pick(logged_in),
            content=tr("Signed in", "已登录", "ログイン済み") if logged_in else tr("Not signed in", "未登录", "未ログイン"),
            tooltip=tr("Account and preferences", "账号与偏好", "アカウントと設定"),
            style=ft.ButtonStyle(alignment=ft.Alignment.CENTER_LEFT, padding=ft.Padding.symmetric(horizontal=Space.LG, vertical=Space.MD)),
            on_click=lambda _e: shell.open_account(),
        ),
    )


def build_rail(shell) -> ft.NavigationRail:
    state = shell.state.layout
    mode = state.nav_mode
    items = nav_items(shell)
    current = shell.state.destination
    extended = mode == layout.NAV_RAIL_EXTENDED
    leading = None
    if extended:
        leading = ft.Container(
            width=Size.NAV_RAIL_EXTENDED - 2 * Space.MD,
            padding=ft.Padding.only(left=Space.SM, top=Space.SM, bottom=Space.SM),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text("IwaraTool", style=text_style(TYPE.subtitle)),
                    ft.IconButton(
                        icon=StateIcon.RAIL.active,
                        tooltip=tr("Collapse the sidebar", "收起侧栏", "サイドバーを折りたたむ"),
                        on_click=lambda _e: shell.toggle_rail(),
                    ),
                ],
            ),
        )
    elif state.width_class in (layout.WIDTH_LARGE, layout.WIDTH_XLARGE):
        leading = ft.IconButton(
            icon=StateIcon.RAIL.rest,
            tooltip=tr("Expand the sidebar", "展开侧栏", "サイドバーを展開"),
            on_click=lambda _e: shell.toggle_rail(),
        )
    first_secondary = next((d for d in items if d in routes.SECONDARY), None)
    destinations = []
    for d in items:
        destinations.append(
            ft.NavigationRailDestination(
                icon=_icon(shell, d, active=False),
                selected_icon=_icon(shell, d, active=True),
                label=routes.label(d),
                # A gap separates the secondary group (rules, repair, tools, settings).
                padding=ft.Padding.only(top=Space.XL) if d == first_secondary else None,
            )
        )
    return ft.NavigationRail(
        selected_index=items.index(current) if current in items else None,
        extended=extended,
        label_type=None if extended else (
            ft.NavigationRailLabelType.NONE if mode == layout.NAV_RAIL_COLLAPSED else ft.NavigationRailLabelType.ALL
        ),
        min_width=Size.NAV_RAIL,
        min_extended_width=Size.NAV_RAIL_EXTENDED,
        group_alignment=-1.0,
        scrollable=True,
        leading=leading,
        trailing=ft.Container(
            padding=ft.Padding.only(bottom=Space.MD),
            content=_account_row(shell) if extended else account_button(shell),
        ),
        pin_trailing_to_bottom=True,
        on_change=_select_handler(shell, items),
        destinations=destinations,
    )
