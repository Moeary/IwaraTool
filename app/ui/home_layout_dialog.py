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

from ..core.home_feed import (
    BROWSE_SORTS,
    KEYWORD_SORTS,
    MAX_SECTIONS,
    MODE_AUTHOR,
    MODE_BROWSE,
    MODE_KEYWORD,
    MODE_SUBSCRIPTIONS,
    MODE_TAGS,
    HomeSectionSpec,
    default_specs,
    load_specs,
    new_section_id,
    sanitize_spec,
    save_specs,
    sort_label,
)
from ..i18n import tr
from ..signal_bus import signal_bus
from .theme import set_secondary_text
from .ui_state import show_fluent_confirmation


def mode_label(mode: str) -> str:
    return {
        MODE_BROWSE: tr("Hot / newest list", "热门 / 最新列表", "人気・新着リスト"),
        MODE_TAGS: tr("Tag search", "标签搜索", "タグ検索"),
        MODE_KEYWORD: tr("Keyword search", "关键词搜索", "キーワード検索"),
        MODE_AUTHOR: tr("Author's works", "作者作品", "作者の作品"),
        MODE_SUBSCRIPTIONS: tr("My subscription feed", "账号订阅流", "購読フィード"),
    }.get(mode, mode)


def spec_summary(spec: HomeSectionSpec) -> str:
    """One line describing a row: what it searches and how it is ordered."""

    kind = tr("Images", "图片", "画像") if spec.content == "image" else tr("Videos", "视频", "動画")
    parts = [mode_label(spec.mode)]
    if spec.mode != MODE_SUBSCRIPTIONS:
        parts.append(kind)
    if spec.value:
        parts.append(spec.value)
    if spec.mode != MODE_SUBSCRIPTIONS:
        parts.append(sort_label(spec.sort) if spec.sort else tr("with sort tabs", "带排序标签", "並び替えタブ付き"))
    return " · ".join(parts)


