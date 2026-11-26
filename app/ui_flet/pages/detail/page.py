"""A post (docs/flet_ui_plan.md 7.3): ``/detail/video/<id>`` and ``/detail/image/<id>``.

Wide windows put the related posts in a side column (``detail_metrics``);
narrower ones list them under the comments.  The cover turns into the player
in place; image posts show their gallery (a click opens the original).
"""
from __future__ import annotations

import asyncio

import flet as ft

from ....core.playback import video_player
from ....core.rules import page_rule_id
from ....core.search.models import SearchVideo
from ....i18n import tr
from ....presentation.media import detail_info
from ....presentation.media.comments import CommentView, comments_hint
from ....presentation.media.detail_metrics import CONTENT_MAX_WIDTH, SIDE_MIN_PAGE_WIDTH, SIDE_WIDTH
from ....presentation.media.detail_text import link_segments
from ....presentation.media.cards import CardView
from ... import layout, routes
from ...components.author_status import AuthorStatus
from ...components.cover import Cover
from ...components.page_frame import PageFrame
from ...components.player import InlinePlayer
from ...components.video_actions import VideoMenu
from ...components.video_card import VideoCard, text_height
from ...components.video_grid import cover_size_for
from ...icons import AppIcon, StateIcon
from ...services.notify import notice
from ...services.images import SUBSCRIPTION
from ...stores.app_state import subscribe, use_shell
from ...stores.detail import DetailModel
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

AVATAR = 40
COMMENT_AVATAR = 32


def open_related(shell, model: DetailModel, card: CardView) -> None:
    shell.details.remember(model.related.get(card.key))
    kind = "image" if card.kind == "image" else "video"
    shell.open(routes.route(routes.DETAIL, kind, card.cover_key))


def linked_text(text: str, style: ft.TextStyle, link_color: str) -> ft.Text:
    spans = [
        ft.TextSpan(piece, url=url, style=ft.TextStyle(color=link_color, decoration=ft.TextDecoration.UNDERLINE))
        if url else ft.TextSpan(piece)
        for piece, url in link_segments(text)
    ]
    return ft.Text(spans=spans, style=style, selectable=True)


# ── stage: cover → player, or the gallery ────────────────────────────────────


def _stage(shell, model: DetailModel, width: float, playing: bool, set_playing) -> ft.Control:
    video = model.state.video
    palette = palette_for(shell.page)
    if model.kind == "image":
        files = model.gallery()
        if not files:
            return ft.Container()
        tall = max(320.0, shell.state.layout.height - 160)  # a portrait picture fits the window height

        def fit(item) -> float:
            return min(width, tall * item.aspect)
        return ft.Column(spacing=Space.SM, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Container(
                on_click=(lambda _e, url=item.original_url: shell.page.run_task(ft.UrlLauncher().launch_url, url))
                if item.original_url else None,
                tooltip=detail_info.gallery_hint(len(files)),
                border_radius=Radius.CARD,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=Cover("gallery_thumb", item.file_id, item.thumb_url, fit(item), aspect=item.aspect, icon=AppIcon.IMAGE,
                              radius=Radius.CARD),
            )
            for item in files
        ])
    # Short or very wide windows: keep the title and actions in view under the 16:9 stage.
    width = min(width, max(280.0, shell.state.layout.height * 0.7) * 16 / 9)
    if playing and video is not None:
        return InlinePlayer(video.video_id, video.title, model.site, width, on_close=lambda: set_playing(False),
                            key=f"player:{video.video_id}")
    overlays: list[ft.Control] = [
        # No cover URL (opened from a library record whose post did not load): the cover cache finds it by id.
        Cover("video" if video and video.thumbnail_url else SUBSCRIPTION, model.item_id,
              video.thumbnail_url if video else "", width, radius=Radius.CARD),
    ]
    if model.can_play:
        overlays.append(ft.Container(
            expand=True, alignment=ft.Alignment.CENTER,
            content=ft.Container(
                width=Size.ICON_LG * 3, height=Size.ICON_LG * 3, border_radius=Radius.FULL,
                bgcolor=palette.cover_scrim, alignment=ft.Alignment.CENTER,
                content=ft.Icon(AppIcon.PLAY, color=palette.on_cover_scrim, size=Size.ICON_LG * 1.6),
            ),
        ))
        if model.quality():
            overlays.append(ft.Container(
                right=Space.SM, bottom=Space.SM, padding=ft.Padding.symmetric(horizontal=Space.XS, vertical=2),
                border_radius=Radius.BADGE, bgcolor=palette.cover_scrim,
                content=ft.Text(model.quality(), style=text_style(TYPE.badge, color=palette.on_cover_scrim)),
            ))
    return ft.Container(
        width=width, height=width * 9 / 16,
        border_radius=Radius.CARD, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        on_click=(lambda _e: shell.page.run_task(_play, shell, model, set_playing)) if model.can_play else None,
        content=ft.Stack(expand=True, controls=overlays),
    )


