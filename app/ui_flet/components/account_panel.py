"""Account and app-wide preferences in one panel (replaces the old avatar menu idea).

docs/flet_ui_plan.md 6.3 puts sign-in, site, content rating, theme and
"about" behind the avatar.  A popup menu cannot hold radio groups, so this is
a panel of segmented buttons: a dialog on wide windows, a bottom sheet on
phones.  Destinations the navigation has no room for are listed here too.
"""
from __future__ import annotations

import flet as ft

from ...core import site as sites
from ...core.rating import RATINGS, rating_label
from ...i18n import tr
from ...presentation import preferences
from ...version import __version__
from .. import layout, routes
from ..icons import AppIcon, NavIcon
from ..stores.app_state import use_shell
from ..theme import text_style
from ..tokens import TYPE, Size, Space
from .navigation import DESTINATION_ICONS, overflow_items


def _section(title: str, control: ft.Control) -> ft.Control:
    return ft.Column(
        spacing=Space.SM,
        controls=[ft.Text(title, style=text_style(TYPE.label)), control],
    )


def _segments(selected: str, options: list[tuple[str, str, ft.IconData | None]], on_select) -> ft.Control:
    def changed(event: ft.Event) -> None:
        values = list(event.control.selected or [])
        if values:
            on_select(values[0])

    return ft.SegmentedButton(
        selected=[selected],
        show_selected_icon=False,
        on_change=changed,
        segments=[ft.Segment(value=value, label=label, icon=icon) for value, label, icon in options],
    )


def panel_content(shell) -> ft.Control:
    state = shell.state
    logged_in = state.logged_in
    narrow = state.layout.width_class == layout.WIDTH_COMPACT  # icons would wrap the theme labels

    def theme_icon(icon):
        return None if narrow else icon

    account = ft.Row(
        spacing=Space.MD,
        controls=[
            ft.Icon(NavIcon.ACCOUNT.pick(logged_in), size=Size.AVATAR),
            ft.Column(
                spacing=0,
                expand=True,
                controls=[
                    ft.Text(
                        tr("Signed in", "已登录", "ログイン済み") if logged_in else tr("Not signed in", "未登录", "未ログイン"),
                        style=text_style(TYPE.subtitle),
                    ),
                    ft.Text(
                        tr("Iwara account", "Iwara 账号", "Iwara アカウント"),
                        style=text_style(TYPE.meta),
                    ),
                ],
            ),
            ft.TextButton(
                content=tr("Account settings", "账号设置", "アカウント設定") if logged_in else tr("Sign in", "登录", "ログイン"),
                icon=AppIcon.LOGIN if not logged_in else None,
                on_click=lambda _e: shell.open(routes.route(routes.SETTINGS, "account")),
            ),
        ],
    )
    controls: list[ft.Control] = [
        account,
        ft.Divider(height=1),
        _section(
            tr("Site", "站点", "サイト"),
            _segments(state.site, [(key, sites.SITES[key].label, None) for key in (sites.SITE_TV, sites.SITE_AI)], shell.set_site),
        ),
        _section(
            tr("Content filter", "内容分级", "コンテンツ区分"),
            _segments(state.rating, [(value, rating_label(value), None) for value in RATINGS], shell.set_rating),
        ),
        _section(
            tr("Theme", "主题", "テーマ"),
            _segments(
                state.theme_mode,
                [
                    ("auto", preferences.theme_mode_label("auto"), theme_icon(AppIcon.THEME_SYSTEM)),
                    ("light", preferences.theme_mode_label("light"), theme_icon(AppIcon.THEME_LIGHT)),
                    ("dark", preferences.theme_mode_label("dark"), theme_icon(AppIcon.THEME_DARK)),
                ],
                shell.set_theme_mode,
            ),
        ),
        _section(
            tr("Language", "语言", "言語"),
            _segments(state.language, [(code, preferences.language_label(code), None) for code in preferences.LANGUAGES], shell.set_language),
        ),
    ]
    extra = overflow_items(shell)
    if extra:
        controls.append(ft.Divider(height=1))
        controls.extend(
            ft.ListTile(
                leading=ft.Icon(DESTINATION_ICONS[d].rest),
                title=ft.Text(routes.label(d)),
                selected=d == state.destination,
                on_click=lambda _e, d=d: shell.select(d),
            )
            for d in extra
        )
    controls.append(ft.Divider(height=1))
    controls.append(
        ft.Row(
            spacing=Space.SM,
            controls=[
                ft.Icon(AppIcon.ABOUT, size=Size.ICON_SM),
                ft.Text(f"IwaraTool {__version__}", style=text_style(TYPE.meta)),
            ],
        )
    )
    return ft.Column(tight=True, spacing=Space.LG, scroll=ft.ScrollMode.AUTO, controls=controls)


@ft.component
def AccountPanelHost():
    """Shows the panel while ``state.account_open``; dismissing it closes it."""

    shell = use_shell()
    state = shell.state
    dialog = None
    if state.account_open:
        content = panel_content(shell)
        dismiss = lambda _e: shell.close_account()  # noqa: E731
        if state.layout.nav_mode == layout.NAV_BAR:
            dialog = ft.BottomSheet(
                show_drag_handle=True,
                scrollable=True,
                on_dismiss=dismiss,
                content=ft.Container(padding=ft.Padding.only(left=Space.XL, right=Space.XL, bottom=Space.XL), content=content),
            )
        else:
            dialog = ft.AlertDialog(
                title=ft.Text(tr("Account and preferences", "账号与偏好", "アカウントと設定")),
                content=ft.Container(width=Size.DIALOG_MAX_WIDTH - 2 * Space.XL, content=content),
                scrollable=True,
                on_dismiss=dismiss,
                actions=[ft.TextButton(content=tr("Close", "关闭", "閉じる"), on_click=dismiss)],
            )
    ft.use_dialog(dialog)
    return ft.Container(width=0, height=0)
