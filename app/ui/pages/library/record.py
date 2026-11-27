"""One library record: the side panel on wide windows, its own page on narrower ones.

The library doubles as a small local player.  The top of the record is a
stage: the cover (tap it, or the enlarge button, for the full-size viewer)
with a play button; playing swaps in the built-in player over the downloaded
copy.  Under it Previous / Next step through the list as filtered and sorted,
and "Play next automatically" moves on when a video ends.  Playing marks the
record watched.  The rest is the record: tags from its NFO (a tap filters the
library by that tag), the NFO's description and people, the stored details,
and the record's management (edit details, locate a moved file, mark
watched, rename, remove).
"""
from __future__ import annotations

import os

import flet as ft

from ....core.playback import video_player
from ....presentation.history import library as lib
from ....presentation.media import video_actions as va
from ... import layout, routes
from ...components.cover import Cover
from ...components.player import InlinePlayer
from ...components.status_badge import BadgeKind, status_badge
from ...components.video_actions import VideoMenu
from ...icons import AppIcon
from ...services.dialogs import prompt_text
from ...services.notify import notice
from ...stores.app_state import subscribe, use_shell
from ...stores.library import LibraryStore
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

SCOPE = "library"
SIDE_PANEL = 400
SIDE_MIN_WIDTH = 1180  # content width from which the record panel sits beside the table
VIEWER_CAPTION = 56

_BADGES = {"moved": BadgeKind.MOVED, "remote": BadgeKind.REMOTE}


def badge_for(store: LibraryStore, record: dict, page: ft.Page) -> ft.Control | None:
    label, key, _detail = store.state_of(record)
    kind = _BADGES.get(key)
    return status_badge(kind, palette_for(page), text=label) if kind else None


def record_menu(shell, store: LibraryStore) -> VideoMenu:
    return VideoMenu(shell, scope=SCOPE, download=False,
                     item_for=lambda card: lib.record_video(store.by_id.get(card.history_id, {})) if card.history_id in store.by_id else None,
                     on_select=lambda card: store.toggle(card.history_id),
                     play_here=lambda card: play_record(shell, card.history_id))


def play_record(shell, video_id: str) -> None:
    """Play in the library's own player: the record opens (panel or page) already playing."""
    shell.library.play(video_id)
    open_record(shell, video_id)


def long_press_record(shell, video_id: str) -> None:
    """Rows: long-press offers the two ways to play a downloaded file; otherwise it selects."""
    store = shell.library
    record = store.by_id.get(video_id)

    async def press() -> None:
        if record is None or not await record_menu(shell, store).choose_player(store.card(record)):
            store.toggle(video_id)

    shell.page.run_task(press)


def side_panel(shell) -> bool:
    return shell.state.layout.content_width >= SIDE_MIN_WIDTH


def open_record(shell, video_id: str) -> None:
    store = shell.library
    if side_panel(shell):
        store.focus(video_id)
    else:
        shell.open(routes.route(routes.LIBRARY, video_id))


def show_record(shell, video_id: str, *, play: bool = False) -> None:
    """Previous / Next: the same place (panel or page), no Back step; keeps playing when asked."""
    store = shell.library
    if not video_id:
        return
    if play:
        store.play(video_id)
    elif store.state.playing:
        store.stop()
    if side_panel(shell):
        store.focus(video_id)
    else:
        shell.navigator.show(routes.route(routes.LIBRARY, video_id))


# ── actions ──────────────────────────────────────────────────────────────────


async def remove_records(shell, video_ids: list[str]) -> None:
    """Undoable: no question first, the notice offers "Undo" (7.7)."""
    shell.library.remove(video_ids)
    if shell.state.path.startswith(routes.route(routes.LIBRARY) + "/"):
        shell.back()


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


