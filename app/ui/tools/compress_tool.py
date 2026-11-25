"""Tool: compress a video to fit Iwara's upload limit (H.264 + AAC, MP4)."""

from __future__ import annotations

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
    X264_PRESETS,
    CompressOptions,
    CompressPlan,
    default_output_path,
    plan_compression,
)
from ...core.tools.ffmpeg import MediaInfo
from ...presentation.tools.texts import tool_text
from ...presentation.tools import common as tools_common
from ...presentation.tools import compress as compress_rules
from ..style.fonts import monospace_font
from ..style.theme import set_secondary_text, set_status_text
from ..common.worker_lifecycle import on_finished, stop_qthreads
from .common import (
    DropArea,
    FfmpegMissingCard,
    FileDropLineEdit,
    card_layout,
    ffmpeg_state,
    labeled_row,
    transparent_scroll,
    video_file_filter,
)
from .workers import CompressWorker, ProbeWorker

_OPTIONS_KEY = compress_rules.OPTIONS_KEY
encoder_label = compress_rules.encoder_label
plan_note_text = compress_rules.plan_note_text


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
            tool_text("source_video"),
            tool_text("iwara_accepts_h_264_aac"),
        )
        row = QHBoxLayout()
        self._source_edit = FileDropLineEdit(card)
        self._source_edit.setPlaceholderText(tool_text("video_file"))
        self._source_edit.editingFinished.connect(lambda: self.load_file(self._source_edit.text()))
        self._source_edit.file_dropped.connect(self.load_file)
        browse = PushButton(tool_text("browse"), card, FluentIcon.FOLDER)
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
        layout = card_layout(card, tool_text("compression"))

        self._limit_combo = ComboBox(card)
        for key, label in compress_rules.limit_choices():
            self._limit_combo.addItem(label, userData=key)
        self._limit_combo.setFixedWidth(220)
        self._limit_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tool_text("size_limit"), self._limit_combo, card)

        self._custom_spin = SpinBox(card)
        self._custom_spin.setRange(*compress_rules.CUSTOM_MB_RANGE)
        self._custom_spin.setSuffix(" MB")
        self._custom_spin.setValue(300)
        self._custom_spin.setFixedWidth(220)
        self._custom_spin.valueChanged.connect(self._on_options_changed)
        self._custom_label = labeled_row(layout, tool_text("custom_limit"), self._custom_spin, card)

        self._margin_spin = SpinBox(card)
        self._margin_spin.setRange(*compress_rules.MARGIN_RANGE)
        self._margin_spin.setSuffix(" %")
        self._margin_spin.setValue(3)
        self._margin_spin.setFixedWidth(220)
        self._margin_spin.valueChanged.connect(self._on_options_changed)
        labeled_row(
            layout,
            tool_text("safety_margin_below_the_limit"),
            self._margin_spin,
            card,
        )

        self._encoder_combo = ComboBox(card)
        self._encoder_combo.setFixedWidth(300)
        self._encoder_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tool_text("encoder"), self._encoder_combo, card)

        self._preset_combo = ComboBox(card)
        for preset in X264_PRESETS:
            self._preset_combo.addItem(preset, userData=preset)
        self._preset_combo.setFixedWidth(220)
        self._preset_combo.currentIndexChanged.connect(self._on_options_changed)
        self._preset_label = labeled_row(
            layout,
            tool_text("x264_speed_slower_better_quality"),
            self._preset_combo,
            card,
        )

        self._audio_combo = ComboBox(card)
        for rate in AUDIO_BITRATES:
            self._audio_combo.addItem(f"{rate} kbps", userData=rate)
        self._audio_combo.setFixedWidth(220)
        self._audio_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tool_text("aac_audio_bitrate"), self._audio_combo, card)

        self._fps_combo = ComboBox(card)
        for value, label in compress_rules.fps_choices():
            self._fps_combo.addItem(label, userData=value)
        self._fps_combo.setFixedWidth(220)
        self._fps_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(layout, tool_text("frame_rate"), self._fps_combo, card)

        self._upscaler_combo = ComboBox(card)
        for name, label in compress_rules.upscaler_choices():
            self._upscaler_combo.addItem(label, userData=name)
        self._upscaler_combo.setFixedWidth(220)
        self._upscaler_combo.currentIndexChanged.connect(self._on_options_changed)
        labeled_row(
            layout,
            tool_text("upscaling_to_720p"),
            self._upscaler_combo,
            card,
        )

        self._downscale_switch = SwitchButton(card)
        self._downscale_switch.setChecked(True)
        self._downscale_switch.checkedChanged.connect(self._on_options_changed)
        labeled_row(
            layout,
            tool_text("lower_the_resolution_when_the"),
            self._downscale_switch,
            card,
        )

        self._force_check = CheckBox(
            tool_text("re_encode_even_if_the"),
            card,
        )
        self._force_check.stateChanged.connect(self._on_options_changed)
        layout.addWidget(self._force_check)
        return card

    def _build_output_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = card_layout(card, tool_text("output"))
        row = QHBoxLayout()
        self._output_edit = FileDropLineEdit(card)
        self._output_edit.setAcceptDrops(False)
        self._output_edit.setPlaceholderText(tool_text("output_mp4"))
        self._output_edit.textEdited.connect(self._on_output_edited)
        browse = PushButton(tool_text("save_as"), card, FluentIcon.SAVE_AS)
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
        self._start_btn = PrimaryPushButton(tool_text("start"), card, FluentIcon.PLAY)
        self._start_btn.clicked.connect(self._start)
        self._cancel_btn = PushButton(tool_text("cancel"), card, FluentIcon.CANCEL)
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
        self._open_folder_btn = PushButton(tool_text("show_in_folder"), card, FluentIcon.FOLDER)
        self._open_folder_btn.clicked.connect(self._open_output_folder)
        self._covers_btn = PushButton(
            tool_text("preview_covers_of_the_result"), card, FluentIcon.PHOTO
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
        saved = compress_rules.saved_form()

        def select(combo: ComboBox, value):
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)

        select(self._limit_combo, saved.limit)
        self._custom_spin.setValue(saved.custom_mb)
        self._margin_spin.setValue(saved.margin_pct)
        self._saved_encoder = saved.encoder
        select(self._preset_combo, saved.x264_preset)
        select(self._audio_combo, saved.audio_kbps)
        select(self._fps_combo, saved.max_fps)
        select(self._upscaler_combo, saved.upscaler)
        self._downscale_switch.setChecked(saved.auto_downscale)
        self._update_option_visibility()

    def _form(self) -> compress_rules.CompressForm:
        return compress_rules.CompressForm(
            limit=str(self._limit_combo.currentData() or "standard"),
            custom_mb=self._custom_spin.value(),
            margin_pct=self._margin_spin.value(),
            encoder=str(self._encoder_combo.currentData() or self._saved_encoder or "libx264"),
            x264_preset=str(self._preset_combo.currentData() or "medium"),
            audio_kbps=int(self._audio_combo.currentData() or 160),
            max_fps=float(self._fps_combo.currentData() or 0.0),
            upscaler=str(self._upscaler_combo.currentData() or "lanczos"),
            auto_downscale=self._downscale_switch.isChecked(),
            force_encode=self._force_check.isChecked(),
        )

    def _save_options(self):
        compress_rules.save_form(self._form())

    def options(self) -> CompressOptions:
        form = self._form()
        if not self._encoder_combo.currentData():
            form = compress_rules.replace_encoder(form, "libx264")
        return form.options()

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
        for name in compress_rules.available_encoders(info):
            self._encoder_combo.addItem(encoder_label(name), userData=name)
        index = self._encoder_combo.findData(wanted)
        self._encoder_combo.setCurrentIndex(index if index >= 0 else 0)
        self._loading_options = False
        self._update_option_visibility()

    # ── ffmpeg / source ──────────────────────────────────────────────────────

    def _on_ffmpeg_changed(self):
        state = ffmpeg_state()
        ready = compress_rules.ffmpeg_ready(state.info)
        self._missing_card.setVisible(state.checked and not ready)
        self._missing_card.set_error(state.error, no_h264=state.info is not None and not ready)
        self._fill_encoders()
        if ready and self._source_edit.text().strip() and self._media is None:
            self.load_file(self._source_edit.text())
        self._update_buttons()

    def _browse_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tools_common.choose_video_title(), tools_common.browse_start(self._source_edit.text(), app_config.download_dir),
            video_file_filter(),
        )
        if path:
            self.load_file(path)

    def load_file(self, path: str):
        path = tools_common.clean_path(path)
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
            self._media_label.setText(tools_common.waiting_text())
            return
        self._media_label.setText(tools_common.reading_text())
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
            self._media_label.setText(tools_common.unreadable_text(error))
            set_status_text(self._media_label, "danger")
            self._refresh_plan()
            return
        set_status_text(self._media_label, "text")
        self._media = media
        self._media_label.setText(compress_rules.media_summary(media))
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
        result = compress_rules.verdict(self._media, plan, options)
        self._verdict_label.setText(result.title)
        set_status_text(self._verdict_label, result.role)
        self._plan_label.setText(result.detail)
        self._notes_label.setText("\n".join(result.notes))
        self._update_buttons()

    @staticmethod
    def _needs_text(media: MediaInfo, options: CompressOptions) -> str:
        return compress_rules.needs_text(media, options)

    # ── Output ───────────────────────────────────────────────────────────────

    def _on_output_edited(self, _text: str):
        self._output_touched = True

    def _browse_output(self):
        current = self._output_edit.text().strip()
        path, _ = QFileDialog.getSaveFileName(self, tool_text("save_as_2"), current, "MP4 (*.mp4)")
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
        output, error = compress_rules.output_for(self._output_edit.text(), self._media.path)
        if output is None:
            self._toast(False, error)
            return
        if self._output_edit.text() != str(output):
            self._output_edit.setText(str(output))
        self._log.clear()
        self._log.hide()
        self._set_result_actions(False)
        self._progress.setValue(0)
        self._started_at = time.monotonic()
        self._status_label.setText(compress_rules.starting_text())
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
            self._status_label.setText(compress_rules.stopping_text())

    def _on_worker_finished(self, worker):
        if self._worker is worker:
            self._worker = None
        self._update_buttons()

    def _on_progress(self, stage: str, _part: float, overall: float):
        self._progress.setValue(int(overall * 1000))
        self._status_label.setText(compress_rules.progress_text(stage, overall, time.monotonic() - self._started_at))

    def _on_succeeded(self, result):
        self._progress.setValue(1000)
        self._last_output = str(result.output)
        self._status_label.setText(compress_rules.done_text(result.size, result.elapsed))
        if "retried" in result.plan.notes:
            self._notes_label.setText(plan_note_text("retried"))
        self._set_result_actions(True)
        self._output_touched = False
        self._toast(True, str(result.output))

    def _on_failed(self, message: str, stderr: str):
        self._status_label.setText(tools_common.failed_title())
        self._log.setPlainText(stderr or message)
        self._log.show()
        self._toast(False, message.splitlines()[-1] if message else "")

    def _on_cancelled(self):
        self._progress.setValue(0)
        self._status_label.setText(compress_rules.cancelled_text())

    def _open_output_folder(self):
        if self._last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._last_output).parent)))

    def _toast(self, ok: bool, message: str):
        (InfoBar.success if ok else InfoBar.error)(
            title=compress_rules.done_title() if ok else tools_common.failed_title(),
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
