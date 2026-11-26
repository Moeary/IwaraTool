"""Subscriptions (docs/flet_ui_plan.md 7.4).

Phones and medium windows: the overview, one card per source with a shelf of
its latest works; a card opens ``/subscriptions/<id>``.  Wide windows: a
list-detail layout, the sources on the left and the chosen source's works on
the right (replacing the old "classic table").
"""
from __future__ import annotations

import flet as ft

from ....i18n import tr
from ....presentation.subscription import text as sub_text
from ....presentation.subscription.helpers import _source_url
from ....presentation.media import card_size
from ....presentation.media.cards import CardView
from ... import layout, routes
from ...components.cover_size import CoverSizeSlider
from ...components.list_detail import ListDetail
from ...components.cover import Cover
from ...components.download_sheet import open_download_sheet
from ...components.page_frame import PageFrame
from ...components.status_badge import BadgeKind
from ...components.video_actions import VideoMenu
from ...components.video_card import VideoCard, text_height
from ...components.video_grid import VideoGrid
from ...icons import AppIcon, NavIcon, StateIcon
from ...services.dialogs import confirm, prompt_text
from ...stores.app_state import subscribe, use_shell
from ...stores.subscriptions import SourceItems
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

SCOPE = "subscription"
SHELF_CARD = 168
LIST_PANE = 320  # the sources list's default width; the divider drags it
LIST_PANE_RANGE = (240, 520)
LIST_PANE_KEY = "flet_subscriptions_list_width"
AVATAR = 44

_BADGES = {
    "new": (BadgeKind.NEW, None),
    "queued": (BadgeKind.QUEUED, None),
    "downloaded": (BadgeKind.DOWNLOADED, None),
    "moved": (BadgeKind.MOVED, None),
    "remote": (BadgeKind.REMOTE, None),
}


def item_badges(model: SourceItems) -> dict[str, tuple[BadgeKind, str | None]]:
    badges = {key: _BADGES[state] for key, state in model.states.items() if state in _BADGES}
    for key, state in model.states.items():
        if state == "unavailable":
            badges[key] = (BadgeKind.FAILED, sub_text.choice_label(sub_text.state_filter_choices(), "unavailable"))
    return badges


def open_post(shell, card: CardView, videos: dict) -> None:
    shell.details.remember(videos.get(card.key), card.cover_key)
    shell.open(routes.route(routes.DETAIL, "video", card.cover_key))


def _avatar(source: dict, size: float) -> ft.Control:
    source_id = int(source.get("id", 0) or 0)
    return Cover("avatar", f"source-{source_id}", str(source.get("avatar_url", "") or ""), size,
                 str(source.get("avatar_path", "") or ""), aspect=1, round=True,
                 icon=AppIcon.AUTHOR if sub_text.source_is_author(source) else AppIcon.PLAYLIST)


def _choice_menu(choices: list[tuple[str, str]], current: str, on_pick, icon) -> ft.Control:
    return ft.PopupMenuButton(
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.SM),
            content=ft.Row(tight=True, spacing=Space.XS, controls=[
                ft.Icon(icon, size=Size.ICON),
                ft.Text(sub_text.choice_label(choices, current), style=text_style(TYPE.label)),
                ft.Icon(StateIcon.EXPAND.rest, size=Size.ICON),
            ]),
        ),
        items=[ft.PopupMenuItem(content=label, checked=key == current, on_click=lambda _e, key=key: on_pick(key))
               for key, label in choices],
    )


async def _delete(shell, source_ids: list[int]) -> None:
    store = shell.subscriptions
    question, _summary = sub_text.delete_confirmation(store.sources, source_ids)
    if await confirm(shell.page, sub_text.delete_title(), question, informative=sub_text.delete_informative(),
                     confirm_label=sub_text.delete_confirm_button(), danger=True):
        await store.remove(source_ids)
        if shell.state.path != routes.route(routes.SUBSCRIPTIONS):
            shell.back()


