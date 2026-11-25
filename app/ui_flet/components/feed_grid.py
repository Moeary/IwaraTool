"""A ``FeedSection`` tab as a full grid, page after page (Home "More ›", the author page).

Pages come from ``core.home.loading.load_feed_page`` off the loop; scrolling
near the end asks for the next one.  Cards can be selected and queued through
the ``DownloadSheet``.
"""
from __future__ import annotations

import asyncio

import flet as ft

from ...core.home.feed import BROWSE_PAGE_LIMIT, FeedSection
from ...core.home.loading import load_feed_page
from ...i18n import tr
from ...presentation.home import section_feed as rules
from ...presentation.media.cards import CardView, video_card
from .. import routes
from ..icons import AppIcon
from ..stores.app_state import use_shell
from ..theme import text_style
from ..tokens import TYPE, Space
from .download_sheet import open_download_sheet
from .video_actions import VideoMenu
from .video_grid import VideoGrid


def open_post(shell, card: CardView, videos: dict) -> None:
    shell.details.remember(videos.get(card.key), card.cover_key)
    kind = "image" if card.kind == "image" else "video"
    shell.open(routes.route(routes.DETAIL, kind, card.cover_key))


@ft.component
def FeedGrid(section: FeedSection, tab_id: str, rating: str, scope: str):
    shell = use_shell()
    cards, set_cards = ft.use_state(list)
    videos, set_videos = ft.use_state(dict)
    selected, set_selected = ft.use_state(frozenset)
    paging, set_paging = ft.use_state(lambda: {"page": -1, "more": True, "loading": False, "error": ""})
    tab = section.tab(tab_id)

    async def load(page: int) -> None:
        if paging["loading"] and page:
            return
        set_paging({**paging, "loading": True})
        result = await asyncio.to_thread(
            load_feed_page, shell.home.manager, tab.kind, tab.request_params(rating), page, BROWSE_PAGE_LIMIT,
            mode=tab.mode, value=tab.value, rating=rating,
        )
        merged, found = (list(cards), dict(videos)) if page else ([], {})
        for item in result.items:
            card = video_card(item)
            if card.key not in found:
                found[card.key] = item
                merged.append(card)
        set_cards(merged)
        set_videos(found)
        if not page:
            set_selected(frozenset())
        set_paging({"page": page, "more": result.has_more, "loading": False, "error": result.error})

    ft.use_effect(lambda: shell.page.run_task(load, 0), [section.id, tab.id, tab.value, rating])

    def toggle(card: CardView) -> None:
        if card.selectable:
            set_selected(frozenset(set(selected) ^ {card.key}))

    def queue() -> None:
        chosen = [videos[key] for key in (selected or [c.key for c in cards]) if key in videos]
        ids = set(rules.queueable_ids(chosen))
        chosen = [video for video in chosen if video.video_id in ids]
        if not chosen:
            return

        async def confirmed(rule_id: str) -> None:
            await shell.downloads.stage_videos(chosen, rule_id=rule_id, label=section.title)
            set_selected(frozenset())

        open_download_sheet(shell, scope=scope, title=rules.queue_text(len(selected), len(chosen)), summary=section.title,
                            on_confirm=confirmed)

    if not cards:
        busy = paging["loading"] or paging["page"] < 0
        return ft.Container(
            expand=True, alignment=ft.Alignment.CENTER,
            content=ft.ProgressRing() if busy else ft.Text(paging["error"] or rules.empty_text(), style=text_style(TYPE.body)),
        )
    bar: list[ft.Control] = []
    if tab.kind == "video":
        count = len(rules.queueable_ids([videos[k] for k in (selected or [c.key for c in cards]) if k in videos]))
        bar.append(ft.Row(spacing=Space.SM, controls=[
            ft.FilledTonalButton(content=rules.queue_text(len(selected), count), icon=AppIcon.DOWNLOAD,
                                 disabled=count == 0, on_click=lambda _e: queue()),
            *([ft.TextButton(content=tr("Clear selection", "取消选择", "選択を解除"), icon=AppIcon.CLOSE,
                             on_click=lambda _e: set_selected(frozenset()))] if selected else []),
        ]))
    grid = VideoGrid(
        cards,
        selected=selected,
        selecting=bool(selected),
        on_open=lambda c: open_post(shell, c, videos),
        on_toggle=toggle,
        menu=VideoMenu(shell, scope=scope, item_for=lambda c: videos.get(c.key), on_select=toggle),
        on_end_reached=(lambda: shell.page.run_task(load, paging["page"] + 1)) if paging["more"] else None,
    )
    return ft.Column(expand=True, spacing=Space.SM, controls=[*bar, grid])
