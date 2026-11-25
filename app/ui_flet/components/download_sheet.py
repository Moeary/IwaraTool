"""The panel shown before anything is queued (docs/flet_ui_plan.md 7.0 ``DownloadSheet``).

Replaces the rule selector every PySide6 page kept on screen: what will be
downloaded, which rule (the page's rule preselected), and whether to keep
that choice as the page's rule.  A dialog on wide windows, a bottom sheet on
phones.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import flet as ft

from ...i18n import tr
from ...presentation.rules import page_scope_name
from .. import layout
from ..theme import text_style
from ..tokens import TYPE, Size, Space


def open_download_sheet(
    shell,
    *,
    scope: str,
    title: str,
    summary: str = "",
    confirm_label: str = "",
    on_confirm: Callable[[str], Awaitable[None]],
) -> None:
    """Ask for the rule, then ``await on_confirm(rule_id)``."""

    page = shell.page
    service = shell.downloads
    choices = service.rule_choices()
    current = service.page_choice(scope)
    if current not in {value for value, _label in choices}:
        current = choices[0][0] if choices else ""
    dropdown = ft.Dropdown(
        label=tr("Download rule", "下载规则", "ダウンロードルール"),
        value=current,
        expand=True,
        enable_filter=len(choices) > 8,
        options=[ft.DropdownOption(key=value, text=label) for value, label in choices],
    )
    remember = ft.Checkbox(
        label=tr(
            f"Use this rule for {page_scope_name(scope)} from now on",
            f"以后「{page_scope_name(scope)}」都用这个规则",
            f"今後「{page_scope_name(scope)}」でこのルールを使う",
        ),
        value=False,
    )
    body: list[ft.Control] = []
    if summary:
        body.append(ft.Text(summary, style=text_style(TYPE.body)))
    body += [ft.Row([dropdown]), remember]
    content = ft.Column(tight=True, spacing=Space.LG, controls=body)
    compact = shell.state.layout.nav_mode == layout.NAV_BAR
    dialog: ft.DialogControl

    def close() -> None:
        page.pop_dialog()

    async def confirm(_event) -> None:
        choice = str(dropdown.value or current)
        if remember.value:
            service.remember_page_rule(scope, choice)
        close()
        await on_confirm(service.resolve_rule(choice))

    confirm_button = ft.FilledButton(content=confirm_label or tr("Download", "下载", "ダウンロード"), on_click=confirm)
    cancel_button = ft.TextButton(content=tr("Cancel", "取消", "キャンセル"), on_click=lambda _e: close())
    if compact:
        dialog = ft.BottomSheet(
            show_drag_handle=True,
            scrollable=True,
            content=ft.Container(
                padding=ft.Padding.only(left=Space.XL, right=Space.XL, bottom=Space.XL),
                content=ft.Column(
                    tight=True,
                    spacing=Space.LG,
                    controls=[
                        ft.Text(title, style=text_style(TYPE.section_title_compact)),
                        content,
                        ft.Row(alignment=ft.MainAxisAlignment.END, controls=[cancel_button, confirm_button]),
                    ],
                ),
            ),
        )
    else:
        dialog = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Container(width=Size.DIALOG_MAX_WIDTH - 2 * Space.XL, content=content),
            actions=[cancel_button, confirm_button],
        )
    page.show_dialog(dialog)
