"""Home (docs/flet_ui_plan.md 7.1): rows of covers, each with its own tabs, age and refresh.

Phones get a horizontal shelf per row (a third card peeks out to show it
scrolls); wider windows show two rows of the cover grid.  "More ›" opens the
row in Search, or for the account feed a full grid at ``/home/<row>/<tab>``.
"""
from __future__ import annotations

import flet as ft

from ....core.home.feed import MODE_AUTHOR
from ....i18n import tr
from ....presentation.home import section_feed as rules
from ....presentation.media.cards import CardView
from ... import layout, routes
from ...components.download_sheet import open_download_sheet
from ...components.feed_grid import FeedGrid
from ...components.page_frame import PageFrame
from ...components.video_actions import VideoMenu
from ...components.video_card import VideoCard, text_height
from ...icons import AppIcon, StateIcon
from ...stores.app_state import subscribe, use_shell
from ...stores.home import HomeRow
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

SCOPE = "home"
SHELF_CARD = 168  # dp, phones (7.1)


def open_card(shell, card: CardView, videos: dict | None = None) -> None:
    shell.details.remember((videos or {}).get(card.key))
    kind = "image" if card.kind == "image" else "video"
    shell.open(routes.route(routes.DETAIL, kind, card.cover_key))


def show_more(shell, row: HomeRow) -> None:
    tab = row.feed.tab
    if not tab.opens_in_search:
        shell.open(routes.route(routes.HOME, row.section.id, tab.id))
        return
    if tab.mode == MODE_AUTHOR:
        shell.open(routes.route(routes.AUTHOR, tab.value))
        return
    request = tab.search_request()
    store = shell.search
    store.set_source("iwara")
    store.set_scope(str(request.get("scope") or "videos"))
    store.set_keyword(str(request.get("keyword") or ""))
    if request.get("sort"):
        store.set_sort(str(request["sort"]))
    shell.select(routes.SEARCH)
    shell.page.run_task(store.search)


def queue_row(shell, row: HomeRow) -> None:
    videos = row.queueable()
    if not videos:
        return

    async def confirmed(rule_id: str) -> None:
        await shell.downloads.stage_videos(videos, rule_id=rule_id, label=row.section.title)
        row.clear_selection()

    open_download_sheet(
        shell,
        scope=SCOPE,
        title=rules.queue_text(len(row.state.selected), len(videos)),
        summary=row.section.title,
        on_confirm=confirmed,
    )


# ── one row ──────────────────────────────────────────────────────────────────