async def _play(shell, model: DetailModel, set_playing) -> None:
    """In place, unless Settings send downloaded files to the system / a custom player."""

    video = model.state.video
    if video is None or not model.can_play:
        return
    mode = video_player.player_mode()
    if mode != video_player.MODE_BUILTIN:
        target = await asyncio.to_thread(video_player.downloaded_copy, video.video_id, shell.home.manager)
        if target and video_player.opens_externally(target, mode):
            ok, message = await asyncio.to_thread(video_player.open_local_video, target, mode=mode)
            if not ok:
                notice(shell.page, "error", tr("Could not open the player", "无法打开播放器", "プレイヤーを開けません"), message)
            return
    set_playing(True)


# ── title, actions, author, description, tags ────────────────────────────────


def _actions(shell, model: DetailModel, set_playing, compact: bool) -> ft.Control:
    state = model.state
    video = state.video
    controls: list[ft.Control] = []
    if model.kind == "video":
        controls.append(ft.FilledButton(
            content=tr("Play", "播放", "再生"), icon=AppIcon.PLAY, disabled=not model.can_play,
            on_click=lambda _e: shell.page.run_task(_play, shell, model, set_playing),
        ))

        async def queue() -> None:
            current = video or SearchVideo(video_id=model.item_id, title=model.item_id)
            await shell.downloads.stage_videos([current], rule_id=page_rule_id("detail"), label=current.title or model.item_id)

        controls.append(ft.OutlinedButton(content=tr("Download", "下载", "ダウンロード"), icon=AppIcon.DOWNLOAD,
                                          disabled=video is not None and not video.downloadable,
                                          on_click=lambda _e: shell.page.run_task(queue)))
    likes = video.likes if video else 0
    like_icon = StateIcon.LIKE.active if state.liked else StateIcon.LIKE.rest
    controls.append(ft.OutlinedButton(
        content=detail_info.like_text(state.liked, likes), icon=like_icon, tooltip=detail_info.like_tooltip(state.liked),
        disabled=state.like_busy or video is None, on_click=lambda _e: shell.page.run_task(model.toggle_like),
    ))

    async def copy_link() -> None:
        await ft.Clipboard().set(model.page_url)
        notice(shell.page, "success", detail_info.link_copied_title(), model.page_url, duration_ms=2000)

    controls += [
        ft.IconButton(icon=AppIcon.BROWSER, tooltip=tr("Open in browser", "在浏览器中打开", "ブラウザーで開く"),
                      on_click=lambda _e: shell.page.run_task(ft.UrlLauncher().launch_url, model.page_url)),
        ft.IconButton(icon=AppIcon.LINK, tooltip=tr("Copy link", "复制链接", "リンクをコピー"),
                      on_click=lambda _e: shell.page.run_task(copy_link)),
    ]
    return ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=controls)


def _author(shell, model: DetailModel) -> ft.Control | None:
    author = model.state.author
    if author is None:
        return None
    palette = palette_for(shell.page)
    return ft.Container(
        padding=Space.MD,
        border_radius=Radius.CARD,
        bgcolor=palette.card,
        content=ft.Column(spacing=Space.SM, controls=[ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            Cover("avatar", author.user_id or author.username, author.avatar_url, AVATAR, aspect=1, round=True,
                  icon=AppIcon.AUTHOR),
            ft.Column(spacing=0, expand=True, controls=[
                ft.Text(author.name, style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(f"@{author.username}", style=text_style(TYPE.meta, color=palette.on_surface_variant),
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ]),
            ft.TextButton(content=tr("Author page", "作者主页", "作者ページ"), icon=AppIcon.AUTHOR,
                          on_click=lambda _e: shell.open(routes.route(routes.AUTHOR, author.username))),
        ]), AuthorStatus(author.username, author.name, author.user_id, author.avatar_url, key=f"status:{author.username}")]),
    )


def _tags(shell, model: DetailModel) -> ft.Control | None:
    chips = model.tags()
    if not chips:
        return None

    def search(tag_id: str) -> None:
        request = detail_info.tag_search_request(model.kind, tag_id)
        store = shell.search
        store.set_source("iwara")
        store.set_scope(request["scope"])
        store.set_keyword(request["keyword"])
        shell.select(routes.SEARCH)
        shell.page.run_task(store.search)

    return ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=[
        ft.Chip(label=label, tooltip=tag_id, on_click=lambda _e, tag_id=tag_id: search(tag_id)) for tag_id, label in chips
    ])


