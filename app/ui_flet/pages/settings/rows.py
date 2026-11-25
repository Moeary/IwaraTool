"""Building blocks of the settings cards: a card, and "label + help + control" rows.

Every change is saved at once (plan 7.11).  Text and number fields save when
they lose focus or Enter is pressed, so typing does not write half values;
the card re-renders afterwards and shows what was actually stored (clamped,
normalized).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Space


def card(page: ft.Page, title: str, rows: list[ft.Control | None], *, description: str = "") -> ft.Control:
    palette = palette_for(page)
    head: list[ft.Control] = [ft.Text(title, style=text_style(TYPE.subtitle))]
    if description:
        head.append(ft.Text(description, style=text_style(TYPE.meta, color=palette.on_surface_variant)))
    return ft.Container(
        padding=Space.LG, border_radius=Radius.CARD, bgcolor=palette.card,
        content=ft.Column(spacing=Space.MD, controls=[*head, *[r for r in rows if r is not None]]),
    )


def hint(page: ft.Page, text: str, *, danger: bool = False) -> ft.Control:
    palette = palette_for(page)
    color = palette.danger.fg if danger else palette.on_surface_variant
    return ft.Text(text, style=text_style(TYPE.meta, color=color))


def row(page: ft.Page, label: str, control: ft.Control, *, help: str = "", compact: bool = False) -> ft.Control:
    """Label (and help) on the left, the control on the right; stacked on phones."""
    texts: list[ft.Control] = [ft.Text(label, style=text_style(TYPE.body))]
    if help:
        texts.append(hint(page, help))
    left = ft.Column(spacing=Space.XXS, expand=not compact, controls=texts)
    if compact:
        return ft.Column(spacing=Space.SM, controls=[left, control])
    return ft.Row(spacing=Space.LG, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[left, control])


def switch_row(page: ft.Page, label: str, value: bool, on_change: Callable[[bool], None], *, help: str = "",
               disabled: bool = False) -> ft.Control:
    texts: list[ft.Control] = [ft.Text(label, style=text_style(TYPE.body))]
    if help:
        texts.append(hint(page, help))
    return ft.Row(spacing=Space.LG, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.Column(spacing=Space.XXS, expand=True, controls=texts),
        ft.Switch(value=bool(value), disabled=disabled, on_change=lambda e: on_change(bool(e.control.value))),
    ])


def dropdown(value: str, options: list[tuple[str, str]], on_select: Callable[[str], None], *, width: float = 240,
             disabled: bool = False) -> ft.Control:
    return ft.Dropdown(
        value=value, dense=True, width=width, disabled=disabled,
        options=[ft.DropdownOption(key=key, text=text) for key, text in options],
        on_select=lambda e: on_select(str(e.control.value or "")),
    )


def text_field(value: Any, on_commit: Callable[[str], None], *, width: float | None = None, hint_text: str = "",
               password: bool = False, label: str = "", disabled: bool = False, error: str = "",
               suffix: str = "", key: str | None = None) -> ft.Control:
    """Saves on blur and on Enter; an unchanged value is not written again."""
    original = str(value if value is not None else "")

    def commit(e) -> None:
        text = str(e.control.value or "")
        if text != original:
            on_commit(text)

    return ft.TextField(
        key=key, value=original, dense=True, width=width, expand=width is None, label=label or None,
        hint_text=hint_text or None, password=password, can_reveal_password=password, disabled=disabled,
        error=error or None, suffix=suffix or None, on_blur=commit, on_submit=commit,
    )


def number_field(value: int, on_commit: Callable[[str], None], *, width: float = 120, suffix: str = "",
                 disabled: bool = False, key: str | None = None) -> ft.Control:
    return text_field(str(value), on_commit, width=width, suffix=suffix, disabled=disabled, key=key)


def buttons(*controls: ft.Control | None) -> ft.Control:
    return ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=[c for c in controls if c is not None])


def use_refresh() -> tuple[int, Callable[..., None]]:
    """``(tick, refresh)``: ``refresh()`` re-renders the calling card after it saved something.

    Key the card's fields with ``tick`` so they show the stored value again
    even when it equals the previous one (junk typed into a number field).
    """
    tick, set_tick = ft.use_state(0)

    def refresh(*_args) -> None:
        set_tick(lambda t: t + 1)

    return tick, refresh
