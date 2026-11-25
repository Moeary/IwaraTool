"""The library (docs/flet_ui_plan.md 7.7): downloaded works.

Phones: list rows (thumbnail, title, "author · date · quality"); a row opens
``/library/<id>``.  Wider windows: a table with sortable columns (a ``ListView``
of rows under a fixed header, built a batch at a time), and from
``SIDE_MIN_WIDTH`` on the chosen record in a side panel.  The cover view is the
shared ``VideoGrid``.  Removing records can be undone from the notice.
"""
from __future__ import annotations

import os

import flet as ft

from ....config import app_config
from ....i18n import tr
from ....presentation.history import library as lib
from ... import layout, routes
from ...components.cover import Cover
from ...components.page_frame import PageFrame
from ...components.status_badge import BadgeKind, status_badge
from ...components.video_actions import VideoMenu
from ...components.video_grid import VideoGrid
from ...icons import AppIcon, StateIcon
from ...services.dialogs import confirm, prompt_text
from ...services.notify import notice
from ...stores.app_state import subscribe, use_shell
from ...stores.library import VIEW_COVER, VIEW_LIST, LibraryStore
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

SCOPE = "library"
THUMB_W = 96
ROW_H = 64
TABLE_ROW_H = 44
END_MARGIN = 600
SIDE_PANEL = 360
SIDE_MIN_WIDTH = 1180  # content width from which the record panel sits beside the table
AUTHOR_PANE = 220
# Table columns after the title: (key, width, right-aligned)
COLUMNS = (("author", 140, False), ("quality", 80, False), ("published", 104, False),
           ("likes", 72, True), ("views", 80, True), ("downloaded", 150, False))

_BADGES = {"moved": BadgeKind.MOVED, "remote": BadgeKind.REMOTE}
TITLE_MIN = 240
DROP_ORDER = ("quality", "published", "likes", "views", "author")  # first to go when the table is narrow
FIXED_W = 40 + 76 + 2 * Space.SM  # checkbox, state, row padding


def table_columns(width: float) -> tuple:
    """The columns that leave the title at least ``TITLE_MIN`` wide."""
    columns = list(COLUMNS)
    for key in DROP_ORDER:
        used = FIXED_W + sum(w for _k, w, _r in columns) + Space.MD * (len(columns) + 2)
        if width - used >= TITLE_MIN:
            break
        columns = [c for c in columns if c[0] != key]
    return tuple(columns)


def _badge(store: LibraryStore, record: dict, page: ft.Page) -> ft.Control | None:
    label, key, _detail = store.state_of(record)
    kind = _BADGES.get(key)
    return status_badge(kind, palette_for(page), text=label) if kind else None


def _menu(shell, store: LibraryStore) -> VideoMenu:
    return VideoMenu(shell, scope=SCOPE, download=False,
                     item_for=lambda card: lib.record_video(store.by_id.get(card.history_id, {})) if card.history_id in store.by_id else None,
                     on_select=lambda card: store.toggle(card.history_id))


def open_record(shell, video_id: str) -> None:
    store = shell.library
    if shell.state.layout.content_width >= SIDE_MIN_WIDTH:
        store.focus(video_id)
    else:
        shell.open(routes.route(routes.LIBRARY, video_id))


# ── actions ──────────────────────────────────────────────────────────────────


async def remove_records(shell, video_ids: list[str]) -> None:
    """Undoable: no question first, the notice offers "Undo" (7.7)."""
    shell.library.remove(video_ids)
    if shell.state.path.startswith(routes.route(routes.LIBRARY) + "/"):
        shell.back()


async def clean_moved(shell, selected: bool = False) -> None:
    store = shell.library
    page = shell.page
    if selected:
        ids = store.selected_moved()
        if not ids:
            return
        title, question, informative, button = lib.clean_selected_texts(len(ids))
    else:
        ids = None
        count = len(lib.moved_ids(store.records, store.state_of))
        if not count:
            notice(page, "success", *lib.nothing_to_clean_notice())
            return
        title, question, informative, button = lib.sync_texts(count)
    if not await confirm(page, title, question, informative=informative, confirm_label=button, danger=True):
        return
    result = await store.clean_moved(ids)
    notice(page, "success", *(lib.cleaned_selected_notice(int(result.get("removed", 0) or 0)) if selected
                               else lib.synced_notice(result)))


