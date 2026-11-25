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
    SegmentedWidget,
    SubtitleLabel,
)

from ...core.search.post_filter import POST_FILTER_SORTS, PUBLISHED_WITHIN_DAYS, PostFilter
from ...presentation.search import post_filter_text as text
from ..style.theme import set_secondary_text, set_status_text

_INTEGER = QRegularExpression(r"^\d{0,10}$")
_DECIMAL = QRegularExpression(r"^\d{0,6}([.,]\d{0,2})?$")


_sort_label = text.sort_label
_within_label = text.within_label
_number_text = text.number_text
_parse_int = text.parse_int
_parse_float = text.parse_float


class SearchPostFilterDialog(MessageBoxBase):
    """Likes / views / duration / age / like ratio / tags, plus a local sort."""

    def __init__(self, parent, current: PostFilter):
        super().__init__(parent)
        self.viewLayout.addWidget(
            SubtitleLabel(text.title(), self)
        )
        hint = CaptionLabel(text.hint(), self)
        hint.setWordWrap(True)
        set_secondary_text(hint)
        self.viewLayout.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self._min_likes, self._max_likes = self._range_row(grid, 0, text.label("likes"), _INTEGER)
        self._min_views, self._max_views = self._range_row(grid, 1, text.label("views"), _INTEGER)
        self._min_minutes, self._max_minutes = self._range_row(
            grid, 2, text.label("duration"), _DECIMAL
        )

        grid.addWidget(BodyLabel(text.label("published"), self), 3, 0)
        self._within_combo = ComboBox(self)
        for days in (None, *PUBLISHED_WITHIN_DAYS):
            self._within_combo.addItem(_within_label(days), userData=days)
        grid.addWidget(self._within_combo, 3, 1)

        grid.addWidget(BodyLabel(text.label("ratio"), self), 3, 2)
        self._ratio_edit = self._number_edit(_DECIMAL, text.label("ratio_hint"))
        self._ratio_edit.setToolTip(text.label("ratio_tooltip"))
        grid.addWidget(self._ratio_edit, 3, 3)

        grid.addWidget(BodyLabel(text.label("exclude"), self), 4, 0)
        self._exclude_edit = LineEdit(self)
        self._exclude_edit.setClearButtonEnabled(True)
        self._exclude_edit.setPlaceholderText(
            text.label("exclude_hint")
        )
        grid.addWidget(self._exclude_edit, 4, 1, 1, 3)

        grid.addWidget(BodyLabel(text.label("sort"), self), 5, 0)
        self._sort_combo = ComboBox(self)
        for key in POST_FILTER_SORTS:
            self._sort_combo.addItem(_sort_label(key), userData=key)
        grid.addWidget(self._sort_combo, 5, 1, 1, 3)
        grid.addWidget(BodyLabel(text.label("orientation"), self), 6, 0)
        # Keep these three choices inline: a transient combo popup can lose
        # focus inside a macOS mask dialog and crash while Qt deletes it.
        self._orientation_selector = SegmentedWidget(self)
        for label, key in (
            (text.orientation_label("any"), "any"),
            (text.orientation_label("portrait"), "portrait"),
            (text.orientation_label("landscape"), "landscape"),
        ):
            self._orientation_selector.addItem(key, label)
        grid.addWidget(self._orientation_selector, 6, 1, 1, 3)
        orientation_hint = CaptionLabel(text.orientation_hint(), self)
        orientation_hint.setWordWrap(True)
        set_secondary_text(orientation_hint)
        grid.addWidget(orientation_hint, 7, 0, 1, 4)
        self.viewLayout.addLayout(grid)

        self._error_label = CaptionLabel("", self)
        set_status_text(self._error_label, "danger")
        self._error_label.hide()
        self.viewLayout.addWidget(self._error_label)

        self.yesButton.setText(text.label("apply"))
        self.cancelButton.setText(text.label("cancel"))
        self._clear_btn = PushButton(text.label("clear"), self.buttonGroup)
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
        low = self._number_edit(pattern, text.label("min"))
        high = self._number_edit(pattern, text.label("max"))
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
        self._orientation_selector.setCurrentItem(value.orientation or "any")

    def _clear(self):
        self._load(PostFilter())
        self._error_label.hide()

    def post_filter(self) -> PostFilter:
        orientation = self._orientation_selector.currentRouteKey()
        return PostFilter(
            min_likes=_parse_int(self._min_likes.text()),
            max_likes=_parse_int(self._max_likes.text()),
            min_views=_parse_int(self._min_views.text()),
            max_views=_parse_int(self._max_views.text()),
            min_duration_minutes=_parse_float(self._min_minutes.text()),
            max_duration_minutes=_parse_float(self._max_minutes.text()),
            published_within_days=self._within_combo.currentData(),
            min_like_ratio_percent=_parse_float(self._ratio_edit.text()),
            exclude_tags=text.build_filter(exclude_tags=self._exclude_edit.text()).exclude_tags,
            sort=str(self._sort_combo.currentData() or "server"),
            orientation="" if orientation == "any" else orientation,
        )

    def validate(self) -> bool:
        value = self.post_filter()
        error = value.validation_error()
        if error:
            self._error_label.setText(text.validation_message(error))
            self._error_label.show()
            return False
        self._result = value
        return True

    @property
    def result_filter(self) -> PostFilter:
        return self._result
