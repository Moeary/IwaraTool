"""Fill a Flet window with demo activity for screenshots (``main_flet.py --demo``).

Runs after the window is mounted, on top of ``demo_fixture`` (offline made-up
Iwara): the search route searches, the downloads route queues a few videos and
cancels one.  Nothing here is imported outside demo runs.
"""
from __future__ import annotations

import asyncio
import os

import demo_fixture


async def play(shell, route: str) -> None:
    from app.ui_flet import routes

    parsed = routes.parse(route or "/")
    destination = parsed.destination
    if destination == routes.DETAIL and os.environ.get("IWARATOOL_DEMO_PLAY"):
        # Play a local clip as the "downloaded copy" (the demo CDN serves no video).
        from app.core.playback import video_player

        clip = os.path.abspath(os.environ["IWARATOOL_DEMO_PLAY"])
        video_player.playable_video_target = lambda *_args, **_kwargs: clip
        model = shell.details.model(*parsed.parts[:2])
        for _ in range(50):
            if model.can_play:
                break
            await asyncio.sleep(0.1)
        model.state.playing = True
        return
    if destination == routes.SEARCH:
        store = shell.search
        store.set_source("iwara")
        await store.search(record=False)
        if os.environ.get("IWARATOOL_DEMO_MENU") and store.cards:
            # Show the action sheet of the first result (what a long press opens on a phone).
            from app.ui_flet.components.video_actions import VideoMenu, open_action_sheet

            card = store.cards[0]
            menu = VideoMenu(shell, scope="search", item_for=lambda c: store.items.get(c.key), on_select=store.toggle)
            actions = await menu.actions(card, touch=True)
            open_action_sheet(shell.page, card.title, actions, lambda action: None)
    elif destination == routes.DOWNLOADS:
        from app.core.download.manager import download_manager

        for index in range(3, 11):
            download_manager.add_url(f"https://www.iwara.tv/video/{demo_fixture.video(index)['id']}")
        await asyncio.sleep(1.0)
        tasks = download_manager.get_tasks()
        if len(tasks) > 2:
            download_manager.cancel_task(tasks[-1].task_id)
