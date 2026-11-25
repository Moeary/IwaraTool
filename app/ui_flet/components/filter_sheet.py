"""``FilterSheet`` (docs/flet_ui_plan.md 7.0): the post-search filter form.

A bottom sheet on phones, a dialog elsewhere.  Fields start from the current
filter; Apply validates the ranges and hands back a new ``PostFilter``.
"""
from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ...core.search.post_filter import PostFilter
from ...presentation.search import post_filter_text as text
from .. import layout
from ..theme import palette_for, text_style
from ..tokens import TYPE, Size, Space


def open_filter_sheet(shell, current: PostFilter, on_apply: Callable[[PostFilter], None]) -> None:
    page = shell.page
    palette = palette_for(page)
    compact = shell.state.layout.nav_mode == layout.NAV_BAR

    def number(value, hint: str, *, decimal: bool = False) -> ft.TextField:
        return ft.TextField(
            value=text.number_text(value),
            hint_text=hint,
            dense=True,
            expand=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(regex_string=r"^\d{0,6}([.,]\d{0,2})?$" if decimal else r"^\d{0,10}$",
                                        allow=True, replacement_string=""),
        )

    min_likes, max_likes = number(current.min_likes, text.label("min")), number(current.max_likes, text.label("max"))
    min_views, max_views = number(current.min_views, text.label("min")), number(current.max_views, text.label("max"))
    min_minutes = number(current.min_duration_minutes, text.label("min"), decimal=True)
    max_minutes = number(current.max_duration_minutes, text.label("max"), decimal=True)
    ratio = number(current.min_like_ratio_percent, text.label("ratio_hint"), decimal=True)
    ratio.tooltip = text.label("ratio_tooltip")
    exclude = ft.TextField(value=", ".join(current.exclude_tags), hint_text=text.label("exclude_hint"), dense=True, expand=True)
    within = ft.Dropdown(
        value=str(current.published_within_days or ""),
        dense=True,
        expand=True,
        options=[ft.DropdownOption(key=str(days or ""), text=name) for name, days in text.within_choices()],
    )
    sort = ft.Dropdown(
        value=current.sort,
        dense=True,
        expand=True,
        options=[ft.DropdownOption(key=key, text=name) for name, key in text.sort_choices()],
    )
    orientation = ft.SegmentedButton(
        selected=[current.orientation or "any"],
        show_selected_icon=False,
        segments=[ft.Segment(value=key, label=text.orientation_label(key)) for key in text.ORIENTATIONS],
    )
    error = ft.Text("", style=text_style(TYPE.meta, color=palette.danger.fg), visible=False)

    def read() -> PostFilter:
        return text.build_filter(
            min_likes=min_likes.value or "",
            max_likes=max_likes.value or "",
            min_views=min_views.value or "",
            max_views=max_views.value or "",
            min_minutes=min_minutes.value or "",
            max_minutes=max_minutes.value or "",
            within_days=int(within.value) if (within.value or "").isdigit() else None,
            min_ratio=ratio.value or "",
            exclude_tags=exclude.value or "",
            sort=sort.value or "server",
            orientation=(list(orientation.selected or ["any"]) or ["any"])[0],
        )

    def apply(_e) -> None:
        value = read()
        message = text.validation_message(value.validation_error())
        if message:
            error.value, error.visible = message, True
            error.update()
            return
        page.pop_dialog()
        on_apply(value)

    def clear(_e) -> None:
        page.pop_dialog()
        on_apply(PostFilter())

    def row(name: str, *controls: ft.Control) -> ft.Control:
        return ft.Column(spacing=Space.XS, controls=[
            ft.Text(name, style=text_style(TYPE.label)),
            ft.Row(spacing=Space.SM, controls=list(controls)),
        ])

    form = ft.Column(
        spacing=Space.MD,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
        controls=[
            ft.Text(text.hint(), style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            row(text.label("likes"), min_likes, max_likes),
            row(text.label("views"), min_views, max_views),
            row(text.label("duration"), min_minutes, max_minutes),
            row(text.label("published"), within),
            row(text.label("ratio"), ratio),
            row(text.label("exclude"), exclude),
            row(text.label("sort"), sort),
            ft.Column(spacing=Space.XS, controls=[
                ft.Text(text.label("orientation"), style=text_style(TYPE.label)),
                ft.Row([orientation], scroll=ft.ScrollMode.AUTO),
                ft.Text(text.orientation_hint(), style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            ]),
            error,
        ],
    )
    buttons = [
        ft.TextButton(content=text.label("clear"), on_click=clear),
        ft.TextButton(content=text.label("cancel"), on_click=lambda _e: page.pop_dialog()),
        ft.FilledButton(content=text.label("apply"), on_click=apply),
    ]
    if compact:
        page.show_dialog(ft.BottomSheet(
            show_drag_handle=True,
            scrollable=True,
            content=ft.Container(
                padding=ft.Padding.only(left=Space.LG, right=Space.LG, bottom=Space.LG),
                content=ft.Column(tight=True, spacing=Space.MD, controls=[
                    ft.Text(text.title(), style=text_style(TYPE.section_title_compact)),
                    form,
                    ft.Row(alignment=ft.MainAxisAlignment.END, controls=buttons),
                ]),
            ),
        ))
    else:
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(text.title()),
            content=ft.Container(width=Size.DIALOG_MAX_WIDTH - 2 * Space.XL, content=form),
            actions=buttons,
        ))
