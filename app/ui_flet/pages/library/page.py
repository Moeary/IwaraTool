"""The library (docs/flet_ui_plan.md 7.7): downloaded works.

Phones: list rows (thumbnail, title, "author · date · quality"); a row opens
``/library/<id>``.  Wider windows: a table with sortable columns (a ``ListView``
of rows under a fixed header, built a batch at a time), and from
``SIDE_MIN_WIDTH`` on the chosen record in a side panel.  The cover view is the
shared ``VideoGrid``.  Removing records can be undone from the notice.
"""
from __future__ import annotations


import flet as ft

from ....config import app_config
from ....i18n import tr
from ....presentation.history import library as lib
from ... import layout, routes
from ...components.cover import Cover
from ...components.page_frame import PageFrame
from ...components.video_grid import VideoGrid
from ...icons import AppIcon, StateIcon
from ...services.dialogs import confirm
from ...services.notify import notice
from ...stores.app_state import subscribe, use_shell
from ...stores.library import VIEW_COVER, VIEW_LIST, LibraryStore
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space
from .filters import active_tag_row, filter_button, filter_sheet
from .record import (
    _BADGES,
    SIDE_MIN_WIDTH,
    SIDE_PANEL,
    CoverViewer,
    RecordPanel,
    badge_for,
    open_record,
    record_menu,
    remove_records,
    toggle_watched,
)

SCOPE = "library"
THUMB_W = 96
ROW_H = 64
TABLE_ROW_H = 44
END_MARGIN = 600
AUTHOR_PANE = 220
# Table columns after the title: (key, width, right-aligned)
COLUMNS = (("author", 140, False), ("quality", 80, False), ("published", 104, False),
           ("likes", 72, True), ("views", 80, True), ("downloaded", 150, False))

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


# ── actions ──────────────────────────────────────────────────────────────────


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


# ── list rows (phones) ───────────────────────────────────────────────────────


def _list_row(shell, store: LibraryStore, record: dict, selecting: bool, selected: bool, palette) -> ft.Control:
    page = shell.page
    video_id = str(record.get("video_id", "") or "")
    card = store.card(record)
    badge = badge_for(store, record, page)
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
            Cover(card.cover_kind, card.cover_key, card.cover_url, THUMB_W, card.cover_local, icon=AppIcon.VIDEO),
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


def _table_row(shell, store: LibraryStore, record: dict, index: int, columns: tuple, selected: bool,
               focused: bool, palette) -> ft.Control:
    page = shell.page
    video_id = str(record.get("video_id", "") or "")
    label, key, detail = store.state_of(record)
    values = {
        "author": str(record.get("author", "") or ""),
        "quality": str(record.get("quality", "") or ""),
        "published": str(record.get("published_at", "") or "")[:10],
        "likes": lib.count_text(record.get("likes")),
        "views": lib.count_text(record.get("views")),
        "downloaded": str(record.get("downloaded_at", "") or "")[:16],
    }
    badge = badge_for(store, record, page)
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


# Rows are memoized components: "load more" (scrolling near the end) and a selection change only build the
# rows whose arguments changed, not every row shown so far (P9: that grew to ~0.9 s at 4 200 rows).
# ``language`` is an argument so a language switch rebuilds them; the palette covers theme changes.


@ft.component
def _ListRowView(shell, store, record, selecting, selected, palette, language):
    return _list_row(shell, store, record, selecting, selected, palette)


@ft.component
def _TableRowView(shell, store, record, index, columns, selected, focused, palette, language):
    return _table_row(shell, store, record, index, columns, selected, focused, palette)


ListRow = ft.memo(_ListRowView)
TableRow = ft.memo(_TableRowView)


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


