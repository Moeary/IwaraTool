"""Search: source, keyword, scope and sort over one cover grid.

Phones read the next page when the grid reaches its end; wider windows page
with buttons.  Selecting cards shows the action bar with "Add to downloads".
"""
from __future__ import annotations

import flet as ft

from ....core.models import TaskStatus
from ....i18n import tr
from ....presentation.media.cards import KIND_AUTHOR, KIND_IMAGE, KIND_PLAYLIST, CardView
from ....presentation.search import options
from ....presentation.search import post_filter_text
from ....presentation.search.download_status import SearchDownloadStatus
from ....presentation.search.history_entries import search_history_label
from ... import layout, routes
from ...components.download_sheet import open_download_sheet
from ...components.filter_sheet import open_filter_sheet
from ...components.cover_size import CoverSizeSlider
from ...components.page_frame import PageFrame
from ...components.status_badge import BadgeKind
from ...components.video_actions import VideoMenu
from ...components.video_grid import VideoGrid
from ...icons import AppIcon, StateIcon
from ...services.notify import notice
from ...stores.app_state import subscribe, use_shell
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

SCOPE = "search"

_STATUS_BADGES = {
    SearchDownloadStatus.DOWNLOADED: BadgeKind.DOWNLOADED,
    SearchDownloadStatus.MOVED: BadgeKind.MOVED,
    SearchDownloadStatus.REMOTE: BadgeKind.REMOTE,
}
_QUEUED = {TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD, TaskStatus.RESOLVING}


def card_badges(shell, cards: list[CardView], statuses: dict) -> dict[str, tuple[BadgeKind, str | None]]:
    """Download state per card: the queue first (downloading / queued), then the library."""

    tasks = {item.task.video_id: item.task for item in shell.tasks.items.values()}
    badges: dict[str, tuple[BadgeKind, str | None]] = {}
    for card in cards:
        if not card.history_id:
            continue
        task = tasks.get(card.history_id)
        if task is not None and task.status == TaskStatus.DOWNLOADING:
            badges[card.key] = (BadgeKind.DOWNLOADING, None)
        elif task is not None and task.status in _QUEUED:
            badges[card.key] = (BadgeKind.QUEUED, None)
        elif statuses.get(card.history_id) in _STATUS_BADGES:
            badges[card.key] = (_STATUS_BADGES[statuses[card.history_id]], None)
    return badges


# ── query bar ────────────────────────────────────────────────────────────────


