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
from .. import layout, routes
from ..icons import AppIcon, NavIcon
from ..stores.app_state import use_shell
from ..services.notify import notice
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


async def _open_output(shell, task: DownloadTask, open_file: bool | None = None) -> None:
    """Completed task: the folder or the player (``None`` follows the setting); phones show the library record."""
    if not shell.caps.open_folder:
        shell.open(routes.route(routes.LIBRARY, task.video_id))
        return
    ok, message = await shell.downloads.run("open_task_output", task.task_id, open_file=open_file)
    if not ok and message:
        notice(shell.page, "warning", tr("Cannot open", "无法打开", "開けません"), message)


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
        if shell.caps.open_folder:
            items.append(ft.PopupMenuItem(content=tr("Open folder", "打开所在文件夹", "フォルダーを開く"), icon=AppIcon.FOLDER_OPEN,
                                          on_click=lambda _e: shell.page.run_task(_open_output, shell, task, False)))
        items.append(ft.PopupMenuItem(content=tr("Show in library", "在资料库中查看", "ライブラリで表示"), icon=NavIcon.LIBRARY.rest,
                                      on_click=lambda _e: shell.open(routes.route(routes.LIBRARY, task.video_id))))
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
    cover_width = 112 if not compact else 56 if shell.state.layout.width < 360 else 72
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
    completed = task.status == TaskStatus.COMPLETED
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.SM),
        border_radius=Radius.CONTROL,
        # Settings › Downloads › "completed card click" (the classic window's behaviour).
        on_click=(lambda _e: shell.page.run_task(_open_output, shell, task)) if completed else None,
        ink=completed,
        content=ft.Row(
            spacing=Space.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[Cover("video", task.video_id, task_cover_url(task), cover_width, task.thumbnail_path),
                      text_column, *actions],
        ),
    )
