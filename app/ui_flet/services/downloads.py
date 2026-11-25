"""Downloads for the Flet pages: staging, typed links and task controls.

Every manager call that may block (a pre-check lists an author's videos, a
task control saves the queue) runs with ``asyncio.to_thread``.  The wording
and the rules come from ``presentation``; this module only sequences them and
shows the notices.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ...config import app_config
from ...core.download.manager import download_manager
from ...core.rules import PAGE_RULE_FOLLOW_DEFAULT, active_rule_id, normalize_rule_payload, page_rule_choice, page_rule_id, rule_store
from ...presentation import rule_editing
from ...core.search.resolution import resolve_queue_ids
from ...core.search.limits import configured_resolution_concurrency
from ...logging_setup import get_logger
from ...presentation import rules as rule_text
from ...presentation.downloads import submission
from ...presentation.downloads.staging import checking_text, review_label, stage, staging_notice, video_payload
from ...presentation.search.queueing import clean_ids, no_link_message, queue_label, queue_payloads, queue_selection, resolve_failed_message, skipped_message
from .. import routes
from .notify import notice

logger = get_logger(__name__)


class DownloadService:
    """One per window; pages reach it as ``shell.downloads``."""

    def __init__(self, shell, manager: Any = download_manager):
        self.shell = shell
        self.manager = manager
        self.staging_busy = False

    def _notice(self, kind: str, title: str, content: str = "", **kwargs) -> None:
        notice(self.shell.page, kind, title, content, **kwargs)

    # ── rules ────────────────────────────────────────────────────────────────

    def rule_choices(self) -> list[tuple[str, str]]:
        return rule_text.rule_choices(rule_store.list_available(), active_rule_id())

    @staticmethod
    def page_choice(scope: str) -> str:
        return page_rule_choice(scope)

    @staticmethod
    def resolve_rule(choice: str) -> str:
        if choice and choice != PAGE_RULE_FOLLOW_DEFAULT and rule_store.find(choice):
            return choice
        return active_rule_id()

    @staticmethod
    def remember_page_rule(scope: str, choice: str) -> None:
        rule_editing.choose_page_rule(scope, choice)

    @staticmethod
    def page_rule(scope: str) -> str:
        return page_rule_id(scope)

    @staticmethod
    def rule_payload(rule_id: str) -> dict[str, Any]:
        rule = rule_store.find(rule_id)
        return normalize_rule_payload(rule["payload"] if rule else None)

    # ── staging (the waiting list on the Downloads page) ─────────────────────

    async def stage_job(self, job: Callable[[], dict[str, Any]], *, allow_direct: bool = True, busy_text: str = "") -> dict[str, Any]:
        """Pre-check what ``job`` lists and put it on the waiting list; one at a time."""

        if self.staging_busy:
            return {"error": "busy", "staged": 0}
        self.staging_busy = True
        title, default_text = checking_text()
        self._notice("info", title, busy_text or default_text, duration_ms=60_000)
        direct = allow_direct and bool(app_config.direct_download_single)
        try:
            result = await asyncio.to_thread(stage, self.manager, job, direct_single=direct)
        finally:
            self.staging_busy = False
        result_notice = staging_notice(result)
        on_downloads = self.shell.state.destination == routes.DOWNLOADS
        review = result_notice.review and not on_downloads
        self._notice(
            result_notice.kind,
            result_notice.title,
            result_notice.content,
            action=review_label() if review else "",
            on_action=(lambda: self.shell.select(routes.DOWNLOADS)) if review else None,
            duration_ms=6000 if review else 3500,
        )
        await self.shell.refresh_badges()
        return result

    async def stage_videos(self, videos: list, *, rule_id: str, label: str) -> dict[str, Any]:
        payloads = [video_payload(video) for video in videos]
        if not payloads:
            return {"staged": 0}
        return await self.stage_job(lambda: self.manager.preview_videos(payloads, rule_id=rule_id, label=label))

    async def stage_search_selection(self, values: list[dict[str, Any]], *, rule_id: str) -> dict[str, Any]:
        """Selected search results: resolve Oreno3D links when needed, then stage videos and playlists."""

        selection = queue_selection(values)
        if selection.warning:
            self._notice("warning", selection.warning)
            return {"staged": 0}
        known = selection.known_videos()
        ids, skipped, errors = selection.video_ids(), 0, []
        if selection.needs_resolution:
            try:
                result = await asyncio.to_thread(
                    resolve_queue_ids, self.manager, selection.videos, concurrency=configured_resolution_concurrency()
                )
            except Exception:
                logger.exception("Resolving queued search results failed")
                result = None
            if not isinstance(result, dict):
                self._notice("warning", resolve_failed_message())
                return {"staged": 0}
            ids = list(result.get("ids") or [])
            skipped = int(result.get("skipped") or 0)
            errors = [str(error) for error in result.get("errors") or []]
        ids = clean_ids(ids)
        playlists = list(selection.playlist_urls)
        if not ids and not playlists:
            self._notice("warning", no_link_message(errors))
            return {"staged": 0}
        payloads = queue_payloads(ids, known)
        label = queue_label()
        outcome = await self.stage_job(
            lambda: self.manager.preview_selection(payloads, playlists, rule_id=rule_id, label=label),
            allow_direct=not playlists,
        )
        if skipped:
            self._notice("warning", skipped_message(skipped))
        if errors:
            self._notice("warning", errors[0])
        return outcome

    async def submit_links(self, text: str, *, rule_id: str) -> bool:
        """Typed or pasted links; True when they were taken (the field can be cleared)."""

        urls = submission.split_submitted_urls(text)
        if not urls:
            self._notice("warning", submission.no_links_message())
            return False
        if await asyncio.to_thread(submission.needs_listing_preview, self.manager, urls):
            url = urls[0]
            result = await self.stage_job(
                lambda: self.manager.preview_listing(url, rule_id=rule_id),
                allow_direct=False,
                busy_text=submission.listing_busy_text(),
            )
            return bool(result.get("staged") or result.get("direct"))
        mark_only = submission.is_mark_only(self.rule_payload(rule_id))
        await asyncio.to_thread(submission.queue_links, self.manager, urls, rule_id=rule_id, mark_only=mark_only)
        self._notice("success", submission.submitted_title(), submission.submitted_text(urls, mark_only=mark_only))
        return True

    async def confirm_staged(self, batch_id: str = "") -> dict[str, int]:
        return await asyncio.to_thread(self.manager.confirm_staged, batch_id=batch_id)

    async def discard_staged(self, batch_id: str = "") -> int:
        return await asyncio.to_thread(self.manager.discard_staged, batch_id=batch_id)

    async def staged_batches(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.manager.staged_batches)

    async def staged_example(self, batch_id: str) -> dict[str, str]:
        return await asyncio.to_thread(self.manager.staged_example, batch_id)

    def staged_rule_name(self, rule_id: str) -> str:
        return self.manager.staged_rule_name(rule_id) if rule_id else ""

    async def restage(self, rule_id: str, batch_id: str) -> int:
        return await asyncio.to_thread(self.manager.restage, rule_id, batch_id=batch_id)

    # ── task controls ────────────────────────────────────────────────────────

    async def run(self, method: str, *args, **kwargs):
        """Call a manager task control off the loop (``cancel_task``, ``retry_task``…)."""

        return await asyncio.to_thread(getattr(self.manager, method), *args, **kwargs)
