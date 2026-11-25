"""Fill a Flet window with demo activity for screenshots (``main_flet.py --demo``).

Runs after the window is mounted, on top of ``demo_fixture`` (offline made-up
Iwara): the search route searches, the downloads route queues a few videos and
cancels one, the subscription, library and author routes get seeded records,
the repair route gets a folder of badly named files (``IWARATOOL_DEMO_STEP``:
``options`` / ``preview`` scans / ``done`` also repairs), the tools route loads
``IWARATOOL_DEMO_VIDEO`` (``IWARATOOL_DEMO_TOOL=covers`` for the cover preview,
``IWARATOOL_DEMO_STEP=run`` compresses it).  Nothing here is imported outside demo runs.
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
    elif destination in (routes.SUBSCRIPTIONS, routes.LIBRARY, routes.AUTHOR) and demo_fixture.ROOT:
        # Library records and six author subscriptions filled by a (fake) refresh.
        await asyncio.to_thread(demo_fixture.seed, demo_fixture.ROOT)
        await shell.subscriptions.load()
        await shell.subscriptions.reload_items()
    elif destination == routes.DOWNLOADS:
        from app.core.download.manager import download_manager

        for index in range(3, 11):
            download_manager.add_url(f"https://www.iwara.tv/video/{demo_fixture.video(index)['id']}")
        await asyncio.sleep(1.0)
        tasks = download_manager.get_tasks()
        if len(tasks) > 2:
            download_manager.cancel_task(tasks[-1].task_id)
    elif destination == routes.REPAIR and demo_fixture.ROOT:
        folder = demo_fixture.ROOT / "Unsorted"
        folder.mkdir(parents=True, exist_ok=True)
        # Repair reads IDs of 8+ characters; the fake API answers any ID by its digits.
        names = [f"Iwara - {demo_fixture.video(i)['title']} [demo{i:04d}].mp4" for i in range(4)]
        names += [f"{demo_fixture.video(i)['title']} 1080p (demo{i:04d}).mp4" for i in range(4, 9)]
        names += ["holiday clip.mp4", "untitled (2).mp4"]
        for name in names:
            (folder / name).write_bytes(b"\0" * 1024)
        store = shell.repair
        store.load()
        store.update(folder=str(folder))
        step = os.environ.get("IWARATOOL_DEMO_STEP", "")
        if step:
            store.go(1)
        if step in ("preview", "done"):
            await store.scan()
        if step == "done":
            await store.repair()
    elif destination == routes.TOOLS and os.environ.get("IWARATOOL_DEMO_VIDEO"):
        video = os.path.abspath(os.environ["IWARATOOL_DEMO_VIDEO"])
        if demo_fixture.ROOT:
            # Show a neutral path in screenshots: work on a copy in the throw-away data folder.
            import shutil

            folder = demo_fixture.ROOT / "Videos"
            folder.mkdir(parents=True, exist_ok=True)
            video = shutil.copy2(video, folder / os.path.basename(video))
        store = shell.tools
        store.load()
        store.choose_tool(os.environ.get("IWARATOOL_DEMO_TOOL", "compress"))
        for _ in range(100):
            if store.state.ffmpeg_checked and not store.state.ffmpeg_busy:
                break
            await asyncio.sleep(0.1)
        if store.state.tool == "covers":
            await store.load_cover_source(video)
            store.move(5)
        else:
            store.set_output(os.path.join(os.path.dirname(video), "sample_iwara.mp4"))
            await store.load_source(video)
            if os.environ.get("IWARATOOL_DEMO_STEP") == "run":
                await store.start()
