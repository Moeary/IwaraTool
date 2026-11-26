"""Downloads (docs/flet_ui_plan.md 7.6): links in, the waiting list, task tabs and rows, the log.

``/downloads`` is the workbench; ``/downloads/log`` the log (its own route so
it gets the whole window on phones).
"""
from __future__ import annotations

import flet as ft

from ....i18n import tr
from ....presentation.downloads.staging import batch_counts_text, confirmed_text, example_text
from ... import layout, routes
from ...components.download_sheet import open_download_sheet
from ...components.page_frame import PageFrame
from ...components.task_row import TaskRow
from ...icons import AppIcon
from ...services.notify import notice
from ...stores.app_state import subscribe, use_shell
from ...stores.tasks import (
    TAB_ACTIVE,
    TAB_ALL,
    TAB_CANCELLED,
    TAB_COMPLETED,
    TAB_FAILED,
    TAB_QUEUED,
    TAB_SKIPPED,
    TABS,
)
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

SCOPE = "downloads"


def tab_label(tab: str) -> str:
    return {
        TAB_ALL: tr("All", "全部", "すべて"),
        TAB_ACTIVE: tr("Downloading", "下载中", "ダウンロード中"),
        TAB_QUEUED: tr("Queued", "排队", "待機"),
        TAB_FAILED: tr("Failed", "失败", "失敗"),
        TAB_COMPLETED: tr("Completed", "已完成", "完了"),
        TAB_SKIPPED: tr("Skipped", "已跳过", "スキップ"),
        TAB_CANCELLED: tr("Cancelled", "已取消", "キャンセル済み"),
    }[tab]


# ── link bar ─────────────────────────────────────────────────────────────────


@ft.component
def LinkBar():
    shell = use_shell()
    drafts = shell.drafts
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    # One line however narrow: paste sits inside the field, the hint is cut rather than wrapped,
    # and phones get the icon-only download button (the field used to wrap its hint over three lines).
    hint = (tr("Paste video, author or playlist links", "粘贴作品、作者或列表链接", "動画・作者・リストのリンク") if compact
            else tr("Paste one or more video, author or playlist links", "粘贴一个或多个作品、作者或播放列表链接",
                    "動画・作者・プレイリストのリンクを貼り付け（複数可）"))
    field = ft.TextField(
        value=drafts.get("download_links", ""),
        hint_text=hint,
        hint_max_lines=1,
        multiline=True,
        min_lines=1,
        max_lines=4,
        expand=True,
        dense=True,
        suffix_icon=ft.IconButton(icon=AppIcon.PASTE, tooltip=tr("Paste", "粘贴", "貼り付け"), on_click=lambda e: e.page.run_task(paste, e)),
        on_change=lambda e: drafts.__setitem__("download_links", e.control.value or ""),
    )

    async def paste(_e) -> None:
        text = (await ft.Clipboard().get() or "").strip()
        if text:
            field.value = text
            drafts["download_links"] = text
            field.update()

    async def submit(_e) -> None:
        text = str(field.value or "")
        if not text.strip():
            notice(shell.page, "warning", tr("Please input a valid URL first", "请先输入有效的 URL", "先に有効な URL を入力してください"))
            return

        async def confirmed(rule_id: str) -> None:
            if await shell.downloads.submit_links(text, rule_id=rule_id):
                field.value = ""
                drafts["download_links"] = ""
                field.update()

        open_download_sheet(
            shell,
            scope=SCOPE,
            title=tr("Download links", "下载链接", "リンクをダウンロード"),
            summary=tr("Authors and playlists are listed first and wait for your confirmation.",
                       "作者与播放列表会先列出全部作品，等待确认后再下载。",
                       "作者・プレイリストは一覧にしてから確認を待ちます。"),
            on_confirm=confirmed,
        )

    return ft.Row(
        spacing=Space.SM,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            field,
            ft.FilledIconButton(icon=AppIcon.DOWNLOAD, tooltip=tr("Download", "下载", "ダウンロード"), on_click=submit)
            if compact else ft.FilledButton(content=tr("Download", "下载", "ダウンロード"), icon=AppIcon.DOWNLOAD, on_click=submit),
        ],
    )


# ── waiting list ─────────────────────────────────────────────────────────────