def _source_menu(shell, source: dict, works: list[ft.PopupMenuItem] | None = None) -> ft.Control:
    """The one "more" menu of a source: ``works`` (the shown works' actions) first, then the source's."""
    store = shell.subscriptions
    source_id = int(source.get("id", 0) or 0)
    enabled = sub_text.source_enabled(source)
    url = _source_url(source)
    items = [*works, ft.PopupMenuItem()] if works else [
        ft.PopupMenuItem(content=sub_text.mark_seen_text(), icon=AppIcon.CHECK,
                         on_click=lambda _e: shell.page.run_task(store.mark_source_seen, source_id)),
    ]
    items = [
        *items,
        ft.PopupMenuItem(content=sub_text.refresh_source_tooltip(), icon=AppIcon.REFRESH, disabled=store.state.refreshing,
                         on_click=lambda _e: shell.page.run_task(store.refresh, [source_id], ignore_disabled=True)),
        ft.PopupMenuItem(content=sub_text.pause_text(enabled), icon=AppIcon.PAUSE if enabled else AppIcon.PLAY,
                         on_click=lambda _e: shell.page.run_task(store.set_enabled, source_id, not enabled)),
    ]
    if sub_text.source_is_author(source):
        username = str(source.get("source_key", "") or "")
        items.append(ft.PopupMenuItem(content=tr("Author page", "作者主页", "作者ページ"), icon=AppIcon.AUTHOR,
                                      on_click=lambda _e: shell.open(routes.route(routes.AUTHOR, username))))
    if url:
        items.append(ft.PopupMenuItem(content=tr("Open on Iwara", "在 Iwara 打开", "Iwaraで開く"), icon=AppIcon.BROWSER,
                                      on_click=lambda _e: shell.page.run_task(ft.UrlLauncher().launch_url, url)))
    items += [
        ft.PopupMenuItem(),
        ft.PopupMenuItem(content=sub_text.delete_title(), icon=AppIcon.DELETE,
                         on_click=lambda _e: shell.page.run_task(_delete, shell, [source_id])),
    ]
    return ft.PopupMenuButton(icon=AppIcon.MORE, tooltip=tr("More", "更多", "その他"), items=items)


def _chips(shell, source: dict) -> list[ft.Control]:
    palette = palette_for(shell.page)
    chips: list[ft.Control] = []
    new = int(source.get("new_count", 0) or 0)
    if new:
        chips.append(ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XXS), border_radius=Radius.BADGE,
            bgcolor=palette.primary_container,
            content=ft.Text(sub_text.new_count_text(new), style=text_style(TYPE.badge, color=palette.on_primary_container)),
        ))
    if not sub_text.source_enabled(source):
        chips.append(ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XXS), border_radius=Radius.BADGE,
            bgcolor=palette.warning.bg,
            content=ft.Text(sub_text.paused_text(), style=text_style(TYPE.badge, color=palette.warning.fg)),
        ))
    return chips


# ── overview (one pane) ──────────────────────────────────────────────────────


