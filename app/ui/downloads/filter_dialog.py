"""Download filter dialog and the shared style of option toggle buttons."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    SwitchButton,
    isDarkTheme,
)

from ...config import app_config
from ...i18n import tr
from ..theme import palette


def option_button_style(checked: bool) -> str:
    """Return a theme-aware style for the legacy download option toggles."""
    p = palette()
    if checked:
        background, foreground, border = p.accent, ("#000000" if isDarkTheme() else "#ffffff"), p.accent
    else:
        background, foreground, border = p.surface, p.text, p.border
    return f"""
QPushButton {{
    background-color: {background};
    color: {foreground};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 8px 10px;
}}
QPushButton:hover {{ background-color: {p.hover if not checked else background}; }}
"""


class FilterDialog(QDialog):
    """Simple filter configuration dialog for likes/date/views."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Filter Rules", "筛选条件", "フィルター条件"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        enable_row = QHBoxLayout()
        enable_row.addWidget(
            BodyLabel(tr("Enable filters", "启用筛选", "フィルターを有効化"), self)
        )
        self._filter_enabled_switch = SwitchButton(self)
        enable_row.addWidget(self._filter_enabled_switch)
        enable_row.addStretch()
        layout.addLayout(enable_row)
        self._build_metric_filter_rows(layout)
        self._build_tag_filter_rows(layout)
        self._build_dialog_footer(layout)

    def _build_metric_filter_rows(self, layout: QVBoxLayout):
        likes_row = QHBoxLayout()
        likes_row.addWidget(
            BodyLabel(tr("Enable likes filter", "启用点赞筛选", "いいね数フィルターを有効化"), self)
        )
        self._likes_switch = SwitchButton(self)
        likes_row.addWidget(self._likes_switch)
        likes_row.addWidget(BodyLabel(tr("Likes >=", "点赞数 >=", "いいね数 >="), self))
        self._likes_edit = LineEdit(self)
        self._likes_edit.setPlaceholderText("0")
        likes_row.addWidget(self._likes_edit)
        layout.addLayout(likes_row)

        views_row = QHBoxLayout()
        views_row.addWidget(
            BodyLabel(tr("Enable views filter", "启用播放筛选", "再生数フィルターを有効化"), self)
        )
        self._views_switch = SwitchButton(self)
        views_row.addWidget(self._views_switch)
        views_row.addWidget(BodyLabel(tr("Views >=", "播放数 >=", "再生数 >="), self))
        self._views_edit = LineEdit(self)
        self._views_edit.setPlaceholderText("0")
        views_row.addWidget(self._views_edit)
        layout.addLayout(views_row)

        date_row = QHBoxLayout()
        date_row.addWidget(
            BodyLabel(tr("Enable date filter", "启用日期筛选", "日付フィルターを有効化"), self)
        )
        self._date_switch = SwitchButton(self)
        date_row.addWidget(self._date_switch)
        date_row.addWidget(BodyLabel(tr("Start YYYY-MM-DD", "起始 YYYY-MM-DD", "開始 YYYY-MM-DD"), self))
        self._start_edit = LineEdit(self)
        self._start_edit.setPlaceholderText("1970-01-01")
        date_row.addWidget(self._start_edit)
        date_row.addWidget(BodyLabel(tr("End YYYY-MM-DD", "结束 YYYY-MM-DD", "終了 YYYY-MM-DD"), self))
        self._end_edit = LineEdit(self)
        self._end_edit.setPlaceholderText(datetime.now().strftime("%Y-%m-%d"))
        date_row.addWidget(self._end_edit)
        layout.addLayout(date_row)

    def _build_tag_filter_rows(self, layout: QVBoxLayout):
        include_row = QHBoxLayout()
        include_row.addWidget(
            BodyLabel(
                tr("Enable include tags", "启用包含标签", "包含タグを有効化"),
                self,
            )
        )
        self._include_tags_switch = SwitchButton(self)
        include_row.addWidget(self._include_tags_switch)
        include_row.addWidget(
            BodyLabel(
                tr("Tags list", "标签列表", "タグ一覧"),
                self,
            )
        )
        self._include_tags_edit = LineEdit(self)
        self._include_tags_edit.setPlaceholderText(
            tr("e.g. 2d,mmd", "例如 2d,mmd", "例: 2d,mmd")
        )
        include_row.addWidget(self._include_tags_edit)
        layout.addLayout(include_row)

        exclude_row = QHBoxLayout()
        exclude_row.addWidget(
            BodyLabel(
                tr("Enable exclude tags", "启用排除标签", "除外タグを有効化"),
                self,
            )
        )
        self._exclude_tags_switch = SwitchButton(self)
        exclude_row.addWidget(self._exclude_tags_switch)
        exclude_row.addWidget(BodyLabel(tr("Tags list", "标签列表", "タグ一覧"), self))
        self._exclude_tags_edit = LineEdit(self)
        self._exclude_tags_edit.setPlaceholderText(
            tr("e.g. vr,ai", "例如 vr,ai", "例: vr,ai")
        )
        exclude_row.addWidget(self._exclude_tags_edit)
        layout.addLayout(exclude_row)

    def _build_dialog_footer(self, layout: QVBoxLayout):
        tips = BodyLabel(
            tr(
                "Each filter can be enabled independently. Empty end date means today.",
                "筛选项可单独启用。结束日期留空表示今天。",
                "各フィルターは個別に有効化できます。終了日が空なら当日扱いです。",
            ),
            self,
        )
        layout.addWidget(tips)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = PrimaryPushButton(tr("Cancel", "取消", "キャンセル"), self)
        save_btn = PrimaryPushButton(tr("Save", "保存", "保存"), self)
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._save_and_accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _load(self):
        self._filter_enabled_switch.setChecked(app_config.filter_enabled)
        self._likes_switch.setChecked(app_config.filter_min_likes_enabled)
        self._views_switch.setChecked(app_config.filter_min_views_enabled)
        self._date_switch.setChecked(app_config.filter_date_enabled)
        self._likes_edit.setText(str(app_config.filter_min_likes))
        self._views_edit.setText(str(app_config.filter_min_views))
        self._start_edit.setText(app_config.filter_start_date or "1970-01-01")
        self._end_edit.setText(app_config.filter_end_date or datetime.now().strftime("%Y-%m-%d"))
        self._include_tags_switch.setChecked(app_config.filter_include_tags_enabled)
        self._include_tags_edit.setText(app_config.filter_include_tags or "")
        self._exclude_tags_switch.setChecked(app_config.filter_exclude_tags_enabled)
        self._exclude_tags_edit.setText(app_config.filter_exclude_tags or "")

    def _save_and_accept(self):
        try:
            likes = int((self._likes_edit.text() or "0").strip())
            views = int((self._views_edit.text() or "0").strip())
            if likes < 0 or views < 0:
                raise ValueError(
                    tr(
                        "Likes/views cannot be negative",
                        "点赞/播放不能为负数",
                        "いいね数/再生数は負数にできません",
                    )
                )

            start = (self._start_edit.text() or "1970-01-01").strip()
            end = (self._end_edit.text() or "").strip()
            datetime.strptime(start, "%Y-%m-%d")
            if end:
                datetime.strptime(end, "%Y-%m-%d")
        except Exception as exc:
            InfoBar.error(
                title=tr("Invalid filter values", "筛选参数无效", "フィルター値が不正です"),
                content=str(exc),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        app_config.filter_enabled = self._filter_enabled_switch.isChecked()
        app_config.filter_min_likes_enabled = self._likes_switch.isChecked()
        app_config.filter_min_views_enabled = self._views_switch.isChecked()
        app_config.filter_date_enabled = self._date_switch.isChecked()
        app_config.filter_min_likes = likes
        app_config.filter_min_views = views
        app_config.filter_start_date = start
        app_config.filter_end_date = end
        app_config.filter_include_tags_enabled = self._include_tags_switch.isChecked()
        app_config.filter_include_tags = self._include_tags_edit.text().strip()
        app_config.filter_exclude_tags_enabled = self._exclude_tags_switch.isChecked()
        app_config.filter_exclude_tags = self._exclude_tags_edit.text().strip()
        self.accept()