async def rename_record(shell, video_id: str) -> None:
    store = shell.library
    record = store.record(video_id) or {}
    path = str(record.get("file_path", "") or "")
    if not path or not os.path.isfile(path):
        notice(shell.page, "error", lib.operation_failed_title(), lib.missing_file_text())
        return
    title, label, button = lib.rename_texts()
    name = await prompt_text(shell.page, title, label, confirm_label=button, value=os.path.basename(path))
    if not name or not name.strip():
        return
    ok, message = await store.rename(video_id, name)
    notice(shell.page, "success" if ok else "error", lib.renamed_title() if ok else lib.operation_failed_title(), message)


async def export_csv(shell) -> None:
    store = shell.library
    page = shell.page
    if not store.visible:
        notice(page, "warning", lib.nothing_to_export_text())
        return
    path = await ft.FilePicker().save_file(dialog_title=lib.export_title(), file_name="history_export.csv",
                                           initial_directory=app_config.app_data_dir, allowed_extensions=["csv"])
    if not path:
        return
    try:
        count = await store.export(path)
    except OSError as exc:
        notice(page, "error", lib.export_failed_title(), str(exc))
        return
    notice(page, "success", *lib.exported_notice(count, path))


# ── the record (side panel / its own page) ───────────────────────────────────


@ft.component
def RecordPanel(video_id: str, *, wide: bool = True):
    shell = use_shell()
    store = shell.library
    subscribe(store.state)
    page = shell.page
    palette = palette_for(page)
    actions, set_actions = ft.use_state(list)
    record = store.record(video_id)
    menu = _menu(shell, store)
    card = store.card(record) if record else None

    async def load_actions() -> None:
        set_actions(await menu.actions(card) if card else [])

    ft.use_effect(lambda: page.run_task(load_actions), [video_id, store.state.revision])
    if record is None or card is None:
        return ft.Container(expand=True, alignment=ft.Alignment.CENTER,
                            content=ft.Text(lib.missing_record_text(), style=text_style(TYPE.body)))
    label, key, detail = store.state_of(record)
    width = SIDE_PANEL - 2 * Space.MD if wide else min(shell.state.layout.body_width(layout.MAX_WIDTH_FORM), 720)

    def run(action) -> None:
        page.run_task(menu.run, card, action)

    def info(name: str, value: str) -> ft.Control | None:
        if not value:
            return None
        return ft.Column(spacing=0, controls=[
            ft.Text(name, style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            ft.Text(value, style=text_style(TYPE.body), selectable=True),
        ])

    labels = lib.column_labels()
    rows = [info(labels["state"], f"{label} — {detail}"),
            info(labels["author"], str(record.get("author", "") or "")),
            info(labels["quality"], str(record.get("quality", "") or "")),
            info(labels["published"], str(record.get("published_at", "") or "")[:10]),
            info(f"{labels['views']} / {labels['likes']}", f"{lib.count_text(record.get('views'))} / {lib.count_text(record.get('likes'))}"),
            info(labels["downloaded"], str(record.get("downloaded_at", "") or "")),
            info(labels["path"], str(record.get("file_path", "") or record.get("remote_path", "") or "")),
            info("ID", video_id)]
    # Playing is what a downloaded record is for: first and filled; the rest keep the menu order.
    ordered = sorted(actions, key=lambda action: action.id != "play")
    buttons: list[ft.Control] = [
        (ft.FilledButton if action.id == "play" else ft.OutlinedButton)(
            content=action.label, on_click=lambda _e, action=action: run(action))
        for action in ordered
    ]
    buttons.append(ft.OutlinedButton(content=lib.rename_texts()[2], icon=AppIcon.EDIT,
                                     on_click=lambda _e: page.run_task(rename_record, shell, video_id)))
    buttons.append(ft.OutlinedButton(content=lib.remove_button(), icon=AppIcon.DELETE,
                                     on_click=lambda _e: page.run_task(remove_records, shell, [video_id])))
    content = ft.Column(spacing=Space.MD, scroll=ft.ScrollMode.AUTO, expand=True, controls=[
        Cover("video", card.cover_key, card.cover_url, width, card.cover_local, icon=AppIcon.VIDEO),
        ft.Text(card.title, style=text_style(TYPE.section_title_compact), selectable=True),
        ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=buttons),
        *[row for row in rows if row is not None],
    ])
    if not wide:
        return content
    return ft.Container(width=SIDE_PANEL, padding=Space.MD, border_radius=Radius.CARD, bgcolor=palette.card, content=content)