@ft.component
def SourceCard(source: dict):
    shell = use_shell()
    store = shell.subscriptions
    palette = palette_for(shell.page)
    view = shell.state.layout
    source_id = int(source.get("id", 0) or 0)
    cards = store.recent.get(source_id, [])
    videos = store.recent_videos.get(source_id, {})

    def open_source(_e=None) -> None:
        shell.open(routes.route(routes.SUBSCRIPTIONS, source_id))

    head = ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        _avatar(source, AVATAR),
        ft.Column(spacing=0, expand=True, controls=[
            ft.Row(spacing=Space.SM, controls=[
                ft.Text(sub_text.source_title(source), style=text_style(TYPE.subtitle), max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                *_chips(shell, source),
            ]),
            ft.Text(sub_text.source_subtitle(source), style=text_style(TYPE.meta, color=palette.on_surface_variant),
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(sub_text.source_stats_text(source), style=text_style(TYPE.meta, color=palette.on_surface_variant),
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ]),
        ft.IconButton(icon=AppIcon.REFRESH, tooltip=sub_text.refresh_source_tooltip(), disabled=store.state.refreshing,
                      on_click=lambda _e: shell.page.run_task(store.refresh, [source_id], ignore_disabled=True)),
        _source_menu(shell, source),
    ])
    if cards:
        menu = VideoMenu(shell, scope=SCOPE, item_for=lambda c: videos.get(c.key))
        shelf: ft.Control = ft.ListView(
            horizontal=True, height=SHELF_CARD * 9 / 16 + text_height(), spacing=Space.MD,
            build_controls_on_demand=True,
            controls=[VideoCard(card, SHELF_CARD, shell.page, touch=view.is_touch,
                                on_open=lambda c: open_post(shell, c, videos), menu=menu, key=card.key) for card in cards],
        )
    else:
        shelf = ft.Text(sub_text.empty_source_text(), style=text_style(TYPE.meta, color=palette.on_surface_variant))
    return ft.Container(
        padding=Space.MD,
        border_radius=Radius.CARD,
        bgcolor=palette.card,
        on_click=open_source,
        content=ft.Column(spacing=Space.MD, controls=[head, shelf]),
    )


# ── one source's works ───────────────────────────────────────────────────────


@ft.component
def SourcePane(source_id: int, width: float | None = None, show_title: bool = True):
    shell = use_shell()
    store = shell.subscriptions
    subscribe(store.state)
    model = store.items_for(source_id)
    state = model.state
    subscribe(state)
    palette = palette_for(shell.page)
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    ft.use_effect(lambda: shell.page.run_task(model.load), [source_id])
    source = store.source(source_id)
    if source is None:
        return ft.Container(expand=True, alignment=ft.Alignment.CENTER,
                            content=ft.ProgressRing() if store.state.loading or not store.state.loaded
                            else ft.Text(sub_text.no_sources_text(), style=text_style(TYPE.body)))

    search = ft.TextField(
        value=state.query, hint_text=sub_text.title_search_placeholder(), prefix_icon=AppIcon.SEARCH, dense=True,
        expand=True, on_change=lambda e: model.set_query(e.control.value or ""),
    )
    menus = [
        _choice_menu(sub_text.state_filter_choices(), state.mode, model.set_mode, AppIcon.FILTER),
        _choice_menu(sub_text.item_sort_choices(), state.sort, model.set_sort, AppIcon.SORT),
        *([] if compact else [CoverSizeSlider()]),
    ]
    # A TextField cannot sit in a wrapping row (unbounded width): phones get two rows instead.
    filters: ft.Control = (
        ft.Column(spacing=Space.XS, controls=[ft.Row(controls=[search]), ft.Row(spacing=Space.XS, controls=menus)])
        if compact else
        ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[search, *menus])
    )
    download_count = len(model.download_ids())
    new_count = len(model.download_ids(only_new=True))

    def queue(only_new: bool) -> None:
        count = new_count if only_new else download_count
        if not count:
            return

        async def confirmed(rule_id: str) -> None:
            await store.download(model, rule_id=rule_id, only_new=only_new)

        open_download_sheet(shell, scope=SCOPE, title=sub_text.download_new_text(count) if only_new
                            else tr(f"Download ({count})", f"下载（{count}）", f"ダウンロード（{count}）"),
                            summary=sub_text.source_title(source), on_confirm=confirmed)

    works = [
        ft.PopupMenuItem(content=sub_text.download_new_text(new_count), icon=AppIcon.NEW, disabled=new_count == 0,
                         on_click=lambda _e: queue(True)),
        ft.PopupMenuItem(content=tr("Select all", "全选", "すべて選択"), icon=AppIcon.SELECT_ALL, on_click=lambda _e: model.select_all()),
        ft.PopupMenuItem(content=sub_text.mark_seen_text(), icon=AppIcon.CHECK,
                         on_click=lambda _e: shell.page.run_task(store.mark_seen, model)),
        ft.PopupMenuItem(content=sub_text.mark_moved_text(), icon=AppIcon.FOLDER, disabled=not state.selected,
                         on_click=lambda _e: shell.page.run_task(store.mark_moved, model)),
        ft.PopupMenuItem(content=sub_text.restore_moved_text(), icon=AppIcon.UNDO, disabled=not state.selected,
                         on_click=lambda _e: shell.page.run_task(store.restore_moved, model)),
    ]
    download = ft.FilledTonalButton(
        content=(tr(f"Download selected ({download_count})", f"下载选中（{download_count}）", f"選択を保存（{download_count}）")
                 if state.selected else tr(f"Download shown ({download_count})", f"下载当前（{download_count}）", f"表示中を保存（{download_count}）")),
        icon=AppIcon.DOWNLOAD, disabled=download_count == 0, on_click=lambda _e: queue(False),
    )
    clear = [ft.IconButton(icon=AppIcon.CLOSE, tooltip=tr("Clear selection", "取消选择", "選択を解除"),
                           on_click=lambda _e: model.clear_selection())] if state.selected else []
    # One header row: who, how many, the download button and one menu (maintainer, 2026-11-26: fewer buttons).
    head = ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        _avatar(source, AVATAR),
        ft.Column(spacing=0, expand=True, controls=[
            ft.Row(spacing=Space.SM, controls=[
                *([ft.Text(sub_text.source_title(source), style=text_style(TYPE.section_title_compact), max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS)] if show_title else []),
                *_chips(shell, source),
            ]),
            ft.Text(sub_text.source_stats_text(source), style=text_style(TYPE.meta, color=palette.on_surface_variant),
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ]),
        *([] if compact else [download]),
        *clear,
        _source_menu(shell, source, works),
    ])
    head_controls: list[ft.Control] = [head, filters, *([download] if compact else [])]
    if state.loading and not model.cards:
        body: ft.Control = ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.ProgressRing())
    elif not model.cards:
        body = ft.Container(expand=True, alignment=ft.Alignment.CENTER,
                            content=ft.Text(sub_text.empty_source_text(), style=text_style(TYPE.body, color=palette.on_surface_variant)))
    else:
        body = VideoGrid(
            model.cards,
            selected=state.selected,
            selecting=bool(state.selected),
            badges=item_badges(model),
            on_open=lambda c: open_post(shell, c, model.videos),
            on_toggle=model.toggle,
            menu=VideoMenu(shell, scope=SCOPE, item_for=lambda c: model.videos.get(c.key), on_select=model.toggle),
            width=width,
            cover_scale=card_size.PANE_SCALE,
        )
    return ft.Column(expand=True, spacing=Space.SM, controls=[*head_controls, body])


