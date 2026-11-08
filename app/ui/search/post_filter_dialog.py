"""Popup that edits the post-search filter, so it costs the page no height."""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from ...core.search.filtering import split_search_terms
from ...core.search.post_filter import POST_FILTER_SORTS, PUBLISHED_WITHIN_DAYS, PostFilter
from ...i18n import tr
from ..theme import set_secondary_text, set_status_text

_INTEGER = QRegularExpression(r"^\d{0,10}$")
_DECIMAL = QRegularExpression(r"^\d{0,6}([.,]\d{0,2})?$")


def _sort_label(key: str) -> str:
    return {
        "server": tr("Keep search order", "保持搜索顺序", "検索順のまま"),
        "likes": tr("Most liked", "点赞最多", "いいね順"),
        "views": tr("Most viewed", "播放最多", "再生数順"),
        "like_ratio": tr("Highest like ratio", "点赞率最高", "いいね率順"),
        "duration_desc": tr("Longest", "时长最长", "長い順"),
        "duration_asc": tr("Shortest", "时长最短", "短い順"),
        "date_desc": tr("Newest", "最新发布", "新しい順"),
        "date_asc": tr("Oldest", "最早发布", "古い順"),
    }[key]


def _within_label(days: int | None) -> str:
    if days is None:
        return tr("Any time", "不限", "指定なし")
    if days == 1:
        return tr("Last 24 hours", "24 小时内", "24時間以内")
    if days == 365:
        return tr("Last year", "一年内", "1年以内")
    return tr(f"Last {days} days", f"{days} 天内", f"{days}日以内")


def _number_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def _parse_float(text: str) -> float | None:
    text = text.strip().replace(",", ".")
    return float(text) if text and text != "." else None