# ── list rows (phones) ───────────────────────────────────────────────────────


def _list_row(shell, store: LibraryStore, record: dict, selecting: bool) -> ft.Control:
    page = shell.page
    palette = palette_for(page)
    video_id = str(record.get("video_id", "") or "")
    card = store.card(record)
    selected = video_id in store.state.selected
    badge = _badge(store, record, page)
    trailing: list[ft.Control] = [badge] if badge else []
    if selecting:
        trailing.append(ft.Icon(StateIcon.SELECT.active if selected else StateIcon.SELECT.rest, size=Size.ICON_LG,
                                color=palette.primary if selected else palette.on_surface_variant))

    def tap(_e) -> None:
        store.toggle(video_id) if selecting else open_record(shell, video_id)

    return ft.Container(
        key=video_id,
        height=ROW_H,
        border_radius=Radius.CONTROL,
        bgcolor=palette.primary_container if selected else None,
        padding=ft.Padding.symmetric(horizontal=Space.XS, vertical=Space.XXS),
        on_click=tap,
        on_long_press=lambda _e: store.toggle(video_id),
        content=ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            Cover("video", card.cover_key, card.cover_url, THUMB_W, card.cover_local, icon=AppIcon.VIDEO),
            ft.Column(spacing=0, expand=True, alignment=ft.MainAxisAlignment.CENTER, controls=[
                ft.Text(card.title, style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(lib.record_line(record), style=text_style(TYPE.meta, color=palette.on_surface_variant),
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ]),
            *trailing,
        ]),
    )


# ── table (wider windows) ────────────────────────────────────────────────────


def _header(shell, store: LibraryStore, columns: tuple) -> ft.Control:
    palette = palette_for(shell.page)
    state = store.state
    labels = lib.column_labels()

    def cell(key: str, width: float | None, right: bool = False) -> ft.Control:
        arrow = [ft.Icon(AppIcon.SORT_DESC if state.reverse else AppIcon.SORT_ASC, size=Size.ICON_SM)] if state.sort == key else []
        return ft.Container(
            width=width, expand=width is None, on_click=lambda _e: store.set_sort(key),
            content=ft.Row(spacing=Space.XXS, tight=True,
                           alignment=ft.MainAxisAlignment.END if right else ft.MainAxisAlignment.START,
                           controls=[ft.Text(labels[key], style=text_style(TYPE.meta, color=palette.on_surface_variant),
                                             max_lines=1), *arrow]),
        )

    every = bool(store.visible) and len(state.selected) >= len(store.visible)
    return ft.Container(
        height=TABLE_ROW_H, padding=ft.Padding.symmetric(horizontal=Space.SM),
        border=ft.Border.only(bottom=ft.BorderSide(1, palette.outline_variant)),
        content=ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Checkbox(value=every, on_change=lambda _e: store.clear_selection() if every else store.select_all()),
            cell("state", 76), cell("title", None),
            *[cell(key, width, right) for key, width, right in columns],
        ]),
    )


def _table_row(shell, store: LibraryStore, record: dict, index: int, columns: tuple) -> ft.Control:
    page = shell.page
    palette = palette_for(page)
    state = store.state
    video_id = str(record.get("video_id", "") or "")
    selected = video_id in state.selected
    focused = video_id == state.focused
    label, key, detail = store.state_of(record)
    values = {
        "author": str(record.get("author", "") or ""),
        "quality": str(record.get("quality", "") or ""),
        "published": str(record.get("published_at", "") or "")[:10],
        "likes": lib.count_text(record.get("likes")),
        "views": lib.count_text(record.get("views")),
        "downloaded": str(record.get("downloaded_at", "") or "")[:16],
    }
    badge = _badge(store, record, page)
    return ft.Container(
        key=video_id,
        height=TABLE_ROW_H,
        padding=ft.Padding.symmetric(horizontal=Space.SM),
        border_radius=Radius.CONTROL,
        bgcolor=palette.primary_container if focused else (palette.field if index % 2 else None),
        on_click=lambda _e: open_record(shell, video_id),
        content=ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Checkbox(value=selected, on_change=lambda _e: store.toggle(video_id)),
            ft.Container(width=76, tooltip=detail, content=badge or ft.Text(label, style=text_style(TYPE.meta))),
            ft.Text(str(record.get("title", "") or video_id), style=text_style(TYPE.body), expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(record.get("title", "") or "")),
            *[ft.Text(values[key], width=width, style=text_style(TYPE.meta), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                      text_align=ft.TextAlign.RIGHT if right else ft.TextAlign.LEFT)
              for key, width, right in columns],
        ]),
    )