@ft.component
def StagingCard():
    shell = use_shell()
    state = shell.state
    batches, set_batches = ft.use_state(list)
    examples, set_examples = ft.use_state(dict)

    async def load() -> None:
        if not state.staged_downloads:
            set_batches([])
            return
        found = await shell.downloads.staged_batches()
        set_batches(found)
        shown = {}
        for batch in found[:3]:
            batch_id = str(batch.get("batch_id") or "")
            example = await shell.downloads.staged_example(batch_id)
            shown[batch_id] = example_text(example) if example else ""
        set_examples(shown)

    ft.use_effect(load, [state.staged_downloads])
    if not state.staged_downloads or not batches:
        return ft.Container(height=0)
    palette = palette_for(shell.page)

    async def confirm(batch_id: str) -> None:
        result = await shell.downloads.confirm_staged(batch_id)
        notice(shell.page, "success", *confirmed_text(result))
        await shell.refresh_badges()

    async def discard(batch_id: str) -> None:
        await shell.downloads.discard_staged(batch_id)
        await shell.refresh_badges()

    rows: list[ft.Control] = [
        ft.Row(
            controls=[
                ft.Icon(AppIcon.INFO, color=palette.info.fg),
                ft.Text(
                    tr(f"{state.staged_downloads} videos wait for confirmation", f"{state.staged_downloads} 个作品待确认",
                       f"{state.staged_downloads} 件が確認待ち"),
                    style=text_style(TYPE.subtitle),
                    expand=True,
                ),
                ft.TextButton(content=tr("Confirm all", "全部确认", "すべて確定"), on_click=lambda _e: shell.page.run_task(confirm, "")),
            ]
        )
    ]
    for batch in batches:
        batch_id = str(batch.get("batch_id") or "")
        label = str(batch.get("label") or tr("Batch", "批次", "バッチ"))
        detail = batch_counts_text(batch)
        rule_name = shell.downloads.staged_rule_name(str(batch.get("rule_id") or ""))
        lines = [ft.Text(label, style=text_style(TYPE.card_title), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                 ft.Text(" · ".join(part for part in (detail, rule_name) if part), style=text_style(TYPE.meta))]
        if examples.get(batch_id):
            lines.append(ft.Text(examples[batch_id], style=text_style(TYPE.meta, color=palette.on_surface_variant),
                                 max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, selectable=True))
        rows.append(
            ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Column(spacing=0, expand=True, controls=lines),
                    ft.IconButton(icon=AppIcon.DELETE, tooltip=tr("Discard", "放弃", "破棄"),
                                  on_click=lambda _e, b=batch_id: shell.page.run_task(discard, b)),
                    ft.FilledTonalButton(content=tr("Confirm", "确认", "確定"),
                                         on_click=lambda _e, b=batch_id: shell.page.run_task(confirm, b)),
                ],
            )
        )
    return ft.Container(
        bgcolor=palette.card,
        border_radius=Radius.CARD,
        padding=Space.LG,
        content=ft.Column(spacing=Space.MD, controls=rows),
    )


# ── tabs and list ────────────────────────────────────────────────────────────


@ft.component
def TaskTabs():
    shell = use_shell()
    index = shell.tasks.index
    subscribe(index)
    chips = []
    for tab in TABS:
        count = index.counts.get(tab, 0)
        if tab not in (TAB_ALL, TAB_ACTIVE, TAB_QUEUED, TAB_FAILED) and not count and index.tab != tab:
            continue  # rarely used tabs appear once they hold something
        chips.append(
            ft.Chip(
                label=f"{tab_label(tab)} {count}" if count else tab_label(tab),
                selected=index.tab == tab,
                show_checkmark=False,
                on_select=lambda _e, tab=tab: shell.tasks.set_tab(tab),
            )
        )
    return ft.Row(spacing=Space.SM, scroll=ft.ScrollMode.AUTO, controls=chips)


@ft.component
def TaskList():
    shell = use_shell()
    store = shell.tasks
    subscribe(store.index)
    if not store.index.loaded:
        return ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.ProgressRing())
    ids = store.visible_ids()
    if not ids:
        return ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(AppIcon.EMPTY, size=Size.AVATAR),
                    ft.Text(tr("No tasks here", "这里没有任务", "タスクはありません"), style=text_style(TYPE.body)),
                ],
            ),
        )
    return ft.ListView(
        expand=True,
        spacing=Space.XS,
        build_controls_on_demand=True,
        controls=[TaskRow(store.items[task_id], key=task_id) for task_id in ids if task_id in store.items],
    )


