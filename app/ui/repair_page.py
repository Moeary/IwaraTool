from __future__ import annotations

import os
import time
from typing import Any

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
)

from ..config import app_config
from ..core.manager import download_manager
from ..core.repair import format_repair_filename
from ..core.rules import active_rule_id, normalize_rule_payload, rule_store
from ..i18n import tr
from ..signal_bus import signal_bus
from .worker_lifecycle import stop_qthreads


class FolderDropLineEdit(LineEdit):
    """Line edit that accepts a dropped local directory."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _directory_from_event(event: QDragEnterEvent | QDropEvent) -> str:
        mime = event.mimeData()
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isdir(path):
                    return path
        return ""

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._directory_from_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        directory = self._directory_from_event(event)
        if directory:
            self.setText(directory)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class RepairWorker(QThread):
    """Run one repair phase without blocking the Qt event loop."""

    item_ready = Signal(dict)
    progress = Signal(int, int)
    activity = Signal(int, int, int)
    result_ready = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        mode: str,
        *,
        folder: str = "",
        filename_template: str = "",
        output_root: str = "",
        move_to_output: bool = True,
        items: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.folder = folder
        self.filename_template = filename_template
        self.output_root = output_root
        self.move_to_output = bool(move_to_output)
        self.items = list(items or [])
        self.options = dict(options or {})
        self._last_progress_emit = 0.0
        self._last_activity_emit = 0.0

    def run(self):
        def callback(item: dict[str, Any], index: int, total: int) -> bool:
            if self.isInterruptionRequested():
                return False
            # A scan can contain hundreds of rows. Keep its result off the
            # table until all worker futures finish; repainting the table for
            # every resolved file makes the Qt event loop increasingly costly.
            if self.mode != "scan":
                self.item_ready.emit(dict(item))
            now = time.monotonic()
            if (
                self.mode != "scan"
                or index == total
                or now - self._last_progress_emit >= 0.15
            ):
                self._last_progress_emit = now
                self.progress.emit(index, total)
            return True

        def activity_callback(completed: int, total: int, active: int):
            if self.isInterruptionRequested():
                return
            now = time.monotonic()
            if completed == total or now - self._last_activity_emit >= 0.2:
                self._last_activity_emit = now
                self.activity.emit(completed, total, active)

        try:
            if self.mode == "scan":
                result = download_manager.scan_repair_folder(
                    self.folder,
                    filename_template=self.filename_template,
                    output_root=self.output_root,
                    move_to_output=self.move_to_output,
                    progress_callback=callback,
                    activity_callback=activity_callback,
                )
            else:
                result = download_manager.repair_folder_files(
                    self.items,
                    options=self.options,
                    progress_callback=callback,
                )
            self.result_ready.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class RepairInterface(QWidget):
    """Repair downloaded videos in place and enrich their sidecar metadata."""

    _COL_STATE = 0
    _COL_FILE = 1
    _COL_ID = 2
    _COL_TITLE = 3
    _COL_DATE = 4
    _COL_TARGET = 5
    _COL_DETAIL = 6
    _MAX_LOG_BLOCKS = 2000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RepairInterface")
        self._worker: RepairWorker | None = None
        self._worker_mode = ""
        self._items: list[dict[str, Any]] = []
        self._items_by_path: dict[str, dict[str, Any]] = {}
        self._path_to_row: dict[str, int] = {}
        self._pending_logs: list[str] = []
        self._repair_log_active = False
        self._last_feedback_log_at = 0.0
        self._scan_feedback = (0, 0, 0)
        self._scan_feedback_timer = QTimer(self)
        self._scan_feedback_timer.setInterval(1000)
        self._scan_feedback_timer.timeout.connect(self._on_scan_feedback_timer)
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(180)
        self._log_flush_timer.timeout.connect(self._flush_logs)
        self._rule_payloads: list[dict[str, Any]] = []

        self._build_ui()
        self._load_rules()
        self._load_saved_state()
        signal_bus.log_message.connect(self._append_log)
        signal_bus.rules_changed.connect(self._load_rules)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("Repair Center", "修复中心", "修復センター"), self))
        title_row.addStretch()
        self._status_label = BodyLabel(
            tr("Choose a folder to begin", "请选择一个文件夹开始", "フォルダーを選択して開始"),
            self,
        )
        title_row.addWidget(self._status_label)
        root.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        self._splitter = splitter
        root.addWidget(splitter, stretch=1)

        # The stacked window takes the largest minimum size of *all* pages,
        # including hidden ones. A tall repair form used to force the entire
        # window above the screen height at 175% DPI. Windows then clamps the
        # size during dragging while Qt grows it again, causing snap-back.
        self._controls_scroll = QScrollArea(splitter)
        self._controls_scroll.setObjectName("RepairControlsScroll")
        self._controls_scroll.setWidgetResizable(True)
        self._controls_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._controls_scroll.setMinimumWidth(360)
        self._controls_scroll.setStyleSheet(
            "QScrollArea#RepairControlsScroll { background: transparent; border: none; }"
            "QScrollArea#RepairControlsScroll > QWidget > QWidget { background: transparent; }"
        )
        left_panel = QWidget()
        self._controls_scroll.setWidget(left_panel)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        folder_card = CardWidget(left_panel)
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(16, 14, 16, 14)
        folder_layout.setSpacing(9)
        folder_layout.addWidget(SubtitleLabel(tr("Source Folder", "视频文件夹", "動画フォルダー"), folder_card))
        folder_layout.addWidget(
            BodyLabel(
                tr(
                    "Drop a folder here or browse it. Subfolders are scanned too.",
                    "可将文件夹拖入此处或浏览选择；也会扫描子文件夹。",
                    "フォルダーをドロップまたは参照できます。サブフォルダーも検索します。",
                ),
                folder_card,
            )
        )
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self._folder_edit = FolderDropLineEdit(folder_card)
        self._folder_edit.setClearButtonEnabled(True)
        self._folder_edit.setPlaceholderText(
            tr("Drop a local folder or enter its path…", "拖入文件夹或输入路径…", "ローカルフォルダーをドロップまたは入力…")
        )
        self._folder_edit.returnPressed.connect(self._scan_folder)
        folder_row.addWidget(self._folder_edit, 1)
        browse_btn = PushButton(tr("Browse", "浏览", "参照"), folder_card, FluentIcon.FOLDER)
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        folder_layout.addLayout(folder_row)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(BodyLabel(tr("Output", "输出", "出力"), folder_card))
        self._output_edit = FolderDropLineEdit(folder_card)
        self._output_edit.setClearButtonEnabled(True)
        self._output_edit.setPlaceholderText(
            tr(
                "Leave blank to use the source folder…",
                "留空则使用输入文件夹…",
                "空欄なら入力フォルダーを使用…",
            )
        )
        self._output_edit.textChanged.connect(self._refresh_target_previews)
        output_row.addWidget(self._output_edit, 1)
        output_browse_btn = PushButton(tr("Browse", "浏览", "参照"), folder_card, FluentIcon.FOLDER)
        output_browse_btn.clicked.connect(self._browse_output_folder)
        output_row.addWidget(output_browse_btn)
        folder_layout.addLayout(output_row)
        folder_layout.addWidget(
            BodyLabel(
                tr(
                    "Files are moved (cut) into this folder; existing targets are never overwritten.",
                    "文件会剪切到此文件夹；已有同名目标不会被覆盖。",
                    "ファイルはこのフォルダーへ移動し、同名の変更先は上書きしません。",
                ),
                folder_card,
            )
        )
        self._scan_btn = PrimaryPushButton(tr("Scan", "扫描", "検索"), folder_card, FluentIcon.SEARCH)
        self._scan_btn.clicked.connect(self._scan_folder)
        folder_layout.addWidget(self._scan_btn)
        left_layout.addWidget(folder_card)

        rule_card = CardWidget(left_panel)
        rule_layout = QVBoxLayout(rule_card)
        rule_layout.setContentsMargins(16, 14, 16, 14)
        rule_layout.setSpacing(9)
        rule_layout.addWidget(SubtitleLabel(tr("Repair Rule", "修复规则", "修復ルール"), rule_card))
        rule_layout.addWidget(
            BodyLabel(
                tr(
                    "Directory segments such as {author}/ are created below the output folder.",
                    "规则中的 {author}/ 等目录段会在输出文件夹下创建。",
                    "{author}/ などのディレクトリ部分は出力フォルダー下に作成されます。",
                ),
                rule_card,
            )
        )
        rule_row = QHBoxLayout()
        rule_row.setSpacing(8)
        rule_row.addWidget(BodyLabel(tr("Rule", "规则", "ルール"), rule_card))
        self._rule_combo = ComboBox(rule_card)
        self._rule_combo.currentIndexChanged.connect(self._on_rule_changed)
        rule_row.addWidget(self._rule_combo, 1)
        rule_layout.addLayout(rule_row)

        template_row = QHBoxLayout()
        template_row.setSpacing(8)
        template_row.addWidget(BodyLabel(tr("Name", "命名", "名前"), rule_card))
        self._template_edit = LineEdit(rule_card)
        self._template_edit.setPlaceholderText("{YYYY-MM-DD}_{title}_{id}.mp4")
        self._template_edit.textChanged.connect(self._refresh_target_previews)
        template_row.addWidget(self._template_edit, 1)
        rule_layout.addLayout(template_row)
        left_layout.addWidget(rule_card)

        options_card = CardWidget(left_panel)
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(16, 14, 16, 14)
        options_layout.setSpacing(7)
        options_layout.addWidget(SubtitleLabel(tr("Repair Options", "修复选项", "修復オプション"), options_card))
        self._rename_check = CheckBox(tr("Rename existing videos", "重命名已有视频", "既存動画を名前変更"), options_card)
        self._rename_check.setChecked(True)
        options_layout.addWidget(self._rename_check)
        self._download_video_check = CheckBox(
            tr("Allow video download when needed", "必要时允许下载视频", "必要時に動画ダウンロードを許可"),
            options_card,
        )
        self._download_video_check.setChecked(False)
        options_layout.addWidget(self._download_video_check)
        options_layout.addWidget(
            BodyLabel(
                tr(
                    "Existing local videos are always kept; this repair page never replaces them.",
                    "已有本地视频始终保留；修复页不会替换它们。",
                    "既存のローカル動画は保持され、修復ページで置き換えることはありません。",
                ),
                options_card,
            )
        )
        self._thumbnail_check = CheckBox(tr("Download cover screenshot", "下载封面截图", "サムネイルを取得"), options_card)
        self._thumbnail_check.setChecked(True)
        options_layout.addWidget(self._thumbnail_check)
        self._nfo_check = CheckBox(tr("Write NFO metadata", "写入 NFO 元数据", "NFO メタデータを書き込む"), options_card)
        self._nfo_check.setChecked(True)
        options_layout.addWidget(self._nfo_check)
        self._history_check = CheckBox(tr("Add to download history", "加入下载历史", "ダウンロード履歴に追加"), options_card)
        self._history_check.setChecked(True)
        options_layout.addWidget(self._history_check)
        left_layout.addWidget(options_card)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._start_btn = PrimaryPushButton(tr("Repair Selected Folder", "开始修复", "修復を開始"), left_panel, FluentIcon.DOWNLOAD)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._start_repair)
        action_row.addWidget(self._start_btn, 1)
        self._stop_btn = PushButton(tr("Stop", "停止", "停止"), left_panel, FluentIcon.CANCEL)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_worker)
        action_row.addWidget(self._stop_btn)
        left_layout.addLayout(action_row)

        log_card = CardWidget(left_panel)
        log_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(8)
        log_header = QHBoxLayout()
        log_header.addWidget(SubtitleLabel(tr("Repair Log", "修复日志", "修復ログ"), log_card))
        log_header.addStretch()
        clear_btn = PushButton(tr("Clear", "清空", "クリア"), log_card, FluentIcon.DELETE)
        clear_btn.clicked.connect(self._clear_log)
        log_header.addWidget(clear_btn)
        log_layout.addLayout(log_header)
        self._log_edit = PlainTextEdit(log_card)
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumBlockCount(self._MAX_LOG_BLOCKS)
        self._log_edit.setPlaceholderText(tr("Repair logs will appear here…", "修复日志将显示在此…", "修復ログはここに表示されます…"))
        mono = QFont("Consolas", 9)
        if not mono.exactMatch():
            mono = QFont("Courier New", 9)
        self._log_edit.setFont(mono)
        log_layout.addWidget(self._log_edit)
        left_layout.addWidget(log_card, stretch=1)

        right_panel = QWidget(splitter)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        list_card = CardWidget(right_panel)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(16, 14, 16, 14)
        list_layout.setSpacing(8)
        list_header = QHBoxLayout()
        list_header.addWidget(SubtitleLabel(tr("Repair List", "修复列表", "修復リスト"), list_card))
        list_header.addStretch()
        self._summary_label = BodyLabel(tr("No folder scanned", "尚未扫描文件夹", "未検索"), list_card)
        list_header.addWidget(self._summary_label)
        list_layout.addLayout(list_header)

        self._table = TableWidget(list_card)
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("File", "文件", "ファイル"),
                "Iwara ID",
                tr("Title", "标题", "タイトル"),
                tr("Date", "日期", "日付"),
                tr("Target Name", "目标名称", "変更後の名前"),
                tr("Detail", "详情", "詳細"),
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        self._table.setWordWrap(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column in (self._COL_FILE, self._COL_TITLE, self._COL_TARGET, self._COL_DETAIL):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        for column, width in {
            self._COL_STATE: 78,
            self._COL_ID: 125,
            self._COL_DATE: 105,
        }.items():
            self._table.setColumnWidth(column, width)
        list_layout.addWidget(self._table, stretch=1)
        right_layout.addWidget(list_card, stretch=1)

        splitter.addWidget(self._controls_scroll)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([410, 1050])

    def _load_rules(self, *_args):
        current_id = active_rule_id()
        entries = rule_store.list_available()
        self._rule_payloads = [normalize_rule_payload(entry.get("payload")) for entry in entries]
        self._rule_combo.blockSignals(True)
        try:
            self._rule_combo.clear()
            self._rule_combo.addItems([
                str(entry.get("name", "") or tr("Unnamed rule", "未命名规则", "名前なしルール"))
                for entry in entries
            ])
            index = next(
                (idx for idx, entry in enumerate(entries) if str(entry.get("id", "")) == current_id),
                0,
            )
            self._rule_combo.setCurrentIndex(index)
        finally:
            self._rule_combo.blockSignals(False)
        self._apply_rule_template(self._rule_combo.currentIndex())

    def _apply_rule_template(self, index: int):
        if not 0 <= index < len(self._rule_payloads):
            return
        payload = self._rule_payloads[index]
        self._template_edit.setText(str(payload.get("filename_template", "") or ""))

    def _on_rule_changed(self, index: int):
        self._apply_rule_template(index)

    def _load_saved_state(self):
        saved_folder = str(app_config.get_ui_value("repair_folder", "") or "")
        if saved_folder and os.path.isdir(saved_folder):
            self._folder_edit.setText(saved_folder)
        saved_output = str(app_config.get_ui_value("repair_output_folder", "") or "")
        if saved_output:
            self._output_edit.setText(saved_output)
        saved_template = str(app_config.get_ui_value("repair_filename_template", "") or "")
        if saved_template:
            self._template_edit.setText(saved_template)

    def _browse_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("Choose source folder", "选择视频文件夹", "動画フォルダーを選択"),
            self._folder_edit.text().strip() or os.getcwd(),
        )
        if selected:
            self._folder_edit.setText(selected)

    def _browse_output_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("Choose output folder", "选择输出文件夹", "出力フォルダーを選択"),
            self._output_edit.text().strip() or self._folder_edit.text().strip() or os.getcwd(),
        )
        if selected:
            self._output_edit.setText(selected)

    def _output_root(self) -> str:
        custom = self._output_edit.text().strip()
        if custom:
            return os.path.abspath(os.path.expanduser(custom))
        source = self._folder_edit.text().strip()
        return os.path.abspath(os.path.expanduser(source)) if source else ""

    def _template(self) -> str:
        return self._template_edit.text().strip() or "{YYYY-MM-DD}_{title}_{id}.mp4"

    def _options(self) -> dict[str, Any]:
        return {
            "filename_template": self._template(),
            "output_root": self._output_root(),
            "move_to_output": True,
            "rename": self._rename_check.isChecked(),
            "download_video": self._download_video_check.isChecked(),
            "download_thumbnail": self._thumbnail_check.isChecked(),
            "collect_nfo": self._nfo_check.isChecked(),
            "add_to_history": self._history_check.isChecked(),
        }

    def _scan_folder(self):
        if self._worker is not None and self._worker.isRunning():
            return
        folder = os.path.abspath(os.path.expanduser(self._folder_edit.text().strip()))
        if not os.path.isdir(folder):
            self._show_warning(
                tr("Folder not found", "文件夹不存在", "フォルダーが見つかりません"),
                tr("Choose or drop a valid local folder first.", "请先选择或拖入有效的本地文件夹。", "有効なローカルフォルダーを選択またはドロップしてください。"),
            )
            return
        output_root = self._output_root() or folder
        self._folder_edit.setText(folder)
        app_config.set_ui_value("repair_folder", folder)
        app_config.set_ui_value("repair_output_folder", self._output_edit.text().strip())
        app_config.set_ui_value("repair_filename_template", self._template())
        self._items.clear()
        self._items_by_path.clear()
        self._clear_table()
        self._repair_log_active = True
        self._last_feedback_log_at = 0.0
        self._scan_feedback = (0, 0, 0)
        self._status_label.setText(
            tr("Scanning files…", "正在扫描文件…", "ファイルを検索中…")
        )
        self._append_log(
            tr(
                f"[Repair] scanning: {folder} -> {output_root}",
                f"[修复] 开始扫描：{folder} → {output_root}",
                f"[修復] 検索開始: {folder} -> {output_root}",
            )
        )
        self._start_worker(
            RepairWorker(
                "scan",
                folder=folder,
                filename_template=self._template(),
                output_root=output_root,
                move_to_output=True,
                parent=self,
            )
        )

    def _start_repair(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._items:
            self._show_warning(
                tr("Nothing to repair", "没有可修复的项目", "修復対象がありません"),
                tr("Scan a folder first.", "请先扫描文件夹。", "先にフォルダーを検索してください。"),
            )
            return
        app_config.set_ui_value("repair_filename_template", self._template())
        app_config.set_ui_value("repair_output_folder", self._output_edit.text().strip())
        self._repair_log_active = True
        self._append_log(
            tr(
                f"[Repair] starting {len(self._items)} files",
                f"[修复] 开始处理 {len(self._items)} 个文件",
                f"[修復] {len(self._items)} 件の処理を開始",
            )
        )
        self._start_worker(
            RepairWorker(
                "repair",
                items=list(self._items),
                options=self._options(),
                parent=self,
            )
        )

    def _start_worker(self, worker: RepairWorker):
        self._worker = worker
        self._worker_mode = worker.mode
        self._scan_btn.setEnabled(False)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        if worker.mode == "scan":
            self._scan_feedback_timer.start()
        else:
            self._scan_feedback_timer.stop()
        worker.item_ready.connect(self._on_item_ready)
        worker.progress.connect(self._on_progress)
        worker.activity.connect(self._on_activity)
        worker.result_ready.connect(self._on_result)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _stop_worker(self):
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.requestInterruption()
        self._status_label.setText(tr("Stopping…", "正在停止…", "停止中…"))

    def _on_progress(self, index: int, total: int):
        if self._worker_mode == "scan":
            if index == total:
                self._status_label.setText(
                    tr(
                        f"Scan completed {index}/{total}",
                        f"扫描完成 {index}/{total}",
                        f"検索完了 {index}/{total}",
                    )
                )
            return
        self._status_label.setText(
            tr(f"Processing {index}/{total}", f"处理中 {index}/{total}", f"処理中 {index}/{total}")
        )

    def _on_activity(self, completed: int, total: int, active: int):
        if self._worker_mode != "scan":
            return
        self._scan_feedback = (max(0, completed), max(0, total), max(0, active))
        self._show_scan_feedback()

    def _on_scan_feedback_timer(self):
        if self._worker_mode != "scan":
            return
        self._show_scan_feedback()

    def _show_scan_feedback(self):
        completed, total, active = self._scan_feedback
        if total <= 0:
            return
        queued = max(0, total - completed - active)
        self._status_label.setText(
            tr(
                f"Scanning {completed}/{total} · active {active} · queued {queued}",
                f"扫描中 {completed}/{total} · 活跃 {active} · 排队 {queued}",
                f"検索中 {completed}/{total} · 実行中 {active} · 待機 {queued}",
            )
        )
        now = time.monotonic()
        if completed == total or now - self._last_feedback_log_at >= 1.0:
            self._last_feedback_log_at = now
            self._append_log(
                tr(
                    f"[Repair] scan progress: {completed}/{total}, active={active}, queued={queued}",
                    f"[修复] 扫描进度：{completed}/{total}，活跃请求 {active}，排队 {queued}",
                    f"[修復] 検索進捗: {completed}/{total}、実行中={active}、待機={queued}",
                )
            )

    def _on_item_ready(self, item: dict[str, Any]):
        path = str(item.get("path", "") or "")
        if not path:
            return
        self._items_by_path[path] = dict(item)
        self._items = list(self._items_by_path.values())
        self._render_item(item)
        self._update_summary()

    def _on_result(self, result: object):
        if self._worker_mode == "scan" and isinstance(result, list):
            self._items = [dict(item) for item in result if isinstance(item, dict)]
            self._items_by_path = {
                str(item.get("path", "") or ""): item
                for item in self._items
                if str(item.get("path", "") or "")
            }
            self._render_all_items()
            ready = sum(1 for item in self._items if item.get("status") == "ready")
            self._append_log(
                tr(
                    f"[Repair] scan complete: {ready}/{len(self._items)} ready",
                    f"[修复] 扫描完成：{ready}/{len(self._items)} 个可修复",
                    f"[修復] 検索完了: {ready}/{len(self._items)} 件が修復可能",
                )
            )
        elif self._worker_mode == "repair" and isinstance(result, dict):
            repaired_items = result.get("items", [])
            if isinstance(repaired_items, list):
                self._items = [dict(item) for item in repaired_items if isinstance(item, dict)]
                self._items_by_path = {
                    str(item.get("path", "") or ""): item
                    for item in self._items
                    if str(item.get("path", "") or "")
                }
                self._render_all_items()
            self._append_log(
                tr(
                    f"[Repair] finished: renamed={result.get('renamed', 0)}, NFO={result.get('nfo', 0)}, cover={result.get('thumbnail', 0)}, skipped={result.get('skipped', 0)}",
                    f"[修复] 完成：重命名 {result.get('renamed', 0)}，NFO {result.get('nfo', 0)}，封面 {result.get('thumbnail', 0)}，跳过 {result.get('skipped', 0)}",
                    f"[修復] 完了: 名前変更={result.get('renamed', 0)}, NFO={result.get('nfo', 0)}, サムネイル={result.get('thumbnail', 0)}, スキップ={result.get('skipped', 0)}",
                )
            )
        self._update_summary()

    def _on_worker_error(self, message: str):
        self._append_log(tr(f"[Repair] error: {message}", f"[修复] 出错：{message}", f"[修復] エラー: {message}"))
        self._show_warning(
            tr("Repair failed", "修复失败", "修復に失敗"),
            str(message),
        )

    def _on_worker_finished(self):
        self._scan_feedback_timer.stop()
        self._worker = None
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        ready = sum(1 for item in self._items if item.get("status") == "ready")
        self._start_btn.setEnabled(bool(self._items) and ready > 0)
        self._update_summary()
        self._repair_log_active = False

    def _render_all_items(self):
        self._table.setUpdatesEnabled(False)
        try:
            self._clear_table()
            self._table.setRowCount(len(self._items))
            for row, item in enumerate(self._items):
                self._render_item(item, row=row)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.viewport().update()
        self._update_summary()

    def _clear_table(self):
        self._path_to_row.clear()
        self._table.clearContents()
        self._table.setRowCount(0)

    def _render_item(self, item: dict[str, Any], *, row: int | None = None):
        path = str(item.get("path", "") or "")
        if not path:
            return
        if row is None:
            row = self._path_to_row.get(path)
        if row is None:
            row = self._table.rowCount()
            self._table.insertRow(row)
        self._path_to_row[path] = row
        state_key = str(item.get("status", "") or "")
        state_text = {
            "ready": tr("Ready", "待修复", "待機"),
            "completed": tr("Done", "已完成", "完了"),
            "not_found": tr("Skip", "跳过", "スキップ"),
            "ambiguous": tr("Check", "待确认", "要確認"),
            "failed": tr("Failed", "失败", "失敗"),
            "skipped": tr("Skip", "跳过", "スキップ"),
        }.get(state_key, state_key or tr("Unknown", "未知", "不明"))
        values = [
            state_text,
            str(item.get("original_name", "") or os.path.basename(path)),
            str(item.get("video_id", "") or ""),
            str(item.get("title", "") or ""),
            str(item.get("published_at", "") or "")[:10],
            str(item.get("target_name", "") or ""),
            str(item.get("message", "") or ""),
        ]
        color = {
            "ready": QColor("#007c91"),
            "completed": QColor("#107c10"),
            "not_found": QColor("#c17d00"),
            "ambiguous": QColor("#c17d00"),
            "failed": QColor("#c42b1c"),
            "skipped": QColor("#666666"),
        }.get(state_key)
        for column, value in enumerate(values):
            cell = self._table.item(row, column) or QTableWidgetItem()
            cell.setText(value)
            cell.setToolTip(value)
            cell.setData(Qt.ItemDataRole.UserRole, path)
            if column == self._COL_STATE:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color:
                    cell.setForeground(color)
            self._table.setItem(row, column, cell)
        target_path = str(item.get("target_path", "") or "")
        if target_path:
            self._path_to_row.setdefault(target_path, row)

    def _refresh_target_previews(self, *_args):
        template = self._template()
        output_root = self._output_root()
        move_to_output = True
        self._table.setUpdatesEnabled(False)
        try:
            for item in self._items:
                if item.get("status") not in {"ready", "completed"}:
                    continue
                metadata = item.get("cached_meta") if isinstance(item.get("cached_meta"), dict) else {}
                path = str(item.get("path", "") or "")
                if not path:
                    continue
                target_name = format_repair_filename(template, metadata, path)
                if not move_to_output:
                    target_name = os.path.basename(target_name)
                target_root = output_root if move_to_output and output_root else os.path.dirname(path)
                item["target_name"] = target_name
                item["target_relative_path"] = target_name
                item["target_path"] = os.path.join(target_root, target_name)
                item["output_root"] = target_root
                item["move_to_output"] = move_to_output
                self._items_by_path[path] = item
                self._render_item(item)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.viewport().update()

    def _update_summary(self):
        counts: dict[str, int] = {}
        for item in self._items:
            key = str(item.get("status", "") or "unknown")
            counts[key] = counts.get(key, 0) + 1
        self._summary_label.setText(
            tr(
                f"Total {len(self._items)} | ready {counts.get('ready', 0)} | done {counts.get('completed', 0)} | skipped {counts.get('not_found', 0) + counts.get('ambiguous', 0) + counts.get('skipped', 0)}",
                f"共 {len(self._items)} | 待修复 {counts.get('ready', 0)} | 已完成 {counts.get('completed', 0)} | 跳过 {counts.get('not_found', 0) + counts.get('ambiguous', 0) + counts.get('skipped', 0)}",
                f"合計 {len(self._items)} | 待機 {counts.get('ready', 0)} | 完了 {counts.get('completed', 0)} | スキップ {counts.get('not_found', 0) + counts.get('ambiguous', 0) + counts.get('skipped', 0)}",
            )
        )

    def _append_log(self, message: str):
        text = str(message or "")
        if not self._repair_log_active and not any(
            token in text for token in ("[Repair]", "[修复]", "[修復]", "[NFO]", "[封面]", "[Thumbnail]")
        ):
            return
        self._pending_logs.append(text)
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    def _flush_logs(self):
        if not self._pending_logs:
            self._log_flush_timer.stop()
            return
        chunk = self._pending_logs[:200]
        del self._pending_logs[:200]
        self._log_edit.appendPlainText("\n".join(chunk))
        scrollbar = self._log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        if not self._pending_logs:
            self._log_flush_timer.stop()

    def _clear_log(self):
        self._pending_logs.clear()
        self._log_edit.clear()

    def _show_warning(self, title: str, content: str):
        InfoBar.warning(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self,
        )

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        worker = self._worker
        if worker is None:
            return True
        return stop_qthreads([worker], timeout_ms=timeout_ms)
