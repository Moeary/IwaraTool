"""One badge for a video's state, the same on covers, list rows and detail pages.

``docs/flet_ui_plan.md`` §5.5: a badge appears only when the state is worth
noticing; "not downloaded" shows nothing.
"""

from __future__ import annotations

from enum import Enum

import flet as ft

from ...i18n import tr
from ..theme import text_style
from ..tokens import TYPE, Palette, Radius, Semantic, Space


class BadgeKind(str, Enum):
    NEW = "new"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    MOVED = "moved"
    REMOTE = "remote"
    FAILED = "failed"


def badge_label(kind: BadgeKind) -> str:
    return {
        BadgeKind.NEW: tr("New", "新", "新着"),
        BadgeKind.QUEUED: tr("Queued", "排队", "待機"),
        BadgeKind.DOWNLOADING: tr("Downloading", "下载中", "ダウンロード中"),
        BadgeKind.DOWNLOADED: tr("Downloaded", "已下载", "ダウンロード済み"),
        BadgeKind.MOVED: tr("Moved", "已移走", "移動済み"),
        BadgeKind.REMOTE: tr("Remote", "远端", "リモート"),
        BadgeKind.FAILED: tr("Failed", "失败", "失敗"),
    }[kind]


def badge_colors(kind: BadgeKind, palette: Palette) -> Semantic:
    if kind == BadgeKind.DOWNLOADING:
        return Semantic(fg=palette.on_primary_container, bg=palette.primary_container)
    return {
        BadgeKind.NEW: palette.info,
        BadgeKind.QUEUED: palette.warning,
        BadgeKind.DOWNLOADED: palette.success,
        BadgeKind.MOVED: palette.warning,
        BadgeKind.REMOTE: palette.info,
        BadgeKind.FAILED: palette.danger,
    }[kind]


def status_badge(kind: BadgeKind, palette: Palette, *, text: str | None = None) -> ft.Container:
    """A small pill; ``text`` replaces the label (e.g. ``42%`` while downloading)."""

    colors = badge_colors(kind, palette)
    return ft.Container(
        content=ft.Text(
            text if text is not None else badge_label(kind),
            style=text_style(TYPE.badge, color=colors.fg),
            no_wrap=True,
        ),
        bgcolor=colors.bg,
        border_radius=Radius.BADGE,
        padding=ft.Padding.symmetric(horizontal=Space.SM - 2, vertical=Space.XXS),
    )