def _scrolled(store: LibraryStore):
    def handler(e: ft.OnScrollEvent) -> None:
        if e.max_scroll_extent and e.pixels >= e.max_scroll_extent - END_MARGIN:
            store.more()
    return handler


# ── the page ─────────────────────────────────────────────────────────────────


def _choice_menu(choices, current, on_pick, icon, tooltip: str = "") -> ft.Control:
    label = next((text for key, text in choices if key == current), "")
    return ft.PopupMenuButton(
        tooltip=tooltip or None,
        content=ft.Container(padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.SM), content=ft.Row(
            tight=True, spacing=Space.XS, controls=[ft.Icon(icon, size=Size.ICON), ft.Text(label, style=text_style(TYPE.label)),
                                                    ft.Icon(StateIcon.EXPAND.rest, size=Size.ICON)])),
        items=[ft.PopupMenuItem(content=text, checked=key == current, on_click=lambda _e, key=key: on_pick(key))
               for key, text in choices],
    )


def _toolbar(shell, store: LibraryStore, compact: bool) -> ft.Control:
    state = store.state
    search = ft.TextField(value=state.query, hint_text=lib.search_placeholder(), prefix_icon=AppIcon.SEARCH, dense=True,
                          expand=True, on_change=lambda e: store.set_query(e.control.value or ""))
    field = _choice_menu(lib.search_field_choices(), lib.SEARCH_FIELDS[state.search_field], store.set_field, AppIcon.TUNE)
    states = _choice_menu(lib.state_choices(), state.state_filter, store.set_state_filter, AppIcon.FILTER)
    sort = ft.PopupMenuButton(icon=AppIcon.SORT, tooltip=tr("Sort", "排序", "並び替え"), items=[
        ft.PopupMenuItem(content=text, checked=key == state.sort, on_click=lambda _e, key=key: store.set_sort(key))
        for key, text in lib.sort_choices()
    ])
    view = ft.IconButton(
        icon=AppIcon.GRID_VIEW if state.view == VIEW_LIST else AppIcon.LIST_VIEW,
        tooltip=dict(lib.view_choices())[VIEW_COVER if state.view == VIEW_LIST else VIEW_LIST],
        on_click=lambda _e: store.set_view(VIEW_COVER if state.view == VIEW_LIST else VIEW_LIST),
    )
    if compact:
        return ft.Column(spacing=Space.XS, controls=[
            ft.Row(controls=[search, view]),
            ft.Row(spacing=0, controls=[field, states, sort]),
        ])
    return ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[search, field, states, sort, view])


def _scopes(shell, store: LibraryStore, author_menu: bool) -> ft.Control:
    state = store.state
    chips: list[ft.Control] = [
        ft.Chip(label=text, tooltip=tip or None, selected=key == state.scope, show_checkmark=False,
                on_select=lambda _e, key=key: store.set_scope(key))
        for key, text, tip in lib.scope_choices()
    ]
    if state.scope == lib.SCOPE_AUTHOR and author_menu:
        current = state.author or lib.all_authors_text(len(store.records))
        chips.append(ft.PopupMenuButton(
            content=ft.Container(padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.SM), content=ft.Row(
                tight=True, spacing=Space.XS, controls=[ft.Icon(AppIcon.AUTHOR, size=Size.ICON),
                                                        ft.Text(current, style=text_style(TYPE.label), max_lines=1),
                                                        ft.Icon(StateIcon.EXPAND.rest, size=Size.ICON)])),
            items=[ft.PopupMenuItem(content=lib.all_authors_text(len(store.records)), on_click=lambda _e: store.set_author("")),
                   *[ft.PopupMenuItem(content=f"{name}  ({count})", checked=name == state.author,
                                      on_click=lambda _e, name=name: store.set_author(name)) for name, count in store.authors]],
        ))
    return ft.Row(spacing=Space.SM, scroll=ft.ScrollMode.AUTO, controls=chips)


