"""Subscribe to a result's author, resolving Oreno3D authors to Iwara first."""

from __future__ import annotations

import webbrowser
from typing import Any

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...core.search import SearchVideo, normalize_video
from ...i18n import tr
from .result_authors import (
    _author_source_info,
    _author_subscription_target,
    _extract_iwara_video_id,
)
from ..worker_lifecycle import on_finished
from .page_dependencies import download_manager, signal_bus
from .author_workers import SearchIwaraAuthorWorker, SearchOrenoAuthorWorker


class SearchAuthorSubscriptionMixin:
    """Subscribe to a result's author, resolving Oreno3D authors to Iwara first."""

    @staticmethod
    def _needs_iwara_author_hydration(video: SearchVideo) -> bool:
        raw = video.raw if isinstance(video.raw, dict) else {}
        if (video.source_kind == "oreno3d" or raw.get("oreno3d_url")) and raw.get("_iwara_metadata_loaded") is not True:
            return True
        return bool(
            (video.download_video_id or video.video_id)
            and raw.get("_iwara_metadata_loaded") is not True
            and not _author_subscription_target(video)
        )

    def _resolve_and_subscribe_author(self, video: SearchVideo):
        if video.video_id in self._pending_author_subscription_video_ids:
            self._show_warning(
                tr(
                    "Iwara author resolution is already running",
                    "Iwara 作者解析已在进行中",
                    "Iwara 作者の解析は既に実行中です",
                )
            )
            return
        self._pending_author_subscription_video_ids.add(video.video_id)
        if video.source_kind == "oreno3d" or video.raw.get("oreno3d_url"):
            self._start_oreno_author_resolution(video)
            self._status_label.setText(
                tr(
                    "Finding the durable Oreno3D author page…",
                    "正在查找可长期访问的 Oreno3D 作者页…",
                    "永続的なOreno3D作者ページを検索中…",
                )
            )
            return
        iwara_id = (
            video.download_video_id
            or _extract_iwara_video_id(video.iwara_url)
            or (video.video_id if video.source_kind == "iwara" else "")
        )
        if iwara_id:
            if not video.download_video_id:
                video.download_video_id = iwara_id
                video.iwara_url = video.iwara_url or f"https://www.iwara.tv/video/{iwara_id}"
            self._start_iwara_author_hydration(video)
        elif video.source_kind == "oreno3d":
            self._start_oreno_link_resolution(
                [video],
                priority=True,
                hydrate_metadata=True,
            )
        else:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            self._show_warning(
                tr(
                    "This result has no Iwara video ID to resolve",
                    "当前结果没有可解析的 Iwara 视频 ID",
                    "この結果には解析可能な Iwara 動画 ID がありません",
                )
            )
            return
        self._status_label.setText(
            tr(
                "Resolving the Iwara author…",
                "正在解析 Iwara 作者…",
                "Iwara 作者を解析中…",
            )
        )

    def _start_oreno_author_resolution(self, video: SearchVideo):
        if any(
            worker.isRunning()
            and not worker.isInterruptionRequested()
            and worker.generation == self._generation
            and worker.video_id == video.video_id
            for worker in self._oreno_author_workers
        ):
            return
        worker = SearchOrenoAuthorWorker(self._generation, video)
        self._oreno_author_workers.append(worker)
        worker.result_ready.connect(self._on_oreno_author_result)
        on_finished(worker, self, self._cleanup_oreno_author_worker)
        worker.start()

    def _on_oreno_author_result(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        video_id = str(result.get("video_id") or "").strip()
        video = next((candidate for candidate in self._all_videos if candidate.video_id == video_id), None)
        if video is None:
            return
        resolved = result.get("result")
        resolved = dict(resolved) if isinstance(resolved, dict) else {}
        raw = video.raw if isinstance(video.raw, dict) else {}
        video.raw = raw
        for key in ("oreno_author_url", "oreno_author_name", "oreno_author_id"):
            value = str(resolved.get(key) or "").strip()
            if value:
                raw[key.replace("oreno_", "oreno3d_")] = value
        iwara_author = resolved.get("iwara_author")
        if isinstance(iwara_author, dict) and iwara_author:
            raw["oreno_iwara_author"] = dict(iwara_author)
            raw.pop("oreno3d_author_error", None)
        elif result.get("error") and not raw.get("oreno_iwara_author"):
            raw["oreno3d_author_error"] = str(result["error"])
        if raw.get("oreno3d_author_url"):
            video.raw["oreno3d_author_url"] = str(raw["oreno3d_author_url"])
        self._update_video_presentation(video)
        self._start_image_loading()

        target = self._author_navigation_target(video)
        if video.video_id in self._pending_author_subscription_video_ids:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            if target:
                source_url, source_origin = _author_source_info(video)
                if source_url:
                    self._subscribe_to_author(
                        target,
                        source_url=source_url,
                        source_origin=source_origin,
                    )
                else:
                    self._subscribe_to_author(target)
            else:
                self._show_warning(
                    tr(
                        "No surviving Iwara author could be found from this Oreno3D author page",
                        "无法从该 Oreno3D 作者页找到仍可用的 Iwara 作者",
                        "このOreno3D作者ページから有効なIwara作者を特定できません",
                    )
                )
        self._finish_pending_author_action(video)
        if video.video_id in self._pending_open_author_video_ids:
            self._pending_open_author_video_ids.discard(video.video_id)
            source_url, _source_origin = _author_source_info(video)
            if target and getattr(self, "_open_author_prefers_app", False):
                signal_bus.author_page_requested.emit(target)  # found an Iwara account: stay in the app
            elif source_url:
                webbrowser.open(source_url)
        if result.get("error") and not raw.get("oreno3d_author_url"):
            self._status_label.setText(str(result.get("error")))

    def _cleanup_oreno_author_worker(self, worker: SearchOrenoAuthorWorker):
        if worker in self._oreno_author_workers:
            self._oreno_author_workers.remove(worker)
        worker.deleteLater()

    def _start_iwara_author_hydration(self, video: SearchVideo):
        iwara_id = str(
            video.download_video_id
            or _extract_iwara_video_id(video.iwara_url)
            or (video.video_id if video.source_kind == "iwara" else "")
            or ""
        ).strip()
        if not iwara_id:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            self._finish_pending_author_action(video)
            return
        video.download_video_id = iwara_id
        if any(
            worker.isRunning()
            and not worker.isInterruptionRequested()
            and worker.generation == self._generation
            and worker.video_id == iwara_id
            for worker in self._iwara_author_workers
        ):
            return
        worker = SearchIwaraAuthorWorker(self._generation, iwara_id)
        self._iwara_author_workers.append(worker)
        worker.result_ready.connect(self._on_iwara_author_result)
        on_finished(worker, self, self._cleanup_iwara_author_worker)
        worker.start()

    def _on_iwara_author_result(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        iwara_id = str(result.get("video_id") or "").strip()
        video = next(
            (
                candidate
                for candidate in self._all_videos
                if candidate.download_video_id == iwara_id
            ),
            None,
        )
        if video is None:
            return
        metadata = result.get("metadata")
        if isinstance(metadata, dict) and metadata:
            self._apply_iwara_metadata(video, metadata)
            self._update_video_presentation(video)
            self._start_image_loading()
        target = self._author_navigation_target(video)
        if video.video_id in self._pending_author_subscription_video_ids:
            self._pending_author_subscription_video_ids.discard(video.video_id)
            if target:
                source_url, source_origin = _author_source_info(video)
                if source_url:
                    self._subscribe_to_author(
                        target,
                        source_url=source_url,
                        source_origin=source_origin,
                    )
                else:
                    self._subscribe_to_author(target)
            else:
                self._show_warning(
                    tr(
                        "Iwara video metadata did not contain an author",
                        "Iwara 视频详情中没有作者信息",
                        "Iwara 動画詳細に作者情報がありません",
                    )
                )
        self._finish_pending_author_action(video)
        if result.get("error") and not target:
            self._status_label.setText(str(result.get("error")))

    def _cleanup_iwara_author_worker(self, worker: SearchIwaraAuthorWorker):
        if worker in self._iwara_author_workers:
            self._iwara_author_workers.remove(worker)
        worker.deleteLater()

    def _apply_iwara_metadata(self, video: SearchVideo, metadata: dict[str, Any]):
        normalized = normalize_video(metadata)
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
        video.raw.update(metadata)
        video.raw["_iwara_metadata_loaded"] = normalized is not None
        video.raw["iwara_id"] = video.download_video_id
        video.raw["iwara_url"] = video.iwara_url

    def _subscribe_to_author(
        self,
        target: tuple[str, str, str, str],
        *,
        source_url: str = "",
        source_origin: str = "",
        navigate: bool = False,
    ):
        username, title, remote_id, avatar_url = target
        kwargs: dict[str, str] = {
            "title": title,
            "remote_id": remote_id,
            "avatar_url": avatar_url,
        }
        if str(source_url or "").strip():
            kwargs["source_url"] = str(source_url).strip()
        if str(source_origin or "").strip():
            kwargs["source_origin"] = str(source_origin).strip()
        try:
            source_id = download_manager.add_author_subscription(
                username,
                **kwargs,
            )
        except TypeError as exc:
            # Keep lightweight manager fakes and older integrations usable.
            if not any(name in str(exc) for name in ("title", "remote_id", "avatar_url", "source_url", "source_origin")):
                self._report_author_subscription_failure(username, exc)
                return
            try:
                source_id = download_manager.add_author_subscription(username)
            except Exception as fallback_exc:
                self._report_author_subscription_failure(username, fallback_exc)
                return
        except Exception as exc:  # database/network integrations should never fail silently
            self._report_author_subscription_failure(username, exc)
            return
        source_id = int(source_id or 0)
        if not source_id:
            self._report_author_subscription_failure(username, None)
            return
        try:
            signal_bus.subscription_source_added.emit(int(source_id))
        except Exception as exc:
            # A refresh listener must not hide a successful database insert or
            # suppress the confirmation shown to the user.
            signal_bus.log_message.emit(f"Subscription refresh notification failed: {exc}")
        signal_bus.log_message.emit(f"Author subscription added: @{username}")
        InfoBar.success(
            title=tr("Author added", "作者已加入订阅", "作者を購読に追加しました"),
            content=tr(
                f"@{username} is now in your local subscriptions",
                f"@{username} 已加入本地订阅，可在订阅页查看可下载内容",
                f"@{username} をローカル購読に追加しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self,
        )
        if navigate:
            signal_bus.subscription_source_requested.emit(source_id)
        return source_id

    def _report_author_subscription_failure(self, username: str, error: Exception | None):
        detail = str(error or "").strip()
        content = tr(
            f"Could not add @{username} to subscriptions",
            f"无法将 @{username} 加入订阅",
            f"@{username} を購読に追加できません",
        )
        if detail:
            content += f": {detail}"
        signal_bus.log_message.emit(f"Author subscription failed: @{username} {detail}".strip())
        self._show_warning(content)
