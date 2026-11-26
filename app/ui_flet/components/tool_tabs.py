"""Tabs across the Tools group on the desktop: rules, repair, upload tools (``routes.TOOL_GROUP``).

The rail has one Tools entry; these tabs switch between its pages.  Each page
keeps its own route, back stack and state.  Phones have no group (repair and
the upload tools are desktop only), so rules there keeps its own title.
"""
from __future__ import annotations

import flet as ft

from .. import routes
from ..stores.app_state import use_shell
from ..tokens import Space


@ft.component
def ToolTabs(current: str):
    shell = use_shell()
    return ft.Row(
        spacing=Space.SM,
        controls=[
            ft.Chip(
                label=routes.tab_label(d),
                selected=d == current,
                show_checkmark=False,
                on_select=lambda _e, d=d: shell.select(d),
            )
            for d in routes.TOOL_GROUP
            if routes.allowed(routes.route(d), desktop=shell.caps.desktop)
        ],
    )


def tool_frame(shell, destination: str, own_title: str) -> dict:
    """``PageFrame`` arguments for a Tools page at its top level: the group title and the tabs."""
    if not shell.caps.desktop:
        return {"title": own_title}
    return {"title": routes.label(routes.TOOLS), "below": ToolTabs(destination)}