def _author_pane(shell, store: LibraryStore) -> ft.Control:
    palette = palette_for(shell.page)
    state = store.state

    def row(name: str, text: str) -> ft.Control:
        chosen = name == state.author
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.SM), border_radius=Radius.CONTROL,
            bgcolor=palette.primary_container if chosen else None, on_click=lambda _e: store.set_author(name),
            content=ft.Text(text, style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=name or None),
        )

    order = ft.Row(spacing=Space.XS, controls=[
        ft.Chip(label=tr("Most videos", "作品数", "作品数"), selected=state.author_sort == lib.AUTHOR_SORT_COUNT,
                show_checkmark=False, on_select=lambda _e: store.set_author_sort(lib.AUTHOR_SORT_COUNT)),
        ft.Chip(label=tr("Name", "名称", "名前"), selected=state.author_sort == lib.AUTHOR_SORT_NAME,
                show_checkmark=False, on_select=lambda _e: store.set_author_sort(lib.AUTHOR_SORT_NAME)),
    ])
    return ft.Container(width=AUTHOR_PANE, content=ft.Column(expand=True, spacing=Space.SM, controls=[
        order,
        ft.ListView(expand=True, spacing=2, build_controls_on_demand=True, controls=[
            row("", lib.all_authors_text(len(store.records))),
            *[row(name, f"{name}  ({count})") for name, count in store.authors],
        ]),
    ]))


def _selection_bar(shell, store: LibraryStore) -> ft.Control | None:
    state = store.state
    if not state.selected:
        return None
    moved = len(store.selected_moved())
    return ft.Row(spacing=Space.SM, wrap=False, scroll=ft.ScrollMode.AUTO, controls=[
        ft.Text(tr(f"{len(state.selected)} selected", f"已选 {len(state.selected)} 项", f"{len(state.selected)} 件選択"),
                style=text_style(TYPE.label)),
        ft.FilledTonalButton(content=tr(f"Delete records ({len(state.selected)})", f"删除记录（{len(state.selected)}）",
                                        f"履歴を削除（{len(state.selected)}）"), icon=AppIcon.DELETE,
                             on_click=lambda _e: shell.page.run_task(remove_records, shell, list(state.selected))),
        *([ft.OutlinedButton(content=lib.clean_selected_texts(moved)[0] + f" ({moved})", icon=AppIcon.CLEAN,
                             on_click=lambda _e: shell.page.run_task(clean_moved, shell, True))] if moved else []),
        ft.TextButton(content=tr("Select all", "全选", "すべて選択"), icon=AppIcon.SELECT_ALL, on_click=lambda _e: store.select_all()),
        ft.TextButton(content=tr("Clear selection", "取消选择", "選択を解除"), icon=AppIcon.CLOSE,
                      on_click=lambda _e: store.clear_selection()),
    ])