class SearchPostFilterDialog(MessageBoxBase):
    """Likes / views / duration / age / like ratio / tags, plus a local sort."""

    def __init__(self, parent, current: PostFilter):
        super().__init__(parent)
        self.viewLayout.addWidget(
            SubtitleLabel(tr("Filter loaded results", "筛选已加载的结果", "読み込み済みの結果を絞り込む"), self)
        )
        hint = CaptionLabel(
            tr(
                "The Iwara search API cannot limit these before searching, so they are applied to the results "
                "on the current page and kept while paging. Leave a box empty for no limit. Oreno3D results "
                "join once their Iwara statistics are loaded.",
                "Iwara 搜索接口无法在搜索前限定这些条件，因此会对当前页已加载的结果进行筛选并重新排序，翻页后继续生效。"
                "留空表示不限；Oreno3D 结果需补全 Iwara 数据后才参与筛选。",
                "Iwara の検索 API は検索前にこれらを指定できないため、現在のページで読み込んだ結果に適用し、"
                "ページ移動後も維持します。空欄は制限なしです。Oreno3D の結果は Iwara の統計を取得後に対象になります。",
            ),
            self,
        )
        hint.setWordWrap(True)
        set_secondary_text(hint)
        self.viewLayout.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self._min_likes, self._max_likes = self._range_row(grid, 0, tr("Likes", "点赞数", "いいね数"), _INTEGER)
        self._min_views, self._max_views = self._range_row(grid, 1, tr("Views", "播放数", "再生数"), _INTEGER)
        self._min_minutes, self._max_minutes = self._range_row(
            grid, 2, tr("Duration (min)", "时长（分钟）", "長さ（分）"), _DECIMAL
        )

        grid.addWidget(BodyLabel(tr("Published", "发布时间", "公開日"), self), 3, 0)
        self._within_combo = ComboBox(self)
        for days in (None, *PUBLISHED_WITHIN_DAYS):
            self._within_combo.addItem(_within_label(days), userData=days)
        grid.addWidget(self._within_combo, 3, 1)

        grid.addWidget(BodyLabel(tr("Min like ratio %", "最低点赞率 %", "最低いいね率 %"), self), 3, 2)
        self._ratio_edit = self._number_edit(_DECIMAL, tr("e.g. 5", "如 5", "例: 5"))
        self._ratio_edit.setToolTip(tr("Likes ÷ views", "点赞数 ÷ 播放数", "いいね数 ÷ 再生数"))
        grid.addWidget(self._ratio_edit, 3, 3)

        grid.addWidget(BodyLabel(tr("Exclude tags", "排除标签", "除外タグ"), self), 4, 0)
        self._exclude_edit = LineEdit(self)
        self._exclude_edit.setClearButtonEnabled(True)
        self._exclude_edit.setPlaceholderText(
            tr("Comma or space separated", "以逗号或空格分隔", "カンマまたはスペース区切り")
        )
        grid.addWidget(self._exclude_edit, 4, 1, 1, 3)

        grid.addWidget(BodyLabel(tr("Sort by", "排序", "並び順"), self), 5, 0)
        self._sort_combo = ComboBox(self)
        for key in POST_FILTER_SORTS:
            self._sort_combo.addItem(_sort_label(key), userData=key)
        grid.addWidget(self._sort_combo, 5, 1, 1, 3)
        self.viewLayout.addLayout(grid)

        self._error_label = CaptionLabel("", self)
        set_status_text(self._error_label, "danger")
        self._error_label.hide()
        self.viewLayout.addWidget(self._error_label)

        self.yesButton.setText(tr("Apply", "应用", "適用"))
        self.cancelButton.setText(tr("Cancel", "取消", "キャンセル"))
        self._clear_btn = PushButton(tr("Clear", "清除条件", "クリア"), self.buttonGroup)
        self._clear_btn.clicked.connect(self._clear)
        self.buttonLayout.insertWidget(0, self._clear_btn, 1)
        self.widget.setMinimumWidth(560)
        self._load(current)
        self._result = current

    def _number_edit(self, pattern: QRegularExpression, placeholder: str) -> LineEdit:
        edit = LineEdit(self)
        edit.setValidator(QRegularExpressionValidator(pattern, edit))
        edit.setPlaceholderText(placeholder)
        edit.setClearButtonEnabled(True)
        edit.returnPressed.connect(self.yesButton.click)
        return edit

    def _range_row(self, grid: QGridLayout, row: int, label: str, pattern: QRegularExpression):
        grid.addWidget(BodyLabel(label, self), row, 0)
        low = self._number_edit(pattern, tr("Min", "最小", "最小"))
        high = self._number_edit(pattern, tr("Max", "最大", "最大"))
        grid.addWidget(low, row, 1)
        dash = QWidget(self)
        dash_layout = QHBoxLayout(dash)
        dash_layout.setContentsMargins(0, 0, 0, 0)
        dash_layout.addWidget(BodyLabel("—", dash), 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(dash, row, 2)
        grid.addWidget(high, row, 3)
        return low, high

    def _load(self, value: PostFilter):
        self._min_likes.setText(_number_text(value.min_likes))
        self._max_likes.setText(_number_text(value.max_likes))
        self._min_views.setText(_number_text(value.min_views))
        self._max_views.setText(_number_text(value.max_views))
        self._min_minutes.setText(_number_text(value.min_duration_minutes))
        self._max_minutes.setText(_number_text(value.max_duration_minutes))
        self._ratio_edit.setText(_number_text(value.min_like_ratio_percent))
        self._exclude_edit.setText(", ".join(value.exclude_tags))
        within = self._within_combo.findData(value.published_within_days)
        self._within_combo.setCurrentIndex(max(0, within))
        sort = self._sort_combo.findData(value.sort)
        self._sort_combo.setCurrentIndex(max(0, sort))

    def _clear(self):
        self._load(PostFilter())
        self._error_label.hide()

    def post_filter(self) -> PostFilter:
        return PostFilter(
            min_likes=_parse_int(self._min_likes.text()),
            max_likes=_parse_int(self._max_likes.text()),
            min_views=_parse_int(self._min_views.text()),
            max_views=_parse_int(self._max_views.text()),
            min_duration_minutes=_parse_float(self._min_minutes.text()),
            max_duration_minutes=_parse_float(self._max_minutes.text()),
            published_within_days=self._within_combo.currentData(),
            min_like_ratio_percent=_parse_float(self._ratio_edit.text()),
            exclude_tags=split_search_terms(self._exclude_edit.text()),
            sort=str(self._sort_combo.currentData() or "server"),
        )

    def validate(self) -> bool:
        value = self.post_filter()
        error = value.validation_error()
        if error:
            names = {
                "likes": tr("likes", "点赞数", "いいね数"),
                "views": tr("views", "播放数", "再生数"),
                "duration": tr("duration", "时长", "長さ"),
            }
            name = names[error]
            self._error_label.setText(
                tr(
                    f"The minimum {name} is larger than the maximum.",
                    f"{name}的最小值大于最大值。",
                    f"{name}の最小値が最大値より大きくなっています。",
                )
            )
            self._error_label.show()
            return False
        self._result = value
        return True

    @property
    def result_filter(self) -> PostFilter:
        return self._result