@ft.component
def QueryBar():
    shell = use_shell()
    store = shell.search
    state = store.state
    subscribe(state)
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    oreno_ok = options.oreno3d_available()

    source = ft.PopupMenuButton(
        tooltip=options.source_tooltip(),
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.SM),
            border_radius=Radius.CONTROL,
            bgcolor=palette_for(shell.page).field,
            content=ft.Row(
                tight=True,
                spacing=Space.XS,
                controls=[ft.Text(options.source_label(state.source, short=True), style=text_style(TYPE.label)),
                          ft.Icon(StateIcon.EXPAND.rest, size=Size.ICON)],
            ),
        ),
        items=[
            ft.PopupMenuItem(
                content=options.source_label(key),
                checked=state.source == key,
                disabled=key == options.SOURCE_ORENO3D and not oreno_ok,
                on_click=lambda _e, key=key: store.set_source(key),
            )
            for key in (options.SOURCE_IWARA, options.SOURCE_ORENO3D)
        ],
    )

    async def submit(_e=None) -> None:
        await store.search()

    field = ft.TextField(
        value=state.keyword,
        hint_text=options.keyword_placeholder(state.scope),
        tooltip=options.scope_hint(state.scope, state.source),
        prefix_icon=AppIcon.SEARCH,
        dense=True,
        expand=True,
        on_change=lambda e: store.set_keyword(e.control.value or ""),
        on_submit=submit,
        # Compact, so the field keeps its height when the clear button appears (a full 48 px button grew it).
        suffix=ft.IconButton(icon=AppIcon.CLEAR, icon_size=Size.ICON, tooltip=tr("Clear", "清除", "クリア"),
                             padding=0, visual_density=ft.VisualDensity.COMPACT,
                             size_constraints=ft.BoxConstraints(max_width=Size.AVATAR_SM, max_height=Size.AVATAR_SM),
                             on_click=lambda _e: store.set_keyword("")) if state.keyword else None,
    )
    button = (
        ft.IconButton(icon=AppIcon.SEARCH, tooltip=tr("Search", "搜索", "検索"), on_click=submit,
                      disabled=state.loading)
        if compact
        else ft.FilledButton(content=tr("Search", "搜索", "検索"), icon=AppIcon.SEARCH, on_click=submit,
                             disabled=state.loading)
    )
    bar = ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[source, field, button])
    # Always the same Column: returning the bar alone without suggestions swapped the root control when
    # suggestions arrived, which rebuilt the field and dropped the focus after each typed character.
    if not state.suggestions:
        return ft.Column(spacing=Space.SM, controls=[bar])
    suggestions = ft.Row(
        scroll=ft.ScrollMode.AUTO,
        spacing=Space.SM,
        controls=[
            ft.Chip(label=label, tooltip=tip, leading=ft.Icon(AppIcon.TAG, size=Size.ICON_SM),
                    on_click=lambda _e, key=key: store.apply_suggestion(key))
            for key, label, tip in state.suggestions
        ],
    )
    return ft.Column(spacing=Space.SM, controls=[bar, suggestions])


@ft.component
def ScopeChips(trailing: ft.Control | None = None):
    shell = use_shell()
    store = shell.search
    state = store.state
    subscribe(state)
    supported = options.supported_scopes(state.source)
    chips: list[ft.Control] = [
        ft.Chip(
            label=label,
            selected=state.scope == key,
            show_checkmark=False,
            on_select=lambda _e, key=key: store.set_scope(key),
        )
        for label, key in options.scope_items()
        if key in supported
    ]
    choices = store.sort_choices()
    current = next((label for label, key in choices if key == state.sort), choices[0][0])
    chips.append(
        ft.PopupMenuButton(
            tooltip=tr("Sort", "排序", "並べ替え"),
            content=ft.Container(
                padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.SM - 2),
                border_radius=Radius.CHIP,
                border=ft.Border.all(1, palette_for(shell.page).outline_variant),
                content=ft.Row(tight=True, spacing=Space.XS, controls=[
                    ft.Icon(AppIcon.SORT, size=Size.ICON_SM),
                    ft.Text(current, style=text_style(TYPE.label)),
                ]),
            ),
            items=[
                ft.PopupMenuItem(content=label, checked=key == state.sort, on_click=lambda _e, key=key: store.set_sort(key))
                for label, key in choices
            ],
        )
    )
    post_filter = state.post_filter
    chips.append(
        ft.Chip(
            label=post_filter_text.button_text(post_filter),
            leading=ft.Icon(AppIcon.FILTER, size=Size.ICON_SM),
            selected=post_filter.is_active(),
            show_checkmark=False,
            tooltip=post_filter_text.button_tooltip(),
            disabled=store.grid_only(),
            on_click=lambda _e: open_filter_sheet(shell, store.state.post_filter, store.set_post_filter),
        )
    )
    chips.append(CoverSizeSlider())
    row = ft.Row(spacing=Space.SM, scroll=ft.ScrollMode.AUTO, controls=chips)
    if trailing is None:
        return row
    return ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[ft.Container(expand=True, content=row), trailing])


# ── results ──────────────────────────────────────────────────────────────────