@ft.component
def LibraryOverview():
    shell = use_shell()
    store = shell.library
    state = store.state
    subscribe(state)
    view = shell.state.layout
    compact = view.width_class == layout.WIDTH_COMPACT
    palette = palette_for(shell.page)
    ft.use_effect(lambda: shell.page.run_task(store.load), [])

    menu = ft.PopupMenuButton(icon=AppIcon.MORE, tooltip=tr("More", "更多", "その他"), items=[
        ft.PopupMenuItem(content=lib.clean_moved_text(), icon=AppIcon.CLEAN, on_click=lambda _e: shell.page.run_task(clean_moved, shell)),
        ft.PopupMenuItem(content=lib.export_csv_text(), icon=AppIcon.EXPORT, on_click=lambda _e: shell.page.run_task(export_csv, shell)),
    ])
    refresh = ft.IconButton(icon=AppIcon.REFRESH, tooltip=lib.reload_tooltip(), disabled=state.loading,
                            on_click=lambda _e: shell.page.run_task(store.load))
    title = routes.label(routes.LIBRARY)
    side = view.content_width >= SIDE_MIN_WIDTH
    author_pane = state.scope == lib.SCOPE_AUTHOR and view.two_pane
    selection = _selection_bar(shell, store)
    top: list[ft.Control] = [
        ft.Text(lib.summary_text(store.counts), style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=2),
        _toolbar(shell, store, compact),
        _scopes(shell, store, author_menu=not author_pane),
    ]
    if selection is not None:
        top.append(selection)

    shown = store.shown()
    panel = side and state.focused in store.by_id
    main_width = (view.body_width() - (SIDE_PANEL + Space.LG if panel else 0)
                  - (AUTHOR_PANE + 1 + 2 * Space.LG if author_pane else 0))
    if not state.loaded:
        body: ft.Control = ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.ProgressRing())
    elif not store.visible:
        body = ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.Column(
            tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=Space.MD, controls=[
                ft.Icon(AppIcon.EMPTY, size=Size.ICON_LG * 2, color=palette.on_surface_variant),
                ft.Text(lib.empty_text(store.filtered or bool(store.records)), style=text_style(TYPE.body),
                        text_align=ft.TextAlign.CENTER),
            ]))
    elif state.view == VIEW_COVER:
        badges = {}
        for record in shown:
            label, key, _d = store.state_of(record)
            if key in _BADGES:
                badges[f"record:{record.get('video_id')}"] = (_BADGES[key], label)
        cards = [store.card(r) for r in shown]
        body = VideoGrid(
            cards,
            selected=frozenset(f"record:{i}" for i in state.selected),
            selecting=bool(state.selected),
            badges=badges,
            on_open=lambda card: open_record(shell, card.history_id),
            on_toggle=lambda card: store.toggle(card.history_id),
            on_end_reached=store.more if state.limit < len(store.visible) else None,
            menu=_menu(shell, store),
            width=main_width,
        )
    elif compact:
        selecting = bool(state.selected)
        body = ft.ListView(expand=True, spacing=Space.XS, build_controls_on_demand=True, on_scroll=_scrolled(store),
                           scroll_interval=100, padding=ft.Padding.only(bottom=Space.XL),
                           controls=[_list_row(shell, store, r, selecting) for r in shown])
    else:
        columns = table_columns(main_width)
        body = ft.Column(expand=True, spacing=0, controls=[
            _header(shell, store, columns),
            ft.ListView(expand=True, build_controls_on_demand=True, item_extent=TABLE_ROW_H, on_scroll=_scrolled(store),
                        scroll_interval=100, controls=[_table_row(shell, store, r, i, columns) for i, r in enumerate(shown)]),
        ])
    row: list[ft.Control] = []
    if author_pane:
        row += [_author_pane(shell, store), ft.VerticalDivider(width=1)]
    row.append(ft.Container(expand=True, content=body))
    if panel:
        row.append(RecordPanel(state.focused, key=f"panel:{state.focused}"))
    content = ft.Column(expand=True, spacing=Space.SM, controls=[
        *top, ft.Row(expand=True, spacing=Space.LG, vertical_alignment=ft.CrossAxisAlignment.STRETCH, controls=row),
    ])
    return PageFrame(title=title, body=content, actions=[refresh, menu])


@ft.component
def RecordPage(video_id: str):
    shell = use_shell()
    store = shell.library
    subscribe(store.state)
    ft.use_effect(lambda: None if store.state.loaded else shell.page.run_task(store.load), [])
    record = store.record(video_id)
    title = str((record or {}).get("title", "") or routes.label(routes.LIBRARY))
    body = (RecordPanel(video_id, wide=False, key=f"record:{video_id}") if store.state.loaded
            else ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.ProgressRing()))
    return PageFrame(title=title, body=body, max_width=layout.MAX_WIDTH_FORM)


@ft.component
def LibraryPage(destination: str, path: str):
    shell = use_shell()
    route = routes.parse(path)
    if route.parts:
        video_id = route.parts[0]
        if shell.state.layout.content_width >= SIDE_MIN_WIDTH:
            shell.library.state.focused = video_id
            return LibraryOverview()
        return RecordPage(video_id, key=f"page:{video_id}")
    return LibraryOverview()
