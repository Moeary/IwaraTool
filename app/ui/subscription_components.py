"""Workers and reusable visual components for subscriptions."""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from PySide6.QtCore import QRect, QThread, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QSplitter, QSplitterHandle, QWidget

from qfluentwidgets import BodyLabel, ListWidget, PrimaryPushButton, isDarkTheme

from ..core.manager import download_manager as _default_download_manager


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
        self._concurrency = max(1, min(_MAX_COVER_DOWNLOAD_CONCURRENCY, int(concurrency)))

    def run(self):
        thread_state = threading.local()
        clients: list[Any] = []
        clients_lock = threading.Lock()

        def fetch(request: tuple[str, str]) -> tuple[str, str]:
            video_id, thumbnail_url = request
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
                    self.thumbnail_ready.emit(video_id, path)
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


def _apply_fluent_scrollbars(widget: QWidget):
    """Use compact Fluent scrollbars without replacing the view's theme QSS."""
    if isDarkTheme():
        track = "rgba(255, 255, 255, 0.06)"
        handle = "rgba(255, 255, 255, 0.30)"
        hover = "rgba(255, 255, 255, 0.46)"
        pressed = "rgba(255, 255, 255, 0.58)"
    else:
        track = "rgba(0, 0, 0, 0.045)"
        handle = "rgba(0, 145, 158, 0.54)"
        hover = "rgba(0, 128, 140, 0.70)"
        pressed = "rgba(0, 112, 124, 0.82)"
    qss = f"""
        QScrollBar:vertical {{
            background: {track};
            width: 10px;
            margin: 4px 2px 4px 2px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical {{
            background: {handle};
            min-height: 36px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {hover}; }}
        QScrollBar::handle:vertical:pressed {{ background: {pressed}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: {track};
            height: 10px;
            margin: 2px 4px 2px 4px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal {{
            background: {handle};
            min-width: 36px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {hover}; }}
        QScrollBar::handle:horizontal:pressed {{ background: {pressed}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
            width: 0px;
        }}
    """
    # Setting this QSS on a TableWidget/ListWidget replaces qfluentwidgets'
    # theme stylesheet.  Style only their native scrollbars instead.
    vertical = getattr(widget, "verticalScrollBar", lambda: None)()
    horizontal = getattr(widget, "horizontalScrollBar", lambda: None)()
    if vertical is not None:
        vertical.setStyleSheet(qss)
    if horizontal is not None:
        horizontal.setStyleSheet(qss)


def _thumbnail_list_style() -> str:
    """Theme-aware canvas and item colors for the native cover list."""
    if isDarkTheme():
        border = "rgba(255, 255, 255, 0.13)"
        foreground = "#f2f2f2"
        hover = "rgba(255, 255, 255, 0.07)"
        selected = "#244954"
        selected_border = "#18c4cf"
    else:
        border = "rgba(0, 0, 0, 0.15)"
        foreground = "#202428"
        hover = "rgba(0, 0, 0, 0.045)"
        selected = "#c9f0f3"
        selected_border = "#008c98"
    return f"""
        QListWidget#SubscriptionThumbnailList {{
            background-color: transparent;
            border: 1px solid {border};
            border-radius: 4px;
            outline: none;
            color: {foreground};
        }}
        QListWidget#SubscriptionThumbnailList::item {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 7px;
            color: {foreground};
            padding: 0px;
        }}
        QListWidget#SubscriptionThumbnailList::item:hover {{
            background-color: {hover};
        }}
        QListWidget#SubscriptionThumbnailList::item:selected {{
            background-color: {selected};
            border-color: {selected_border};
            color: {foreground};
        }}
    """


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


class _FluentSplitterHandle(QSplitterHandle):
    """Small rounded grip that makes the otherwise subtle splitter discoverable."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self._grip = QFrame(self)
        self._grip.setObjectName("FluentSplitterGrip")
        self._grip.setFrameShape(QFrame.Shape.NoFrame)
        self._grip.setStyleSheet(
            "QFrame#FluentSplitterGrip { background: rgba(0, 160, 170, 0.48); border-radius: 3px; }"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.orientation() == Qt.Orientation.Vertical:
            grip_width, grip_height = min(56, max(32, self.width() - 12)), 4
        else:
            grip_width, grip_height = 4, min(56, max(32, self.height() - 12))
        self._grip.setGeometry(
            max(0, (self.width() - grip_width) // 2),
            max(0, (self.height() - grip_height) // 2),
            grip_width,
            grip_height,
        )


class _FluentContentSplitter(QSplitter):
    def createHandle(self):
        return _FluentSplitterHandle(self.orientation(), self)


def _style_content_splitter(splitter: QSplitter):
    """Make the vertical content splitter look like a subtle Fluent grab handle."""
    if isDarkTheme():
        base = "rgba(255, 255, 255, 0.06)"
        hover = "rgba(255, 255, 255, 0.18)"
        pressed = "rgba(255, 255, 255, 0.28)"
    else:
        base = "rgba(0, 0, 0, 0.04)"
        hover = "rgba(0, 0, 0, 0.10)"
        pressed = "rgba(0, 0, 0, 0.18)"
    splitter.setStyleSheet(
        f"""
        QSplitter::handle {{ background: {base}; }}
        QSplitter::handle:horizontal {{ height: 10px; }}
        QSplitter::handle:vertical {{ width: 10px; }}
        QSplitter::handle:hover {{ background: {hover}; }}
        QSplitter::handle:pressed {{ background: {pressed}; }}
        """
    )


