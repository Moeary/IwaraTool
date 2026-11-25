"""Fill a Flet window with demo activity for screenshots (``main_flet.py --demo``).

Runs after the window is mounted, on top of ``demo_fixture`` (offline made-up
Iwara): the search route searches, the downloads route queues a few videos and
cancels one.  Nothing here is imported outside demo runs.
"""
from __future__ import annotations

import asyncio

import demo_fixture


async def play(shell, route: str) -> None:
    from app.ui_flet import routes

    destination = routes.parse(route or "/").destination
    if destination == routes.SEARCH:
        store = shell.search
        store.set_source("iwara")
        await store.search(record=False)
    elif destination == routes.DOWNLOADS:
        from app.core.download.manager import download_manager

        for index in range(3, 11):
            download_manager.add_url(f"https://www.iwara.tv/video/{demo_fixture.video(index)['id']}")
        await asyncio.sleep(1.0)
        tasks = download_manager.get_tasks()
        if len(tasks) > 2:
            download_manager.cancel_task(tasks[-1].task_id)
