"""Tool: preview the 16 cover choices Iwara will offer for a video."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PushButton,
    StrongBodyLabel,
    SwitchButton,
)

from ...config import app_config
from ...core.tools.covers import COVER_COUNT, CoverCandidate, cover_candidates, format_timecode
from ...core.tools.ffmpeg import MediaInfo
from ...presentation.tools.texts import tool_text
from ...presentation.tools import common as tools_common
from ...presentation.tools import covers as cover_rules
from ..style.theme import qcolor, set_secondary_text, set_status_text
from ..common.worker_lifecycle import on_finished, stop_qthreads
from .common import (
    DropArea,
    FfmpegMissingCard,
    FileDropLineEdit,
    card_layout,
    ffmpeg_state,
    video_file_filter,
)
from .workers import CoverWorker, ProbeWorker, SaveFrameWorker

CARD_ASPECT = cover_rules.CARD_ASPECT


def crop_rect(width: float, height: float, aspect: float = CARD_ASPECT) -> QRectF:
    """The centred ``aspect`` region of a ``width × height`` image."""

    if width <= 0 or height <= 0:
        return QRectF()
    return QRectF(*cover_rules.crop_box(width, height, aspect))


def fit_rect(source_w: float, source_h: float, box: QRectF) -> QRectF:
    if source_w <= 0 or source_h <= 0:
        return QRectF(box)
    scale = min(box.width() / source_w, box.height() / source_h)
    w, h = source_w * scale, source_h * scale
    return QRectF(box.x() + (box.width() - w) / 2, box.y() + (box.height() - h) / 2, w, h)


class CoverTile(QWidget):
    """One candidate: the frame as a card would show it, its number and time."""

    clicked = Signal(int)

    def __init__(self, index: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.index = index
        self.pixmap: QPixmap | None = None
        self.caption = ""
        self.message = ""
        self.selected = False
        self.crop = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(120, 100)

    def sizeHint(self) -> QSize:
        return QSize(180, 144)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        caption_h = 20
        area = QRectF(self.rect()).adjusted(3, 3, -3, -3 - caption_h)
        image_box = fit_rect(CARD_ASPECT, 1, area)
        path = QPainterPath()
        path.addRoundedRect(image_box, 6, 6)
        painter.fillPath(path, QColor(0, 0, 0, 40) if self.pixmap is None else QColor(8, 8, 12))
        if self.pixmap is not None and not self.pixmap.isNull():
            painter.save()
            painter.setClipPath(path)
            if self.crop:
                painter.drawPixmap(image_box, self.pixmap, crop_rect(self.pixmap.width(), self.pixmap.height()))
            else:
                painter.drawPixmap(fit_rect(self.pixmap.width(), self.pixmap.height(), image_box), self.pixmap, QRectF(self.pixmap.rect()))
            painter.restore()
        else:
            painter.setPen(qcolor("text_secondary"))
            painter.drawText(image_box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message or "…")
        # Number badge, like the order on Iwara's thumbnail picker.
        badge = QRectF(image_box.x() + 6, image_box.y() + 6, 26, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, f"{self.index + 1:02d}")
        if self.selected:
            pen = painter.pen()
            pen.setColor(qcolor("accent"))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(image_box.adjusted(1, 1, -1, -1), 6, 6)
        painter.setPen(qcolor("text") if self.selected else qcolor("text_secondary"))
        caption = QRectF(image_box.x(), image_box.bottom() + 2, image_box.width(), caption_h)
        painter.drawText(caption, Qt.AlignmentFlag.AlignCenter, self.caption)


class CoverPreview(QWidget):
    """The selected frame, full, with the part a card hides dimmed."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.pixmap: QPixmap | None = None
        self.crop = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 160)

    def set_pixmap(self, pixmap: QPixmap | None):
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect())
        if self.pixmap is None or self.pixmap.isNull():
            path = QPainterPath()
            path.addRoundedRect(box.adjusted(1, 1, -1, -1), 8, 8)
            painter.fillPath(path, QColor(0, 0, 0, 30))
            painter.setPen(qcolor("text_secondary"))
            painter.drawText(
                box, Qt.AlignmentFlag.AlignCenter,
                cover_rules.preview_empty_text(),
            )
            return
        target = fit_rect(self.pixmap.width(), self.pixmap.height(), box)
        painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        if self.crop:
            visible = crop_rect(target.width(), target.height()).translated(target.topLeft())
            shade = QColor(0, 0, 0, 140)
            painter.fillRect(QRectF(target.left(), target.top(), target.width(), visible.top() - target.top()), shade)
            painter.fillRect(QRectF(target.left(), visible.bottom(), target.width(), target.bottom() - visible.bottom()), shade)
            painter.fillRect(QRectF(target.left(), visible.top(), visible.left() - target.left(), visible.height()), shade)
            painter.fillRect(QRectF(visible.right(), visible.top(), target.right() - visible.right(), visible.height()), shade)


