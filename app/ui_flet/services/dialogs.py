"""Awaitable confirmation and text-input dialogs."""
from __future__ import annotations

import asyncio

import flet as ft

from ...i18n import tr
from ..theme import palette_for, text_style
from ..tokens import TYPE, Size, Space


def _cancel_label() -> str:
    return tr("Cancel", "取消", "キャンセル")


async def confirm(page: ft.Page, title: str, message: str, *, confirm_label: str, informative: str = "",
                  danger: bool = False, cancel_label: str = "") -> bool:
    """``True`` when the user confirms; closing the dialog any other way is ``False``."""

    loop = asyncio.get_running_loop()
    answer: asyncio.Future[bool] = loop.create_future()

    def done(value: bool) -> None:
        if not answer.done():
            answer.set_result(value)
        page.pop_dialog()

    palette = palette_for(page)
    body: list[ft.Control] = [ft.Text(message, style=text_style(TYPE.body))]
    if informative:
        body.append(ft.Text(informative, style=text_style(TYPE.meta, color=palette.on_surface_variant)))
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, style=text_style(TYPE.section_title_compact)),
        content=ft.Container(width=Size.DIALOG_MAX_WIDTH, content=ft.Column(tight=True, spacing=Space.SM, controls=body)),
        actions=[
            ft.TextButton(content=cancel_label or _cancel_label(), on_click=lambda _e: done(False)),
            ft.FilledButton(
                content=confirm_label,
                style=ft.ButtonStyle(bgcolor=palette.danger.fg, color=palette.on_primary) if danger else None,
                on_click=lambda _e: done(True),
            ),
        ],
        on_dismiss=lambda _e: answer.done() or answer.set_result(False),
    )
    page.show_dialog(dialog)
    return await answer


async def prompt_text(page: ft.Page, title: str, label: str, *, confirm_label: str, value: str = "") -> str | None:
    """The entered text, or ``None`` when cancelled."""

    loop = asyncio.get_running_loop()
    answer: asyncio.Future[str | None] = loop.create_future()
    field = ft.TextField(label=label, value=value, autofocus=True)

    def done(result: str | None) -> None:
        if not answer.done():
            answer.set_result(result)
        page.pop_dialog()

    field.on_submit = lambda _e: done(field.value or "")
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, style=text_style(TYPE.section_title_compact)),
        content=ft.Container(width=Size.DIALOG_MAX_WIDTH, content=field),
        actions=[
            ft.TextButton(content=_cancel_label(), on_click=lambda _e: done(None)),
            ft.FilledButton(content=confirm_label, on_click=lambda _e: done(field.value or "")),
        ],
        on_dismiss=lambda _e: answer.done() or answer.set_result(None),
    )
    page.show_dialog(dialog)
    return await answer
