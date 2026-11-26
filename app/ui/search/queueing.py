"""Add selected search results to the download queue (rules in ``presentation.search.queueing``)."""

from __future__ import annotations

from typing import Any

from ...presentation.search.queueing import (
    clean_ids,
    is_queueable,
    no_link_message,
    queue_label,
    queue_payloads,
    queue_selection,
    resolve_failed_message,
    resolving_text,
    skipped_message,
)
from ..common.worker_lifecycle import on_finished
from .page_dependencies import download_manager
from .oreno_workers import SearchQueueResolveWorker


class SearchQueueingMixin:
    """Add selected search results to the download queue."""

    @staticmethod
    def _is_queueable(value: dict[str, Any]) -> bool:
        """Videos and whole playlists can be queued; image posts cannot."""

        return is_queueable(value)

    def _queue_selected(self):
        selection = queue_selection(self._selected_data())
        if selection.warning:
            self._show_warning(selection.warning)
            return

        rule_id = self._rule_picker.selected_rule_id()
        self._queue_playlist_urls = selection.playlist_urls
        self._queue_videos = selection.known_videos()

        if selection.needs_resolution:
            if self._queue_resolve_worker is not None and self._queue_resolve_worker.isRunning():
                return
            worker = SearchQueueResolveWorker(
                selection.videos,
                concurrency=self._search_resolution_concurrency(),
            )
            self._queue_resolve_worker = worker
            self._queue_resolve_rule_id = rule_id
            self._queue_selected_btn.setEnabled(False)
            self._status_label.setText(resolving_text())
            worker.result_ready.connect(self._on_queue_resolved)
            on_finished(worker, self, self._cleanup_queue_resolve_worker)
            worker.start()
            return

        self._enqueue_video_ids(selection.video_ids(), rule_id=rule_id)

    def _enqueue_video_ids(
        self,
        ids: list[str],
        *,
        skipped: int = 0,
        errors: list[str] | None = None,
        rule_id: str = "",
    ):
        ids = clean_ids(ids)
        errors = errors or []
        playlist_urls = list(self._queue_playlist_urls)
        if not ids and not playlist_urls:
            self._show_warning(no_link_message(errors))
            self._sync_selection_buttons()
            return
        selected_rule_id = str(rule_id or self._rule_picker.selected_rule_id())
        payloads = queue_payloads(ids, self._queue_videos)
        label = queue_label()
        self._staging_flow.start(
            lambda: download_manager.preview_selection(payloads, playlist_urls, rule_id=selected_rule_id, label=label),
            allow_direct=not playlist_urls,
        )
        if skipped:
            self._show_warning(skipped_message(skipped))
        if errors:
            self._show_warning(errors[0])

    def _on_queue_resolved(self, result: object):
        if not isinstance(result, dict):
            self._show_warning(resolve_failed_message())
            return
        self._enqueue_video_ids(
            list(result.get("ids") or []),
            skipped=int(result.get("skipped") or 0),
            errors=[str(error) for error in result.get("errors") or []],
            rule_id=self._queue_resolve_rule_id,
        )
        self._sync_selection_buttons()

    def _cleanup_queue_resolve_worker(self, worker: SearchQueueResolveWorker):
        if self._queue_resolve_worker is worker:
            self._queue_resolve_worker = None
            self._queue_resolve_rule_id = ""
        worker.deleteLater()
        self._sync_selection_buttons()