def edit_details(shell, video_id: str) -> None:
    """A small form over the editable columns; saving reloads the library (history_changed)."""
    store = shell.library
    page = shell.page
    record = store.record(video_id) or {}
    labels = lib.edit_field_labels()
    fields = {key: ft.TextField(label=text, value=str(record.get(key, "") or ""), dense=True)
              for key, text in labels.items()}
    title, save = lib.edit_details_texts()

    async def apply(_e) -> None:
        values = {key: field.value or "" for key, field in fields.items()}
        page.pop_dialog()
        if await store.edit_details(video_id, values):
            notice(page, "success", lib.details_saved_text(), duration_ms=1800)

    page.show_dialog(ft.AlertDialog(
        title=ft.Text(title),
        content=ft.Container(width=Size.DIALOG_MAX_WIDTH - 2 * Space.XL,
                             content=ft.Column(tight=True, spacing=Space.MD, controls=list(fields.values()))),
        actions=[ft.TextButton(content=lib.cancel_text(), on_click=lambda _e: page.pop_dialog()),
                 ft.FilledButton(content=save, on_click=apply)],
    ))


async def relocate_record(shell, video_id: str) -> None:
    store = shell.library
    record = store.record(video_id) or {}
    current = str(record.get("file_path", "") or "")
    folder = os.path.dirname(current) if current and os.path.isdir(os.path.dirname(current)) else None
    files = await ft.FilePicker().pick_files(dialog_title=lib.relocate_text(), initial_directory=folder,
                                             allowed_extensions=["mp4", "mkv", "webm", "mov", "m4v"])
    if not files:
        return
    ok, message = await store.relocate(video_id, files[0].path or "")
    notice(shell.page, "success" if ok else "error", lib.relocated_title() if ok else lib.operation_failed_title(), message)


async def toggle_watched(shell, video_ids: list[str], watched: bool) -> None:
    count = await shell.library.set_watched(video_ids, watched)
    notice(shell.page, "success", lib.marked_notice(count, watched), duration_ms=1800)


# ── the stage: cover or player ───────────────────────────────────────────────


def _over(palette, icon, tooltip: str, on_click, *, size: float = Size.ICON) -> ft.Control:
    return ft.IconButton(icon=icon, icon_size=size, icon_color=palette.on_cover_scrim, bgcolor=palette.cover_scrim,
                         tooltip=tooltip, on_click=on_click)


def stage(shell, store: LibraryStore, card, video_id: str, width: float, playable: bool) -> ft.Control:
    palette = palette_for(shell.page)
    state = store.state

    def finished() -> None:
        following = store.neighbour(video_id, 1)
        if state.autoplay and following:
            show_record(shell, following, play=True)

    if state.playing == video_id:
        return InlinePlayer(video_id, card.title, shell.state.site, width, on_close=store.stop, on_complete=finished,
                            key=f"library-play:{video_id}")
    cover = ft.Container(
        on_click=lambda _e: store.view_cover(video_id), tooltip=lib.enlarge_text(), border_radius=Radius.CARD,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=Cover(card.cover_kind, card.cover_key, card.cover_url, width, card.cover_local, icon=AppIcon.VIDEO,
                      radius=Radius.CARD),
    )
    layers: list[ft.Control] = [cover, ft.Container(right=Space.XS, top=Space.XS, content=_over(
        palette, AppIcon.ENLARGE, lib.enlarge_text(), lambda _e: store.view_cover(video_id), size=Size.ICON_SM))]
    if playable:
        layers.append(ft.Container(left=0, right=0, top=0, bottom=0, alignment=ft.Alignment.CENTER, content=_over(
            palette, AppIcon.PLAY, lib.play_here_text(), lambda _e: store.play(video_id), size=Size.ICON_LG * 1.5)))
    return ft.Stack(width=width, height=width * 9 / 16, controls=layers)


