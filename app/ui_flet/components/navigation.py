"""Rail and bottom bar of the shell (docs/flet_ui_plan.md 6.3).

Plain builders called while the shell renders; they read the ``Shell`` and
call back into it.  Destinations missing from a bar (secondary ones on
phones, everything but the primary five in the collapsed rail) are reached
from the account panel.
"""
from __future__ import annotations

import flet as ft

from ...core.rating import RATINGS, rating_label
from ...i18n import tr
from ...presentation.downloads.task_statuses import badge_text
from ...presentation.settings import groups
from .. import layout, routes
from ..icons import IconPair, NavIcon, SettingsIcon, StateIcon
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space

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

SETTINGS_GROUP_ICONS: dict[str, IconPair] = {
    "appearance": SettingsIcon.APPEARANCE,
    "browsing": SettingsIcon.BROWSING,
    "account": NavIcon.ACCOUNT,
    "downloads": NavIcon.DOWNLOADS,
    "search": NavIcon.SEARCH,
    "subscriptions": NavIcon.SUBSCRIPTIONS,
    "storage": SettingsIcon.STORAGE,
    "system": SettingsIcon.SYSTEM,
    "shortcuts": SettingsIcon.KEYBOARD,
    "about": SettingsIcon.ABOUT,
}


def nav_items(shell) -> tuple[str, ...]:
    """Destinations the bar or the rail's upper group shows."""

    return routes.PRIMARY


def foot_items(shell) -> tuple[str, ...]:
    """Entries pinned to the rail's foot (Tools, Settings) when the window is tall enough."""

    state = shell.state.layout
    if state.nav_mode in (layout.NAV_BAR, layout.NAV_RAIL_COLLAPSED) or not state.is_desktop:
        return ()
    entries = routes.entries(desktop=True)
    # Too short for them (841×673 with labels): they move to the account panel.
    return routes.FOOT if state.rail_fits(len(entries)) else ()


def overflow_items(shell) -> tuple[str, ...]:
    """Entries only the account panel offers."""

    shown = set(nav_items(shell)) | set(foot_items(shell))
    entries = routes.entries(desktop=shell.state.layout.is_desktop)
    return tuple(d for d in entries if d not in shown)


def current_entry(shell) -> str:
    return routes.entry_for(shell.state.destination, desktop=shell.state.layout.is_desktop)


def nav_icon(shell, destination: str, *, active: bool) -> ft.Control:
    """A destination's icon with the downloads badge (rail, bar and the phone drawer)."""
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
            shell.select_entry(items[index])

    return on_change


def bar_items(shell) -> tuple[str, ...]:
    """The bar's destinations: the five primary ones (overflow ones open as secondary pages, Shell.secondary_page)."""

    return nav_items(shell)


def build_bar(shell) -> ft.NavigationBar:
    items = bar_items(shell)
    current = current_entry(shell)
    return ft.NavigationBar(
        selected_index=items.index(current) if current in items else 0,
        label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
        on_change=_select_handler(shell, items),
        destinations=[
            ft.NavigationBarDestination(
                icon=nav_icon(shell, d, active=False),
                selected_icon=nav_icon(shell, d, active=True),
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
        width=shell.state.layout.nav_width - 2 * Space.MD,
        content=ft.TextButton(
            icon=NavIcon.ACCOUNT.pick(logged_in),
            content=(shell.state.profile.get("name") or tr("Signed in", "已登录", "ログイン済み")) if logged_in
            else tr("Not signed in", "未登录", "未ログイン"),
            tooltip=tr("Account and preferences", "账号与偏好", "アカウントと設定"),
            style=ft.ButtonStyle(alignment=ft.Alignment.CENTER_LEFT, padding=ft.Padding.symmetric(horizontal=Space.LG, vertical=Space.MD)),
            on_click=lambda _e: shell.open_account(),
        ),
    )


def _foot_item(shell, destination: str, mode: str, *, selected: bool) -> ft.Control:
    """A rail-looking entry for the foot: the indicator pill, icon and label as the rail draws them."""

    palette = palette_for(shell.page)
    icon = nav_icon(shell, destination, active=selected)
    color = palette.on_surface if selected else palette.on_surface_variant
    label = ft.Text(routes.label(destination), style=text_style(TYPE.label, color=color),
                    weight=ft.FontWeight.W_700 if selected else None)
    indicator = palette.primary_container if selected else None
    select = lambda _e: shell.select_entry(destination)  # noqa: E731
    pill = ft.Container(width=Size.NAV_INDICATOR_WIDTH, height=Size.NAV_INDICATOR_HEIGHT, border_radius=Radius.FULL,
                        bgcolor=indicator, alignment=ft.Alignment.CENTER, content=icon)
    if mode == layout.NAV_RAIL_EXTENDED:
        # As the extended rail draws its own items: the pill around the icon only, the label beside it.
        return ft.Container(
            width=shell.state.layout.nav_width - 2 * Space.MD,
            padding=ft.Padding.symmetric(vertical=Space.SM),
            on_click=select,
            content=ft.Row(spacing=Space.XS, controls=[pill, label]),
        )
    return ft.Container(
        width=Size.NAV_RAIL,
        on_click=select,
        padding=ft.Padding.symmetric(vertical=Space.XS),
        content=ft.Column(tight=True, spacing=Space.XS, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            pill,
            label,
        ]),
    )


def rating_button(shell) -> ft.Control:
    """The content rating at the top of the rail (All / SFW / NSFW); a menu switches it."""

    palette = palette_for(shell.page)
    rating = shell.state.rating
    filtered = rating != RATINGS[0]
    color = palette.on_primary_container if filtered else palette.on_surface_variant
    return ft.PopupMenuButton(
        tooltip=groups.about_texts()["rating_title"],
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XXS),
            border_radius=Radius.FULL,
            bgcolor=palette.primary_container if filtered else None,
            border=None if filtered else ft.Border.all(1, palette.outline_variant),
            content=ft.Row(tight=True, spacing=Space.XXS, controls=[
                ft.Icon(SettingsIcon.RATING.pick(filtered), size=Size.ICON_SM, color=color),
                ft.Text(rating_label(rating), style=text_style(TYPE.badge, color=color)),
            ]),
        ),
        items=[ft.PopupMenuItem(content=rating_label(value), checked=value == rating,
                                on_click=lambda _e, v=value: shell.set_rating(v)) for value in RATINGS],
    )


