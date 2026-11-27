"""An author: ``/author/<username>``.

The header card has the avatar, the name and the two status chips with their
buttons (``AuthorStatus``); below, "Videos / Images" tabs over a paged grid
that can be selected and queued.
"""
from __future__ import annotations

import asyncio

import flet as ft

from ....core import site as sites
from ....core.home.loading import call_with_client
from ....core.search.normalize import avatar_url
from ....presentation.author import page as author_page
from ....presentation.author.status import clean_username, profile_follow
from ... import layout, routes
from ...components.author_status import AuthorStatus
from ...components.cover import Cover
from ...components.feed_grid import FeedGrid
from ...components.page_frame import PageFrame
from ...icons import AppIcon
from ...stores.app_state import use_shell
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Space

SCOPE = "subscription"  # the old author page queued with the subscription page's rule
AVATAR = 64


@ft.component
def AuthorBody(username: str):
    shell = use_shell()
    palette = palette_for(shell.page)
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    profile, set_profile = ft.use_state(lambda: {"name": "", "avatar": "", "user_id": ""})
    tab_id, set_tab_id = ft.use_state("videos")

    async def load_profile() -> None:
        result, error = await asyncio.to_thread(call_with_client, shell.home.manager,
                                                lambda client: client.get_user_profile(username))
        user, _problem = profile_follow(result, error)
        if user is not None:
            set_profile({"name": str(user.get("name") or ""), "avatar": avatar_url(user), "user_id": str(user.get("id") or "")})

    ft.use_effect(load_profile, [username])
    name = profile["name"] or username
    section = author_page.author_section(username, name)
    header = ft.Container(
        padding=Space.LG if not compact else Space.MD,
        border_radius=Radius.CARD,
        bgcolor=palette.card,
        content=ft.Column(spacing=Space.MD, controls=[
            ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                Cover("avatar", username, profile["avatar"], AVATAR, aspect=1, round=True, icon=AppIcon.AUTHOR),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(name, style=text_style(TYPE.section_title), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"@{username}", style=text_style(TYPE.meta, color=palette.on_surface_variant)),
                ]),
                ft.IconButton(icon=AppIcon.BROWSER, tooltip=author_page.open_on_site_text(),
                              on_click=lambda _e: shell.page.run_task(ft.UrlLauncher().launch_url, sites.profile_url(username))),
            ]),
            AuthorStatus(username, name, profile["user_id"], profile["avatar"], key=f"status:{username}"),
        ]),
    )
    tabs = ft.Row(spacing=Space.SM, controls=[
        ft.Chip(label=tab.label, selected=tab.id == tab_id, show_checkmark=False,
                on_select=lambda _e, tab_id=tab.id: set_tab_id(tab_id))
        for tab in section.tabs
    ])
    return ft.Column(expand=True, spacing=Space.MD, controls=[
        header, tabs,
        ft.Container(expand=True, content=FeedGrid(section, tab_id, shell.state.rating, SCOPE, key=f"{username}:{tab_id}")),
    ])


@ft.component
def AuthorPage(destination: str, path: str):
    route = routes.parse(path)
    username = clean_username(route.parts[0] if route.parts else "")
    return PageFrame(title=routes.label(routes.AUTHOR), body=AuthorBody(username, key=username))