def _toolbar(shell, store: LibraryStore, compact: bool, open_filters) -> ft.Control:
    state = store.state
    search = ft.TextField(value=state.query, hint_text=lib.search_placeholder(), prefix_icon=AppIcon.SEARCH, dense=True,
                          expand=True, on_change=lambda e: store.set_query(e.control.value or ""))
    view = ft.IconButton(
        icon=AppIcon.GRID_VIEW if state.view == VIEW_LIST else AppIcon.LIST_VIEW,
        tooltip=dict(lib.view_choices())[VIEW_COVER if state.view == VIEW_LIST else VIEW_LIST],
        on_click=lambda _e: store.set_view(VIEW_COVER if state.view == VIEW_LIST else VIEW_LIST),
    )
    if compact:
        # One row on phones: the search field, then field / state / sort / tags in one sheet.
        return ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[search, filter_button(store, lambda: open_filters(False)), view])
    field = _choice_menu(lib.search_field_choices(), lib.SEARCH_FIELDS[state.search_field], store.set_field, AppIcon.TUNE)
    states = _choice_menu(lib.state_choices(), state.state_filter, store.set_state_filter, AppIcon.FILTER)
    sort = ft.PopupMenuButton(icon=AppIcon.SORT, tooltip=tr("Sort", "排序", "並び替え"), items=[
        ft.PopupMenuItem(content=text, checked=key == state.sort, on_click=lambda _e, key=key: store.set_sort(key))
        for key, text in lib.sort_choices()
    ])
    tags = filter_button(store, lambda: open_filters(True), tags_only=True)
    return ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[search, field, states, sort, tags, view])


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
        ft.OutlinedButton(content=lib.watched_texts(False), icon=AppIcon.WATCHED,
                          on_click=lambda _e: shell.page.run_task(toggle_watched, shell, list(state.selected), True)),
        ft.TextButton(content=lib.watched_texts(True),
                      on_click=lambda _e: shell.page.run_task(toggle_watched, shell, list(state.selected), False)),
        ft.TextButton(content=tr("Select all", "全选", "すべて選択"), icon=AppIcon.SELECT_ALL, on_click=lambda _e: store.select_all()),
        ft.TextButton(content=tr("Clear selection", "取消选择", "選択を解除"), icon=AppIcon.CLOSE,
                      on_click=lambda _e: store.clear_selection()),
    ])


def _vid(record: dict) -> str:
    return str(record.get("video_id", "") or "")


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
    find, set_find = ft.use_state("")  # the sheet's tag search

    def set_sheet(value: str) -> None:
        state.sheet = value
        if not value:
            set_find("")

    ft.use_dialog(filter_sheet(shell, lambda: set_sheet(""), find, set_find, tags_only=state.sheet == "tags")
                  if state.sheet else None)

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
        ft.Text(lib.summary_text(store.counts), style=text_style(TYPE.meta, color=palette.on_surface_variant),
                max_lines=1 if compact else 2, overflow=ft.TextOverflow.ELLIPSIS),
        _toolbar(shell, store, compact, lambda tags_only: set_sheet("tags" if tags_only else "all")),
        _scopes(shell, store, author_menu=not author_pane),
    ]
    tag_row = active_tag_row(store)
    if tag_row is not None:
        top.append(tag_row)
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
            menu=record_menu(shell, store),
            width=main_width,
        )
    elif compact:
        selecting = bool(state.selected)
        body = ft.ListView(expand=True, spacing=Space.XS, build_controls_on_demand=True, on_scroll=_scrolled(store),
                           scroll_interval=100, padding=ft.Padding.only(bottom=Space.XL),
                           controls=[ListRow(shell, store, r, selecting, _vid(r) in state.selected, palette,
                                             shell.state.language, key=_vid(r)) for r in shown])
    else:
        columns = table_columns(main_width)
        body = ft.Column(expand=True, spacing=0, controls=[
            _header(shell, store, columns),
            ft.ListView(expand=True, build_controls_on_demand=True, item_extent=TABLE_ROW_H, on_scroll=_scrolled(store),
                        scroll_interval=100, controls=[
                            TableRow(shell, store, r, i, columns, _vid(r) in state.selected, _vid(r) == state.focused,
                                     palette, shell.state.language, key=_vid(r))
                            for i, r in enumerate(shown)]),
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
    return PageFrame(title=title, body=ft.Stack(expand=True, controls=[content, CoverViewer()]), actions=[refresh, menu])


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
    return PageFrame(title=title, body=ft.Stack(expand=True, controls=[body, CoverViewer()]), max_width=layout.MAX_WIDTH_FORM)


@ft.component
def LibraryPage(destination: str, path: str):
    shell = use_shell()
    # Leaving the library stops its player (and closes the cover viewer) instead of resuming on return.
    ft.use_effect(lambda: None, [], cleanup=lambda: (shell.library.stop(), shell.library.view_cover("")))
    route = routes.parse(path)
    if route.parts:
        video_id = route.parts[0]
        if shell.state.layout.content_width >= SIDE_MIN_WIDTH:
            shell.library.state.focused = video_id
            return LibraryOverview()
        return RecordPage(video_id, key=f"page:{video_id}")
    return LibraryOverview()