def build_rail(shell) -> ft.NavigationRail:
    state = shell.state.layout
    mode = state.nav_mode
    items = nav_items(shell)
    current = current_entry(shell)
    extended = mode == layout.NAV_RAIL_EXTENDED
    leading = None
    if extended:
        leading = ft.Container(
            width=shell.state.layout.nav_width - 2 * Space.MD,
            padding=ft.Padding.only(left=Space.SM, top=Space.SM, bottom=Space.SM),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Row(tight=True, spacing=Space.SM, controls=[
                        ft.Text("IwaraTool", style=text_style(TYPE.subtitle)),
                        rating_button(shell),
                    ]),
                    ft.IconButton(
                        icon=StateIcon.RAIL.active,
                        tooltip=tr("Collapse the sidebar", "收起侧栏", "サイドバーを折りたたむ"),
                        on_click=lambda _e: shell.toggle_rail(),
                    ),
                ],
            ),
        )
    else:
        expand = ft.IconButton(
            icon=StateIcon.RAIL.rest,
            tooltip=tr("Expand the sidebar", "展开侧栏", "サイドバーを展開"),
            on_click=lambda _e: shell.toggle_rail(),
        ) if state.width_class in (layout.WIDTH_LARGE, layout.WIDTH_XLARGE) else None
        leading = ft.Column(tight=True, spacing=Space.XS, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[c for c in (expand, rating_button(shell)) if c is not None])
    destinations = [
        ft.NavigationRailDestination(
            icon=nav_icon(shell, d, active=False),
            selected_icon=nav_icon(shell, d, active=True),
            label=routes.label(d),
        )
        for d in items
    ]
    # Flutter's rail cannot split its destinations, so the foot (Tools, Settings) is drawn
    # in the pinned trailing area, above the account entry, looking like rail items.
    foot = [_foot_item(shell, d, mode, selected=d == current) for d in foot_items(shell)]
    return ft.NavigationRail(
        selected_index=items.index(current) if current in items else None,
        extended=extended,
        label_type=None if extended else (
            ft.NavigationRailLabelType.NONE if mode == layout.NAV_RAIL_COLLAPSED else ft.NavigationRailLabelType.ALL
        ),
        min_width=Size.NAV_RAIL,
        min_extended_width=shell.state.layout.nav_width if extended else Size.NAV_RAIL_EXTENDED,
        group_alignment=-1.0,
        scrollable=True,
        leading=leading,
        trailing=ft.Container(
            padding=ft.Padding.only(bottom=Space.MD),
            content=ft.Column(
                tight=True,
                spacing=Space.XS,
                horizontal_alignment=ft.CrossAxisAlignment.START if extended else ft.CrossAxisAlignment.CENTER,
                controls=[*foot, _account_row(shell) if extended else account_button(shell)],
            ),
        ),
        pin_trailing_to_bottom=True,
        on_change=_select_handler(shell, items),
        destinations=destinations,
    )