def player_bar(shell, store: LibraryStore, video_id: str) -> ft.Control:
    state = store.state
    previous, following = store.neighbour(video_id, -1), store.neighbour(video_id, 1)
    playing = state.playing == video_id
    return ft.Row(spacing=Space.XS, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.IconButton(icon=AppIcon.SKIP_PREVIOUS, tooltip=lib.previous_text(), disabled=not previous,
                      on_click=lambda _e: show_record(shell, previous, play=playing)),
        ft.Text(lib.position_text(store.position(video_id), len(store.visible)), style=text_style(TYPE.meta)),
        ft.IconButton(icon=AppIcon.SKIP_NEXT, tooltip=lib.next_text(), disabled=not following,
                      on_click=lambda _e: show_record(shell, following, play=playing)),
        ft.Container(expand=True),
        ft.Text(lib.autoplay_text(), style=text_style(TYPE.meta), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ft.Switch(value=state.autoplay, on_change=lambda e: store.set_autoplay(bool(e.control.value))),
    ])


# ── the full-size cover ──────────────────────────────────────────────────────


@ft.component
def CoverViewer():
    """The cover of ``state.viewer`` as large as the window allows; zoom with the wheel or a pinch."""
    shell = use_shell()
    store = shell.library
    state = store.state
    subscribe(state)
    view = shell.state.layout
    palette = palette_for(shell.page)
    record = store.record(state.viewer) if state.viewer else None
    dialog = None
    if record is not None:
        video_id = state.viewer
        card = store.card(record)
        inset = Space.SM if view.width_class == layout.WIDTH_COMPACT else Space.XL
        room_w = max(200.0, view.width - 2 * inset)
        room_h = max(160.0, view.height - 2 * inset - VIEWER_CAPTION)
        width = min(room_w, room_h * 16 / 9)
        previous, following = store.neighbour(video_id, -1), store.neighbour(video_id, 1)
        close = lambda _e=None: store.view_cover("")  # noqa: E731
        picture = ft.InteractiveViewer(
            min_scale=1, max_scale=5, boundary_margin=ft.Margin.all(0),
            content=Cover(card.cover_kind, card.cover_key, card.cover_url, width, card.cover_local, icon=AppIcon.IMAGE,
                          radius=Radius.CARD, fit=ft.BoxFit.CONTAIN, placeholder=palette.scrim,
                          key=f"viewer:{video_id}"),
        )
        caption = ft.Row(spacing=Space.XS, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.IconButton(icon=AppIcon.CHEVRON_LEFT, icon_color=palette.on_cover_scrim, tooltip=lib.previous_text(),
                          disabled=not previous, on_click=lambda _e: store.view_cover(previous)),
            ft.Text(card.title, style=text_style(TYPE.label, color=palette.on_cover_scrim), expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER),
            ft.Text(lib.position_text(store.position(video_id), len(store.visible)),
                    style=text_style(TYPE.meta, color=palette.on_cover_scrim)),
            ft.IconButton(icon=AppIcon.CHEVRON_RIGHT, icon_color=palette.on_cover_scrim, tooltip=lib.next_text(),
                          disabled=not following, on_click=lambda _e: store.view_cover(following)),
            ft.IconButton(icon=AppIcon.CLOSE, icon_color=palette.on_cover_scrim, tooltip=lib.close_text(), on_click=close),
        ])
        dialog = ft.AlertDialog(
            bgcolor=palette.scrim,
            inset_padding=inset,
            content_padding=0,
            on_dismiss=close,
            content=ft.Container(width=width, content=ft.Column(tight=True, spacing=0, controls=[
                picture, ft.Container(height=VIEWER_CAPTION, padding=ft.Padding.symmetric(horizontal=Space.XS), content=caption),
            ])),
        )
    ft.use_dialog(dialog)
    return ft.Container(width=0, height=0)


# ── the record ───────────────────────────────────────────────────────────────


