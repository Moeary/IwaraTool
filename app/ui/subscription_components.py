"""Workers and reusable visual components for subscriptions."""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from PySide6.QtCore import QRect, QThread, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QSplitter, QWidget

from qfluentwidgets import BodyLabel, ListWidget, PrimaryPushButton

from ..core.manager import download_manager as _default_download_manager
from .theme import FluentSplitter, apply_scrollbars, cover_grid_qss, set_secondary_text, style_splitter


class _SubscriptionManagerProxy:
    def __getattr__(self, name: str):
        page_module = sys.modules.get("app.ui.subscription_page")
        manager = getattr(page_module, "download_manager", _default_download_manager)
        return getattr(manager, name)


download_manager = _SubscriptionManagerProxy()


class SubscriptionRefreshWorker(QThread):
    finished = Signal(dict)
    progress = Signal(dict)

    def __init__(
        self,
        source_id: int | list[int] | None = None,
        *,
        ignore_disabled: bool = False,
    ):
        super().__init__()
        if isinstance(source_id, (list, tuple, set)):
            self._source_ids = list(dict.fromkeys(int(value) for value in source_id if value))
        elif source_id:
            self._source_ids = [int(source_id)]
        else:
            self._source_ids = []
        self._ignore_disabled = bool(ignore_disabled)

    def run(self):
        if self._source_ids:
            summaries: list[dict[str, Any]] = []
            total = len(self._source_ids)
            for index, source_id in enumerate(self._source_ids, start=1):
                source = download_manager.subscriptions.get_source(source_id) or {}
                title = str(source.get("title", "") or source.get("source_key", "") or "")
                self.progress.emit(
                    {
                        "stage": "started",
                        "index": index,
                        "total": total,
                        "source_id": source_id,
                        "title": title,
                    }
                )
                result: dict[str, Any]
                try:
                    result = download_manager.refresh_subscription_source(
                        source_id,
                        ignore_enabled=self._ignore_disabled,
                    )
                except TypeError as exc:
                    # Preserve compatibility with lightweight manager fakes that
                    # still expose the original one-argument refresh method.
                    if "ignore_enabled" not in str(exc):
                        raise
                    result = download_manager.refresh_subscription_source(source_id)
                except Exception as exc:
                    result = {
                        "source_id": source_id,
                        "title": title,
                        "error": str(exc),
                    }
                result = dict(result or {})
                result.setdefault("source_id", source_id)
                result.setdefault("title", title)
                summaries.append(result)
                self.progress.emit(
                    {
                        "stage": "finished",
                        "index": index,
                        "total": total,
                        "source_id": source_id,
                        "title": str(result.get("title", "") or title),
                        "summary": result,
                    }
                )
            summary = download_manager._subscription_refresh_summary(summaries)
        else:
            summary = download_manager.refresh_all_subscriptions(self.progress.emit)
        self.finished.emit(summary)


class SubscriptionImportAuthorsWorker(QThread):
    finished = Signal(dict)

    def run(self):
        self.finished.emit(download_manager.import_followed_author_subscriptions())


class SubscriptionEnqueueWorker(QThread):
    finished = Signal(dict)

    def __init__(self, video_ids: list[str], *, rule_id: str = ""):
        super().__init__()
        self._video_ids = list(video_ids)
        self._rule_id = str(rule_id or "")

    def run(self):
        self.finished.emit(
            download_manager.submit_subscription_items(
                self._video_ids,
                rule_id=self._rule_id,
            )
        )


class SubscriptionAvatarWorker(QThread):
    avatar_ready = Signal(int, str, str)
    done = Signal()

    def __init__(self, source_ids: list[int]):
        super().__init__()
        self._source_ids = list(source_ids)

    def run(self):
        for source_id in self._source_ids:
            result = download_manager.refresh_subscription_source_avatar(source_id)
            avatar_path = str(result.get("avatar_path", "") or "")
            if avatar_path:
                self.avatar_ready.emit(
                    int(result.get("source_id", source_id) or source_id),
                    str(result.get("avatar_url", "") or ""),
                    avatar_path,
                )
        self.done.emit()


_DEFAULT_COVER_DOWNLOAD_CONCURRENCY = 6
_MAX_COVER_DOWNLOAD_CONCURRENCY = 16


