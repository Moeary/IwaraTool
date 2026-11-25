"""One download task: cover, title, progress, state and its actions (docs/flet_ui_plan.md 7.6).

Replaces the PySide6 wide table.  Two lines on phones, one line from medium
widths on.  The row subscribes to its own ``TaskItem``, so progress redraws
only this row.
"""
from __future__ import annotations

import flet as ft

from ...core.models import DownloadTask, TaskStatus, status_label
from ...i18n import tr
from ...presentation.downloads.task_statuses import _ACTIVE_STATUSES
from ...presentation.downloads.task_text import priority_text, progress_ratio, task_cover_url, task_meta_text, task_url
from .. import layout
from ..icons import AppIcon
from ..stores.app_state import use_shell
from ..stores.tasks import TaskItem
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space
from .cover import Cover
from .status_badge import BadgeKind, status_badge

_QUEUED = frozenset({TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD, TaskStatus.RESOLVING, TaskStatus.STAGED})


def task_badge(task: DownloadTask, palette) -> ft.Control:
    status = task.status
    if status == TaskStatus.DOWNLOADING:
        return status_badge(BadgeKind.DOWNLOADING, palette, text=f"{progress_ratio(task) * 100:.0f}%")
    if status == TaskStatus.COMPLETED:
        return status_badge(BadgeKind.DOWNLOADED, palette)
    if status == TaskStatus.FAILED:
        return status_badge(BadgeKind.FAILED, palette)
    if status in _QUEUED or status == TaskStatus.CANCELLING:
        return status_badge(BadgeKind.QUEUED, palette, text=status_label(status))
    return status_badge(BadgeKind.NEUTRAL, palette, text=status_label(status))


def _menu(shell, task: DownloadTask) -> ft.Control:
    service = shell.downloads
    task_id = task.task_id
    items: list[ft.PopupMenuItem] = []

    async def run(method: str, *args, **kwargs):
        await service.run(method, *args, **kwargs)

    async def copy_link(_e):
        await ft.Clipboard().set(task_url(task))

    async def open_browser(_e):
        await ft.UrlLauncher().launch_url(task_url(task))

    if task.status == TaskStatus.COMPLETED:
        items.append(ft.PopupMenuItem(content=tr("Open folder", "打开所在文件夹", "フォルダーを開く"), icon=AppIcon.FOLDER_OPEN,
                                      on_click=lambda _e: shell.page.run_task(run, "open_task_output", task_id, open_file=False)))
    if task.status in _ACTIVE_STATUSES or task.status in _QUEUED:
        for value in (1, 0, -1):
            items.append(ft.PopupMenuItem(
                content=tr(f"Priority: {priority_text(value)}", f"优先级：{priority_text(value)}", f"優先度: {priority_text(value)}"),
                checked=int(task.priority or 0) == value,
                on_click=lambda _e, value=value: shell.page.run_task(run, "set_task_priority", task_id, value),
            ))
    items += [
        ft.PopupMenuItem(content=tr("Copy link", "复制链接", "リンクをコピー"), icon=AppIcon.COPY, on_click=copy_link),
        ft.PopupMenuItem(content=tr("Open in browser", "在浏览器打开", "ブラウザーで開く"), icon=AppIcon.BROWSER, on_click=open_browser),
    ]
    if task.status not in _ACTIVE_STATUSES:
        items.append(ft.PopupMenuItem(content=tr("Remove from list", "从列表移除", "一覧から削除"), icon=AppIcon.DELETE,
                                      on_click=lambda _e: shell.page.run_task(run, "remove_task", task_id)))
    return ft.PopupMenuButton(icon=AppIcon.MORE, tooltip=tr("More", "更多", "その他"), items=items)


def _primary_action(shell, task: DownloadTask) -> ft.Control | None:
    service = shell.downloads
    task_id = task.task_id
    if task.status in _ACTIVE_STATUSES or task.status in (TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD):
        if task.status == TaskStatus.CANCELLING:
            return None
        return ft.IconButton(icon=AppIcon.CANCEL, tooltip=tr("Cancel", "取消", "キャンセル"),
                             on_click=lambda _e: shell.page.run_task(service.run, "cancel_task", task_id))
    if task.status == TaskStatus.FAILED:
        return ft.IconButton(icon=AppIcon.RETRY, tooltip=tr("Retry", "重试", "再試行"),
                             on_click=lambda _e: shell.page.run_task(service.run, "retry_task", task_id))
    if task.status == TaskStatus.CANCELLED:
        return ft.IconButton(icon=AppIcon.UNDO, tooltip=tr("Restore", "恢复", "元に戻す"),
                             on_click=lambda _e: shell.page.run_task(service.run, "restore_cancelled_task", task_id))
    return None


@ft.component
def TaskRow(item: TaskItem):
    shell = use_shell()
    task = item.task
    palette = palette_for(shell.page)
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    title = ft.Text(task.title or task.video_id or task.url, style=text_style(TYPE.card_title), max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, tooltip=task.title or None)
    meta = ft.Text(task_meta_text(task), style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS, expand=True, tooltip=task.error_msg or None)
    progress = None
    if task.status == TaskStatus.DOWNLOADING:
        progress = ft.ProgressBar(value=progress_ratio(task) or None, bar_height=Size.PROGRESS_BAR, border_radius=Radius.FULL)
    elif task.status == TaskStatus.RESOLVING:
        progress = ft.ProgressBar(value=None, bar_height=Size.PROGRESS_BAR, border_radius=Radius.FULL)
    actions = [control for control in (_primary_action(shell, task), _menu(shell, task)) if control is not None]
    text_column = ft.Column(
        spacing=Space.XS,
        expand=True,
        controls=[title, ft.Row(spacing=Space.SM, controls=[task_badge(task, palette), meta]), *([progress] if progress else [])],
    )
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.SM),
        border_radius=Radius.CONTROL,
        content=ft.Row(
            spacing=Space.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[Cover("video", task.video_id, task_cover_url(task), 72 if compact else 112, task.thumbnail_path),
                      text_column, *actions],
        ),
    )
