"""Build a filename template by choosing fields instead of typing placeholders."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, ComboBox, FluentIcon, LineEdit, PushButton, TransparentToolButton

from ...core.download.template_builder import (
    EXTENSION,
    TemplateField,
    TemplatePreset,
    compose_template,
    parse_template,
    template_fields,
    template_presets,
)
from ...i18n import tr
from ..theme import set_secondary_text

# Fields offered, in order, when the user clicks "add field".
_ADD_ORDER = ("title", "id", "YYYY-MM-DD", "quality", "author", "username", "YYYY", "MM", "views", "likes")


class _FieldRow(QWidget):
    """One ``[field] [text after it] [remove]`` line."""

    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, fields: list[TemplateField], token: str, after: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.field_combo = ComboBox(self)
        for field in fields:
            self.field_combo.addItem(f"{field.label}  {{{field.token}}}", userData=field.token)
        self.field_combo.setToolTip(
            "\n".join(f"{{{field.token}}}  →  {field.example}" for field in fields)
        )
        self.field_combo.setCurrentIndex(max(0, next((i for i, f in enumerate(fields) if f.token == token), 0)))
        self.after_edit = LineEdit(self)
        self.after_edit.setFixedWidth(120)
        self.after_edit.setText(after)
        self.after_edit.setPlaceholderText(tr("then: _ - /", "后接：_ - /", "続き: _ - /"))
        self.after_edit.setToolTip(
            tr(
                "Text placed right after this field. Use _ or - to join, a space, or / to start a new folder.",
                "紧跟在该字段之后的文字：用 _ 或 - 连接，也可以是空格，写 / 则进入下一级文件夹。",
                "このフィールドの直後に入れる文字。_ や - で連結、/ で次のフォルダー階層に入ります。",
            )
        )
        self.remove_btn = TransparentToolButton(FluentIcon.CLOSE, self)
        self.remove_btn.setToolTip(tr("Remove this field", "移除此字段", "このフィールドを削除"))
        layout.addWidget(self.field_combo, 1)
        layout.addWidget(self.after_edit)
        layout.addWidget(self.remove_btn)
        self.field_combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.after_edit.textChanged.connect(lambda *_: self.changed.emit())
        self.remove_btn.clicked.connect(lambda *_: self.remove_requested.emit(self))

    def value(self) -> tuple[str, str]:
        return str(self.field_combo.currentData() or ""), self.after_edit.text()


class FilenameTemplateBuilder(QWidget):
    """Preset picker plus a row-per-field editor that emits the composed template."""

    template_changed = Signal(str)
    MAX_ROWS = 10

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._fields = template_fields()
        self._presets: list[TemplatePreset] = template_presets()
        self._rows: list[_FieldRow] = []
        self._syncing = False
        self._representable = True
        self._build_ui()
        self.set_template(self._presets[0].template)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        head = QGridLayout()
        head.setHorizontalSpacing(10)
        head.setVerticalSpacing(8)
        self.preset_combo = ComboBox(self)
        self.preset_combo.addItem(tr("Custom", "自定义", "カスタム"))
        for preset in self._presets:
            self.preset_combo.addItem(preset.label)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_chosen)
        head.addWidget(BodyLabel(tr("Preset", "预设", "プリセット"), self), 0, 0)
        head.addWidget(self.preset_combo, 0, 1)
        self.prefix_edit = LineEdit(self)
        self.prefix_edit.setPlaceholderText(
            tr("Optional fixed text or folder, e.g. iwara/", "可选：固定文字或文件夹，例如 iwara/", "任意: 固定文字やフォルダー 例: iwara/")
        )
        self.prefix_edit.textChanged.connect(lambda *_: self._on_edited())
        head.addWidget(BodyLabel(tr("Starts with", "起始文字", "先頭の文字"), self), 1, 0)
        head.addWidget(self.prefix_edit, 1, 1)
        head.setColumnStretch(1, 1)
        root.addLayout(head)

        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(6)
        root.addLayout(self._rows_box)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.add_btn = PushButton(tr("Add field", "添加字段", "フィールドを追加"), self, FluentIcon.ADD)
        self.add_btn.clicked.connect(self._add_row)
        foot.addWidget(self.add_btn)
        ext_label = BodyLabel(tr("Then the extension", "末尾自动加上", "末尾に自動付与") + f"  {EXTENSION}", self)
        set_secondary_text(ext_label)
        foot.addWidget(ext_label)
        foot.addStretch(1)
        root.addLayout(foot)

        self.status_label = BodyLabel("", self)
        self.status_label.setWordWrap(True)
        set_secondary_text(self.status_label)
        self.status_label.hide()
        root.addWidget(self.status_label)

    # -- model <-> widgets -------------------------------------------------

    def template(self) -> str:
        return compose_template(self.prefix_edit.text(), [row.value() for row in self._rows])

    def set_template(self, template: str):
        """Show ``template`` in the builder; text it cannot represent is left as is."""

        if self._rows and self._representable and template == self.template():
            self._sync_preset(template)
            return
        parts = parse_template(template)
        self._representable = parts is not None
        self._rows_box_enabled(self._representable)
        if parts is None:
            self._sync_preset(template)
            return
        self._syncing = True
        try:
            self.prefix_edit.setText(parts.prefix)
            for row in self._rows:
                row.setParent(None)
                row.deleteLater()
            self._rows = []
            for token, after in parts.rows[: self.MAX_ROWS]:
                self._append_row(token, after)
        finally:
            self._syncing = False
        self._refresh_controls()
        self._sync_preset(template)

    def _rows_box_enabled(self, parseable: bool):
        for row in self._rows:
            row.setEnabled(parseable)
        self.prefix_edit.setEnabled(parseable)
        self.add_btn.setEnabled(parseable and len(self._rows) < self.MAX_ROWS)
        self.status_label.setVisible(not parseable)
        if not parseable:
            self.status_label.setText(
                tr(
                    "The full template below can't be shown as fields (unknown placeholder or unmatched brace). Fix it there, or pick a preset.",
                    "下方的完整规则无法拆成字段（占位符未知或括号不匹配）。请直接在下方修正，或选择一个预设。",
                    "下の完全なテンプレートはフィールドに分解できません（不明なプレースホルダーまたは括弧の不一致）。下で修正するかプリセットを選んでください。",
                )
            )

    def _append_row(self, token: str, after: str):
        row = _FieldRow(self._fields, token, after, self)
        row.changed.connect(self._on_edited)
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._rows_box.addWidget(row)

    def _refresh_controls(self):
        self.add_btn.setEnabled(len(self._rows) < self.MAX_ROWS)
        for row in self._rows:
            row.remove_btn.setEnabled(len(self._rows) > 1)

    def _sync_preset(self, template: str):
        index = next((i + 1 for i, preset in enumerate(self._presets) if preset.template == template), 0)
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

    # -- user actions --------------------------------------------------------

    def _on_preset_chosen(self, index: int):
        if index <= 0:
            return
        template = self._presets[index - 1].template
        self.set_template(template)
        self.template_changed.emit(template)

    def _on_edited(self, *_args):
        if self._syncing or not self._rows:
            return
        template = self.template()
        self._sync_preset(template)
        self.template_changed.emit(template)

    def _add_row(self):
        if len(self._rows) >= self.MAX_ROWS:
            return
        used = {row.value()[0] for row in self._rows}
        token = next((t for t in _ADD_ORDER if t not in used), _ADD_ORDER[0])
        self._syncing = True
        try:
            if self._rows and not self._rows[-1].after_edit.text():
                self._rows[-1].after_edit.setText("_")
            self._append_row(token, "")
        finally:
            self._syncing = False
        self._refresh_controls()
        self._on_edited()

    def _remove_row(self, row: _FieldRow):
        if len(self._rows) <= 1 or row not in self._rows:
            return
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._refresh_controls()
        self._on_edited()