def _bulk_menu(shell) -> ft.Control:
    service = shell.downloads

    async def run(method: str, *args, **kwargs) -> None:
        result = await service.run(method, *args, **kwargs)
        await shell.tasks.reload()
        if method == "retry_all_failed" and isinstance(result, tuple):
            notice(shell.page, "info", tr(f"{result[0]} retried", f"已重试 {result[0]} 个", f"{result[0]} 件を再試行"))

    return ft.PopupMenuButton(
        icon=AppIcon.MORE,
        tooltip=tr("More", "更多", "その他"),
        items=[
            ft.PopupMenuItem(content=tr("Retry failed", "重试失败任务", "失敗を再試行"), icon=AppIcon.RETRY,
                             on_click=lambda _e: shell.page.run_task(run, "retry_all_failed")),
            ft.PopupMenuItem(content=tr("Cancel all", "全部取消", "すべてキャンセル"), icon=AppIcon.CANCEL,
                             on_click=lambda _e: shell.page.run_task(run, "cancel_all_active")),
            ft.PopupMenuItem(content=tr("Restore cancelled", "恢复已取消任务", "キャンセルを元に戻す"), icon=AppIcon.UNDO,
                             on_click=lambda _e: shell.page.run_task(run, "restore_all_cancelled")),
            ft.PopupMenuItem(content=tr("Clear completed", "清除已完成", "完了を消去"), icon=AppIcon.CLEAN,
                             on_click=lambda _e: shell.page.run_task(run, "clear_completed")),
        ],
    )


@ft.component
def LogButton():
    shell = use_shell()
    log = shell.tasks.log
    subscribe(log)
    icon = ft.Icon(AppIcon.LOG, badge=ft.Badge(small_size=Space.SM) if log.unseen_errors else None)
    return ft.IconButton(icon=icon, tooltip=tr("Log", "运行日志", "ログ"),
                         on_click=lambda _e: shell.open(routes.route(routes.DOWNLOADS, "log")))


@ft.component
def DownloadsPage(destination: str, path: str):
    shell = use_shell()
    route = routes.parse(path)
    if route.parts[:1] == ("log",):
        return LogPage()
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    query = ft.TextField(
        hint_text=tr("Filter tasks", "筛选任务", "タスクを絞り込み"),
        prefix_icon=AppIcon.FILTER,
        dense=True,
        width=None if compact else 240,
        expand=True if compact else None,
        value=shell.tasks.index.query,
        on_change=lambda e: shell.tasks.set_query(e.control.value or ""),
    )
    tools = ft.Row(
        spacing=Space.MD,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[ft.Container(expand=True, content=TaskTabs()), *([] if compact else [query])],
    )
    body = ft.Column(
        expand=True,
        spacing=Space.MD,
        controls=[LinkBar(), StagingCard(), tools, *([ft.Row([query])] if compact else []), TaskList()],
    )
    return PageFrame(
        title=routes.label(routes.DOWNLOADS),
        body=body,
        actions=[LogButton(), _bulk_menu(shell)],
        max_width=layout.MAX_WIDTH_LIST_DETAIL,
    )


@ft.component
def LogPage():
    shell = use_shell()
    log = shell.tasks.log
    subscribe(log)

    def seen() -> None:
        if log.unseen_errors:
            log.unseen_errors = 0

    ft.use_effect(seen, [len(log.lines)])
    palette = palette_for(shell.page)
    lines = log.lines[-500:]
    body = ft.Container(
        expand=True,
        bgcolor=palette.lowest,
        border_radius=Radius.CARD,
        padding=Space.MD,
        content=ft.ListView(
            expand=True,
            auto_scroll=True,
            controls=[ft.Text(line, style=text_style(TYPE.meta), selectable=True) for line in lines]
            or [ft.Text(tr("Logs will appear here…", "操作日志将显示在此…", "ログはここに表示されます…"), style=text_style(TYPE.meta))],
        ),
    )
    return PageFrame(title=tr("Log", "运行日志", "ログ"), body=body, max_width=layout.MAX_WIDTH_LIST_DETAIL)
