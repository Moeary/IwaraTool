"""Editor for which rows the Home page shows, and in what order.

Rows are checkable list entries (like the table-field dialog) so enabling,
re-ordering, editing and deleting all feel the same as elsewhere in the app.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    ListWidget,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    ToolButton,
)

from ...core.home.feed import (
    MAX_SECTIONS,
    MODE_BROWSE,
    HomeSectionSpec,
    default_specs,
    load_specs,
    save_specs,
)
from ...presentation.home import layout as home_layout
from ...presentation.home.layout import mode_label, spec_summary  # noqa: F401 - re-exported
from ...signal_bus import signal_bus
from ..style.theme import set_secondary_text
from ..common.fluent_dialogs import show_fluent_confirmation


class SectionEditorDialog(MessageBoxBase):
    """Add or edit one Home row."""

    def __init__(self, spec: HomeSectionSpec | None, parent=None):
        super().__init__(parent)
        self._original = spec
        self.result_spec: HomeSectionSpec | None = None
        self.viewLayout.addWidget(SubtitleLabel(
            home_layout.editor_title(spec is not None), self,
        ))

        def field(label: str, widget: QWidget) -> QHBoxLayout:
            row = QHBoxLayout()
            caption = BodyLabel(label, self)
            caption.setFixedWidth(96)
            set_secondary_text(caption)
            row.addWidget(caption)
            row.addWidget(widget, 1)
            self.viewLayout.addLayout(row)
            return row

        self._title = LineEdit(self)
        labels = home_layout.field_labels()
        self._title.setPlaceholderText(labels["title_hint"])
        self._title.setClearButtonEnabled(True)
        field(labels["title"], self._title)

        self._mode = ComboBox(self)
        for mode in home_layout.MODES:
            self._mode.addItem(mode_label(mode))
            self._mode.setItemData(self._mode.count() - 1, mode)
        field(labels["mode"], self._mode)

        self._content = ComboBox(self)
        for key, label in home_layout.content_choices():
            self._content.addItem(label)
            self._content.setItemData(self._content.count() - 1, key)
        self._content_row = field(labels["content"], self._content)

        self._value = LineEdit(self)
        self._value.setClearButtonEnabled(True)
        self._value_caption = self._add_value_row()

        self._sort = ComboBox(self)
        self._sort_row = field(labels["sort"], self._sort)

        self._hint = CaptionLabel("", self)
        self._hint.setWordWrap(True)
        set_secondary_text(self._hint)
        self.viewLayout.addWidget(self._hint)

        texts = home_layout.list_texts()
        self.cancelButton.setText(texts["cancel"])
        self.yesButton.setText(texts["save"])
        self.widget.setMinimumWidth(560)

        self._mode.currentIndexChanged.connect(self._sync_fields)
        if spec is not None:
            self._title.setText(spec.title)
            self._mode.setCurrentIndex(max(0, self._mode.findData(spec.mode)))
            self._content.setCurrentIndex(max(0, self._content.findData(spec.content)))
            self._value.setText(spec.value)
        self._sync_fields(initial_sort=spec.sort if spec else None)

    def _add_value_row(self) -> BodyLabel:
        row = QHBoxLayout()
        caption = BodyLabel("", self)
        caption.setFixedWidth(96)
        set_secondary_text(caption)
        row.addWidget(caption)
        row.addWidget(self._value, 1)
        self.viewLayout.addLayout(row)
        self._value_row = row
        return caption

    def _set_row_visible(self, row: QHBoxLayout, visible: bool):
        for index in range(row.count()):
            widget = row.itemAt(index).widget()
            if widget is not None:
                widget.setVisible(visible)

    def _sync_fields(self, *_args, initial_sort: str | None = None):
        mode = str(self._mode.currentData() or MODE_BROWSE)
        previous = initial_sort if initial_sort is not None else str(self._sort.currentData() or "")
        self._set_row_visible(self._value_row, home_layout.needs_value(mode))
        self._set_row_visible(self._sort_row, home_layout.shows_sort(mode))
        self._set_row_visible(self._content_row, home_layout.shows_content(mode))
        caption, placeholder = home_layout.value_caption(mode)
        self._value_caption.setText(caption)
        self._value.setPlaceholderText(placeholder)
        self._hint.setText(home_layout.mode_hint(mode))
        self._sort.blockSignals(True)
        self._sort.clear()
        for sort, label in home_layout.sort_choices(mode):
            self._sort.addItem(label)
            self._sort.setItemData(self._sort.count() - 1, sort)
        index = self._sort.findData(home_layout.pick_sort(mode, previous))
        self._sort.setCurrentIndex(index if index >= 0 else 0)
        self._sort.blockSignals(False)

    def collect(self) -> HomeSectionSpec | None:
        return home_layout.build_spec(
            self._original,
            mode=str(self._mode.currentData() or MODE_BROWSE),
            content=str(self._content.currentData() or "video"),
            title=self._title.text(),
            value=self._value.text(),
            sort=str(self._sort.currentData() or ""),
        )

    def validate(self) -> bool:
        spec = self.collect()
        if spec is None:
            self._hint.setText(home_layout.missing_value_text())
            return False
        self.result_spec = spec
        return True

    @classmethod
    def edit(cls, parent, spec: HomeSectionSpec | None = None) -> HomeSectionSpec | None:
        dialog = cls(spec, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result_spec
        return None


class HomeLayoutDialog(MessageBoxBase):
    """Ordered, checkable list of Home rows with add / edit / remove."""

    def __init__(self, parent=None, specs: list[HomeSectionSpec] | None = None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(home_layout.customize_title(), self))
        hint = BodyLabel(home_layout.customize_hint(), self)
        hint.setWordWrap(True)
        set_secondary_text(hint)
        self.viewLayout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(10)
        self._list = ListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(lambda _item: self._edit_current())
        self._list.currentRowChanged.connect(lambda _row: self._sync_buttons())
        self._list.itemChanged.connect(lambda _item: self._sync_buttons())
        body.addWidget(self._list, 1)

        tools = QVBoxLayout()
        tools.setSpacing(6)
        texts = home_layout.list_texts()
        self._add_btn = PrimaryPushButton(texts["add"], self, FluentIcon.ADD)
        self._add_btn.clicked.connect(self._add)
        self._edit_btn = PushButton(texts["edit"], self, FluentIcon.EDIT)
        self._edit_btn.clicked.connect(self._edit_current)
        self._remove_btn = PushButton(texts["remove"], self, FluentIcon.DELETE)
        self._remove_btn.clicked.connect(self._remove_current)
        self._up_btn = ToolButton(FluentIcon.UP, self)
        self._up_btn.setToolTip(texts["up"])
        self._up_btn.clicked.connect(lambda: self._move(-1))
        self._down_btn = ToolButton(FluentIcon.DOWN, self)
        self._down_btn.setToolTip(texts["down"])
        self._down_btn.clicked.connect(lambda: self._move(1))
        self._reset_btn = PushButton(texts["reset"], self)
        self._reset_btn.clicked.connect(self._reset)
        for widget in (self._add_btn, self._edit_btn, self._remove_btn):
            tools.addWidget(widget)
        arrows = QHBoxLayout()
        arrows.addWidget(self._up_btn)
        arrows.addWidget(self._down_btn)
        arrows.addStretch(1)
        tools.addLayout(arrows)
        tools.addStretch(1)
        tools.addWidget(self._reset_btn)
        body.addLayout(tools)
        self.viewLayout.addLayout(body, 1)

        self._note = CaptionLabel("", self)
        set_secondary_text(self._note)
        self.viewLayout.addWidget(self._note)

        self.cancelButton.setText(texts["cancel"])
        self.yesButton.setText(texts["save"])
        self.widget.setMinimumSize(700, 520)

        self._populate(specs if specs is not None else load_specs())

    # ── list ─────────────────────────────────────────────────────────────────

    def _populate(self, specs: list[HomeSectionSpec]):
        self._list.clear()
        for spec in specs:
            self._append(spec)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._sync_buttons()

    def _append(self, spec: HomeSectionSpec, *, row: int | None = None) -> QListWidgetItem:
        item = QListWidgetItem()
        self._fill(item, spec)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if row is None:
            self._list.addItem(item)
        else:
            self._list.insertItem(row, item)
        return item

    @staticmethod
    def _fill(item: QListWidgetItem, spec: HomeSectionSpec):
        item.setText(f"{spec.display_title()}    ·    {spec_summary(spec)}")
        item.setData(Qt.ItemDataRole.UserRole, spec)
        item.setCheckState(Qt.CheckState.Checked if spec.enabled else Qt.CheckState.Unchecked)

    def specs(self) -> list[HomeSectionSpec]:
        result: list[HomeSectionSpec] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            spec: HomeSectionSpec = item.data(Qt.ItemDataRole.UserRole)
            result.append(replace(spec, enabled=item.checkState() == Qt.CheckState.Checked))
        return result

    def _sync_buttons(self):
        row = self._list.currentRow()
        has = row >= 0
        self._edit_btn.setEnabled(has)
        self._remove_btn.setEnabled(has)
        self._up_btn.setEnabled(has and row > 0)
        self._down_btn.setEnabled(has and row < self._list.count() - 1)
        self._add_btn.setEnabled(self._list.count() < MAX_SECTIONS)
        self._note.setText(home_layout.empty_note(self.specs()))

    def _add(self):
        spec = SectionEditorDialog.edit(self, None)
        if spec is None:
            return
        self._append(spec)
        self._list.setCurrentRow(self._list.count() - 1)
        self._sync_buttons()

    def _edit_current(self):
        row = self._list.currentRow()
        if row < 0:
            return
        item = self._list.item(row)
        current: HomeSectionSpec = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        edited = SectionEditorDialog.edit(self, replace(current, enabled=enabled))
        if edited is not None:
            self._fill(item, edited)
            self._sync_buttons()

    def _remove_current(self):
        row = self._list.currentRow()
        if row < 0:
            return
        self._list.takeItem(row)
        if self._list.count():
            self._list.setCurrentRow(min(row, self._list.count() - 1))
        self._sync_buttons()

    def _move(self, delta: int):
        row = self._list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self._list.count():
            return
        spec = self.specs()[row]
        self._list.takeItem(row)
        self._append(spec, row=target)
        self._list.setCurrentRow(target)
        self._sync_buttons()

    def _reset(self):
        if show_fluent_confirmation(self, *home_layout.reset_question()):
            self._populate(default_specs())

    def validate(self) -> bool:
        save_specs(self.specs())
        signal_bus.home_layout_changed.emit()
        return True

    @classmethod
    def edit(cls, parent) -> bool:
        return cls(parent).exec() == QDialog.DialogCode.Accepted
