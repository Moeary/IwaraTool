from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
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
    ScrollArea,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    TransparentToolButton,
)

from ...core.manager import download_manager  # noqa: F401 - patch point, see page_dependencies
from ...i18n import tr
from ...signal_bus import signal_bus
from ..ui_state import keep_table_widths
from ..theme import PAGE_MARGINS, PAGE_SPACING, FluentSplitter, apply_scrollbars, set_secondary_text
from .controls import RepairControlsMixin
from .folder_input import FolderDropLineEdit
from .log_panel import RepairLogMixin
from .results_table import RepairResultsTableMixin
from .running import RepairRunningMixin
from .worker import RepairWorker


class RepairInterface(
    RepairControlsMixin,
    RepairRunningMixin,
    RepairResultsTableMixin,
    RepairLogMixin,
    QWidget,
):
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
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("Repair Center", "修复中心", "修復センター"), self))
        title_row.addStretch()
        self._status_label = BodyLabel(
            tr("Choose a folder to begin", "请选择一个文件夹开始", "フォルダーを選択して開始"),
            self,
        )
        set_secondary_text(self._status_label)
        title_row.addWidget(self._status_label)
        root.addLayout(title_row)

        splitter = FluentSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        self._splitter = splitter
        root.addWidget(splitter, stretch=1)

        left_panel, left_layout = self._build_controls_scroll(splitter)
        left_layout.addWidget(self._build_folder_card(left_panel))
        left_layout.addWidget(self._build_rule_card(left_panel))
        left_layout.addWidget(self._build_options_card(left_panel))
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

        left_layout.addWidget(self._build_log_card(left_panel), stretch=1)

        right_panel = self._build_results_panel(splitter)

        splitter.addWidget(self._controls_scroll)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([410, 1050])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._folder_edit.textChanged.connect(self._refresh_target_previews)
        self._update_mode_controls()

    def _build_controls_scroll(self, splitter) -> tuple[QWidget, QVBoxLayout]:
        # The stacked window takes the largest minimum size of *all* pages,
        # including hidden ones. A tall repair form used to force the entire
        # window above the screen height at 175% DPI. Windows then clamps the
        # size during dragging while Qt grows it again, causing snap-back.
        self._controls_scroll = ScrollArea(splitter)
        self._controls_scroll.setObjectName("RepairControlsScroll")
        self._controls_scroll.setWidgetResizable(True)
        self._controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._controls_scroll.setMinimumWidth(340)
        self._controls_scroll.setStyleSheet(
            "QScrollArea#RepairControlsScroll { background: transparent; border: none; }"
            "QScrollArea#RepairControlsScroll > QWidget > QWidget { background: transparent; }"
        )
        left_panel = QWidget()
        self._controls_scroll.setWidget(left_panel)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        return left_panel, left_layout

    def _build_folder_card(self, left_panel: QWidget) -> CardWidget:
        folder_card = CardWidget(left_panel)
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(16, 14, 16, 14)
        folder_layout.setSpacing(9)
        folder_layout.addWidget(SubtitleLabel(tr("Source Folder", "视频文件夹", "動画フォルダー"), folder_card))
        self._folder_hint = BodyLabel(
            tr(
                "Drop a folder here or browse it. Subfolders are scanned too.",
                "可将文件夹拖入此处或浏览选择；也会扫描子文件夹。",
                "フォルダーをドロップまたは参照できます。サブフォルダーも検索します。",
            ),
            folder_card,
        )
        # Unwrapped hints become the scroll content's minimum width and hide
        # the controls beside the path inputs in narrow splitter panes.
        self._folder_hint.setWordWrap(True)
        set_secondary_text(self._folder_hint)
        folder_layout.addWidget(self._folder_hint)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self._folder_edit = FolderDropLineEdit(folder_card)
        self._folder_edit.setClearButtonEnabled(True)
        self._folder_edit.setPlaceholderText(
            tr("Drop a local folder or enter its path…", "拖入文件夹或输入路径…", "ローカルフォルダーをドロップまたは入力…")
        )
        self._folder_edit.returnPressed.connect(self._scan_folder)
        folder_row.addWidget(self._folder_edit, 1)
        self._source_browse_btn = PushButton(tr("Browse", "浏览", "参照"), folder_card, FluentIcon.FOLDER)
        self._source_browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self._source_browse_btn)
        folder_layout.addLayout(folder_row)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(BodyLabel(tr("Mode", "模式", "モード"), folder_card))
        self._mode_combo = ComboBox(folder_card)
        self._mode_combo.addItems([
            tr("Rename in place", "原地重命名", "現在の場所で名前変更"),
            tr("Organize into output folder", "整理到输出目录", "出力フォルダーに整理"),
        ])
        mode_row.addWidget(self._mode_combo, 1)
        folder_layout.addLayout(mode_row)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(BodyLabel(tr("Output", "输出", "出力"), folder_card))
        self._output_edit = FolderDropLineEdit(folder_card)
        self._output_edit.setClearButtonEnabled(True)
        self._output_edit.setPlaceholderText(
            tr(
                "Leave blank to organize inside the source folder…",
                "留空则在输入文件夹内整理…",
                "空欄なら入力フォルダー内に整理…",
            )
        )
        self._output_edit.textChanged.connect(self._refresh_target_previews)
        output_row.addWidget(self._output_edit, 1)
        self._output_browse_btn = PushButton(tr("Browse", "浏览", "参照"), folder_card, FluentIcon.FOLDER)
        self._output_browse_btn.clicked.connect(self._browse_output_folder)
        output_row.addWidget(self._output_browse_btn)
        folder_layout.addLayout(output_row)
        self._mode_hint = BodyLabel(folder_card)
        self._mode_hint.setWordWrap(True)
        set_secondary_text(self._mode_hint)
        folder_layout.addWidget(self._mode_hint)
        self._scan_btn = PrimaryPushButton(tr("Scan", "扫描", "検索"), folder_card, FluentIcon.SEARCH)
        self._scan_btn.clicked.connect(self._scan_folder)
        folder_layout.addWidget(self._scan_btn)
        return folder_card

    def _build_rule_card(self, left_panel: QWidget) -> CardWidget:
        rule_card = CardWidget(left_panel)
        rule_layout = QVBoxLayout(rule_card)
        rule_layout.setContentsMargins(16, 14, 16, 14)
        rule_layout.setSpacing(9)
        rule_layout.addWidget(SubtitleLabel(tr("Repair Rule", "修复规则", "修復ルール"), rule_card))
        self._rule_hint = BodyLabel(
            tr(
                "In-place mode uses only the filename. Organize mode also applies directory segments such as {author}/.",
                "原地模式只应用文件名段；整理模式还会应用 {author}/ 等目录段。",
                "現在の場所ではファイル名のみを適用します。整理モードでは {author}/ などのディレクトリ部分も適用します。",
            ),
            rule_card,
        )
        self._rule_hint.setWordWrap(True)
        set_secondary_text(self._rule_hint)
        rule_layout.addWidget(self._rule_hint)
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
        return rule_card

    def _build_options_card(self, left_panel: QWidget) -> CardWidget:
        options_card = CardWidget(left_panel)
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(16, 14, 16, 14)
        options_layout.setSpacing(7)
        options_layout.addWidget(SubtitleLabel(tr("Repair Options", "修复选项", "修復オプション"), options_card))
        self._rename_check = CheckBox(tr("Rename existing videos", "重命名已有视频", "既存動画を名前変更"), options_card)
        self._rename_check.setChecked(True)
        self._rename_check.toggled.connect(self._refresh_target_previews)
        options_layout.addWidget(self._rename_check)
        self._download_video_check = CheckBox(
            tr("Allow video download when needed", "必要时允许下载视频", "必要時に動画ダウンロードを許可"),
            options_card,
        )
        self._download_video_check.setChecked(False)
        options_layout.addWidget(self._download_video_check)
        self._local_video_hint = BodyLabel(
            tr(
                "Existing local videos are always kept; this repair page never replaces them.",
                "已有本地视频始终保留；修复页不会替换它们。",
                "既存のローカル動画は保持され、修復ページで置き換えることはありません。",
            ),
            options_card,
        )
        self._local_video_hint.setWordWrap(True)
        set_secondary_text(self._local_video_hint)
        options_layout.addWidget(self._local_video_hint)
        self._thumbnail_check = CheckBox(tr("Download cover screenshot", "下载封面截图", "サムネイルを取得"), options_card)
        self._thumbnail_check.setChecked(True)
        self._thumbnail_check.toggled.connect(self._refresh_target_previews)
        options_layout.addWidget(self._thumbnail_check)
        self._nfo_check = CheckBox(tr("Write NFO metadata", "写入 NFO 元数据", "NFO メタデータを書き込む"), options_card)
        self._nfo_check.setChecked(True)
        self._nfo_check.toggled.connect(self._refresh_target_previews)
        options_layout.addWidget(self._nfo_check)
        self._history_check = CheckBox(tr("Add to download history", "加入下载历史", "ダウンロード履歴に追加"), options_card)
        self._history_check.setChecked(True)
        options_layout.addWidget(self._history_check)
        return options_card

    def _build_log_card(self, left_panel: QWidget) -> CardWidget:
        log_card = CardWidget(left_panel)
        log_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(8)
        log_header = QHBoxLayout()
        log_header.addWidget(SubtitleLabel(tr("Repair Log", "修复日志", "修復ログ"), log_card))
        log_header.addStretch()
        clear_btn = TransparentToolButton(FluentIcon.DELETE, log_card)
        clear_btn.setToolTip(tr("Clear log", "清空日志", "ログをクリア"))
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
        apply_scrollbars(self._log_edit)
        log_layout.addWidget(self._log_edit)
        return log_card

    def _build_results_panel(self, splitter) -> QWidget:
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
        self._summary_label = BodyLabel(tr("No folder scanned", "尚未扫描文件夹", "未検索"), list_card)
        self._summary_label.setWordWrap(True)
        set_secondary_text(self._summary_label)
        list_header.addStretch()
        list_layout.addLayout(list_header)
        # A full-width row lets the wrapped summary grow in height; inside the
        # header's QHBoxLayout it was clipped once it needed a second line.
        list_layout.addWidget(self._summary_label)

        self._table = TableWidget(list_card)
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("Original Path", "原路径", "元のパス"),
                "Iwara ID",
                tr("Title", "标题", "タイトル"),
                tr("Date", "日期", "日付"),
                tr("Target Path", "目标路径", "変更先パス"),
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
        keep_table_widths(self._table, {self._COL_STATE: 78, self._COL_ID: 125, self._COL_DATE: 105})
        list_layout.addWidget(self._table, stretch=1)
        right_layout.addWidget(list_card, stretch=1)
        return right_panel

    def refresh_theme_styles(self):
        apply_scrollbars(self._log_edit)
        self._table.setUpdatesEnabled(False)
        try:
            for item in self._items:
                path = str(item.get("path", "") or "")
                if path in self._path_to_row:
                    self._render_item(item)
        finally:
            self._table.setUpdatesEnabled(True)

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
