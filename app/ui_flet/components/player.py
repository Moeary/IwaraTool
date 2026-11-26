"""The built-in player (docs/flet_ui_plan.md 7.3): flet-video over the local copy or the loopback stream.

A downloaded copy (or its NAS URL) plays directly; otherwise the Iwara file
is signed and served through ``core.playback`` 's loopback proxy, which adds
the account token and renews expired links.  Leaving the player releases the
proxy entry.
"""
from __future__ import annotations

import asyncio

import flet as ft
import flet_video as ftv

from ...core import site as sites
from ...core.playback import preview_stream, video_player
from ...i18n import tr
from ...logging_setup import get_logger
from ..icons import AppIcon
from ..stores.app_state import use_shell
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space

logger = get_logger(__name__)


def resolve_source(manager, video_id: str, site: str) -> tuple[str, bool]:
    """``(url or path, is_proxy_entry)``; blocking (history lookup, signing the file URL)."""

    local = video_player.downloaded_copy(video_id, manager)
    if local:
        return local, False
    with sites.site_scope(site):
        stream = preview_stream.open_stream(manager.api, video_id)
    return stream.url, True


def release(url: str) -> None:
    if url:
        try:
            preview_stream.get_proxy().unregister(url)
        except Exception:
            logger.debug("Releasing a stream failed", exc_info=True)


@ft.component
def InlinePlayer(video_id: str, title: str, site: str, width: float, on_close=None):
    shell = use_shell()
    palette = palette_for(shell.page)
    source, set_source = ft.use_state(lambda: {"url": "", "error": ""})
    held = ft.use_ref("")  # the proxy entry to release on unmount

    async def prepare() -> None:
        try:
            url, proxied = await asyncio.to_thread(resolve_source, shell.home.manager, video_id, site)
        except Exception as exc:
            logger.warning("Could not prepare %s for playback", video_id, exc_info=True)
            set_source({"url": "", "error": str(exc)})
            return
        if proxied:
            held.current = url
        set_source({"url": url, "error": ""})

    def cleanup() -> None:
        release(held.current)
        held.current = ""

    ft.use_effect(prepare, [video_id], cleanup=cleanup)
    height = width * 9 / 16
    if source["error"]:
        content: ft.Control = ft.Column(
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=Space.SM,
            controls=[
                ft.Icon(AppIcon.ERROR, color=palette.danger.fg, size=Size.ICON_LG),
                ft.Text(tr("Could not play this video", "无法播放该视频", "この動画を再生できません"),
                        style=text_style(TYPE.label, color=palette.on_cover_scrim)),
                ft.Text(source["error"], style=text_style(TYPE.meta, color=palette.on_cover_scrim),
                        max_lines=3, overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER),
            ],
        )
    elif not source["url"]:
        content = ft.Column(
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=Space.SM,
            controls=[
                ft.ProgressRing(),
                ft.Text(tr("Loading the video…", "正在加载视频…", "動画を読み込み中…"),
                        style=text_style(TYPE.meta, color=palette.on_cover_scrim)),
            ],
        )
    else:
        content = ftv.Video(
            playlist=[ftv.VideoMedia(source["url"])],
            title=title,
            autoplay=True,
            aspect_ratio=16 / 9,
            fill_color=palette.scrim,
            expand=True,
            on_error=lambda e: set_source({"url": "", "error": str(e.data or "")}),
        )
    stack: list[ft.Control] = [ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=content)]
    if on_close is not None:
        stack.append(ft.Container(
            right=Space.XS, top=Space.XS,
            content=ft.IconButton(icon=AppIcon.CLOSE, icon_color=palette.on_cover_scrim, bgcolor=palette.cover_scrim,
                                  tooltip=tr("Stop playing", "停止播放", "再生を停止"), on_click=lambda _e: on_close()),
        ))
    return ft.Container(
        width=width,
        height=height,
        bgcolor=palette.scrim,
        border_radius=Radius.CARD,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Stack(expand=True, controls=stack),
    )
