"""A cover from the image cache: placeholder until it lands, then the picture (fades in)."""
from __future__ import annotations

import flet as ft

from ..icons import AppIcon
from ..services.images import use_cached_image
from ..stores.app_state import use_shell
from ..theme import palette_for
from ..tokens import Motion, Radius


@ft.component
def Cover(
    kind: str,
    key: str,
    url: str,
    width: float,
    local: str = "",
    *,
    aspect: float = 16 / 9,
    round: bool = False,
    icon=AppIcon.VIDEO,
    radius: float = Radius.CHIP,
    fit: ft.BoxFit = ft.BoxFit.COVER,  # CONTAIN shows the whole picture (the library's cover viewer)
    placeholder=None,  # background behind the picture; the cover placeholder color by default
):
    shell = use_shell()
    path = use_cached_image(shell.images, kind, key, url, local)
    palette = palette_for(shell.page)
    height = width / aspect
    if round:
        side = min(width, height) * 0.72
        picture = ft.Container(
            width=side,
            height=side,
            border_radius=Radius.FULL,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            bgcolor=palette.field,
            alignment=ft.Alignment.CENTER,
            content=ft.Image(src=path, width=side, height=side, fit=ft.BoxFit.COVER, gapless_playback=True,
                             error_content=ft.Icon(icon)) if path else ft.Icon(icon),
        )
        return ft.Container(width=width, height=height, alignment=ft.Alignment.CENTER, content=picture)
    content = (
        ft.Image(
            src=path,
            width=width,
            height=height,
            fit=fit,
            gapless_playback=True,
            fade_in_animation=Motion.COVER_FADE,
            cache_width=int(width * 2),  # decode at display size (HiDPI), not the original
            error_content=ft.Icon(icon, color=palette.on_surface_variant),
        )
        if path
        else ft.Icon(icon, color=palette.on_surface_variant)
    )
    return ft.Container(
        width=width,
        height=height,
        border_radius=radius,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        alignment=ft.Alignment.CENTER,
        bgcolor=palette.cover_placeholder if placeholder is None else placeholder,
        content=content,
    )