# ── comments ─────────────────────────────────────────────────────────────────


def _comment(shell, model: DetailModel, comment: CommentView, *, reply: bool = False) -> ft.Control:
    palette = palette_for(shell.page)
    state = model.state
    body: list[ft.Control] = [
        ft.Row(spacing=Space.SM, controls=[
            ft.Text(comment.author_name, style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(comment.meta, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, expand=True),
        ]),
        linked_text(comment.body, text_style(TYPE.body), palette.primary),
    ]
    if not reply and comment.reply_count:
        replies = state.replies.get(comment.id)
        if replies is None:
            loading = comment.id in state.replies_loading
            body.append(ft.TextButton(
                content=comment.replies_label(), disabled=loading,
                icon=StateIcon.EXPAND.rest,
                on_click=lambda _e: shell.page.run_task(model.load_replies, comment.id),
            ))
        else:
            body.append(ft.Column(spacing=Space.MD, controls=[_comment(shell, model, r, reply=True) for r in replies]))
    return ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
        Cover("avatar", comment.avatar_key, comment.avatar_url, COMMENT_AVATAR, aspect=1, round=True, icon=AppIcon.AUTHOR),
        ft.Column(spacing=Space.XS, expand=True, controls=body),
    ])


def _comments(shell, model: DetailModel) -> ft.Control:
    state = model.state
    palette = palette_for(shell.page)
    controls: list[ft.Control] = [ft.Text(model.comments_title(), style=text_style(TYPE.section_title_compact))]
    controls += [_comment(shell, model, comment) for comment in state.comments]
    hint = "" if state.comments_loading else comments_hint(state.comment_error, len(state.comments))
    if hint:
        controls.append(ft.Text(hint, style=text_style(TYPE.body, color=palette.on_surface_variant)))
    if state.comments_loading:
        controls.append(ft.ProgressRing(width=Size.ICON_LG, height=Size.ICON_LG))
    elif state.more_comments:
        controls.append(ft.OutlinedButton(content=tr("More comments", "更多评论", "さらにコメント"),
                                          on_click=lambda _e: shell.page.run_task(model.more_comments)))
    return ft.Column(spacing=Space.LG, controls=controls)


# ── related ──────────────────────────────────────────────────────────────────


def _related(shell, model: DetailModel, width: float, *, side: bool) -> ft.Control | None:
    cards = model.state.related
    if not cards:
        return None
    view = shell.state.layout
    menu = VideoMenu(shell, scope="detail", item_for=lambda c: model.related.get(c.key))

    def card(item: CardView, card_width: float) -> ft.Control:
        return VideoCard(item, card_width, shell.page, touch=view.is_touch,
                         on_open=lambda c: open_related(shell, model, c), menu=menu, key=item.key)

    title = ft.Text(tr("Related", "相关作品", "関連作品"), style=text_style(TYPE.section_title_compact))
    if side:
        return ft.Column(spacing=Space.SM, controls=[title, *[RelatedRow(item, model, key=item.key) for item in cards]])
    metrics = layout.grid_metrics(width, view.width_class, text_height=text_height(), cover_size=cover_size_for(shell))
    rows = [
        ft.Row(spacing=metrics.gap, vertical_alignment=ft.CrossAxisAlignment.START,
               controls=[card(item, metrics.tile_width) for item in cards[start:start + metrics.columns]])
        for start in range(0, len(cards), metrics.columns)
    ]
    return ft.Column(spacing=metrics.gap, controls=[title, *rows])


RELATED_COVER = 152


