"""Workers and reusable visual components for subscriptions."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QSplitter, QWidget

from qfluentwidgets import BodyLabel, ListWidget, PrimaryPushButton

from ...presentation.subscription.text import SOURCE_SORT_OPTIONS
from ...core.search.limits import DEFAULT_COVER_DOWNLOAD_CONCURRENCY, MAX_COVER_DOWNLOAD_CONCURRENCY
from ...core.subscription.batch import cache_thumbnails, refresh_avatars, refresh_sources, source_id_list
from ..common.worker_lifecycle import ManagedThread
from ..style.theme import FluentSplitter, apply_scrollbars, cover_grid_qss, set_secondary_text, style_splitter
from .page_dependencies import download_manager


class SubscriptionRefreshWorker(ManagedThread):
    # Never shadow QThread.finished: owners release the thread only on the
    # native signal, after run() has returned.
    result_ready = Signal(dict)
    progress = Signal(dict)

    def __init__(
        self,
        source_id: int | list[int] | None = None,
        *,
        ignore_disabled: bool = False,
    ):
        super().__init__()
        self._source_ids = source_id_list(source_id)
        self._ignore_disabled = bool(ignore_disabled)

    def run(self):
        self.result_ready.emit(
            refresh_sources(
                download_manager,
                self._source_ids,
                ignore_disabled=self._ignore_disabled,
                on_progress=self.progress.emit,
            )
        )


class SubscriptionImportAuthorsWorker(ManagedThread):
    result_ready = Signal(dict)

    def run(self):
        self.result_ready.emit(download_manager.import_followed_author_subscriptions())


class SubscriptionAvatarWorker(ManagedThread):
    avatar_ready = Signal(int, str, str)

    def __init__(self, source_ids: list[int]):
        super().__init__()
        self._source_ids = list(source_ids)

    def run(self):
        refresh_avatars(
            download_manager, self._source_ids, self.avatar_ready.emit, should_stop=self.isInterruptionRequested,
        )


_DEFAULT_COVER_DOWNLOAD_CONCURRENCY = DEFAULT_COVER_DOWNLOAD_CONCURRENCY
_MAX_COVER_DOWNLOAD_CONCURRENCY = MAX_COVER_DOWNLOAD_CONCURRENCY


class SubscriptionThumbnailWorker(ManagedThread):
    thumbnail_ready = Signal(str, str)
    done = Signal()

    def __init__(
        self,
        requests: list[tuple[str, str]],
        *,
        force: bool = False,
        concurrency: int = DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    ):
        super().__init__()
        self._requests = list(requests)
        self._force = bool(force)
        self.succeeded = 0
        self.failed = 0
        self._concurrency = concurrency

    def run(self):
        try:
            self.succeeded, self.failed = cache_thumbnails(
                download_manager,
                self._requests,
                self.thumbnail_ready.emit,
                force=self._force,
                concurrency=self._concurrency,
                should_stop=self.isInterruptionRequested,
            )
        finally:
            self.done.emit()


_CONTROL_HEIGHT = 36
_ROW_SPACING = 10
_DEFAULT_SUBSCRIPTION_GRID_COLUMNS = 3
_MAX_SUBSCRIPTION_GRID_COLUMNS = 8
_SOURCE_SORT_OPTIONS = SOURCE_SORT_OPTIONS
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