@ft.component
def RecordPanel(video_id: str, *, wide: bool = True):
    shell = use_shell()
    store = shell.library
    subscribe(store.state)
    page = shell.page
    palette = palette_for(page)
    actions, set_actions = ft.use_state(list)
    record = store.record(video_id)
    menu = record_menu(shell, store)
    card = store.card(record) if record else None

    async def load_actions() -> None:
        set_actions(await menu.actions(card) if card else [])

    ft.use_effect(lambda: page.run_task(load_actions), [video_id, store.state.revision])
    if record is None or card is None:
        return ft.Container(expand=True, alignment=ft.Alignment.CENTER,
                            content=ft.Text(lib.missing_record_text(), style=text_style(TYPE.body)))
    label, key, detail = store.state_of(record)
    width = SIDE_PANEL - 2 * Space.MD if wide else min(shell.state.layout.body_width(layout.MAX_WIDTH_FORM), 720)
    nfo = store.nfo.get(video_id)
    tags = store.tags_of(record)
    watched = bool(str(record.get("watched_at", "") or ""))
    playable = key in ("ok", "remote")

    def run(action) -> None:
        if action.id == va.PLAY_BUILTIN or (
                action.id == va.PLAY and not (action.value and video_player.opens_externally(action.value))):
            store.play(video_id)  # the built-in player plays here, not on the post's page
            return
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
            info(lib.people_label(), "、".join(nfo.people) if nfo else ""),
            info(labels["quality"], str(record.get("quality", "") or "")),
            info(labels["published"], str(record.get("published_at", "") or "")[:10]),
            info(f"{labels['views']} / {labels['likes']}", f"{lib.count_text(record.get('views'))} / {lib.count_text(record.get('likes'))}"),
            info(labels["downloaded"], str(record.get("downloaded_at", "") or "")),
            info(labels["path"], str(record.get("file_path", "") or record.get("remote_path", "") or "")),
            info("ID", video_id),
            info(lib.plot_label(), nfo.plot if nfo else "")]
    chosen = {tag.casefold() for tag in store.state.tags}
    tag_row = ft.Row(wrap=True, spacing=Space.XS, run_spacing=Space.XS, controls=[
        ft.Chip(label=tag, leading=ft.Icon(AppIcon.TAG, size=Size.ICON_SM), selected=tag.casefold() in chosen,
                show_checkmark=False, on_select=lambda _e, tag=tag: store.toggle_tag(tag))
        for tag in tags
    ]) if tags else None
    # Playing is what a downloaded record is for: first and filled; the rest keep the menu order.
    ordered = sorted(actions, key=lambda action: action.id != va.PLAY)
    buttons: list[ft.Control] = [
        (ft.FilledButton if action.id == va.PLAY else ft.OutlinedButton)(
            content=action.label, on_click=lambda _e, action=action: run(action))
        for action in ordered
    ]
    manage: list[ft.PopupMenuItem] = [
        ft.PopupMenuItem(content=lib.watched_texts(watched), icon=AppIcon.WATCHED,
                         on_click=lambda _e: page.run_task(toggle_watched, shell, [video_id], not watched)),
        ft.PopupMenuItem(content=lib.edit_details_texts()[0], icon=AppIcon.EDIT,
                         on_click=lambda _e: edit_details(shell, video_id)),
        ft.PopupMenuItem(content=lib.rename_texts()[2], icon=AppIcon.FILE,
                         on_click=lambda _e: page.run_task(rename_record, shell, video_id)),
    ]
    if shell.caps.desktop:
        manage.append(ft.PopupMenuItem(content=lib.relocate_text(), icon=AppIcon.LOCATE,
                                       on_click=lambda _e: page.run_task(relocate_record, shell, video_id)))
    manage.append(ft.PopupMenuItem(content=lib.remove_button(), icon=AppIcon.DELETE,
                                   on_click=lambda _e: page.run_task(remove_records, shell, [video_id])))
    buttons.append(ft.PopupMenuButton(icon=AppIcon.MORE, tooltip=lib.manage_text(), items=manage))
    content = ft.Column(spacing=Space.MD, scroll=ft.ScrollMode.AUTO, expand=True, controls=[c for c in (
        stage(shell, store, card, video_id, width, playable),
        player_bar(shell, store, video_id),
        ft.Text(card.title, style=text_style(TYPE.section_title_compact), selectable=True),
        tag_row,
        ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=buttons),
        *[row for row in rows if row is not None],
    ) if c is not None])
    if not wide:
        return content
    return ft.Container(width=SIDE_PANEL, padding=Space.MD, border_radius=Radius.CARD, bgcolor=palette.card, content=content)