def _header(shell, row: HomeRow, compact: bool) -> ft.Control:
    state = row.state
    palette = palette_for(shell.page)
    tabs = row.section.tabs
    fold = ft.Container(
        content=ft.Row(tight=True, spacing=Space.XS, controls=[
            ft.Text(row.section.title, style=text_style(TYPE.section_title_compact if compact else TYPE.section_title),
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Icon(StateIcon.EXPAND.rest if state.collapsed else StateIcon.EXPAND.active, size=Size.ICON,
                    color=palette.on_surface_variant),
        ]),
        padding=ft.Padding.symmetric(vertical=Space.XS),
        border_radius=Radius.CONTROL,
        ink=True,
        tooltip=rules.fold_tooltip(state.collapsed),
        on_click=lambda _e: row.toggle_fold(),
    )
    menu_items = [
        ft.PopupMenuItem(content=rules.refresh_tooltip(False), icon=AppIcon.REFRESH,
                         on_click=lambda _e: shell.page.run_task(row.reload, True)),
    ]
    if row.feed.tab.kind == "video":
        count = len(row.queueable())
        menu_items.insert(0, ft.PopupMenuItem(content=rules.queue_text(len(state.selected), count), icon=AppIcon.DOWNLOAD,
                                              disabled=count == 0, on_click=lambda _e: queue_row(shell, row)))
    if state.selected:
        menu_items.append(ft.PopupMenuItem(content=tr("Clear selection", "取消选择", "選択を解除"), icon=AppIcon.CLOSE,
                                           on_click=lambda _e: row.clear_selection()))
    trailing: list[ft.Control] = []
    if state.busy:
        trailing.append(ft.ProgressRing(width=Size.ICON_SM, height=Size.ICON_SM, stroke_width=2))
    elif state.age and not compact:
        trailing.append(ft.Text(state.age, style=text_style(TYPE.meta, color=palette.on_surface_variant),
                                tooltip=state.age_tooltip or None))
    if state.pending:
        trailing.append(ft.TextButton(content=rules.new_posts_text(), on_click=lambda _e: row.apply_pending()))
    trailing += [
        ft.PopupMenuButton(icon=AppIcon.MORE, tooltip=tr("More", "更多", "その他"), items=menu_items),
        ft.TextButton(content=row.feed.more_text(), on_click=lambda _e: show_more(shell, row)),
    ]
    rows: list[ft.Control] = [ft.Row(
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=Space.XS,
        controls=[ft.Container(expand=True, content=ft.Row([fold])), *trailing],
    )]
    if len(tabs) > 1 and not state.collapsed:
        rows.append(ft.Row(scroll=ft.ScrollMode.AUTO, spacing=Space.SM, controls=[
            ft.Chip(label=tab.label, selected=tab.id == state.tab_id, show_checkmark=False,
                    on_select=lambda _e, tab_id=tab.id: row.select_tab(tab_id))
            for tab in tabs
        ]))
    return ft.Column(spacing=Space.XS, controls=rows)


def _message(shell, row: HomeRow) -> ft.Control:
    state = row.state
    palette = palette_for(shell.page)
    controls: list[ft.Control] = [ft.Text(state.message, style=text_style(TYPE.body, color=palette.on_surface_variant))]
    if state.login_required:
        controls.append(ft.FilledButton(content=rules.login_button_text(), icon=AppIcon.LOGIN,
                                        on_click=lambda _e: shell.open_account()))
    if state.retry:
        controls.append(ft.OutlinedButton(content=rules.retry_text(), icon=AppIcon.REFRESH,
                                          on_click=lambda _e: shell.page.run_task(row.reload, True)))
    if state.loading:
        controls.insert(0, ft.ProgressRing(width=Size.ICON_LG, height=Size.ICON_LG))
    return ft.Container(padding=ft.Padding.symmetric(vertical=Space.LG), content=ft.Row(
        wrap=True, spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=controls))


@ft.component
def HomeRowView(row: HomeRow):
    shell = use_shell()
    state = row.state
    subscribe(state)
    view = shell.state.layout
    compact = view.width_class == layout.WIDTH_COMPACT
    header = _header(shell, row, compact)
    if state.collapsed:
        return header
    if not state.cards:
        return ft.Column(spacing=Space.SM, controls=[header, _message(shell, row)])

    menu = VideoMenu(shell, scope=SCOPE, item_for=lambda c: row.videos.get(c.key), on_select=row.toggle)

    def card(item: CardView, width: float) -> ft.Control:
        return VideoCard(item, width, shell.page, selected=item.key in state.selected, selecting=bool(state.selected),
                         touch=view.is_touch, on_open=lambda c: open_card(shell, c, row.videos), on_toggle=row.toggle,
                         menu=menu, key=item.key)

    if compact:
        height = SHELF_CARD * 9 / 16 + text_height()
        body: ft.Control = ft.ListView(
            horizontal=True,
            height=height,
            spacing=Space.MD,
            build_controls_on_demand=True,
            controls=[card(item, SHELF_CARD) for item in state.cards],
        )
    else:
        metrics = layout.grid_metrics(view.body_width(), view.width_class, text_height=text_height())
        shown = state.cards[: metrics.columns * 2]
        body = ft.Column(spacing=metrics.gap, controls=[
            ft.Row(spacing=metrics.gap, vertical_alignment=ft.CrossAxisAlignment.START,
                   controls=[card(item, metrics.tile_width) for item in shown[start:start + metrics.columns]])
            for start in range(0, len(shown), metrics.columns)
        ])
    return ft.Column(spacing=Space.SM, controls=[header, body])


# ── the page ─────────────────────────────────────────────────────────────────


@ft.component
def HomeFeed():
    shell = use_shell()
    store = shell.home

    async def first_show() -> None:
        store.refresh_ages()
        await store.reload_all()

    ft.use_effect(first_show, [])
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    menu = ft.PopupMenuButton(
        icon=AppIcon.MORE,
        tooltip=tr("More", "更多", "その他"),
        items=[
            ft.PopupMenuItem(content=tr("Refresh all", "全部刷新", "すべて更新"), icon=AppIcon.REFRESH,
                             on_click=lambda _e: shell.page.run_task(store.reload_all, True)),
            ft.PopupMenuItem(content=tr("Fold all", "全部折叠", "すべて折りたたむ"), icon=StateIcon.EXPAND.active,
                             on_click=lambda _e: store.fold_all(True)),
            ft.PopupMenuItem(content=tr("Unfold all", "全部展开", "すべて展開"), icon=StateIcon.EXPAND.rest,
                             on_click=lambda _e: store.fold_all(False)),
            ft.PopupMenuItem(content=tr("Customize rows…", "自定义栏目…", "行をカスタマイズ…"), icon=AppIcon.TUNE,
                             on_click=lambda _e: shell.open(routes.route(routes.SETTINGS, "home"))),
        ],
    )
    body = ft.ListView(
        expand=True,
        spacing=Space.XL if not compact else Space.LG,
        padding=ft.Padding.only(bottom=Space.XL),
        controls=[HomeRowView(row, key=row.section.id) for row in store.rows],
    )
    return PageFrame(title=routes.label(routes.HOME), body=body, actions=[menu])


@ft.component
def HomeBrowse(section_id: str, tab_id: str):
    """A row in full (the account feed has no Search equivalent): the grid, page after page."""

    shell = use_shell()
    row = shell.home.row(section_id)
    if row is None:
        return PageFrame(title=routes.label(routes.HOME), body=ft.Container())
    return PageFrame(title=row.section.title, body=FeedGrid(row.section, tab_id, row.feed.rating, SCOPE))


@ft.component
def HomePage(destination: str, path: str):
    route = routes.parse(path)
    if len(route.parts) >= 2:
        return HomeBrowse(route.parts[0], route.parts[1])
    return HomeFeed()
