"""``VideoActions`` (docs/flet_ui_plan.md 7.0): the unified action list as a menu or an action sheet.

Entries come from ``presentation.media.video_actions`` (same order as the old
right-click menus).  Where the downloaded copy is takes a history lookup, so
the list is built when the menu opens, not for every card on screen.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import flet as ft

from ...core.download.history_actions import remote_unreachable_message
from ...core.download.locations import FileLocation
from ...core.playback import video_player
from ...core.rules import page_rule_id
from ...core.search.models import SearchVideo
from ...presentation.media import detail_info
from ...presentation.media import video_actions as va
from ...presentation.media.cards import KIND_IMAGE, CardView
from .. import routes
from ..icons import AppIcon, StateIcon
from ..services.notify import notice
from ..theme import text_style
from ..tokens import TYPE, Space

_ICONS = {
    va.DETAIL: AppIcon.OPEN_IN_NEW,
    va.PLAY: AppIcon.PLAY,
    va.OPEN_FOLDER: AppIcon.FOLDER_OPEN,
    va.COPY_PATH: AppIcon.COPY,
    va.NAS_FOLDER: AppIcon.FOLDER_OPEN,
    va.COPY_NAS_PATH: AppIcon.COPY,
    va.DOWNLOAD: AppIcon.DOWNLOAD,
    va.OPEN_BROWSER: AppIcon.BROWSER,
    va.COPY_LINK: AppIcon.LINK,
    va.AUTHOR_PAGE: AppIcon.AUTHOR,
    va.SELECT: StateIcon.SELECT.rest,
}


class VideoMenu:
    """What a page's video cards offer; ``item_for`` maps a card to its ``SearchVideo``."""

    def __init__(
        self,
        shell,
        *,
        scope: str,
        item_for: Callable[[CardView], Any],
        on_select: Callable[[CardView], None] | None = None,
        download: bool = True,
    ):
        self.shell = shell
        self.scope = scope
        self.item_for = item_for
        self.on_select = on_select
        self.download = download  # False on the library: its records are downloaded already

    @property
    def manager(self):
        return self.shell.home.manager

    def _kind(self, card: CardView) -> str:
        return "image" if card.kind == KIND_IMAGE else "video"

    async def actions(self, card: CardView, *, touch: bool = False) -> list[va.VideoAction]:
        video = self.item_for(card)
        if not isinstance(video, SearchVideo):
            return []
        history_id = card.history_id
        image = card.kind == KIND_IMAGE
        location = FileLocation()
        if history_id and not image:
            location = await asyncio.to_thread(va.record_location, self.manager, history_id)
        url = detail_info.page_url(self._kind(card), history_id) if history_id else str(video.source_url or "")
        return va.video_actions(
            location,
            url=url,
            detail=bool(history_id),
            can_stream=bool(history_id) and not image and video.downloadable,
            download=self.download and card.selectable and not image,
            author=video.author_username,
            select=touch and self.on_select is not None and card.selectable,
            open_folder=self.shell.caps.open_folder,
        )

    async def run(self, card: CardView, action: va.VideoAction) -> None:
        shell, page = self.shell, self.shell.page
        video = self.item_for(card)
        kind = self._kind(card)
        if action.id in (va.DETAIL, va.PLAY):
            if action.id == va.PLAY and action.value and video_player.opens_externally(action.value):
                ok, message = await asyncio.to_thread(video_player.open_local_video, action.value)
                if not ok:
                    notice(page, "error", va.cannot_open_title(), message)
                return
            shell.details.remember(video, card.history_id)
            if action.id == va.PLAY:
                shell.details.model(kind, card.history_id).state.playing = True
            shell.open(routes.route(routes.DETAIL, kind, card.history_id))
        elif action.id == va.OPEN_FOLDER:
            ok, message = await asyncio.to_thread(self.manager.open_history_output, card.history_id, open_file=False)
            if not ok:
                notice(page, "warning", va.cannot_open_title(), message)
        elif action.id == va.NAS_FOLDER:
            location = await asyncio.to_thread(va.record_location, self.manager, card.history_id)
            notice(page, "warning", va.cannot_open_title(), remote_unreachable_message(location, for_folder=True))
        elif action.id in (va.COPY_PATH, va.COPY_NAS_PATH, va.COPY_LINK):
            await ft.Clipboard().set(action.value)
            notice(page, "success", va.copied_title(), action.value, duration_ms=1800)
        elif action.id == va.OPEN_BROWSER:
            await ft.UrlLauncher().launch_url(action.value)
        elif action.id == va.DOWNLOAD and isinstance(video, SearchVideo):
            await shell.downloads.stage_videos([video], rule_id=page_rule_id(self.scope), label=video.title or video.video_id)
        elif action.id == va.AUTHOR_PAGE:
            shell.open(routes.route(routes.AUTHOR, action.value))
        elif action.id == va.SELECT and self.on_select is not None:
            self.on_select(card)


def menu_items(actions: list[va.VideoAction], on_pick: Callable[[va.VideoAction], None]) -> list[ft.PopupMenuItem]:
    items: list[ft.PopupMenuItem] = []
    group = None
    for action in actions:
        if group is not None and action.group != group:
            items.append(ft.PopupMenuItem())  # no content: a divider
        group = action.group
        items.append(ft.PopupMenuItem(content=action.label, icon=_ICONS.get(action.id),
                                      on_click=lambda _e, action=action: on_pick(action)))
    return items


def open_action_sheet(page: ft.Page, title: str, actions: list[va.VideoAction],
                      on_pick: Callable[[va.VideoAction], None]) -> None:
    """Phones: the same entries as a bottom sheet."""

    def pick(action: va.VideoAction) -> None:
        page.pop_dialog()
        on_pick(action)

    rows: list[ft.Control] = [ft.Container(
        padding=ft.Padding.symmetric(horizontal=Space.LG, vertical=Space.SM),
        content=ft.Text(title, style=text_style(TYPE.label), max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
    )]
    group = None
    for action in actions:
        if group is not None and action.group != group:
            rows.append(ft.Divider(height=1))
        group = action.group
        rows.append(ft.ListTile(leading=ft.Icon(_ICONS.get(action.id)), title=ft.Text(action.label),
                                on_click=lambda _e, action=action: pick(action)))
    page.show_dialog(ft.BottomSheet(
        show_drag_handle=True,
        content=ft.Container(
            padding=ft.Padding.only(bottom=Space.LG),
            content=ft.Column(tight=True, spacing=0, scroll=ft.ScrollMode.AUTO, controls=rows),
        ),
    ))