# ── the page ─────────────────────────────────────────────────────────────────


def _toolbar(shell) -> ft.Control:
    store = shell.subscriptions
    state = store.state
    return ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.TextField(value=state.query, hint_text=sub_text.source_search_placeholder(), prefix_icon=AppIcon.SEARCH,
                     dense=True, expand=True, on_change=lambda e: store.set_query(e.control.value or "")),
        _choice_menu(sub_text.source_sort_choices(), state.sort, store.set_sort, AppIcon.SORT),
    ])


def _progress(shell) -> ft.Control | None:
    state = shell.subscriptions.state
    if not state.refreshing:
        return None
    palette = palette_for(shell.page)
    value = state.refresh_done / state.refresh_total if state.refresh_total else None
    return ft.Column(spacing=Space.XS, controls=[
        ft.ProgressBar(value=value),
        ft.Text(state.refresh_line, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS),
    ])


def _empty(shell) -> ft.Control:
    store = shell.subscriptions
    if store.state.loading or not store.state.loaded:
        return ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.ProgressRing())
    return ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.Column(
        tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=Space.MD, controls=[
            ft.Icon(NavIcon.SUBSCRIPTIONS.rest, size=Size.ICON_LG * 2),
            ft.Text(sub_text.no_sources_text(), style=text_style(TYPE.body), text_align=ft.TextAlign.CENTER),
            ft.FilledButton(content=sub_text.add_title(), icon=AppIcon.ADD,
                            on_click=lambda _e: shell.page.run_task(_add, shell)),
        ]))


async def _add(shell) -> None:
    text = await prompt_text(shell.page, sub_text.add_title(), sub_text.add_prompt(), confirm_label=sub_text.add_button())
    if text and text.strip():
        await shell.subscriptions.add(text)


def _list_row(shell, source: dict, selected: bool) -> ft.Control:
    palette = palette_for(shell.page)
    store = shell.subscriptions
    source_id = int(source.get("id", 0) or 0)
    new = int(source.get("new_count", 0) or 0)
    missing = int(source.get("undownloaded_count", 0) or 0)
    trailing: list[ft.Control] = []
    if new:
        trailing.append(ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XXS), border_radius=Radius.BADGE,
            bgcolor=palette.primary_container,
            content=ft.Text(str(new), style=text_style(TYPE.badge, color=palette.on_primary_container)),
            tooltip=sub_text.new_count_text(new),
        ))
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.SM),
        border_radius=Radius.CONTROL,
        bgcolor=palette.primary_container if selected else None,
        on_click=lambda _e: store.select_source(source_id),
        content=ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            _avatar(source, 36),
            ft.Column(spacing=0, expand=True, controls=[
                ft.Text(sub_text.source_title(source), style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(tr(f"{missing} not downloaded", f"{missing} 个未下载", f"未保存 {missing}")
                        + ("" if sub_text.source_enabled(source) else "  ·  " + sub_text.paused_text()),
                        style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1),
            ]),
            *trailing,
        ]),
    )


