"""Tool: compress a video to fit Iwara's upload limit (H.264 + AAC, MP4)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    SwitchButton,
)

from ...config import app_config
from ...core.tools.compress import (
    AUDIO_BITRATES,
    MB,
    SIZE_PRESETS_MB,
    UPSCALERS,
    X264_PRESETS,
    CompressOptions,
    CompressPlan,
    default_output_path,
    plan_compression,
)
from ...core.tools.ffmpeg import MediaInfo
from ...i18n import tr
from ..fonts import monospace_font
from ..theme import set_secondary_text, set_status_text
from ..worker_lifecycle import on_finished, stop_qthreads
from .common import (
    DropArea,
    FfmpegMissingCard,
    FileDropLineEdit,
    card_layout,
    ffmpeg_state,
    format_bytes,
    format_clock,
    labeled_row,
    transparent_scroll,
    video_file_filter,
)
from .workers import CompressWorker, ProbeWorker

_OPTIONS_KEY = "tools_compress_options_v1"


def encoder_label(name: str) -> str:
    return {
        "libx264": tr("x264 two-pass (best quality for the size)", "x264 两遍编码（同体积画质最好）", "x264 2 パス（サイズ当たり最高画質）"),
        "h264_videotoolbox": tr("VideoToolbox (Apple hardware, fast)", "VideoToolbox（Apple 硬件，快）", "VideoToolbox（Apple ハードウェア・高速）"),
        "h264_nvenc": tr("NVENC (NVIDIA hardware, fast)", "NVENC（NVIDIA 硬件，快）", "NVENC（NVIDIA ハードウェア・高速）"),
        "h264_qsv": tr("Quick Sync (Intel hardware, fast)", "Quick Sync（Intel 硬件，快）", "Quick Sync（Intel ハードウェア・高速）"),
        "h264_amf": tr("AMF (AMD hardware, fast)", "AMF（AMD 硬件，快）", "AMF（AMD ハードウェア・高速）"),
    }.get(name, name)


def plan_note_text(note: str) -> str:
    return {
        "remux": tr(
            "The video stream already qualifies; only the container / audio is converted (fast, no quality loss).",
            "视频流已符合要求，只转换封装或音频（很快，画质无损）。",
            "映像はそのまま使えます。コンテナ／音声だけ変換します（高速・無劣化）。",
        ),
        "upscaled": tr(
            "Below 720p: scaled up to 720p as Iwara requires.",
            "低于 720p：按 Iwara 要求放大到 720p。",
            "720p 未満のため 720p に拡大します。",
        ),
        "downscaled": tr(
            "The size budget is too small for the source resolution, so it is scaled down to keep the picture clean.",
            "体积预算撑不起原分辨率，已降低分辨率以保证画面干净。",
            "容量に対して解像度が高すぎるため、画質を保つよう縮小します。",
        ),
        "fps_capped": tr("The frame rate is capped.", "已限制帧率。", "フレームレートを制限します。"),
        "low_quality": tr(
            "The bitrate is very low for this length; expect visible blocking. Trimming the video or a lower frame rate helps.",
            "按这个时长码率很低，画面会明显糊块；剪短视频或降低帧率会好些。",
            "長さに対してビットレートが低く、ブロックノイズが目立ちます。短くするかフレームレートを下げてください。",
        ),
        "too_long": tr(
            "Too long to fit under the limit even at the lowest usable bitrate. Split or trim the video, or use the premium limit.",
            "即使用最低可用码率也装不进上限。请分段或剪短，或改用会员上限。",
            "最低ビットレートでも上限に収まりません。分割・短縮するか、プレミアムの上限を使ってください。",
        ),
        "retried": tr(
            "The first encode came out over the limit and was redone at a lower bitrate.",
            "第一次编码超出上限，已降低码率重新编码。",
            "最初のエンコードが上限を超えたため、低いビットレートでやり直しました。",
        ),
    }.get(note, note)


class CompressTool(DropArea):
    """Pick a video, see what it needs, and compress it for upload."""

    covers_requested = Signal(str)
    open_settings = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CompressTool")
        self._media: MediaInfo | None = None
        self._plan: CompressPlan | None = None
        self._probe_worker: ProbeWorker | None = None
        self._probe_generation = 0
        self._worker: CompressWorker | None = None
        self._output_touched = False
        self._started_at = 0.0
        self._last_output = ""
        self._loading_options = True

        self._build_ui()
        self._load_options()
        self._loading_options = False
        self.file_dropped.connect(self.load_file)
        state = ffmpeg_state()
        state.changed.connect(self._on_ffmpeg_changed)
        state.ensure()
        self._on_ffmpeg_changed()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll, content, layout = transparent_scroll("CompressToolScroll", self)
        root.addWidget(scroll)

        self._missing_card = FfmpegMissingCard(content)
        self._missing_card.open_settings.connect(self.open_settings.emit)
        layout.addWidget(self._missing_card)
        layout.addWidget(self._build_source_card(content))
        layout.addWidget(self._build_options_card(content))
        layout.addWidget(self._build_output_card(content))
        layout.addWidget(self._build_run_card(content))
        layout.addStretch(1)

    def _build_source_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = card_layout(
            card,
            tr("Source Video", "源视频", "元の動画"),
            tr(
                "Iwara accepts H.264 + AAC in MP4, at least 720p, up to 300 MB (600 MB for premium). "
                "Drop a video anywhere on this tool or browse for it.",
                "Iwara 上传要求：H.264 + AAC 编码、MP4 封装，分辨率不低于 720p，大小不超过 300MB（会员 600MB）。"
                "可把视频拖到本工具任意位置，或点击浏览。",
                "Iwara の条件：H.264 + AAC の MP4、720p 以上、300 MB まで（プレミアムは 600 MB）。"
                "動画をこのツールにドロップするか参照してください。",
            ),
        )
        row = QHBoxLayout()
        self._source_edit = FileDropLineEdit(card)
        self._source_edit.setPlaceholderText(tr("Video file", "视频文件", "動画ファイル"))
        self._source_edit.editingFinished.connect(lambda: self.load_file(self._source_edit.text()))
        self._source_edit.file_dropped.connect(self.load_file)
        browse = PushButton(tr("Browse…", "浏览…", "参照…"), card, FluentIcon.FOLDER)
        browse.clicked.connect(self._browse_source)
        row.addWidget(self._source_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        self._media_label = BodyLabel("", card)
        self._media_label.setWordWrap(True)
        self._media_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._media_label)
        self._verdict_label = StrongBodyLabel("", card)
        self._verdict_label.setWordWrap(True)
        layout.addWidget(self._verdict_label)
        return card

    def _build_options_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = card_layout(card, tr("Compression", "压缩设置", "圧縮設定"))

        self._limit_combo = ComboBox(card)
        self._limit_combo.addItem(tr("Standard · 300 MB", "普通用户 · 300MB", "一般 · 300 MB"), userData="standard")
        self._limit_combo.addItem(tr("Premium · 600 MB", "会员 · 600MB", "プレミアム · 600 MB"), userData="premium")
        self._limit_combo.addItem(tr("Custom", "自定义", "カスタム"), userData="custom")
        self._limit_combo.setFixedWidth(220)
        self._limit_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tr("Size limit", "大小上限", "サイズ上限"), self._limit_combo, card)

        self._custom_spin = SpinBox(card)
        self._custom_spin.setRange(10, 20000)
        self._custom_spin.setSuffix(" MB")
        self._custom_spin.setValue(300)
        self._custom_spin.setFixedWidth(220)
        self._custom_spin.valueChanged.connect(self._on_options_changed)
        self._custom_label = labeled_row(layout, tr("Custom limit", "自定义上限", "カスタム上限"), self._custom_spin, card)

        self._margin_spin = SpinBox(card)
        self._margin_spin.setRange(0, 20)
        self._margin_spin.setSuffix(" %")
        self._margin_spin.setValue(3)
        self._margin_spin.setFixedWidth(220)
        self._margin_spin.valueChanged.connect(self._on_options_changed)
        labeled_row(
            layout,
            tr("Safety margin below the limit", "预留余量", "上限からの余裕"),
            self._margin_spin,
            card,
        )

        self._encoder_combo = ComboBox(card)
        self._encoder_combo.setFixedWidth(300)
        self._encoder_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tr("Encoder", "编码器", "エンコーダー"), self._encoder_combo, card)

        self._preset_combo = ComboBox(card)
        for preset in X264_PRESETS:
            self._preset_combo.addItem(preset, userData=preset)
        self._preset_combo.setFixedWidth(220)
        self._preset_combo.currentIndexChanged.connect(self._on_options_changed)
        self._preset_label = labeled_row(
            layout,
            tr("x264 speed (slower = better quality)", "x264 速度（越慢画质越好）", "x264 速度（遅いほど高画質）"),
            self._preset_combo,
            card,
        )

        self._audio_combo = ComboBox(card)
        for rate in AUDIO_BITRATES:
            self._audio_combo.addItem(f"{rate} kbps", userData=rate)
        self._audio_combo.setFixedWidth(220)
        self._audio_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tr("AAC audio bitrate", "AAC 音频码率", "AAC 音声ビットレート"), self._audio_combo, card)

        self._fps_combo = ComboBox(card)
        self._fps_combo.addItem(tr("Keep (cap at 60)", "保持原帧率（最高 60）", "そのまま（上限 60）"), userData=60.0)
        self._fps_combo.addItem("30 fps", userData=30.0)
        self._fps_combo.addItem(tr("Keep exactly", "完全保持", "完全に維持"), userData=0.0)
        self._fps_combo.setFixedWidth(220)
        self._fps_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tr("Frame rate", "帧率", "フレームレート"), self._fps_combo, card)

        self._upscaler_combo = ComboBox(card)
        for name in UPSCALERS:
            self._upscaler_combo.addItem(
                {"lanczos": "Lanczos", "spline": "Spline", "bicubic": "Bicubic"}.get(name, name), userData=name
            )
        self._upscaler_combo.setFixedWidth(220)
        self._upscaler_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(
            layout,
            tr("Upscaling to 720p", "放大到 720p 的算法", "720p への拡大方式"),
            self._upscaler_combo,
            card,
        )

        self._downscale_switch = SwitchButton(card)
        self._downscale_switch.setChecked(True)
        self._downscale_switch.checkedChanged.connect(self._on_options_changed)
        labeled_row(
            layout,
            tr(
                "Lower the resolution when the size budget is too small (never below 720p)",
                "体积预算不够时自动降低分辨率（不低于 720p）",
                "容量が足りないときは解像度を下げる（720p 未満にはしない）",
            ),
            self._downscale_switch,
            card,
        )

        self._force_check = CheckBox(
            tr("Re-encode even if the file already qualifies", "即使已符合要求也重新编码", "条件を満たしていても再エンコードする"),
            card,
        )
        self._force_check.stateChanged.connect(self._on_options_changed)
        layout.addWidget(self._force_check)
        return card

    def _build_output_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = card_layout(card, tr("Output", "输出", "出力"))
        row = QHBoxLayout()
        self._output_edit = FileDropLineEdit(card)
        self._output_edit.setAcceptDrops(False)
        self._output_edit.setPlaceholderText(tr("Output MP4", "输出 MP4 文件", "出力 MP4"))
        self._output_edit.textEdited.connect(self._on_output_edited)
        browse = PushButton(tr("Save As…", "另存为…", "名前を付けて保存…"), card, FluentIcon.SAVE_AS)
        browse.clicked.connect(self._browse_output)
        row.addWidget(self._output_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        self._plan_label = BodyLabel("", card)
        self._plan_label.setWordWrap(True)
        layout.addWidget(self._plan_label)
        self._notes_label = BodyLabel("", card)
        self._notes_label.setWordWrap(True)
        set_secondary_text(self._notes_label)
        layout.addWidget(self._notes_label)
        return card

    def _build_run_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)
        row = QHBoxLayout()
        self._start_btn = PrimaryPushButton(tr("Start", "开始压缩", "開始"), card, FluentIcon.PLAY)
        self._start_btn.clicked.connect(self._start)
        self._cancel_btn = PushButton(tr("Cancel", "取消", "キャンセル"), card, FluentIcon.CANCEL)
        self._cancel_btn.clicked.connect(self._cancel)
        self._cancel_btn.setEnabled(False)
        self._status_label = BodyLabel("", card)
        self._status_label.setWordWrap(True)
        set_secondary_text(self._status_label)
        row.addWidget(self._start_btn)
        row.addWidget(self._cancel_btn)
        row.addWidget(self._status_label, 1)
        layout.addLayout(row)
        self._progress = ProgressBar(card)
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        result_row = QHBoxLayout()
        self._open_folder_btn = PushButton(tr("Show in Folder", "打开所在文件夹", "フォルダーを開く"), card, FluentIcon.FOLDER)
        self._open_folder_btn.clicked.connect(self._open_output_folder)
        self._covers_btn = PushButton(
            tr("Preview Covers of the Result", "计算成品的封面", "完成ファイルのカバーを確認"), card, FluentIcon.PHOTO
        )
        self._covers_btn.clicked.connect(lambda: self.covers_requested.emit(self._last_output))
        result_row.addWidget(self._open_folder_btn)
        result_row.addWidget(self._covers_btn)
        result_row.addStretch()
        layout.addLayout(result_row)
        self._set_result_actions(False)

        self._log = PlainTextEdit(card)
        self._log.setReadOnly(True)
        self._log.setFont(monospace_font())
        self._log.setFixedHeight(140)
        self._log.hide()
        layout.addWidget(self._log)
        return card

    # ── Options ──────────────────────────────────────────────────────────────

    def _load_options(self):
        try:
            saved = json.loads(str(app_config.get_ui_value(_OPTIONS_KEY, "") or "{}"))
        except ValueError:
            saved = {}
        defaults = CompressOptions()

        def select(combo: ComboBox, value):
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)

        select(self._limit_combo, saved.get("limit", "standard"))
        self._custom_spin.setValue(int(saved.get("custom_mb", 300) or 300))
        self._margin_spin.setValue(int(saved.get("margin_pct", round(defaults.margin * 100))))
        self._saved_encoder = str(saved.get("encoder", defaults.encoder))
        select(self._preset_combo, saved.get("x264_preset", defaults.x264_preset))
        select(self._audio_combo, int(saved.get("audio_kbps", defaults.audio_kbps)))
        select(self._fps_combo, float(saved.get("max_fps", defaults.max_fps)))
        select(self._upscaler_combo, saved.get("upscaler", defaults.upscaler))
        self._downscale_switch.setChecked(bool(saved.get("auto_downscale", defaults.auto_downscale)))
        self._update_option_visibility()

    def _save_options(self):
        app_config.set_ui_value(
            _OPTIONS_KEY,
            json.dumps(
                {
                    "limit": self._limit_combo.currentData(),
                    "custom_mb": self._custom_spin.value(),
                    "margin_pct": self._margin_spin.value(),
                    "encoder": self._encoder_combo.currentData() or self._saved_encoder,
                    "x264_preset": self._preset_combo.currentData(),
                    "audio_kbps": self._audio_combo.currentData(),
                    "max_fps": self._fps_combo.currentData(),
                    "upscaler": self._upscaler_combo.currentData(),
                    "auto_downscale": self._downscale_switch.isChecked(),
                }
            ),
        )

    def options(self) -> CompressOptions:
        limit = self._limit_combo.currentData()
        limit_mb = SIZE_PRESETS_MB.get(limit, self._custom_spin.value())
        return CompressOptions(
            limit_mb=float(limit_mb),
            margin=self._margin_spin.value() / 100,
            encoder=str(self._encoder_combo.currentData() or "libx264"),
            x264_preset=str(self._preset_combo.currentData() or "medium"),
            audio_kbps=int(self._audio_combo.currentData() or 160),
            auto_downscale=self._downscale_switch.isChecked(),
            upscaler=str(self._upscaler_combo.currentData() or "lanczos"),
            max_fps=float(self._fps_combo.currentData() or 0.0),
            force_encode=self._force_check.isChecked(),
        )

    def _update_option_visibility(self):
        custom = self._limit_combo.currentData() == "custom"
        self._custom_spin.setVisible(custom)
        self._custom_label.setVisible(custom)
        x264 = (self._encoder_combo.currentData() or "libx264") == "libx264"
        self._preset_combo.setVisible(x264)
        self._preset_label.setVisible(x264)

    def _on_options_changed(self, *_args):
        self._update_option_visibility()
        if self._loading_options:
            return
        self._save_options()
        self._refresh_plan()

    def _fill_encoders(self):
        info = ffmpeg_state().info
        wanted = self._encoder_combo.currentData() or self._saved_encoder
        self._loading_options = True
        self._encoder_combo.clear()
        names = []
        if info is not None:
            names = (["libx264"] if info.has_libx264 else []) + list(info.hardware_encoders)
        for name in names:
            self._encoder_combo.addItem(encoder_label(name), userData=name)
        index = self._encoder_combo.findData(wanted)
        self._encoder_combo.setCurrentIndex(index if index >= 0 else 0)
        self._loading_options = False
        self._update_option_visibility()

    # ── ffmpeg / source ──────────────────────────────────────────────────────

    def _on_ffmpeg_changed(self):
        state = ffmpeg_state()
        ready = state.info is not None and bool(state.info.has_libx264 or state.info.hardware_encoders)
        self._missing_card.setVisible(state.checked and not ready)
        if state.info is not None and not ready:
            self._missing_card.set_error(
                tr(
                    "This ffmpeg build has no H.264 encoder (libx264).",
                    "这个 ffmpeg 没有 H.264 编码器（libx264）。",
                    "この ffmpeg には H.264 エンコーダー（libx264）がありません。",
                )
            )
        else:
            self._missing_card.set_error(state.error)
        self._fill_encoders()
        if ready and self._source_edit.text().strip() and self._media is None:
            self.load_file(self._source_edit.text())
        self._update_buttons()

    def _browse_source(self):
        start = self._source_edit.text().strip() or str(app_config.download_dir or "")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Choose a video", "选择视频", "動画を選択"), str(Path(start).parent if Path(start).is_file() else start),
            video_file_filter(),
        )
        if path:
            self.load_file(path)

    def load_file(self, path: str):
        path = str(path or "").strip().strip('"')
        if self._source_edit.text() != path:
            self._source_edit.setText(path)
        if self._worker is not None:
            return
        if self._media is not None and str(self._media.path) == path:
            return  # editingFinished also fires when the box merely loses focus
        self._media = None
        self._plan = None
        self._probe_generation += 1
        self._set_result_actions(False)
        self._log.hide()
        self._progress.setValue(0)
        self._status_label.clear()
        if not path:
            self._media_label.clear()
            self._verdict_label.clear()
            self._refresh_plan()
            return
        if not self._output_touched:
            self._output_edit.setText(str(default_output_path(Path(path))))
        info = ffmpeg_state().info
        if info is None:
            self._media_label.setText(tr("Waiting for ffmpeg…", "等待 ffmpeg…", "ffmpeg を待っています…"))
            return
        self._media_label.setText(tr("Reading the video…", "正在读取视频信息…", "動画を読み込んでいます…"))
        self._verdict_label.clear()
        worker = ProbeWorker(self._probe_generation, info, path)
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
        if generation != self._probe_generation:
            return
        if media is None:
            self._media_label.setText(tr(f"Cannot read this file: {error}", f"无法读取此文件：{error}", f"読み込めません: {error}"))
            set_status_text(self._media_label, "danger")
            self._refresh_plan()
            return
        set_status_text(self._media_label, "text")
        self._media = media
        width, height = media.display_size
        parts = [
            f"{width}×{height}",
            f"{media.fps:.3g} fps" if media.fps else "",
            format_clock(media.duration),
            format_bytes(media.size),
            f"{media.video_codec}" + (f" / {media.audio_codec}" if media.audio_codec else tr(" / no audio", " / 无音轨", " / 音声なし")),
            f"{(media.bit_rate or media.size * 8 / media.duration) / 1000:.0f} kbps",
        ]
        self._media_label.setText(" · ".join(part for part in parts if part))
        self._refresh_plan()

    # ── Plan ─────────────────────────────────────────────────────────────────

    def _refresh_plan(self):
        if self._media is None:
            self._plan = None
            self._plan_label.clear()
            self._notes_label.clear()
            if not self._source_edit.text().strip():
                self._verdict_label.clear()
            self._update_buttons()
            return
        options = self.options()
        plan = plan_compression(self._media, options)
        self._plan = plan
        if plan.mode == "ready":
            self._verdict_label.setText(
                tr(
                    "✓ Ready to upload as it is: H.264 + AAC MP4, at least 720p and under the limit.",
                    "✓ 已满足上传要求，可直接上传：H.264 + AAC 的 MP4，不低于 720p，且未超过上限。",
                    "✓ このままアップロードできます（H.264 + AAC の MP4、720p 以上、上限以内）。",
                )
            )
            set_status_text(self._verdict_label, "success")
            self._plan_label.setText(
                tr(
                    "Tick “Re-encode even if the file already qualifies” to compress it anyway.",
                    "如仍要压缩，请勾选“即使已符合要求也重新编码”。",
                    "それでも圧縮する場合は「条件を満たしていても再エンコードする」をオンにします。",
                )
            )
        elif plan.mode == "impossible":
            self._verdict_label.setText(tr("✗ Cannot fit under the limit", "✗ 无法压到上限以内", "✗ 上限内に収まりません"))
            set_status_text(self._verdict_label, "danger")
            self._plan_label.setText(
                tr(
                    f"Only {plan.video_kbps} kbps would be left for the picture.",
                    f"画面只剩 {plan.video_kbps} kbps 可用。",
                    f"映像に使えるのは {plan.video_kbps} kbps だけです。",
                )
            )
        else:
            self._verdict_label.setText(self._needs_text(self._media, options))
            set_status_text(self._verdict_label, "warning")
            if plan.mode == "remux":
                audio = tr("copy", "直接复制", "コピー") if plan.copy_audio or not plan.has_audio else f"AAC {plan.audio_kbps} kbps"
                self._plan_label.setText(
                    tr(
                        f"Output: {plan.width}×{plan.height}, video copied, audio {audio}, about {format_bytes(plan.estimated_bytes)}",
                        f"输出：{plan.width}×{plan.height}，视频直接复制，音频 {audio}，约 {format_bytes(plan.estimated_bytes)}",
                        f"出力: {plan.width}×{plan.height}、映像コピー、音声 {audio}、約 {format_bytes(plan.estimated_bytes)}",
                    )
                )
            else:
                audio = f" + AAC {plan.audio_kbps} kbps" if plan.has_audio else ""
                self._plan_label.setText(
                    tr(
                        f"Output: {plan.width}×{plan.height} @ {plan.fps:.3g} fps, H.264 {plan.video_kbps} kbps{audio}, "
                        f"about {format_bytes(plan.estimated_bytes)} (limit {options.limit_mb:g} MB)",
                        f"输出：{plan.width}×{plan.height} @ {plan.fps:.3g} fps，H.264 {plan.video_kbps} kbps{audio}，"
                        f"约 {format_bytes(plan.estimated_bytes)}（上限 {options.limit_mb:g}MB）",
                        f"出力: {plan.width}×{plan.height} @ {plan.fps:.3g} fps、H.264 {plan.video_kbps} kbps{audio}、"
                        f"約 {format_bytes(plan.estimated_bytes)}（上限 {options.limit_mb:g} MB）",
                    )
                )
        self._notes_label.setText("\n".join(plan_note_text(note) for note in plan.notes if note != "remux" or plan.mode == "remux"))
        self._update_buttons()

    @staticmethod
    def _needs_text(media: MediaInfo, options: CompressOptions) -> str:
        reasons = []
        if media.size > options.limit_bytes:
            reasons.append(tr(f"over {options.limit_mb:g} MB", f"超过 {options.limit_mb:g}MB", f"{options.limit_mb:g} MB 超過"))
        if media.short_side < 720:
            reasons.append(tr("below 720p", "低于 720p", "720p 未満"))
        if media.video_codec != "h264":
            reasons.append(tr(f"video is {media.video_codec}", f"视频编码为 {media.video_codec}", f"映像が {media.video_codec}"))
        if media.has_audio and media.audio_codec != "aac":
            reasons.append(tr(f"audio is {media.audio_codec}", f"音频编码为 {media.audio_codec}", f"音声が {media.audio_codec}"))
        if not media.is_mp4:
            reasons.append(tr("not an MP4 file", "不是 MP4 封装", "MP4 ではない"))
        if not reasons:
            return tr("Re-encoding as requested.", "按要求重新编码。", "指定どおり再エンコードします。")
        return tr("Needs processing: ", "需要处理：", "処理が必要: ") + tr(", ", "，", "、").join(reasons)

    # ── Output ───────────────────────────────────────────────────────────────

    def _on_output_edited(self, _text: str):
        self._output_touched = True

    def _browse_output(self):
        current = self._output_edit.text().strip()
        path, _ = QFileDialog.getSaveFileName(self, tr("Save as", "另存为", "名前を付けて保存"), current, "MP4 (*.mp4)")
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self._output_touched = True
            self._output_edit.setText(path)

    # ── Running ──────────────────────────────────────────────────────────────

    def _update_buttons(self):
        running = self._worker is not None
        state = ffmpeg_state()
        self._start_btn.setEnabled(
            not running
            and self._probe_worker is None
            and state.info is not None
            and self._plan is not None
            and self._plan.runnable
            and self._encoder_combo.count() > 0
        )
        self._cancel_btn.setEnabled(running)
        self._source_edit.setEnabled(not running)
        self._output_edit.setEnabled(not running)

    def _set_result_actions(self, visible: bool):
        self._open_folder_btn.setVisible(visible)
        self._covers_btn.setVisible(visible)

    def _start(self):
        info = ffmpeg_state().info
        if info is None or self._media is None or self._plan is None or not self._plan.runnable:
            return
        output_text = self._output_edit.text().strip()
        if not output_text:
            self._output_edit.setText(str(default_output_path(self._media.path)))
            output_text = self._output_edit.text()
        output = Path(output_text)
        if output.suffix.lower() != ".mp4":
            output = output.with_suffix(".mp4")
            self._output_edit.setText(str(output))
        try:
            same = output.resolve() == self._media.path.resolve()
        except OSError:
            same = False
        if same:
            self._toast(False, tr("Choose a different output file.", "请换一个输出文件名，不能覆盖源文件。", "元ファイルとは別の出力先を選んでください。"))
            return
        self._log.clear()
        self._log.hide()
        self._set_result_actions(False)
        self._progress.setValue(0)
        self._started_at = time.monotonic()
        self._status_label.setText(tr("Starting ffmpeg…", "正在启动 ffmpeg…", "ffmpeg を起動しています…"))
        worker = CompressWorker(info, self._media, self._plan, self.options(), output)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        on_finished(worker, self, self._on_worker_finished)
        self._worker = worker
        worker.start()
        self._update_buttons()

    def _cancel(self):
        if self._worker is not None:
            self._worker.requestInterruption()
            self._status_label.setText(tr("Stopping…", "正在停止…", "停止しています…"))

    def _on_worker_finished(self, worker):
        if self._worker is worker:
            self._worker = None
        self._update_buttons()

    def _on_progress(self, stage: str, _part: float, overall: float):
        self._progress.setValue(int(overall * 1000))
        stage_text = {
            "pass1": tr("Pass 1 of 2 (analysis)", "第 1 遍（分析）", "1 パス目（解析）"),
            "pass2": tr("Pass 2 of 2 (encoding)", "第 2 遍（编码）", "2 パス目（エンコード）"),
            "encode": tr("Encoding", "编码中", "エンコード中"),
            "remux": tr("Converting the container", "转换封装中", "コンテナ変換中"),
            "done": tr("Finishing", "收尾中", "仕上げ中"),
        }.get(stage, stage)
        elapsed = time.monotonic() - self._started_at
        eta = ""
        if 0.02 < overall < 1:
            eta = tr(f", about {format_clock(elapsed / overall - elapsed)} left", f"，约剩 {format_clock(elapsed / overall - elapsed)}", f"、残り約 {format_clock(elapsed / overall - elapsed)}")
        self._status_label.setText(f"{stage_text} · {overall * 100:.0f}%{eta}")

    def _on_succeeded(self, result):
        self._progress.setValue(1000)
        self._last_output = str(result.output)
        self._status_label.setText(
            tr(
                f"Done: {format_bytes(result.size)} in {format_clock(result.elapsed)}",
                f"完成：{format_bytes(result.size)}，用时 {format_clock(result.elapsed)}",
                f"完了: {format_bytes(result.size)}（{format_clock(result.elapsed)}）",
            )
        )
        if "retried" in result.plan.notes:
            self._notes_label.setText(plan_note_text("retried"))
        self._set_result_actions(True)
        self._output_touched = False
        self._toast(True, str(result.output))

    def _on_failed(self, message: str, stderr: str):
        self._status_label.setText(tr("Failed", "失败", "失敗"))
        self._log.setPlainText(stderr or message)
        self._log.show()
        self._toast(False, message.splitlines()[-1] if message else "")

    def _on_cancelled(self):
        self._progress.setValue(0)
        self._status_label.setText(tr("Cancelled", "已取消", "キャンセルしました"))

    def _open_output_folder(self):
        if self._last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._last_output).parent)))

    def _toast(self, ok: bool, message: str):
        (InfoBar.success if ok else InfoBar.error)(
            title=tr("Compressed", "压缩完成", "圧縮完了") if ok else tr("Failed", "失败", "失敗"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000 if ok else 8000,
            parent=self.window(),
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        return stop_qthreads([self._worker, self._probe_worker], timeout_ms=timeout_ms)
