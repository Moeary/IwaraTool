from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
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

from ...core.download.template_builder import template_presets
from ...core.download.manager import download_manager  # noqa: F401 - patch point, see page_dependencies
from ...presentation.repair.texts import repair_text
from ...signal_bus import signal_bus
from ..style.fonts import monospace_font
from ..common.ui_state import keep_table_widths
from ..style.theme import PAGE_MARGINS, PAGE_SPACING, FluentSplitter, apply_scrollbars, set_secondary_text
from .controls import RepairControlsMixin
from .folder_input import FolderDropLineEdit
from .log_panel import RepairLogMixin
from .results_table import RepairResultsTableMixin
from .running import RepairRunningMixin
from .worker import RepairPreviewWorker, RepairWorker


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
    _ID_EDIT_TRIGGERS = (
        QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RepairInterface")
        self._worker: RepairWorker | None = None
        self._worker_mode = ""
        self._items: list[dict[str, Any]] = []
        self._items_by_path: dict[str, dict[str, Any]] = {}
        self._path_to_row: dict[str, int] = {}
        # Row -> rendered text, so unchanged rows are not repainted.
        self._row_signatures: dict[int, tuple[str, ...]] = {}
        self._state_styles: dict[str, tuple[str, Any]] | None = None
        self._preview_worker: RepairPreviewWorker | None = None
        self._preview_generation = 0
        self._preview_dirty = False
        # Option edits (often keystrokes) are coalesced into one preview.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._run_target_preview)
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
        title_row.addWidget(TitleLabel(repair_text("repair_center"), self))
        title_row.addStretch()
        self._status_label = BodyLabel(
            repair_text("choose_a_folder_to_begin"),
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
        self._start_btn = PrimaryPushButton(repair_text("repair_selected_folder"), left_panel, FluentIcon.DOWNLOAD)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._start_repair)
        action_row.addWidget(self._start_btn, 1)
        self._stop_btn = PushButton(repair_text("stop"), left_panel, FluentIcon.CANCEL)
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
        folder_layout.addWidget(SubtitleLabel(repair_text("source_folder"), folder_card))
        self._folder_hint = BodyLabel(
            repair_text("drop_a_folder_here_or"),
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
            repair_text("drop_a_local_folder_or")
        )
        self._folder_edit.returnPressed.connect(self._scan_folder)
        folder_row.addWidget(self._folder_edit, 1)
        self._source_browse_btn = PushButton(repair_text("browse"), folder_card, FluentIcon.FOLDER)
        self._source_browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self._source_browse_btn)
        folder_layout.addLayout(folder_row)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(BodyLabel(repair_text("mode"), folder_card))
        self._mode_combo = ComboBox(folder_card)
        self._mode_combo.addItems([
            repair_text("rename_in_place"),
            repair_text("organize_into_output_folder"),
        ])
        mode_row.addWidget(self._mode_combo, 1)
        folder_layout.addLayout(mode_row)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(BodyLabel(repair_text("output"), folder_card))
        self._output_edit = FolderDropLineEdit(folder_card)
        self._output_edit.setClearButtonEnabled(True)
        self._output_edit.setPlaceholderText(
            repair_text("leave_blank_to_organize_inside")
        )
        self._output_edit.textChanged.connect(self._refresh_target_previews)
        output_row.addWidget(self._output_edit, 1)
        self._output_browse_btn = PushButton(repair_text("browse"), folder_card, FluentIcon.FOLDER)
        self._output_browse_btn.clicked.connect(self._browse_output_folder)
        output_row.addWidget(self._output_browse_btn)
        folder_layout.addLayout(output_row)
        self._mode_hint = BodyLabel(folder_card)
        self._mode_hint.setWordWrap(True)
        set_secondary_text(self._mode_hint)
        folder_layout.addWidget(self._mode_hint)
        self._scan_btn = PrimaryPushButton(repair_text("scan"), folder_card, FluentIcon.SEARCH)
        self._scan_btn.clicked.connect(self._scan_folder)
        folder_layout.addWidget(self._scan_btn)
        return folder_card

    def _build_rule_card(self, left_panel: QWidget) -> CardWidget:
        rule_card = CardWidget(left_panel)
        rule_layout = QVBoxLayout(rule_card)
        rule_layout.setContentsMargins(16, 14, 16, 14)
        rule_layout.setSpacing(9)
        rule_layout.addWidget(SubtitleLabel(repair_text("repair_rule"), rule_card))
        self._rule_hint = BodyLabel(
            repair_text("in_place_mode_uses_only"),
            rule_card,
        )
        self._rule_hint.setWordWrap(True)
        set_secondary_text(self._rule_hint)
        rule_layout.addWidget(self._rule_hint)
        rule_row = QHBoxLayout()
        rule_row.setSpacing(8)
        rule_row.addWidget(BodyLabel(repair_text("rule"), rule_card))
        self._rule_combo = ComboBox(rule_card)
        self._rule_combo.currentIndexChanged.connect(self._on_rule_changed)
        rule_row.addWidget(self._rule_combo, 1)
        rule_layout.addLayout(rule_row)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_row.addWidget(BodyLabel(repair_text("preset"), rule_card))
        self._preset_combo = ComboBox(rule_card)
        self._template_presets = template_presets()
        self._preset_combo.addItem(repair_text("custom"))
        for preset in self._template_presets:
            self._preset_combo.addItem(preset.label)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_chosen)
        preset_row.addWidget(self._preset_combo, 1)
        rule_layout.addLayout(preset_row)

        template_row = QHBoxLayout()
        template_row.setSpacing(8)
        template_row.addWidget(BodyLabel(repair_text("name"), rule_card))
        self._template_edit = LineEdit(rule_card)
        self._template_edit.setPlaceholderText("{YYYY-MM-DD}_{title}_{id}.mp4")
        self._template_edit.textChanged.connect(self._sync_preset_combo)
        self._template_edit.textChanged.connect(self._refresh_target_previews)
        template_row.addWidget(self._template_edit, 1)
        rule_layout.addLayout(template_row)
        return rule_card

    def _build_options_card(self, left_panel: QWidget) -> CardWidget:
        options_card = CardWidget(left_panel)
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(16, 14, 16, 14)
        options_layout.setSpacing(7)
        options_layout.addWidget(SubtitleLabel(repair_text("repair_options"), options_card))
        self._rename_check = CheckBox(repair_text("rename_existing_videos"), options_card)
        self._rename_check.setChecked(True)
        self._rename_check.toggled.connect(self._refresh_target_previews)
        options_layout.addWidget(self._rename_check)
        self._download_video_check = CheckBox(
            repair_text("allow_video_download_when_needed"),
            options_card,
        )
        self._download_video_check.setChecked(False)
        options_layout.addWidget(self._download_video_check)
        self._local_video_hint = BodyLabel(
            repair_text("existing_local_videos_are_always"),
            options_card,
        )
        self._local_video_hint.setWordWrap(True)
        set_secondary_text(self._local_video_hint)
        options_layout.addWidget(self._local_video_hint)
        self._thumbnail_check = CheckBox(repair_text("download_cover_screenshot"), options_card)
        self._thumbnail_check.setChecked(True)
        self._thumbnail_check.toggled.connect(self._refresh_target_previews)
        options_layout.addWidget(self._thumbnail_check)
        self._nfo_check = CheckBox(repair_text("write_nfo_metadata"), options_card)
        self._nfo_check.setChecked(True)
        self._nfo_check.toggled.connect(self._refresh_target_previews)
        options_layout.addWidget(self._nfo_check)
        self._history_check = CheckBox(repair_text("add_to_download_history"), options_card)
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
        log_header.addWidget(SubtitleLabel(repair_text("repair_log"), log_card))
        log_header.addStretch()
        clear_btn = TransparentToolButton(FluentIcon.DELETE, log_card)
        clear_btn.setToolTip(repair_text("clear_log"))
        clear_btn.clicked.connect(self._clear_log)
        log_header.addWidget(clear_btn)
        log_layout.addLayout(log_header)
        self._log_edit = PlainTextEdit(log_card)
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumBlockCount(self._MAX_LOG_BLOCKS)
        self._log_edit.setPlaceholderText(repair_text("repair_logs_will_appear_here"))
        self._log_edit.setFont(monospace_font(9))
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
        list_header.addWidget(SubtitleLabel(repair_text("repair_list"), list_card))
        self._summary_label = BodyLabel(repair_text("no_folder_scanned"), list_card)
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
                repair_text("state"),
                repair_text("original_path"),
                "Iwara ID",
                repair_text("title"),
                repair_text("date"),
                repair_text("target_path"),
                repair_text("detail"),
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Only the Iwara ID cell of unfinished rows is editable (see
        # ``_render_item``): typing an ID looks that file up again.
        self._table.setEditTriggers(self._ID_EDIT_TRIGGERS)
        self._table.itemChanged.connect(self._on_table_item_changed)
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
        self._row_signatures.clear()
        self._state_styles = self._build_state_styles()
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