def _pager(shell) -> ft.Control:
    store = shell.search
    state = store.state
    page_number = state.current_page + 1
    last = f" / {state.last_page + 1}" if state.last_page is not None else ""
    return ft.Row(
        tight=True,
        spacing=0,
        controls=[
            ft.IconButton(icon=AppIcon.CHEVRON_LEFT, tooltip=tr("Previous page", "上一页", "前のページ"),
                          disabled=state.loading or state.current_page <= 0,
                          on_click=lambda _e: shell.page.run_task(store.previous_page)),
            ft.Text(tr(f"Page {page_number}{last}", f"第 {page_number}{last} 页", f"{page_number}{last} ページ"),
                    style=text_style(TYPE.meta)),
            ft.IconButton(icon=AppIcon.CHEVRON_RIGHT, tooltip=tr("Next page", "下一页", "次のページ"),
                          disabled=state.loading or not state.has_next,
                          on_click=lambda _e: shell.page.run_task(store.next_page)),
        ],
    )


@ft.component
def StatusRow(inline: bool = False):
    shell = use_shell()
    state = shell.search.state
    subscribe(state)
    palette = palette_for(shell.page)
    paged = shell.state.layout.width_class not in (layout.WIDTH_COMPACT,)
    controls: list[ft.Control] = [
        ft.Text(state.status, style=text_style(TYPE.meta, color=palette.on_surface_variant), expand=not inline,
                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
    ]
    if state.searched and paged and shell.search.cards:
        controls.append(_pager(shell))
    return ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, tight=inline, controls=controls)


@ft.component
def SelectionBar():
    shell = use_shell()
    store = shell.search
    state = store.state
    subscribe(state)
    if not state.selecting:
        return ft.Container(height=0)
    count = len(state.selected)
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT

    async def queue() -> None:
        values = store.selected_values()

        async def confirmed(rule_id: str) -> None:
            outcome = await shell.downloads.stage_search_selection(values, rule_id=rule_id)
            if outcome.get("staged") or outcome.get("direct"):
                store.clear_selection()

        open_download_sheet(
            shell,
            scope=SCOPE,
            title=tr(f"Download {count} selected", f"下载选中的 {count} 项", f"選択した {count} 件をダウンロード"),
            summary=tr("They are checked first and wait on the Downloads page for your confirmation.",
                       "会先检查一遍，然后在下载页等待确认。",
                       "先に確認し、ダウンロードページで確定を待ちます。"),
            on_confirm=confirmed,
        )

    palette = palette_for(shell.page)
    return ft.Container(
        bgcolor=palette.primary_container,
        border_radius=Radius.CONTROL,
        padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XS),
        content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.IconButton(icon=AppIcon.CLOSE, tooltip=tr("Clear selection", "取消选择", "選択を解除"),
                              on_click=lambda _e: store.clear_selection()),
                ft.Text(tr(f"{count} selected", f"已选 {count} 项", f"{count} 件選択"), style=text_style(TYPE.label),
                        expand=True),
                ft.IconButton(icon=AppIcon.SELECT_ALL, tooltip=tr("Select all", "全选", "すべて選択"),
                              on_click=lambda _e: store.select_all()),
                ft.FilledButton(
                    content=tr(f"Download ({count})", f"加入下载（{count}）", f"ダウンロード（{count}）") if not compact
                    else tr(f"Download {count}", f"下载 {count}", f"保存 {count}"),
                    icon=AppIcon.DOWNLOAD,
                    on_click=lambda _e: shell.page.run_task(queue),
                ),
            ],
        ),
    )


def _empty(icon, text: str) -> ft.Control:
    return ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[ft.Icon(icon, size=Size.AVATAR), ft.Text(text, style=text_style(TYPE.body), text_align=ft.TextAlign.CENTER)],
        ),
    )