class SectionEditorDialog(MessageBoxBase):
    """Add or edit one Home row."""

    def __init__(self, spec: HomeSectionSpec | None, parent=None):
        super().__init__(parent)
        self._original = spec
        self.result_spec: HomeSectionSpec | None = None
        self.viewLayout.addWidget(SubtitleLabel(
            tr("Edit row", "编辑栏目", "欄を編集") if spec else tr("Add a row", "添加栏目", "欄を追加"), self,
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
        self._title.setPlaceholderText(tr("Leave empty to name it automatically", "留空则自动命名", "空欄なら自動で命名"))
        self._title.setClearButtonEnabled(True)
        field(tr("Title", "标题", "タイトル"), self._title)

        self._mode = ComboBox(self)
        for mode in (MODE_BROWSE, MODE_TAGS, MODE_KEYWORD, MODE_AUTHOR, MODE_SUBSCRIPTIONS):
            self._mode.addItem(mode_label(mode))
            self._mode.setItemData(self._mode.count() - 1, mode)
        field(tr("Source", "来源", "ソース"), self._mode)

        self._content = ComboBox(self)
        for key, label in (("video", tr("Videos", "视频", "動画")), ("image", tr("Images", "图片", "画像"))):
            self._content.addItem(label)
            self._content.setItemData(self._content.count() - 1, key)
        self._content_row = field(tr("Type", "类型", "種類"), self._content)

        self._value = LineEdit(self)
        self._value.setClearButtonEnabled(True)
        self._value_caption = self._add_value_row()

        self._sort = ComboBox(self)
        self._sort_row = field(tr("Order", "排序", "並び順"), self._sort)

        self._hint = CaptionLabel("", self)
        self._hint.setWordWrap(True)
        set_secondary_text(self._hint)
        self.viewLayout.addWidget(self._hint)

        self.cancelButton.setText(tr("Cancel", "取消", "キャンセル"))
        self.yesButton.setText(tr("Save", "保存", "保存"))
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
        needs_value = mode in {MODE_TAGS, MODE_KEYWORD, MODE_AUTHOR}
        self._set_row_visible(self._value_row, needs_value)
        self._set_row_visible(self._sort_row, mode != MODE_SUBSCRIPTIONS)
        self._set_row_visible(self._content_row, mode not in {MODE_SUBSCRIPTIONS, MODE_TAGS})
        captions = {
            MODE_TAGS: (tr("Tags", "标签", "タグ"), tr("e.g. hmv  (comma-separate several)", "例如 hmv（多个标签用逗号分隔）", "例: hmv（複数はカンマ区切り）")),
            MODE_KEYWORD: (tr("Keyword", "关键词", "キーワード"), tr("Text to search for", "要搜索的文字", "検索する文字")),
            MODE_AUTHOR: (tr("Author", "作者", "作者"), tr("Username, e.g. alice", "作者用户名，例如 alice", "ユーザー名 例: alice")),
        }
        caption, placeholder = captions.get(mode, ("", ""))
        self._value_caption.setText(caption)
        self._value.setPlaceholderText(placeholder)
        hints = {
            MODE_BROWSE: tr(
                "Without a fixed order the row shows Trending / Popular / Newest tabs.",
                "不指定排序时，该栏目会带有 趋势 / 热度 / 最新 三个标签页。",
                "並び順を指定しない場合は トレンド/人気/新着 のタブが付きます。",
            ),
            MODE_TAGS: tr(
                "Uses Iwara tags; localized tag names are matched through the tag dictionary.",
                "使用 Iwara 标签；本地化的标签名会通过标签词典匹配。",
                "Iwaraのタグを使います。翻訳名はタグ辞書で照合されます。",
            ),
            MODE_KEYWORD: tr("Searches Iwara's text index.", "搜索 Iwara 的文本索引。", "Iwaraの全文検索を使います。"),
            MODE_AUTHOR: tr("Shows the newest works of one author.", "显示某位作者的最新作品。", "指定した作者の最新作を表示します。"),
            MODE_SUBSCRIPTIONS: tr("Needs a signed-in Iwara account.", "需要先登录 Iwara 账号。", "Iwaraアカウントへのログインが必要です。"),
        }
        self._hint.setText(hints.get(mode, ""))
        self._sort.blockSignals(True)
        self._sort.clear()
        if mode == MODE_BROWSE:
            self._sort.addItem(tr("Trending / Popular / Newest tabs", "趋势 / 热度 / 最新 标签页", "トレンド/人気/新着タブ"))
            self._sort.setItemData(0, "")
        sorts = KEYWORD_SORTS if mode == MODE_KEYWORD else BROWSE_SORTS
        for sort in sorts:
            self._sort.addItem(sort_label(sort))
            self._sort.setItemData(self._sort.count() - 1, sort)
        index = self._sort.findData(previous)
        self._sort.setCurrentIndex(index if index >= 0 else 0)
        self._sort.blockSignals(False)

    def collect(self) -> HomeSectionSpec | None:
        mode = str(self._mode.currentData() or MODE_BROWSE)
        original = self._original
        spec = sanitize_spec({
            "id": original.id if original else new_section_id(),
            "mode": mode,
            "content": str(self._content.currentData() or "video"),
            "title": self._title.text(),
            "value": self._value.text(),
            "sort": str(self._sort.currentData() or ""),
            "enabled": original.enabled if original else True,
        })
        if (
            spec is not None
            and original is not None
            and original.is_builtin
            and (spec.mode, spec.content, spec.sort) != (original.mode, original.content, original.sort)
        ):
            # A built-in row turned into something else must stop borrowing the
            # built-in name and its remembered tab.
            spec = replace(spec, id=new_section_id())
        return spec

    def validate(self) -> bool:
        spec = self.collect()
        if spec is None:
            self._hint.setText(tr("Enter what to search for.", "请填写要搜索的内容。", "検索する内容を入力してください。"))
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
        self.viewLayout.addWidget(SubtitleLabel(tr("Customize Home", "自定义首页", "ホームのカスタマイズ"), self))
        hint = BodyLabel(
            tr(
                "Tick the rows to show and move them into the order you like. "
                "Any search you can run on the Search page can become a row.",
                "勾选要显示的栏目并调整顺序。搜索页里能搜到的内容，都可以做成首页栏目。",
                "表示する欄を選び、順序を調整します。検索ページで検索できる内容は欄にできます。",
            ),
            self,
        )
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
        self._add_btn = PrimaryPushButton(tr("Add row", "添加栏目", "欄を追加"), self, FluentIcon.ADD)
        self._add_btn.clicked.connect(self._add)
        self._edit_btn = PushButton(tr("Edit", "编辑", "編集"), self, FluentIcon.EDIT)
        self._edit_btn.clicked.connect(self._edit_current)
        self._remove_btn = PushButton(tr("Remove", "删除", "削除"), self, FluentIcon.DELETE)
        self._remove_btn.clicked.connect(self._remove_current)
        self._up_btn = ToolButton(FluentIcon.UP, self)
        self._up_btn.setToolTip(tr("Move up", "上移", "上へ"))
        self._up_btn.clicked.connect(lambda: self._move(-1))
        self._down_btn = ToolButton(FluentIcon.DOWN, self)
        self._down_btn.setToolTip(tr("Move down", "下移", "下へ"))
        self._down_btn.clicked.connect(lambda: self._move(1))
        self._reset_btn = PushButton(tr("Reset to default", "恢复默认", "初期設定に戻す"), self)
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

        self.cancelButton.setText(tr("Cancel", "取消", "キャンセル"))
        self.yesButton.setText(tr("Save", "保存", "保存"))
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
        self._note.setText(
            tr("Nothing selected will show an empty Home page.", "全部取消勾选将显示空白首页。", "すべて外すとホームは空になります。")
            if self._list.count() and not any(spec.enabled for spec in self.specs())
            else ""
        )

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
        if show_fluent_confirmation(
            self,
            tr("Reset Home", "恢复默认首页", "ホームを初期化"),
            tr("Replace the list with the default rows?", "用默认栏目替换当前列表？", "一覧を初期の欄に置き換えますか？"),
        ):
            self._populate(default_specs())

    def validate(self) -> bool:
        save_specs(self.specs())
        signal_bus.home_layout_changed.emit()
        return True

    @classmethod
    def edit(cls, parent) -> bool:
        return cls(parent).exec() == QDialog.DialogCode.Accepted
