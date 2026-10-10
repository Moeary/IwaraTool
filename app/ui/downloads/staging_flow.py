"""Add videos to the download workbench's waiting list from any page.

The pre-check (which may hit the site, e.g. for an author's whole list) runs
in the background; the videos then wait on the workbench for confirmation and
a notice offers to go there.  A lone downloadable video may start at once
(Settings: direct_download_single).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from qfluentwidgets import InfoBar, InfoBarPosition, PushButton

from ...config import app_config
from ...core.rules import active_rule_id
from ...core.search.models import SearchVideo
from ...i18n import tr
from ..worker_lifecycle import ManagedThread, on_finished, stop_qthreads
from .page_dependencies import download_manager


def video_payload(video: SearchVideo, video_id: str = "") -> dict[str, Any]:
    """An API-shaped dict for the pre-check (rule checks, file-name example)."""

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


def go_to_workbench(host: QWidget):
    window = host.window()
    page = getattr(window, "_download_page", None)
    if page is not None and hasattr(window, "switchTo"):
        window.switchTo(page)


class StageWorker(ManagedThread):
    """Pre-check (``job`` returns a preview) and stage it, off the GUI thread."""

    result_ready = Signal(dict)

    def __init__(self, job: Callable[[], dict[str, Any]], direct_single: bool):
        super().__init__()
        self._job = job
        self._direct_single = direct_single

    def run(self):
        try:
            result = download_manager.stage_preview(self._job(), direct_single=self._direct_single)
        except Exception as exc:  # a failed lookup must still end the busy state
            result = {"error": str(exc), "staged": 0}
        if not self.isInterruptionRequested():
            self.result_ready.emit(result)


class StagingFlow(QObject):
    """One pre-check at a time for ``host``, then a notice (with "Review") about the result."""

    busy_changed = Signal(bool)
    staged = Signal(dict)  # the stage_preview() result, once it is on the workbench

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._worker: StageWorker | None = None
        self._info_bar = None

    def busy(self) -> bool:
        return self._worker is not None

    @property
    def worker(self) -> StageWorker | None:
        """The running pre-check, for a page's shutdown list."""
        return self._worker

    def start(self, job: Callable[[], dict[str, Any]], *, busy_text: str = "", allow_direct: bool = True) -> bool:
        """Pre-check what ``job`` lists and add it to the waiting list; False if one is running."""

        if self._worker is not None:
            return False
        self._info_bar = InfoBar.info(
            title=tr("Checking", "正在预解析", "確認中"),
            content=busy_text or tr(
                "Checking what is already here and what the rule keeps…",
                "正在核对已有项、站点可用性与下载规则…",
                "保存済み・利用可否・ルールを確認しています…",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=False,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self._host,
        )
        worker = StageWorker(job, allow_direct and bool(app_config.direct_download_single))
        worker.result_ready.connect(self._on_ready)
        on_finished(worker, self._host, self._on_finished)
        self._worker = worker
        self.busy_changed.emit(True)
        worker.start()
        return True

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

    def _on_ready(self, result: dict[str, Any]):
        self._close_info_bar()
        if result.get("error"):
            self._notice(InfoBar.error, tr("Failed", "失败", "失敗"), str(result["error"]))
            return
        if result.get("direct"):
            text = (tr("Marked as downloaded", "已标记为已下载", "保存済みにしました") if result.get("marked")
                    else tr("Download started", "已开始下载", "ダウンロードを開始しました"))
            self._notice(InfoBar.success, text, tr("One video, so it did not wait for confirmation.",
                                                   "单个可下载视频，未进入待确认。", "1 本のため確認を省略しました。"))
            return
        self.staged.emit(result)
        if not result.get("staged"):
            self._notice(InfoBar.warning, tr("Nothing new", "没有新内容", "新しい項目なし"),
                         tr("These videos are already in the task list.", "这些作品已在任务列表中。", "既にタスク一覧にあります。"))
            return
        self._notice(
            InfoBar.success,
            tr(f"{result['staged']} waiting for confirmation", f"已加入待确认 {result['staged']} 个", f"確認待ちに {result['staged']} 件"),
            tr(
                f"Ready {result.get('ready', 0)} · skipped {result.get('skip', 0)} · cannot download {result.get('unavailable', 0)}",
                f"可下载 {result.get('ready', 0)} · 跳过 {result.get('skip', 0)} · 不能下 {result.get('unavailable', 0)}",
                f"保存可 {result.get('ready', 0)}・スキップ {result.get('skip', 0)}・不可 {result.get('unavailable', 0)}",
            ),
            review=True,
        )

    def _notice(self, kind, title: str, content: str, *, review: bool = False):
        bar = kind(
            title=title, content=content, orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=6000 if review else 3500, parent=self._host,
        )
        if review and getattr(self._host.window(), "_download_page", None) is not self._host:
            button = PushButton(tr("Review", "去确认", "確認する"), bar)
            button.clicked.connect(lambda: (go_to_workbench(self._host), bar.close()))
            bar.addWidget(button)

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return stop_qthreads([self._worker], timeout_ms=timeout_ms)


def flow_for(host: QWidget) -> StagingFlow:
    """The host's own flow, created the first time it is needed."""

    flow = getattr(host, "_staging_flow", None)
    if not isinstance(flow, StagingFlow):
        flow = StagingFlow(host)
        host._staging_flow = flow
    return flow


def stage_videos(host: QWidget, videos: list[SearchVideo], *, label: str, rule_id: str = "") -> bool:
    """Add videos a page already shows (home, author page, detail page) to the waiting list."""

    payloads = [video_payload(video) for video in videos]
    if not payloads:
        return False
    rule = rule_id or active_rule_id()
    return flow_for(host).start(lambda: download_manager.preview_videos(payloads, rule_id=rule, label=label))
