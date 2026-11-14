"""Tools page: pick a tool on the left, use it on the right."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon, ListWidget, TitleLabel

from ...config import app_config
from ...i18n import tr
from ..theme import PAGE_MARGINS, PAGE_SPACING
from .common import ffmpeg_state


@dataclass(frozen=True)
class ToolEntry:
    key: str
    icon: FluentIcon
    title: str
    summary: str
    factory: Callable[[QWidget], QWidget]


def _compress_tool(parent: QWidget) -> QWidget:
    from .compress_tool import CompressTool

    return CompressTool(parent)


def _cover_tool(parent: QWidget) -> QWidget:
    from .cover_tool import CoverTool

    return CoverTool(parent)


def tool_entries() -> tuple[ToolEntry, ...]:
    """Every tool, in list order; built on demand so labels follow the language."""

    return (
        ToolEntry(
            "compress",
            FluentIcon.VIDEO,
            tr("Upload Compressor", "上传压缩", "アップロード用圧縮"),
            tr(
                "Fit a video under Iwara's 300 / 600 MB limit as H.264 + AAC MP4, at least 720p.",
                "把视频压到 Iwara 的 300 / 600MB 上限以内：H.264 + AAC、MP4，不低于 720p。",
                "Iwara の 300 / 600 MB 上限に収まるよう H.264 + AAC の MP4（720p 以上）に変換します。",
            ),
            _compress_tool,
        ),
        ToolEntry(
            "covers",
            FluentIcon.PHOTO,
            tr("Cover Preview", "封面预览", "カバーのプレビュー"),
            tr(
                "See the 16 screenshots Iwara will offer as the cover before uploading.",
                "上传前预览 Iwara 会给出的 16 张封面截图。",
                "アップロード前に Iwara が提示する 16 枚のカバー候補を確認します。",
            ),
            _cover_tool,
        ),
    )


class ToolsInterface(QWidget):
    """Hosts the tools; each one is built the first time it is opened."""

    open_settings_requested = Signal(str)  # settings category

    _TOOL_KEY = "tools_current_v1"
    _NAV_WIDTH = 216

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToolsInterface")
        self._entries = tool_entries()
        self._index = {entry.key: row for row, entry in enumerate(self._entries)}
        self._tools: dict[str, QWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)
        root.addWidget(TitleLabel(tr("Tools", "工具", "ツール"), self))

        row = QHBoxLayout()
        row.setSpacing(20)
        self._nav = ListWidget(self)
        self._nav.setObjectName("toolsNav")
        self._nav.setFixedWidth(self._NAV_WIDTH)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        row.addWidget(self._nav)
        self._stack = QStackedWidget(self)
        row.addWidget(self._stack, 1)
        root.addLayout(row, 1)

        for entry in self._entries:
            item = QListWidgetItem(entry.icon.icon(), entry.title)
            item.setSizeHint(QSize(self._NAV_WIDTH - 8, 44))
            item.setToolTip(entry.summary)
            self._nav.addItem(item)
            placeholder = QWidget(self._stack)
            self._stack.addWidget(placeholder)

        self._nav.currentRowChanged.connect(self._on_row_changed)
        saved = str(app_config.get_ui_value(self._TOOL_KEY, self._entries[0].key) or "")
        self._initial_row = self._index.get(saved, 0)
        self._shown = False

    def showEvent(self, event):
        # Tools (and the ffmpeg lookup) start when the page is first opened,
        # not at app launch.
        if not self._shown:
            self._shown = True
            ffmpeg_state().ensure()
            self._nav.setCurrentRow(self._initial_row)
        super().showEvent(event)

    def _on_row_changed(self, row: int):
        if not 0 <= row < len(self._entries):
            return
        entry = self._entries[row]
        self._stack.setCurrentWidget(self.tool(entry.key))
        app_config.set_ui_value(self._TOOL_KEY, entry.key)

    def tool(self, key: str) -> QWidget:
        """The tool widget for ``key``, created on first use."""

        existing = self._tools.get(key)
        if existing is not None:
            return existing
        row = self._index[key]
        widget = self._entries[row].factory(self._stack)
        open_settings = getattr(widget, "open_settings", None)
        if open_settings is not None:
            open_settings.connect(lambda: self.open_settings_requested.emit("tools"))
        covers_requested = getattr(widget, "covers_requested", None)
        if covers_requested is not None:
            covers_requested.connect(self.open_covers)
        placeholder = self._stack.widget(row)
        self._stack.insertWidget(row, widget)
        self._stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self._tools[key] = widget
        return widget

    def select_tool(self, key: str):
        row = self._index.get(key)
        if row is None:
            return
        self._shown = True
        if self._nav.currentRow() == row:
            self._on_row_changed(row)
        else:
            self._nav.setCurrentRow(row)

    def open_covers(self, path: str):
        """Show the cover preview for ``path`` (the compressor's result)."""

        self.select_tool("covers")
        self.tool("covers").load_file(path, reload=True)  # the file may have been rewritten

    @property
    def busy(self) -> bool:
        return any(getattr(tool, "busy", False) for tool in self._tools.values())

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        from ..worker_lifecycle import stop_qthreads

        results = [stop_qthreads([ffmpeg_state().worker()], timeout_ms=timeout_ms)]
        for tool in self._tools.values():
            stop = getattr(tool, "shutdown", None)
            if callable(stop):
                results.append(bool(stop(timeout_ms=timeout_ms)))
        return all(results)
