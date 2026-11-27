"""The Tools hub on the desktop: every tool listed on the left, the chosen one on the right.

Rules (``/rules``), repair (``/repair``) and the upload tools (``/tools/<key>``)
keep their own routes, back stacks and state; this frame only puts the tool
list beside them on windows wide enough for two panes.  Narrow windows open
the list at ``/tools`` and each tool as its own page; phones have rules only.
"""
from __future__ import annotations

import flet as ft

from ...presentation.tools import hub
from .. import layout
from ..icons import AppIcon, NavIcon
from ..stores.app_state import use_shell
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space
from .list_detail import ListDetail
from .page_frame import PageFrame

HUB_NAV_WIDTH = 248
HUB_WIDTH_KEY = "flet_tools_list_width"
ICONS = {
    "rules": NavIcon.RULES.rest,
    "repair": NavIcon.REPAIR.rest,
    "compress": AppIcon.VIDEO,
    "covers": AppIcon.IMAGE,
}


@ft.component
def ToolHubList(current: str, *, detailed: bool = False):
    """The tools, grouped; ``detailed`` adds each summary (the narrow window's /tools page)."""
    shell = use_shell()
    palette = palette_for(shell.page)
    titles = hub.group_titles()
    controls: list[ft.Control] = []
    group = ""
    for entry in hub.entries(desktop=shell.caps.desktop):
        if entry.group != group:
            group = entry.group
            controls.append(ft.Container(
                padding=ft.Padding.only(left=Space.MD, top=Space.MD if controls else 0, bottom=Space.XXS),
                content=ft.Text(titles[group], style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            ))
        chosen = entry.key == current
        controls.append(ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.SM + 2),
            border_radius=Radius.CONTROL,
            bgcolor=palette.primary_container if chosen else None,
            on_click=lambda _e, k=entry.key: shell.open_tool(k),
            tooltip=None if detailed else entry.summary,
            content=ft.Row(spacing=Space.MD, controls=[
                ft.Icon(ICONS.get(entry.key, AppIcon.MORE), size=Size.ICON,
                        color=palette.on_primary_container if chosen else None),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(entry.title, style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    *([ft.Text(entry.summary, style=text_style(TYPE.meta, color=palette.on_surface_variant))]
                      if detailed else []),
                ]),
            ]),
        ))
    return ft.ListView(expand=True, spacing=Space.XXS, controls=controls)


def hub_frame(shell, current: str, body, *, title: str, actions: list[ft.Control] | None = None,
              max_width: float = layout.MAX_WIDTH_LIST_DETAIL) -> ft.Control:
    """A tool page: beside the hub list on wide desktop windows, alone (with its own title) otherwise.

    ``body`` is a control, or ``body(width)`` building it for the width it gets.
    """
    view = shell.state.layout
    build = body if callable(body) else (lambda _width: body)
    if not (shell.caps.desktop and view.two_pane):
        return PageFrame(title=title, body=build(view.body_width(max_width)), actions=actions, max_width=max_width)

    def detail(width: float) -> ft.Control:
        return ft.Column(expand=True, spacing=Space.SM, controls=[
            ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Text(title, style=text_style(TYPE.section_title_compact), expand=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                *(actions or []),
            ]),
            ft.Container(expand=True, content=build(width)),
        ])

    row = ListDetail(ToolHubList(current), detail, total_width=view.body_width(layout.MAX_WIDTH_GRID),
                     store_key=HUB_WIDTH_KEY, default=HUB_NAV_WIDTH, key="tools-hub")
    return PageFrame(title=hub.hub_title(), body=row, max_width=layout.MAX_WIDTH_GRID)
