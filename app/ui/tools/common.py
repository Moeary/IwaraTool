"""Pieces the tool pages share: ffmpeg lookup, file input, workers' base."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, CardWidget, FluentIcon, LineEdit, PushButton, ScrollArea, SubtitleLabel

from ...config import app_config
from ...core.tools.ffmpeg import FfmpegError, FfmpegInfo, locate
from ...i18n import tr
from ..theme import CARD_MARGINS, set_secondary_text
from ..worker_lifecycle import ManagedThread

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".ts", ".mpg", ".mpeg")


def video_file_filter() -> str:
    patterns = " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)
    return f"{tr('Videos', '视频', '動画')} ({patterns});;{tr('All files', '所有文件', 'すべてのファイル')} (*)"


def format_bytes(size: float) -> str:
    """Decimal units, the way Iwara's upload limit is stated."""

    size = float(size or 0)
    for unit in ("B", "KB", "MB"):
        if size < 1000:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.2f} GB"


def format_clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# ── ffmpeg lookup ────────────────────────────────────────────────────────────


class FfmpegState(QObject):
    """The ffmpeg every tool uses, looked up off the GUI thread and cached.

    Running ``ffmpeg -encoders`` takes a moment, and a configured network
    path can take longer, so the lookup never runs on the GUI thread.
    """

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.info: FfmpegInfo | None = None
        self.error = ""
        self.checked = False
        self._worker: _LocateWorker | None = None

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def refresh(self):
        """Look ffmpeg up again (after Settings changed the path)."""

        if self._worker is not None:
            self._worker.stale = True
            return
        worker = _LocateWorker(str(app_config.ffmpeg_path or ""))
        worker.located.connect(self._on_located)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def ensure(self):
        if not self.checked and self._worker is None:
            self.refresh()

    def _on_located(self, info, error: str):
        self.info = info
        self.error = error
        self.checked = True

    def _on_finished(self):
        worker, self._worker = self._worker, None
        if worker is not None and worker.stale:
            self.refresh()
            return
        self.changed.emit()

    def worker(self):
        return self._worker


class _LocateWorker(ManagedThread):
    located = Signal(object, str)

    def __init__(self, configured: str):
        super().__init__()
        self.configured = configured
        self.stale = False

    def run(self):
        try:
            self.located.emit(locate(self.configured), "")
        except FfmpegError as exc:
            self.located.emit(None, str(exc))
        except Exception as exc:  # a broken binary must not take the page down
            self.located.emit(None, str(exc))


_STATE: FfmpegState | None = None


def ffmpeg_state() -> FfmpegState:
    global _STATE
    if _STATE is None:
        _STATE = FfmpegState()
    return _STATE


class FfmpegMissingCard(CardWidget):
    """Shown in place of a tool until an ffmpeg program is available."""

    open_settings = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*CARD_MARGINS)
        layout.setSpacing(8)
        layout.addWidget(SubtitleLabel(tr("ffmpeg not found", "未找到 ffmpeg", "ffmpeg が見つかりません"), self))
        self._detail = BodyLabel("", self)
        self._detail.setWordWrap(True)
        set_secondary_text(self._detail)
        layout.addWidget(self._detail)
        row = QHBoxLayout()
        button = PushButton(tr("Choose in Settings", "前往设置选择", "設定で選択"), self, FluentIcon.SETTING)
        button.clicked.connect(self.open_settings.emit)
        row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.set_error("")

    def set_error(self, error: str):
        text = tr(
            "This tool runs the ffmpeg program. Install it (macOS: brew install ffmpeg; Windows: winget install ffmpeg) "
            "or choose ffmpeg / ffmpeg.exe under Settings → Tools.",
            "此工具需要调用 ffmpeg 程序。请先安装（macOS：brew install ffmpeg；Windows：winget install ffmpeg），"
            "或在“设置 → 工具”中选择 ffmpeg / ffmpeg.exe 的位置。",
            "このツールは ffmpeg を使います。インストールするか（macOS: brew install ffmpeg / Windows: winget install ffmpeg）、"
            "設定 → ツールで ffmpeg / ffmpeg.exe を選択してください。",
        )
        if error and error != "ffmpeg was not found":
            text += "\n" + error
        self._detail.setText(text)


# ── Inputs and layout ────────────────────────────────────────────────────────


class FileDropLineEdit(LineEdit):
    """Path box that accepts a dropped video file."""

    file_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setClearButtonEnabled(True)

    @staticmethod
    def file_from_event(event: QDragEnterEvent | QDropEvent) -> str:
        mime = event.mimeData()
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            if url.isLocalFile() and os.path.isfile(url.toLocalFile()):
                return url.toLocalFile()
        return ""

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self.file_from_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        path = self.file_from_event(event)
        if path:
            self.setText(path)
            self.file_dropped.emit(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class DropArea(QWidget):
    """A tool's whole area accepts a dropped video, not just the path box."""

    file_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if FileDropLineEdit.file_from_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        path = FileDropLineEdit.file_from_event(event)
        if path:
            self.file_dropped.emit(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


def transparent_scroll(name: str, parent: QWidget) -> tuple[ScrollArea, QWidget, QVBoxLayout]:
    """A frameless scroll area whose content shows the window background."""

    scroll = ScrollArea(parent)
    scroll.setObjectName(name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet(
        f"QScrollArea#{name} {{ background: transparent; border: none; }}"
        f"QScrollArea#{name} > QWidget > QWidget {{ background: transparent; }}"
    )
    scroll.viewport().setAutoFillBackground(False)
    content = QWidget()
    content.setAutoFillBackground(False)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 12, 0)
    layout.setSpacing(12)
    scroll.setWidget(content)
    return scroll, content, layout


def card_layout(card: CardWidget, title: str, hint: str = "") -> QVBoxLayout:
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*CARD_MARGINS)
    layout.setSpacing(9)
    layout.addWidget(SubtitleLabel(title, card))
    if hint:
        label = BodyLabel(hint, card)
        label.setWordWrap(True)
        set_secondary_text(label)
        layout.addWidget(label)
    return layout


def labeled_row(layout: QVBoxLayout, text: str, control: QWidget, parent: QWidget) -> BodyLabel:
    row = QHBoxLayout()
    label = BodyLabel(text, parent)
    label.setWordWrap(True)
    row.addWidget(label, 1)
    row.addWidget(control)
    layout.addLayout(row)
    return label


def suggested_dir(path: str) -> str:
    return str(Path(path).parent) if path else ""