class CoverTool(DropArea):
    """Compute and show the 16 candidates; pick one, export it."""

    open_settings = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CoverTool")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._media: MediaInfo | None = None
        self._candidates: list[CoverCandidate] = []
        self._pixmaps: dict[int, QPixmap] = {}
        self._selected = -1
        self._generation = 0
        self._probe_worker: ProbeWorker | None = None
        self._cover_worker: CoverWorker | None = None
        self._save_worker: SaveFrameWorker | None = None
        self._done = 0

        self._build_ui()
        self.file_dropped.connect(self.load_file)
        state = ffmpeg_state()
        state.changed.connect(self._on_ffmpeg_changed)
        state.ensure()
        self._on_ffmpeg_changed()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._missing_card = FfmpegMissingCard(self)
        self._missing_card.open_settings.connect(self.open_settings.emit)
        root.addWidget(self._missing_card)

        source = CardWidget(self)
        layout = card_layout(
            source,
            tool_text("cover_preview"),
            tool_text("after_an_upload_iwara_offers"),
        )
        row = QHBoxLayout()
        self._source_edit = FileDropLineEdit(source)
        self._source_edit.setPlaceholderText(tool_text("video_file"))
        self._source_edit.editingFinished.connect(lambda: self.load_file(self._source_edit.text()))
        self._source_edit.file_dropped.connect(self.load_file)
        browse = PushButton(tool_text("browse"), source, FluentIcon.FOLDER)
        browse.clicked.connect(self._browse)
        row.addWidget(self._source_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        info_row = QHBoxLayout()
        self._info_label = BodyLabel("", source)
        self._info_label.setWordWrap(True)
        info_row.addWidget(self._info_label, 1)
        info_row.addWidget(BodyLabel(tool_text("crop_like_a_card_3"), source))
        self._crop_switch = SwitchButton(source)
        self._crop_switch.setChecked(cover_rules.crop_enabled())
        self._crop_switch.checkedChanged.connect(self._on_crop_changed)
        info_row.addWidget(self._crop_switch)
        layout.addLayout(info_row)
        root.addWidget(source)

        body = QHBoxLayout()
        body.setSpacing(12)
        grid_card = CardWidget(self)
        grid = QGridLayout(grid_card)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(6)
        self._tiles: list[CoverTile] = []
        for index in range(COVER_COUNT):
            tile = CoverTile(index, grid_card)
            tile.clicked.connect(self._select)
            grid.addWidget(tile, index // 4, index % 4)
            self._tiles.append(tile)
        body.addWidget(grid_card, 3)

        detail_card = CardWidget(self)
        detail = QVBoxLayout(detail_card)
        detail.setContentsMargins(14, 14, 14, 14)
        detail.setSpacing(8)
        self._preview = CoverPreview(detail_card)
        detail.addWidget(self._preview, 1)
        self._detail_title = StrongBodyLabel("", detail_card)
        detail.addWidget(self._detail_title)
        self._detail_label = CaptionLabel("", detail_card)
        self._detail_label.setWordWrap(True)
        self._detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_secondary_text(self._detail_label)
        detail.addWidget(self._detail_label)
        buttons = QHBoxLayout()
        self._export_btn = PushButton(tool_text("save_frame"), detail_card, FluentIcon.SAVE)
        self._export_btn.clicked.connect(self._export)
        self._copy_btn = PushButton(tool_text("copy_time"), detail_card, FluentIcon.COPY)
        self._copy_btn.clicked.connect(self._copy_time)
        buttons.addWidget(self._export_btn)
        buttons.addWidget(self._copy_btn)
        buttons.addStretch()
        detail.addLayout(buttons)
        body.addWidget(detail_card, 2)
        root.addLayout(body, 1)

        self._status_label = CaptionLabel("", self)
        set_secondary_text(self._status_label)
        root.addWidget(self._status_label)
        self._apply_crop()
        self._reset_tiles("")

    # ── ffmpeg / source ──────────────────────────────────────────────────────

    def _on_ffmpeg_changed(self):
        state = ffmpeg_state()
        self._missing_card.setVisible(state.checked and state.info is None)
        self._missing_card.set_error(state.error)
        if state.info is not None and self._source_edit.text().strip() and self._media is None and self._probe_worker is None:
            self.load_file(self._source_edit.text())
        self._update_buttons()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tools_common.choose_video_title(),
            tools_common.browse_start(self._source_edit.text(), app_config.download_dir), video_file_filter(),
        )
        if path:
            self.load_file(path)

    def load_file(self, path: str, *, reload: bool = False):
        path = tools_common.clean_path(path)
        if self._source_edit.text() != path:
            self._source_edit.setText(path)
        if not reload and self._media is not None and str(self._media.path) == path:
            return  # editingFinished also fires when the box merely loses focus
        self._generation += 1
        if self._cover_worker is not None:
            self._cover_worker.requestInterruption()
        self._media = None
        self._candidates = []
        self._pixmaps.clear()
        self._selected = -1
        self._done = 0
        self._show_detail()
        if not path:
            self._info_label.clear()
            self._status_label.clear()
            self._reset_tiles("")
            self._update_buttons()
            return
        info = ffmpeg_state().info
        if info is None:
            self._info_label.setText(tools_common.waiting_text())
            return
        self._info_label.setText(tools_common.reading_text())
        set_status_text(self._info_label, "text")
        self._reset_tiles("…")
        worker = ProbeWorker(self._generation, info, path)
        worker.probed.connect(self._on_probed)
        on_finished(worker, self, self._on_probe_finished)
        self._probe_worker = worker
        worker.start()
        self._update_buttons()

    def _on_probe_finished(self, worker):
        if self._probe_worker is worker:
            self._probe_worker = None
        self._update_buttons()

    def _on_probed(self, generation: int, media, error: str):
        if generation != self._generation:
            return
        if media is None:
            self._info_label.setText(tools_common.unreadable_text(error))
            set_status_text(self._info_label, "danger")
            self._reset_tiles("")
            return
        self._media = media
        self._info_label.setText(cover_rules.media_summary(media))
        self._candidates = cover_candidates(media.picture_duration, media.fps)
        for tile, candidate in zip(self._tiles, self._candidates):
            tile.caption = format_timecode(candidate.seconds)
            tile.message = cover_rules.tile_extracting_text()
            tile.update()
        self._status_label.setText(cover_rules.extracting_text(0))
        worker = CoverWorker(self._generation, ffmpeg_state().info, media.path, self._candidates)
        worker.frame_ready.connect(self._on_frame)
        on_finished(worker, self, self._on_cover_finished)
        self._cover_worker = worker
        worker.start()

    def _on_cover_finished(self, worker):
        if self._cover_worker is worker:
            self._cover_worker = None
        self._update_buttons()

    def _on_frame(self, generation: int, index: int, data: bytes, error: str):
        if generation != self._generation or not 0 <= index < len(self._tiles):
            return
        tile = self._tiles[index]
        image = QImage.fromData(data, "PNG") if data else QImage()
        if image.isNull():
            tile.pixmap = None
            tile.message = cover_rules.tile_failed_text(error)
        else:
            pixmap = QPixmap.fromImage(image)
            self._pixmaps[index] = pixmap
            tile.pixmap = pixmap
        tile.update()
        self._done += 1
        if self._done >= COVER_COUNT:
            failed = sum(1 for t in self._tiles if t.pixmap is None)
            self._status_label.setText(cover_rules.finished_text(failed))
            if self._selected < 0:
                self._select(0)
        else:
            self._status_label.setText(cover_rules.extracting_text(self._done))
        if index == self._selected:
            self._show_detail()

    def _reset_tiles(self, message: str):
        for tile in self._tiles:
            tile.pixmap = None
            tile.caption = ""
            tile.selected = False
            tile.message = message or cover_rules.tile_empty_text()
            tile.update()

    # ── Selection ────────────────────────────────────────────────────────────

    def _select(self, index: int):
        if not 0 <= index < len(self._candidates):
            return
        self._selected = index
        for tile in self._tiles:
            selected = tile.index == index
            if tile.selected != selected:
                tile.selected = selected
                tile.update()
        self._show_detail()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_detail(self):
        if not 0 <= self._selected < len(self._candidates):
            self._preview.set_pixmap(None)
            self._detail_title.clear()
            self._detail_label.clear()
            self._update_buttons()
            return
        candidate = self._candidates[self._selected]
        self._preview.set_pixmap(self._pixmaps.get(self._selected))
        self._detail_title.setText(cover_rules.detail_title(candidate))
        self._detail_label.setText(cover_rules.detail_text(candidate))
        self._update_buttons()

    def keyPressEvent(self, event):
        moves = {
            Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -4, Qt.Key.Key_Down: 4,
        }
        step = moves.get(event.key())
        if step is not None and self._candidates:
            current = self._selected if self._selected >= 0 else 0
            self._select(max(0, min(len(self._candidates) - 1, current + step)))
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_crop_changed(self, checked: bool):
        cover_rules.set_crop(checked)
        self._apply_crop()

    def _apply_crop(self):
        crop = self._crop_switch.isChecked()
        for tile in self._tiles:
            tile.crop = crop
            tile.update()
        self._preview.crop = crop
        self._preview.update()

    # ── Actions ──────────────────────────────────────────────────────────────

    def _update_buttons(self):
        has = self._media is not None and 0 <= self._selected < len(self._candidates)
        self._export_btn.setEnabled(has and self._save_worker is None and ffmpeg_state().info is not None)
        self._copy_btn.setEnabled(has)

    def _copy_time(self):
        if 0 <= self._selected < len(self._candidates):
            QGuiApplication.clipboard().setText(format_timecode(self._candidates[self._selected].seconds))

    def _export(self):
        if self._media is None or not 0 <= self._selected < len(self._candidates):
            return
        candidate = self._candidates[self._selected]
        default = cover_rules.default_frame_path(self._media.path, candidate)
        path, _ = QFileDialog.getSaveFileName(
            self, tool_text("save_frame_2"), str(default), "PNG (*.png);;JPEG (*.jpg)"
        )
        if not path:
            return
        info = ffmpeg_state().info
        if info is None:
            return
        worker = SaveFrameWorker(info, self._media.path, candidate.seconds, Path(path))
        worker.saved.connect(self._on_saved)
        on_finished(worker, self, self._on_save_finished)
        self._save_worker = worker
        worker.start()
        self._update_buttons()

    def _on_save_finished(self, worker):
        if self._save_worker is worker:
            self._save_worker = None
        self._update_buttons()

    def _on_saved(self, target: str, error: str):
        (InfoBar.success if not error else InfoBar.error)(
            title=cover_rules.saved_title(not error),
            content=target if not error else error,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self.window(),
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        return stop_qthreads([self._probe_worker, self._cover_worker, self._save_worker], timeout_ms=timeout_ms)