class SubscriptionThumbnailWorker(QThread):
    thumbnail_ready = Signal(str, str)
    done = Signal()

    def __init__(
        self,
        requests: list[tuple[str, str]],
        *,
        force: bool = False,
        concurrency: int = _DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    ):
        super().__init__()
        self._requests = list(requests)
        self._force = bool(force)
        self.succeeded = 0
        self.failed = 0
        self._concurrency = max(1, min(_MAX_COVER_DOWNLOAD_CONCURRENCY, int(concurrency)))

    def run(self):
        thread_state = threading.local()
        clients: list[Any] = []
        clients_lock = threading.Lock()

        def fetch(request: tuple[str, str]) -> tuple[str, str]:
            video_id, thumbnail_url = request
            if self.isInterruptionRequested():
                return video_id, ""
            client = getattr(thread_state, "api_client", None)
            if client is None:
                create_client = getattr(download_manager, "create_worker_api_client", None)
                if callable(create_client):
                    client = create_client()
                    thread_state.api_client = client
                    with clients_lock:
                        clients.append(client)
            try:
                try:
                    path = download_manager.cache_subscription_thumbnail(
                        video_id,
                        thumbnail_url,
                        force=self._force,
                        api_client=client,
                    )
                except TypeError as exc:
                    # Compatibility with manager fakes and older extensions
                    # that have force= but not api_client=, or only two args.
                    if "api_client" not in str(exc) and "force" not in str(exc):
                        raise
                    try:
                        path = download_manager.cache_subscription_thumbnail(
                            video_id,
                            thumbnail_url,
                            force=self._force,
                        )
                    except TypeError as force_exc:
                        if "force" not in str(force_exc):
                            raise
                        path = download_manager.cache_subscription_thumbnail(video_id, thumbnail_url)
                return video_id, path or ""
            except Exception:
                return video_id, ""

        executor = ThreadPoolExecutor(
            max_workers=min(self._concurrency, len(self._requests)),
            thread_name_prefix="subscription-cover",
        )
        try:
            futures = [executor.submit(fetch, request) for request in self._requests]
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    break
                video_id, path = future.result()
                if path:
                    self.succeeded += 1
                    self.thumbnail_ready.emit(video_id, path)
                else:
                    self.failed += 1
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            close_client = getattr(download_manager, "close_worker_api_client", None)
            if callable(close_client):
                for client in clients:
                    close_client(client)
            self.done.emit()


_CONTROL_HEIGHT = 36
_ROW_SPACING = 10
_DEFAULT_SUBSCRIPTION_GRID_COLUMNS = 3
_MAX_SUBSCRIPTION_GRID_COLUMNS = 8
_SOURCE_SORT_OPTIONS = (
    "title",
    "created_at",
    "last_checked_at",
    "new_count",
    "undownloaded_count",
    "item_count",
    "source_origin",
    "source_key",
)
_SOURCE_SORT_FIELDS = frozenset(_SOURCE_SORT_OPTIONS)



class ResponsiveCoverList(ListWidget):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


def _style_action_button(button: PrimaryPushButton, *, min_width: int = 0):
    button.setFixedHeight(_CONTROL_HEIGHT)
    if min_width:
        button.setMinimumWidth(min_width)
    font = button.font()
    if font.pointSize() < 10:
        font.setPointSize(10)
    button.setFont(font)


def _style_inline_label(label: BodyLabel):
    label.setFixedHeight(_CONTROL_HEIGHT)
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    set_secondary_text(label)


def _apply_fluent_scrollbars(widget: QWidget):
    """Use compact Fluent scrollbars without replacing the view's theme QSS."""
    apply_scrollbars(widget)


def _thumbnail_list_style() -> str:
    """Theme-aware canvas and item colors for the native cover list."""
    return cover_grid_qss("SubscriptionThumbnailList")


def _grid_text_height(list_widget: ListWidget, width: int, fallback_lines: int) -> int:
    """Measure the tallest cover caption after Qt word-wrapping it."""

    text_width = max(1, int(width))
    height = QFontMetrics(list_widget.font()).lineSpacing() * max(1, fallback_lines)
    for index in range(list_widget.count()):
        item = list_widget.item(index)
        if item is None:
            continue
        metrics = QFontMetrics(item.font())
        height = max(
            height,
            metrics.boundingRect(
                QRect(0, 0, text_width, 10000),
                Qt.TextFlag.TextWordWrap,
                item.text(),
            ).height(),
        )
    return height


# The subscription page shares the app-wide splitter look.
_FluentContentSplitter = FluentSplitter


def _style_content_splitter(splitter: QSplitter):
    """Re-apply the shared splitter colors after a theme switch."""
    if isinstance(splitter, FluentSplitter):
        splitter.refresh_theme_style()
    else:
        style_splitter(splitter)
