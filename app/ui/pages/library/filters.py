"""The library's filters in one sheet: search field, state, sort and tags.

Phones keep a single toolbar row (search, this sheet, the view switch); wider
windows keep their menus in the toolbar and open the sheet for tags.  Every
choice applies at once, so the sheet has no Apply button.  Tags come from the
NFO beside each video, else the tags saved with the download
(``LibraryStore.tags``).
"""
from __future__ import annotations

import flet as ft

from ....presentation.history import library as lib
from ... import layout
from ...icons import AppIcon
from ...theme import palette_for, text_style
from ...tokens import TYPE, Size, Space

TAGS_SHOWN = 80  # chips drawn at most; the find field narrows the rest


def active_filters(store) -> int:
    state = store.state
    return len(state.tags) + (state.state_filter != lib.STATE_ALL) + (state.search_field != 0)


def _chips(choices, current, on_pick) -> ft.Control:
    return ft.Row(wrap=True, spacing=Space.XS, run_spacing=Space.XS, controls=[
        ft.Chip(label=text, selected=key == current, show_checkmark=False, on_select=lambda _e, key=key: on_pick(key))
        for key, text in choices
    ])


def _section(title: str, *controls: ft.Control) -> ft.Control:
    return ft.Column(spacing=Space.XS, controls=[ft.Text(title, style=text_style(TYPE.label)), *controls])


def filter_sheet(shell, on_close, find: str, set_find, *, tags_only: bool = False) -> ft.DialogControl:
    """Built by the library page on each render (``use_dialog`` takes a dialog, not a component); ``find``
    is the tag search text the page keeps."""
    store = shell.library
    state = store.state
    palette = palette_for(shell.page)
    compact = shell.state.layout.nav_mode == layout.NAV_BAR

    chosen = {tag.casefold() for tag in state.tags}
    needle = find.strip().casefold()
    every = [(tag, count) for tag, count in store.tag_list if not needle or needle in tag.casefold()]
    every.sort(key=lambda item: item[0].casefold() not in chosen)  # the chosen ones first, then by use
    tag_chips = [
        ft.Chip(label=f"{tag}  {count}", selected=tag.casefold() in chosen, show_checkmark=False,
                on_select=lambda _e, tag=tag: store.toggle_tag(tag))
        for tag, count in every[:TAGS_SHOWN]
    ]
    tags = _section(
        lib.tags_title(),
        ft.Text(lib.tags_hint() if store.tag_list else lib.no_tags_text(),
                style=text_style(TYPE.meta, color=palette.on_surface_variant)),
        *([ft.TextField(value=find, hint_text=lib.filter_tags_placeholder(), prefix_icon=AppIcon.SEARCH, dense=True,
                        on_change=lambda e: set_find(e.control.value or ""))] if len(store.tag_list) > 12 else []),
        ft.Row(wrap=True, spacing=Space.XS, run_spacing=Space.XS, controls=tag_chips),
        *([ft.TextButton(content=lib.clear_tags_text(), icon=AppIcon.CLEAR, on_click=lambda _e: store.clear_tags())]
          if state.tags else []),
    )
    sections: list[ft.Control] = []
    if not tags_only:
        direction = ft.IconButton(
            icon=AppIcon.SORT_DESC if state.reverse else AppIcon.SORT_ASC,
            tooltip=lib.sort_direction_text(state.reverse),
            on_click=lambda _e: store.set_sort(state.sort),  # the same column again flips it
        )
        sections += [
            _section(lib.search_field_title(), _chips(lib.search_field_choices(), lib.SEARCH_FIELDS[state.search_field],
                                                      store.set_field)),
            _section(lib.state_title(), _chips(lib.state_choices(), state.state_filter, store.set_state_filter)),
            _section(lib.sort_title(), ft.Row(spacing=Space.XS, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
                ft.Container(expand=True, content=_chips(lib.sort_choices(), state.sort, store.set_sort)), direction])),
        ]
    sections.append(tags)
    body = ft.Column(tight=True, spacing=Space.LG, scroll=ft.ScrollMode.AUTO, controls=sections)
    title = lib.tags_title() if tags_only else lib.filters_title()
    close = ft.TextButton(content=lib.close_text(), on_click=lambda _e: on_close())
    if compact:
        return ft.BottomSheet(
            show_drag_handle=True, scrollable=True, on_dismiss=lambda _e: on_close(),
            content=ft.Container(padding=ft.Padding.only(left=Space.LG, right=Space.LG, bottom=Space.LG),
                                 content=ft.Column(tight=True, spacing=Space.MD, controls=[
                                     ft.Text(title, style=text_style(TYPE.section_title_compact)), body,
                                     ft.Row(alignment=ft.MainAxisAlignment.END, controls=[close])])),
        )
    return ft.AlertDialog(
        title=ft.Text(title), scrollable=True, on_dismiss=lambda _e: on_close(),
        content=ft.Container(width=Size.DIALOG_MAX_WIDTH - 2 * Space.XL, content=body), actions=[close],
    )


def filter_button(store, on_open, *, tags_only: bool = False) -> ft.Control:
    count = len(store.state.tags) if tags_only else active_filters(store)
    icon = ft.Icon(AppIcon.TAG if tags_only else AppIcon.FILTER, badge=ft.Badge(label=str(count)) if count else None)
    return ft.IconButton(icon=icon, tooltip=lib.tags_title() if tags_only else lib.filters_title(), on_click=lambda _e: on_open())


def active_tag_row(store) -> ft.Control | None:
    if not store.state.tags:
        return None
    return ft.Row(spacing=Space.XS, scroll=ft.ScrollMode.AUTO, controls=[
        *[ft.Chip(label=tag, leading=ft.Icon(AppIcon.TAG, size=Size.ICON_SM),
                       on_delete=lambda _e, tag=tag: store.toggle_tag(tag)) for tag in sorted(store.state.tags, key=str.casefold)],
        ft.TextButton(content=lib.clear_tags_text(), on_click=lambda _e: store.clear_tags()),
    ])