@ft.component
def RelatedRow(card: CardView, model: DetailModel):
    """The side column's compact entry: a small cover beside the title."""

    shell = use_shell()
    palette = palette_for(shell.page)
    hovered, set_hovered = ft.use_state(False)
    cover = ft.Stack(width=RELATED_COVER, height=RELATED_COVER * 9 / 16, controls=[
        Cover(card.cover_kind, card.cover_key, card.cover_url, RELATED_COVER, card.cover_local, radius=Radius.CHIP),
    ])
    if card.duration:
        cover.controls.append(ft.Container(
            right=Space.XS, bottom=Space.XS, padding=ft.Padding.symmetric(horizontal=Space.XS, vertical=1),
            border_radius=Radius.BADGE, bgcolor=palette.cover_scrim,
            content=ft.Text(card.duration, style=text_style(TYPE.badge, color=palette.on_cover_scrim)),
        ))
    return ft.Container(
        padding=Space.XS,
        border_radius=Radius.CONTROL,
        bgcolor=palette.card if hovered else None,
        on_hover=lambda e: set_hovered(bool(e.data)),
        on_click=lambda _e: open_related(shell, model, card),
        tooltip=card.tooltip or None,
        content=ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            cover,
            ft.Column(spacing=2, expand=True, controls=[
                ft.Text(card.title, style=text_style(TYPE.card_title), max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(card.subtitle, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(card.meta, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ]),
        ]),
    )


# ── page ─────────────────────────────────────────────────────────────────────


@ft.component
def DetailBody(model: DetailModel):
    shell = use_shell()
    state = model.state
    subscribe(state)
    playing = state.playing

    def set_playing(value: bool) -> None:
        state.playing = bool(value)
    palette = palette_for(shell.page)
    view = shell.state.layout
    compact = view.width_class == layout.WIDTH_COMPACT
    available = view.body_width(CONTENT_MAX_WIDTH)
    side = view.content_width >= SIDE_MIN_PAGE_WIDTH and bool(state.related)
    main_width = available - SIDE_WIDTH - Space.XL if side else available

    async def start() -> None:
        await model.check_local()
        await model.start(shell.details.previews.get(model.item_id))

    def leave() -> None:
        state.playing = False

    ft.use_effect(start, [model.kind, model.item_id], cleanup=leave)

    if state.error and state.video is None:
        return ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=ft.Column(
            tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=Space.MD, controls=[
                ft.Icon(AppIcon.ERROR, color=palette.danger.fg, size=Size.ICON_LG),
                ft.Text(state.error, style=text_style(TYPE.body), text_align=ft.TextAlign.CENTER, max_lines=4,
                        overflow=ft.TextOverflow.ELLIPSIS, selectable=True),
                ft.OutlinedButton(content=tr("Try again", "重试", "再試行"), icon=AppIcon.REFRESH,
                                  on_click=lambda _e: shell.page.run_task(start)),
            ]))
    video = state.video
    main: list[ft.Control] = [_stage(shell, model, main_width, playing, set_playing)]
    if video is not None:
        main.append(ft.Column(spacing=Space.XS, controls=[
            ft.Text(video.title, style=text_style(TYPE.section_title_compact if compact else TYPE.section_title),
                    selectable=True),
            ft.Text(model.meta_text(), style=text_style(TYPE.meta, color=palette.on_surface_variant)),
        ]))
    if state.loading and video is None:
        main.append(ft.ProgressRing())
    if state.error:
        main.append(ft.Text(state.error, style=text_style(TYPE.body, color=palette.danger.fg), max_lines=4,
                            overflow=ft.TextOverflow.ELLIPSIS))
    if model.embedded():
        main.append(ft.Text(detail_info.embedded_text(), style=text_style(TYPE.body, color=palette.warning.fg)))
    main.append(_actions(shell, model, set_playing, compact))
    for block in (_author(shell, model), _tags(shell, model)):
        if block is not None:
            main.append(block)
    if model.description():
        main.append(linked_text(model.description(), text_style(TYPE.body), palette.primary))
    main.append(ft.Divider())
    main.append(_comments(shell, model))
    if not side:
        related = _related(shell, model, main_width, side=False)
        if related is not None:
            main += [ft.Divider(), related]
    main_column = ft.Column(spacing=Space.LG, width=main_width, controls=main)
    if not side:
        return ft.ListView(expand=True, padding=ft.Padding.only(bottom=Space.XL), controls=[main_column])
    related = _related(shell, model, SIDE_WIDTH, side=True)
    return ft.ListView(expand=True, padding=ft.Padding.only(bottom=Space.XL), controls=[
        ft.Row(spacing=Space.XL, vertical_alignment=ft.CrossAxisAlignment.START,
               controls=[main_column, ft.Container(width=SIDE_WIDTH, content=related)]),
    ])


@ft.component
def DetailPage(destination: str, path: str):
    shell = use_shell()
    route = routes.parse(path)
    kind = route.parts[0] if route.parts else "video"
    item_id = route.parts[1] if len(route.parts) > 1 else ""
    model = shell.details.model(kind, item_id)
    title = tr("Image", "图片", "画像") if model.kind == "image" else tr("Video", "视频", "動画")
    return PageFrame(title=title, body=DetailBody(model, key=f"{model.kind}:{model.item_id}"),
                     max_width=CONTENT_MAX_WIDTH)
