"""Author status: subscribed in this app? followed on Iwara?

Two chips and the buttons that change them, shared by the author page, the
subscription pane and the post detail page.  Texts and the profile parsing
come from ``presentation.author.status``.
"""
from __future__ import annotations

import asyncio

import flet as ft

from ...core.home.loading import call_with_client
from ...presentation.author import status as author_text
from .. import routes
from ..icons import AppIcon, StateIcon
from ..services.dialogs import confirm
from ..services.notify import notice
from ..stores.app_state import subscribe, use_shell
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Space


def _chip(page: ft.Page, text: str, tone: str) -> ft.Control:
    palette = palette_for(page)
    roles = {"success": palette.success, "warning": palette.warning, "accent": palette.info}
    role = roles.get(tone)
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XXS),
        border_radius=Radius.BADGE,
        bgcolor=role.bg if role else palette.field,
        content=ft.Text(text, style=text_style(TYPE.meta, color=role.fg if role else palette.on_surface_variant)),
    )


@ft.component
def AuthorStatus(username: str, name: str = "", user_id: str = "", avatar: str = "", *, show_open: bool = True):
    shell = use_shell()
    page = shell.page
    manager = shell.home.manager
    username = author_text.clean_username(username)
    status, set_status = ft.use_state(lambda: {
        "source": None, "web": author_text.WEB_LOADING, "following": False, "user_id": user_id, "busy": False, "error": "",
    })
    logged_in = shell.state.logged_in
    subscribe(shell.subscriptions.state)
    revision = shell.subscriptions.state.revision  # subscribed or removed elsewhere

    async def load() -> None:
        source = None
        try:
            source = await asyncio.to_thread(manager.find_author_subscription, username) if username else None
        except Exception:
            source = None
        if not username or not logged_in:
            set_status({**status, "source": source, "web": author_text.WEB_SIGNED_OUT, "following": False})
            return
        set_status({**status, "source": source, "web": author_text.WEB_LOADING})
        result, error = await asyncio.to_thread(call_with_client, manager, lambda client: client.get_user_profile(username))
        user, problem = author_text.profile_follow(result, error)
        if user is None:
            set_status({**status, "source": source, "web": author_text.WEB_ERROR, "error": problem})
            return
        set_status({**status, "source": source, "web": author_text.WEB_READY, "following": bool(user.get("following")),
                    "user_id": str(user.get("id") or user_id), "error": ""})

    ft.use_effect(load, [username, logged_in, revision])

    async def toggle_subscription() -> None:
        source = status["source"]
        if source is not None:
            title, message = author_text.unsubscribe_confirmation(username)
            if not await confirm(page, title, message, confirm_label=author_text.subscribe_button_text(True), danger=True):
                return
            try:
                await asyncio.to_thread(manager.remove_subscription_source, int(source.get("id", 0) or 0))
            except Exception as exc:
                notice(page, "error", author_text.remove_failed_title(), str(exc))
                return
        else:
            try:
                await asyncio.to_thread(lambda: manager.add_author_subscription(
                    username, title=name or username, remote_id=status["user_id"], avatar_url=avatar))
            except Exception as exc:
                notice(page, "error", author_text.subscribe_failed_title(), str(exc))
                return
            notice(page, "success", *author_text.subscribed_notice(username))
        await load()
        page.run_task(shell.subscriptions.load)

    async def toggle_follow() -> None:
        target = status["user_id"]
        if not target or status["web"] != author_text.WEB_READY:
            return
        wanted = not status["following"]
        set_status({**status, "busy": True})
        result, error = await asyncio.to_thread(call_with_client, manager, lambda client: client.set_following(target, wanted))
        ok, message = result if isinstance(result, tuple) and len(result) == 2 else (False, error)
        if ok:
            notice(page, "success", author_text.follow_done_title(wanted), f"@{username}")
            set_status({**status, "busy": False, "following": wanted})
        else:
            notice(page, "error", author_text.follow_failed_title(), str(message or error))
            set_status({**status, "busy": False})

    source = status["source"]
    local_text, local_tone = author_text.local_chip_state(source)
    web_text, web_tone = author_text.web_chip_state(status["web"], status["following"])
    usable = status["web"] == author_text.WEB_READY and bool(status["user_id"])
    controls: list[ft.Control] = [
        _chip(page, local_text, local_tone),
        ft.Container(content=_chip(page, web_text, web_tone), tooltip=status["error"] or None),
        ft.FilledTonalButton(
            content=author_text.subscribe_button_text(source is not None),
            icon=AppIcon.REMOVE if source is not None else StateIcon.SUBSCRIBE.rest,
            disabled=not username,
            on_click=lambda _e: page.run_task(toggle_subscription),
        ),
        ft.OutlinedButton(
            content=author_text.follow_button_text(status["following"]),
            icon=StateIcon.FOLLOW.active if status["following"] else StateIcon.FOLLOW.rest,
            disabled=not usable or status["busy"],
            tooltip=None if usable or status["web"] == author_text.WEB_LOADING else author_text.follow_unavailable_tooltip(),
            on_click=lambda _e: page.run_task(toggle_follow),
        ),
    ]
    if show_open and source is not None:
        source_id = int(source.get("id", 0) or 0)
        controls.append(ft.TextButton(content=author_text.open_subscription_text(), icon=AppIcon.OPEN_IN_NEW,
                                      on_click=lambda _e: shell.open(routes.route(routes.SUBSCRIPTIONS, source_id))))
    return ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=controls)