@ft.component
def SubscriptionsOverview():
    shell = use_shell()
    store = shell.subscriptions
    state = store.state
    subscribe(state)
    view = shell.state.layout
    compact = view.width_class == layout.WIDTH_COMPACT
    ft.use_effect(lambda: shell.page.run_task(store.load), [])

    menu = ft.PopupMenuButton(icon=AppIcon.MORE, tooltip=tr("More", "更多", "その他"), items=[
        ft.PopupMenuItem(content=sub_text.import_followed_text(), icon=AppIcon.AUTHORS,
                         on_click=lambda _e: shell.page.run_task(store.import_followed)),
        ft.PopupMenuItem(content=tr("Refresh all", "全部刷新", "すべて更新"), icon=AppIcon.REFRESH, disabled=state.refreshing,
                         on_click=lambda _e: shell.page.run_task(store.refresh, None)),
    ])
    add = (ft.IconButton(icon=AppIcon.ADD, tooltip=sub_text.add_title(), on_click=lambda _e: shell.page.run_task(_add, shell))
           if compact else
           ft.FilledButton(content=sub_text.add_title(), icon=AppIcon.ADD, on_click=lambda _e: shell.page.run_task(_add, shell)))
    title = routes.label(routes.SUBSCRIPTIONS)
    sources = store.visible_sources()
    progress = _progress(shell)
    top: list[ft.Control] = []
    if store.sources and not compact:
        top.append(ft.Text(sub_text.overview_stats_text(store.sources),
                           style=text_style(TYPE.meta, color=palette_for(shell.page).on_surface_variant)))
    if progress is not None:
        top.append(progress)
    if not store.sources:
        return PageFrame(title=title, body=ft.Column(expand=True, controls=[*top, _empty(shell)]), actions=[add, menu])

    if view.two_pane:
        selected = state.selected_source
        left = ft.Column(expand=True, spacing=Space.SM, controls=[
            _toolbar(shell),
            ft.ListView(expand=True, spacing=2, controls=[_list_row(shell, s, int(s.get("id", 0) or 0) == selected) for s in sources]),
        ])

        def pane(width: float) -> ft.Control:
            return SourcePane(selected, width, key=f"pane:{selected}") if selected else ft.Container()

        body: ft.Control = ft.Column(expand=True, spacing=Space.SM, controls=[
            *top,
            ft.Container(expand=True, content=ListDetail(
                left, pane, total_width=view.body_width(layout.MAX_WIDTH_GRID), store_key=LIST_PANE_KEY,
                default=LIST_PANE, low=LIST_PANE_RANGE[0], high=LIST_PANE_RANGE[1], key="subscriptions")),
        ])
        return PageFrame(title=title, body=body, actions=[add, menu], max_width=layout.MAX_WIDTH_GRID)

    body = ft.Column(expand=True, spacing=Space.MD, controls=[
        *top, _toolbar(shell),
        ft.ListView(expand=True, spacing=Space.MD, padding=ft.Padding.only(bottom=Space.XL),
                    controls=[SourceCard(s, key=f"source:{s.get('id')}:{state.revision}") for s in sources]),
    ])
    return PageFrame(title=title, body=body, actions=[add, menu])


@ft.component
def SourcePage(source_id: int):
    shell = use_shell()
    store = shell.subscriptions
    subscribe(store.state)
    ft.use_effect(lambda: None if store.state.loaded else shell.page.run_task(store.load), [])
    source = store.source(source_id)
    title = sub_text.source_title(source) if source else routes.label(routes.SUBSCRIPTIONS)
    progress = _progress(shell)
    body = ft.Column(expand=True, spacing=Space.SM, controls=[
        *([progress] if progress is not None else []), SourcePane(source_id, show_title=False, key=f"pane:{source_id}"),
    ])
    return PageFrame(title=title, body=body)


@ft.component
def SubscriptionsPage(destination: str, path: str):
    shell = use_shell()
    route = routes.parse(path)
    if route.parts and route.parts[0].isdigit():
        source_id = int(route.parts[0])
        if shell.state.layout.two_pane:
            shell.subscriptions.state.selected_source = source_id
            return SubscriptionsOverview()
        return SourcePage(source_id, key=f"page:{source_id}")
    return SubscriptionsOverview()
