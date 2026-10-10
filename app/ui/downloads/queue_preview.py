"""Look before queueing: classify in the background, confirm in a dialog, then queue.

Every way of adding downloads (the workbench's author / playlist links, the
search, subscription, home and author pages, a video's detail page) first
shows how many videos there are, how many cannot or will not download, where
the files go and what they will be called, under a rule the user can change.
Cancelling keeps the selection as a pending stack on the download workbench.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from qfluentwidgets import InfoBar, InfoBarPosition

from ...core.download.listing_preview import is_downloadable
from ...core.rules import active_rule_id
from ...core.search.models import SearchVideo
from ...i18n import tr
from ...signal_bus import signal_bus
from ..worker_lifecycle import ManagedThread, on_finished, stop_qthreads
from .page_dependencies import download_manager
from .queue_confirm_dialog import OUTCOME_CONFIRM, QueueConfirmDialog


OnChosen = Callable[[dict[str, Any], list[dict[str, Any]]], None]


def video_payload(video: SearchVideo, video_id: str = "") -> dict[str, Any]:
    """An API-shaped dict for the confirmation (rule checks, file-name example)."""

    video_id = video_id or video.download_video_id or video.video_id
    raw = video.raw if isinstance(video.raw, dict) else {}
    if video.source_kind == "iwara" and str(raw.get("id", "") or "") == video_id:
        return raw
    return {
        "id": video_id,
        "title": video.title,
        "numLikes": video.likes,
        "numViews": video.views,
        "numComments": video.comments,
        "createdAt": video.published_at,
        "tags": list(video.tags),
        "slug": video.slug,
        "rating": video.rating,
        "user": {"username": video.author_username, "name": video.author_name},
        # Likes / views / tags of another site say nothing about the Iwara rule.
        "_stats_known": video.source_kind == "iwara",
    }


def _notify(host: QWidget, title: str, message: str):
    InfoBar.success(
        title=title,
        content=message,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP,
        duration=3500,
        parent=host,
    )


def queue_chosen(host: QWidget, preview: dict[str, Any], chosen: list[dict[str, Any]], *, source_label: str = ""):
    """Queue the confirmed videos under the confirmed rule (or only mark them, if the rule says so)."""

    if preview.get("mark_only"):
        marked = download_manager.mark_video_items_downloaded([item["raw"] for item in chosen])
        signal_bus.history_changed.emit()
        message = tr(f"Marked {marked} as downloaded", f"已标记 {marked} 个为已下载", f"{marked} 件を保存済みにしました")
    else:
        queued = download_manager.enqueue_video_ids(
            [str(item["video_id"]) for item in chosen],
            source_label=source_label or str(preview.get("label", "") or ""),
            rule_id=str(preview.get("rule_id", "") or ""),
        )
        message = tr(f"Queued {queued} videos", f"已加入队列 {queued} 个作品", f"{queued} 件をキューに追加しました")
    _notify(host, tr("Submitted", "已提交", "送信しました"), message)


class PreviewWorker(ManagedThread):
    """Run one preview job (it may hit the network) off the GUI thread."""

    result_ready = Signal(dict)

    def __init__(self, job: Callable[[], dict[str, Any]]):
        super().__init__()
        self._job = job

    def run(self):
        try:
            result = self._job()
        except Exception as exc:  # a failed lookup must still end the busy state
            result = {"error": str(exc), "items": []}
        if not self.isInterruptionRequested():
            self.result_ready.emit(result)


class QueuePreviewFlow(QObject):
    """One confirmation at a time for ``host``: busy notice, dialog, then ``on_chosen``."""

    busy_changed = Signal(bool)

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._worker: PreviewWorker | None = None
        self._info_bar = None

    def busy(self) -> bool:
        return self._worker is not None

    @property
    def worker(self) -> PreviewWorker | None:
        """The running lookup, for a page's shutdown list."""
        return self._worker

    def start(self, job: Callable[[], dict[str, Any]], on_chosen: OnChosen, *, busy_text: str = "") -> bool:
        """Run ``job`` (returns a preview dict); False if a preview is already running."""

        if self._worker is not None:
            return False
        self._info_bar = InfoBar.info(
            title=tr("Checking", "正在检查", "確認中"),
            content=busy_text or tr(
                "Listing the videos, what is already here and what the rule keeps…",
                "正在列出作品，并核对已有项与下载规则…",
                "動画・保存済み・ルールを確認しています…",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=False,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self._host,
        )
        worker = PreviewWorker(job)
        worker.result_ready.connect(lambda preview: self._on_ready(preview, on_chosen))
        on_finished(worker, self._host, self._on_finished)
        self._worker = worker
        self.busy_changed.emit(True)
        worker.start()
        return True

    def open_stack(self, stack_id: str, on_chosen: OnChosen | None = None):
        """Re-open a pending stack (local data only) in the same dialog."""

        preview = download_manager.open_pending_stack(stack_id)
        if preview is None:
            return
        self.show(preview, on_chosen or (lambda p, chosen: queue_chosen(self._host, p, chosen)), stack_id=stack_id)

    def _on_finished(self, worker):
        if worker is self._worker:
            self._worker = None
            self._close_info_bar()
            self.busy_changed.emit(False)
        worker.deleteLater()

    def _close_info_bar(self):
        info_bar, self._info_bar = self._info_bar, None
        if info_bar is not None:
            try:
                info_bar.close()
            except RuntimeError:
                pass  # already gone

    def _on_ready(self, preview: dict[str, Any], on_chosen: OnChosen):
        self._close_info_bar()
        error = str(preview.get("error", "") or "")
        if error:
            InfoBar.error(
                title=tr("Failed", "失败", "失敗"),
                content=error,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=6000,
                parent=self._host,
            )
            return
        self.show(preview, on_chosen)

    def show(self, preview: dict[str, Any], on_chosen: OnChosen, *, stack_id: str = ""):
        """The dialog; confirm runs ``on_chosen``, cancel keeps it as a pending stack."""

        dialog = QueueConfirmDialog(
            preview, reclassify=download_manager.reclassify_preview, from_stack=bool(stack_id), parent=self._host.window(),
        )
        confirmed = dialog.ask() == OUTCOME_CONFIRM
        final, checked, chosen = dialog.preview, dialog.checked_ids(), dialog.checked_items()
        dialog.deleteLater()
        if confirmed:
            on_chosen(final, chosen)
        if stack_id:
            # A stack keeps the latest rule and checks, and stays until what it wanted is downloaded.
            download_manager.save_pending_stack(final, checked, stack_id=stack_id)
            if confirmed:
                download_manager.prune_pending_stacks()
            return
        if not confirmed and any(is_downloadable(item) for item in final.get("items") or []):
            download_manager.save_pending_stack(final, checked)
            _notify(
                self._host,
                tr("Kept for later", "已留在待下载", "保留しました"),
                tr(
                    "Find it on the download workbench under Pending.",
                    "可在下载工作台的“待下载”里随时再打开。",
                    "ダウンロード画面の「保留」から開き直せます。",
                ),
            )

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return stop_qthreads([self._worker], timeout_ms=timeout_ms)


def flow_for(host: QWidget) -> QueuePreviewFlow:
    """The host's own flow, created the first time it is needed."""

    flow = getattr(host, "_queue_preview_flow", None)
    if not isinstance(flow, QueuePreviewFlow):
        flow = QueuePreviewFlow(host)
        host._queue_preview_flow = flow
    return flow


def confirm_videos(host: QWidget, videos: list[SearchVideo], *, label: str, rule_id: str = "") -> bool:
    """Confirm, then queue, videos a page already shows (home, author page, detail page)."""

    payloads = [video_payload(video) for video in videos]
    if not payloads:
        return False
    rule = rule_id or active_rule_id()
    return flow_for(host).start(
        lambda: download_manager.preview_videos(payloads, rule_id=rule, label=label),
        lambda preview, chosen: queue_chosen(host, preview, chosen, source_label=label),
    )
