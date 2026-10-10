"""Resolve Oreno3D results to their Iwara videos in the background."""

from __future__ import annotations

import webbrowser
from typing import Any

from PySide6.QtCore import QTimer

from qfluentwidgets import LineEdit

from ...core.search import SearchVideo, normalize_video
from ...i18n import tr
from .result_authors import _author_subscription_target
from .oreno_workers import SearchOrenoLinkWorker
from ..worker_lifecycle import on_finished
from .page_dependencies import signal_bus


class SearchOrenoLinksMixin:
    """Resolve Oreno3D results to their Iwara videos in the background."""

    def _start_oreno_link_resolution(
        self,
        videos: list[SearchVideo],
        *,
        priority: bool = False,
        hydrate_metadata: bool = True,
    ):
        pending = {
            video.video_id
            for worker in self._oreno_link_workers
            if worker.isRunning() and worker.generation == self._generation
            for video in worker.videos
        }
        candidates = [
            video
            for video in videos
            if video.source_kind == "oreno3d"
            and not video.iwara_url
            and not video.download_video_id
            and (priority or video.video_id not in pending)
        ]
        if not candidates:
            return
        worker = SearchOrenoLinkWorker(
            self._generation,
            candidates,
            concurrency=1 if priority else self._search_resolution_concurrency(),
            hydrate_metadata=hydrate_metadata,
        )
        self._oreno_link_workers.append(worker)
        worker.item_ready.connect(self._on_oreno_link_item)
        worker.result_ready.connect(self._on_oreno_links_resolved)
        worker.progress.connect(self._on_oreno_link_progress)
        on_finished(worker, self, self._cleanup_oreno_link_worker)
        worker.start()

    def _on_oreno_link_progress(self, current: int, total: int):
        if total <= 0:
            return
        self._status_label.setText(
            tr(
                f"Loading Iwara IDs {current}/{total}…",
                f"正在加载 Iwara ID {current}/{total}…",
                f"Iwara IDを読み込み中 {current}/{total}…",
            )
        )

    def _apply_oreno_link(self, video: SearchVideo, link: dict[str, Any]) -> bool:
        for key in ("oreno_author_id", "oreno_author_name", "oreno_author_url"):
            value = str(link.get(key) or "").strip()
            if value:
                video.raw[key.replace("oreno_", "oreno3d_")] = value
        if isinstance(link.get("iwara_author"), dict) and link["iwara_author"]:
            video.raw["oreno_iwara_author"] = dict(link["iwara_author"])
            video.raw.pop("oreno3d_author_error", None)
        elif link.get("author_error") and not video.raw.get("oreno_iwara_author"):
            video.raw["oreno3d_author_error"] = str(link["author_error"])
        video_id = str(link.get("id") or "").strip()
        if not video_id:
            # The source author remains useful even when the video link is
            # absent. A late failed request must not erase an earlier success.
            if not video.download_video_id:
                video.raw["oreno3d_resolution_error"] = str(link.get("error") or "")
            return True
        video.raw.pop("oreno3d_resolution_error", None)
        iwara_url = str(link.get("url") or "").strip()
        # Oreno3D's cover is already loaded asynchronously when the search
        # card appears.  Keep that bridge cover after Iwara metadata arrives;
        # otherwise the metadata's thumbnail URL invalidates the cache key and
        # the same card visibly downloads a second image from Iwara.
        is_oreno_bridge = video.source_kind == "oreno3d" or bool(video.raw.get("oreno3d_url"))
        bridge_thumbnail_url = str(
            video.raw.get("oreno3d_thumbnail_url") or video.thumbnail_url or ""
        )
        if is_oreno_bridge and "oreno3d_thumbnail_url" not in video.raw:
            video.raw["oreno3d_thumbnail_url"] = bridge_thumbnail_url
        bridge_url = str(video.raw.get("oreno3d_url") or video.source_url or "")
        metadata = link.get("metadata")
        normalized = normalize_video(metadata) if isinstance(metadata, dict) else None
        if normalized is not None:
            for field_name in (
                "title",
                "author_username",
                "author_name",
                "published_at",
                "likes",
                "views",
                "duration",
                "comments",
                "rating",
                "tags",
                "origins",
                "characters",
                "thumbnail_url",
                "slug",
            ):
                setattr(video, field_name, getattr(normalized, field_name))
        if is_oreno_bridge:
            video.thumbnail_url = bridge_thumbnail_url
        if normalized is not None:
            video.raw.update(metadata)
            video.raw["_iwara_metadata_loaded"] = True
            video.raw.pop("iwara_metadata_error", None)
        elif not video.raw.get("_iwara_metadata_loaded") and link.get("metadata_error"):
            video.raw["iwara_metadata_error"] = str(link["metadata_error"])
        video.raw["oreno3d_url"] = bridge_url
        video.raw["oreno3d_id"] = video.video_id.removeprefix("oreno3d:")
        video.download_video_id = video_id
        video.iwara_url = iwara_url or f"https://www.iwara.tv/video/{video_id}"
        video.downloadable = True
        video.source_kind = "iwara"
        video.source_url = video.iwara_url
        video.raw["iwara_id"] = video_id
        video.raw["iwara_url"] = video.iwara_url
        return True

    def _on_oreno_link_item(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        video_key = str(result.get("video_id") or "")
        link = result.get("link")
        if not video_key or not isinstance(link, dict):
            return
        video = next(
            (candidate for candidate in self._all_videos if candidate.video_id == video_key),
            None,
        )
        if video is None or not self._apply_oreno_link(video, link):
            return

        focused = self.focusWidget()
        if video.video_id in self._pending_detail_video_ids:
            detail_id = self._preview_video_id(video)
            if detail_id:
                self._pending_detail_video_ids.discard(video.video_id)
                signal_bus.media_detail_requested.emit("video", detail_id)
            elif link.get("error"):
                self._pending_detail_video_ids.discard(video.video_id)
                self._show_warning(str(link["error"]))
        if video.video_id in self._pending_preview_video_ids:
            preview_id = self._preview_video_id(video)
            if preview_id:
                self._pending_preview_video_ids.discard(video.video_id)
                signal_bus.video_preview_requested.emit(preview_id, video.title, "")
            elif link.get("error"):
                self._pending_preview_video_ids.discard(video.video_id)
                self._show_warning(str(link["error"]))
        if video.video_id in self._pending_open_video_ids:
            if video.iwara_url:
                self._pending_open_video_ids.discard(video.video_id)
                # Open at the ID stage, without waiting for metadata.
                webbrowser.open(video.iwara_url)
            elif link.get("error"):
                self._pending_open_video_ids.discard(video.video_id)
                self._show_warning(str(link["error"]))
        self._update_video_presentation(video)
        self._start_image_loading()
        self._maybe_subscribe_pending_author(video)
        if isinstance(focused, LineEdit) and focused.isVisible():
            QTimer.singleShot(0, lambda focused=focused: self._restore_tag_edit_focus(focused))
        self._update_status()

    def _on_oreno_links_resolved(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        links = result.get("links") or {}
        errors = [str(error) for error in result.get("errors") or []]
        pending_ids = list(self._pending_author_subscription_video_ids)
        for video_key in pending_ids:
            video = next(
                (candidate for candidate in self._all_videos if candidate.video_id == video_key),
                None,
            )
            if video is None:
                self._pending_author_subscription_video_ids.discard(video_key)
                continue
            self._maybe_subscribe_pending_author(video)
            if video_key in self._pending_author_subscription_video_ids and (
                video_key not in links or errors
            ):
                self._pending_author_subscription_video_ids.discard(video_key)
                self._show_warning(
                    tr(
                        "Could not resolve the Iwara author for this Oreno3D result",
                        "无法解析该 Oreno3D 结果对应的 Iwara 作者",
                        "この Oreno3D 結果に対応する Iwara 作者を解析できません",
                    )
                )
        self._update_status()
        if errors and not any(link.get("id") for link in links.values()):
            self._show_warning(errors[0])

    def _maybe_subscribe_pending_author(self, video: SearchVideo):
        if video.video_id not in self._pending_author_subscription_video_ids:
            return
        target = _author_subscription_target(video)
        if not target:
            return
        self._pending_author_subscription_video_ids.discard(video.video_id)
        self._subscribe_to_author(target)

    def _cleanup_oreno_link_worker(self, worker: SearchOrenoLinkWorker):
        if worker in self._oreno_link_workers:
            self._oreno_link_workers.remove(worker)
        worker.deleteLater()
