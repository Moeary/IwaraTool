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
from ...i18n import tr
from ..theme import qcolor, set_secondary_text, set_status_text
from ..worker_lifecycle import on_finished, stop_qthreads
from .common import (
    DropArea,
    FfmpegMissingCard,
    FileDropLineEdit,
    card_layout,
    ffmpeg_state,
    format_bytes,
    format_clock,
    video_file_filter,
)
from .workers import CoverWorker, ProbeWorker, SaveFrameWorker

_CROP_KEY = "tools_cover_crop_v1"
CARD_ASPECT = 3 / 2  # Iwara's video cards; the calculator this follows crops to 300×200


def crop_rect(width: float, height: float, aspect: float = CARD_ASPECT) -> QRectF:
    """The centred ``aspect`` region of a ``width × height`` image."""

    if width <= 0 or height <= 0:
        return QRectF()
    if width / height > aspect:
        cropped = height * aspect
        return QRectF((width - cropped) / 2, 0, cropped, height)
    cropped = width / aspect
    return QRectF(0, (height - cropped) / 2, width, cropped)


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
                tr("Pick a candidate to see it here", "点选候选封面，在此查看大图", "候補を選ぶとここに表示されます"),
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
            tr("Cover Preview", "封面预览", "カバーのプレビュー"),
            tr(
                "After an upload Iwara offers 16 screenshots as the cover, apparently taken at the middle of 16 equal "
                "slices (duration × (2i+1)/32), numbered here in the order of Iwara's picker (left to right, top to "
                "bottom). Check the final file before uploading so a good frame is on offer. This is a community "
                "finding, not confirmed by Iwara.",
                "上传后 Iwara 会给出 16 张截图供选择封面，推测取自把视频等分成 16 段后每段的正中间（时长 × (2i+1)/32）。"
                "编号与 Iwara 封面选择里的顺序一致（从左到右、从上到下）。上传前用最终成品算一遍，就能知道有没有合适的封面可选。"
                "该算法来自社区推测，未经 Iwara 官方确认。",
                "アップロード後、Iwara はカバー候補として 16 枚のスクリーンショットを提示します。動画を 16 等分した各区間の中央"
                "（長さ × (2i+1)/32）と推定されています。番号は Iwara の選択画面の順（左上から）です。"
                "アップロード前に最終ファイルで確認できます（非公式の推定です）。",
            ),
        )
        row = QHBoxLayout()
        self._source_edit = FileDropLineEdit(source)
        self._source_edit.setPlaceholderText(tr("Video file", "视频文件", "動画ファイル"))
        self._source_edit.editingFinished.connect(lambda: self.load_file(self._source_edit.text()))
        self._source_edit.file_dropped.connect(self.load_file)
        browse = PushButton(tr("Browse…", "浏览…", "参照…"), source, FluentIcon.FOLDER)
        browse.clicked.connect(self._browse)
        row.addWidget(self._source_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        info_row = QHBoxLayout()
        self._info_label = BodyLabel("", source)
        self._info_label.setWordWrap(True)
        info_row.addWidget(self._info_label, 1)
        info_row.addWidget(BodyLabel(tr("Crop like a card (3:2)", "按卡片比例裁切（3:2）", "カード比率で切り抜き（3:2）"), source))
        self._crop_switch = SwitchButton(source)
        self._crop_switch.setChecked(str(app_config.get_ui_value(_CROP_KEY, "1")) != "0")
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
        self._export_btn = PushButton(tr("Save Frame…", "保存此帧…", "フレームを保存…"), detail_card, FluentIcon.SAVE)
        self._export_btn.clicked.connect(self._export)
        self._copy_btn = PushButton(tr("Copy Time", "复制时间码", "時間をコピー"), detail_card, FluentIcon.COPY)
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
        start = self._source_edit.text().strip() or str(app_config.download_dir or "")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Choose a video", "选择视频", "動画を選択"),
            str(Path(start).parent if Path(start).is_file() else start), video_file_filter(),
        )
        if path:
            self.load_file(path)

    def load_file(self, path: str, *, reload: bool = False):
        path = str(path or "").strip().strip('"')
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
            self._info_label.setText(tr("Waiting for ffmpeg…", "等待 ffmpeg…", "ffmpeg を待っています…"))
            return
        self._info_label.setText(tr("Reading the video…", "正在读取视频信息…", "動画を読み込んでいます…"))
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
            self._info_label.setText(tr(f"Cannot read this file: {error}", f"无法读取此文件：{error}", f"読み込めません: {error}"))
            set_status_text(self._info_label, "danger")
            self._reset_tiles("")
            return
        self._media = media
        width, height = media.display_size
        frames = media.frame_count or round(media.duration * (media.fps or 30))
        self._info_label.setText(
            " · ".join(
                part for part in (
                    f"{width}×{height}",
                    f"{media.fps:.3f}".rstrip("0").rstrip(".") + " fps" if media.fps else "",
                    format_clock(media.duration) + f" ({media.duration:.3f}s)",
                    tr(f"{frames} frames", f"{frames} 帧", f"{frames} フレーム"),
                    format_bytes(media.size),
                ) if part
            )
        )
        self._candidates = cover_candidates(media.duration, media.fps)
        for tile, candidate in zip(self._tiles, self._candidates):
            tile.caption = format_timecode(candidate.seconds)
            tile.message = tr("Extracting…", "截图中…", "抽出中…")
            tile.update()
        self._status_label.setText(tr(f"Extracting 0 / {COVER_COUNT}", f"正在截图 0 / {COVER_COUNT}", f"抽出中 0 / {COVER_COUNT}"))
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
            tile.message = tr(f"Failed: {error}", f"失败：{error}", f"失敗: {error}") if error else tr("Failed", "失败", "失敗")
        else:
            pixmap = QPixmap.fromImage(image)
            self._pixmaps[index] = pixmap
            tile.pixmap = pixmap
        tile.update()
        self._done += 1
        if self._done >= COVER_COUNT:
            failed = sum(1 for t in self._tiles if t.pixmap is None)
            text = tr("All 16 candidates ready. Click one to inspect it.", "16 张候选已全部生成，点选查看。", "16 枚の候補ができました。クリックで確認できます。")
            if failed:
                text += tr(f" {failed} failed.", f" 其中 {failed} 张失败。", f" {failed} 枚失敗しました。")
            self._status_label.setText(text)
            if self._selected < 0:
                self._select(0)
        else:
            self._status_label.setText(
                tr(f"Extracting {self._done} / {COVER_COUNT}", f"正在截图 {self._done} / {COVER_COUNT}", f"抽出中 {self._done} / {COVER_COUNT}")
            )
        if index == self._selected:
            self._show_detail()

    def _reset_tiles(self, message: str):
        for tile in self._tiles:
            tile.pixmap = None
            tile.caption = ""
            tile.selected = False
            tile.message = message or tr("Load a video", "载入视频后生成", "動画を読み込んでください")
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
        self._detail_title.setText(
            tr(
                f"No. {candidate.number} of {COVER_COUNT} · {format_timecode(candidate.seconds)}",
                f"第 {candidate.number} 张（共 {COVER_COUNT} 张）· {format_timecode(candidate.seconds)}",
                f"{candidate.number} / {COVER_COUNT} 枚目 · {format_timecode(candidate.seconds)}",
            )
        )
        self._detail_label.setText(
            tr(
                f"{candidate.seconds:.6f} s · frame {candidate.frame} (0-based)",
                f"{candidate.seconds:.6f} 秒 · 帧号 {candidate.frame}（第 {candidate.frame + 1} 帧）",
                f"{candidate.seconds:.6f} 秒 · フレーム {candidate.frame}（{candidate.frame + 1} 枚目）",
            )
        )
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
        app_config.set_ui_value(_CROP_KEY, "1" if checked else "0")
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
        default = self._media.path.with_name(f"{self._media.path.stem}_cover{candidate.number:02d}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save frame", "保存此帧", "フレームを保存"), str(default), "PNG (*.png);;JPEG (*.jpg)"
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
            title=tr("Saved", "已保存", "保存しました") if not error else tr("Failed", "失败", "失敗"),
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