@ft.component
def Results():
    shell = use_shell()
    store = shell.search
    state = store.state
    subscribe(state)
    subscribe(shell.tasks.index)  # queue badges follow tasks coming and going
    if not state.searched:
        return _empty(AppIcon.SEARCH, tr("Search Iwara or Oreno3D, or leave the box empty to browse the latest videos.",
                                         "搜索 Iwara 或 Oreno3D；留空直接搜索可浏览最新视频。",
                                         "Iwara または Oreno3D を検索します。空欄なら最新の動画を表示します。"))
    if not store.cards:
        if state.loading:
            return ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.ProgressRing())
        return _empty(AppIcon.EMPTY, tr("Nothing found", "没有找到结果", "見つかりませんでした"))

    def open_card(card: CardView) -> None:
        item = store.items.get(card.key)
        if card.kind == KIND_AUTHOR:
            shell.open(routes.route(routes.AUTHOR, getattr(item, "username", "") or card.cover_key))
        elif card.kind == KIND_PLAYLIST:
            store.set_scope("playlists")
            store.set_keyword(getattr(item, "source_url", "") or card.cover_key)
            shell.page.run_task(store.search)
        elif card.history_id:
            shell.details.remember(item, card.history_id)
            kind = "image" if card.kind == KIND_IMAGE else "video"
            shell.open(routes.route(routes.DETAIL, kind, card.history_id))
        else:
            notice(shell.page, "info", tr("Finding this video on Iwara…", "正在 Iwara 上查找这个视频…",
                                          "Iwara で動画を探しています…"))
            if getattr(item, "source_kind", "") == "oreno3d":
                shell.page.run_task(resolve_and_open, item)

    async def resolve_and_open(item) -> None:
        """Oreno3D results that resolve on demand: find the Iwara video, then open it."""
        history_id, error = await store.resolve_one(item)
        if history_id:
            shell.details.remember(item, history_id)
            shell.open(routes.route(routes.DETAIL, "video", history_id))
        else:
            notice(shell.page, "warning", tr("Not found on Iwara", "未在 Iwara 上找到", "Iwara で見つかりません"),
                   error)

    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT

    def end_reached() -> None:
        shell.page.run_task(store.load_more)

    grid = VideoGrid(
        store.cards,
        selected=state.selected,
        selecting=state.selecting,
        badges=card_badges(shell, store.cards, store.statuses),
        on_open=open_card,
        on_toggle=store.toggle,
        on_end_reached=end_reached if compact and state.has_next else None,
        menu=VideoMenu(shell, scope="search", item_for=lambda c: store.items.get(c.key), on_select=store.toggle),
    )
    if compact and state.loading:
        return ft.Column(expand=True, spacing=Space.SM, controls=[
            grid, ft.Row([ft.ProgressRing(width=Size.ICON_LG, height=Size.ICON_LG)], alignment=ft.MainAxisAlignment.CENTER)
        ])
    return grid


def _history_menu(shell) -> ft.Control:
    store = shell.search
    history = store.state.history
    items = [
        ft.PopupMenuItem(content=search_history_label(entry), icon=AppIcon.HISTORY,
                         on_click=lambda _e, entry=entry: store.apply_history(entry))
        for entry in history[:12]
    ] or [ft.PopupMenuItem(content=tr("No recent searches", "还没有搜索记录", "検索履歴はありません"), disabled=True)]
    return ft.PopupMenuButton(icon=AppIcon.HISTORY, tooltip=tr("Recent searches", "搜索记录", "検索履歴"), items=items)


@ft.component
def SearchPage(destination: str, path: str):
    shell = use_shell()
    subscribe(shell.search.state)

    def first_visit() -> None:
        if not shell.search.state.searched:
            shell.search.auto_search()  # opening the page shows results, as the classic page does

    ft.use_effect(first_visit, [])
    short = shell.state.layout.height_class == layout.HEIGHT_COMPACT
    # Landscape phones: chips and the result line share a row so the grid keeps some height.
    controls = [QueryBar(), ScopeChips(trailing=StatusRow(inline=True)) if short else ScopeChips()]
    if not short:
        controls.append(StatusRow())
    body = ft.Column(
        expand=True,
        spacing=Space.SM if short else Space.MD,
        controls=[*controls, SelectionBar(), Results()],
    )
    return PageFrame(title=routes.label(routes.SEARCH), body=body, actions=[_history_menu(shell)])
