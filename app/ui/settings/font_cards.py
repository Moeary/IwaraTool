"""Interface font card: platform-adaptive by default, or a font the user picks."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QListWidgetItem, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    ListWidget,
    MessageBoxBase,
    PushButton,
    SearchLineEdit,
    SubtitleLabel,
)

from ...config import app_config
from ...i18n import current_language, tr
from ...signal_bus import signal_bus
from ..fonts import (
    AUTO_FONT,
    build_font_stack,
    current_platform,
    font_available,
    interface_font_families,
    selectable_font_families,
    system_ui_family,
    user_font_family,
)
from ..theme import set_secondary_text

PREVIEW_TEXT = "IwaraTool Aa 0123 · 中文界面示例 · 日本語のサンプル"


def _stack_font(families: list[str], point_size: int = 0) -> QFont:
    font = QFont()
    font.setFamilies(families)
    if point_size:
        font.setPointSize(point_size)
    return font


def _stack_for(family: str) -> list[str]:
    """What the interface would use with ``family`` chosen (AUTO_FONT for automatic)."""

    return build_font_stack(
        current_platform(),
        current_language(),
        user_family=family,
        system_family=system_ui_family(),
        available=font_available,
    )


class FontPickerDialog(MessageBoxBase):
    """Searchable list of installed fonts, each drawn in itself, with a live preview."""

    def __init__(self, parent, families: list[str], current: str):
        super().__init__(parent)
        self.viewLayout.addWidget(
            SubtitleLabel(tr("Interface font", "界面字体", "表示フォント"), self)
        )
        hint = CaptionLabel(
            tr(
                "Characters the font lacks (such as Chinese or Japanese) are filled in by the platform fonts.",
                "所选字体缺少的字符（例如中文、日文）会由平台字体自动补齐。",
                "フォントに無い文字（中国語・日本語など）はプラットフォームのフォントで補われます。",
            ),
            self,
        )
        hint.setWordWrap(True)
        set_secondary_text(hint)
        self.viewLayout.addWidget(hint)

        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(tr("Search fonts", "搜索字体", "フォントを検索"))
        self._search.textChanged.connect(self._filter)
        self.viewLayout.addWidget(self._search)

        self._list = ListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(320)
        auto = QListWidgetItem(tr("Automatic (follow the platform)", "自动（跟随平台）", "自動（プラットフォームに合わせる）"))
        auto.setData(Qt.ItemDataRole.UserRole, AUTO_FONT)
        self._list.addItem(auto)
        for family in families:
            item = QListWidgetItem(family)
            item.setData(Qt.ItemDataRole.UserRole, family)
            item.setFont(_stack_font([family], 11))
            self._list.addItem(item)
        self._list.currentItemChanged.connect(self._update_preview)
        self._list.itemDoubleClicked.connect(lambda _item: self.yesButton.click())
        self.viewLayout.addWidget(self._list)

        self._preview = BodyLabel(PREVIEW_TEXT, self)
        self._preview.setWordWrap(True)
        self._preview.setMinimumHeight(40)
        self.viewLayout.addWidget(self._preview)

        self.yesButton.setText(tr("Use this font", "使用此字体", "このフォントを使う"))
        self.cancelButton.setText(tr("Cancel", "取消", "キャンセル"))
        self.widget.setMinimumWidth(520)
        self._select(current)

    def _select(self, family: str):
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == family:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return
        self._list.setCurrentRow(0)

    def _filter(self, text: str):
        needle = text.strip().casefold()
        for row in range(1, self._list.count()):  # automatic stays listed
            item = self._list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _update_preview(self, item, _previous=None):
        family = self.selected_family if item is not None else AUTO_FONT
        self._preview.setFont(_stack_font(_stack_for(family), 13))

    @property
    def selected_family(self) -> str:
        item = self._list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or AUTO_FONT) if item is not None else AUTO_FONT


class SettingsFontCardsMixin:
    """Interface font card on the General settings page."""

    def _build_font_card(self):
        card = self._settings_board.create_card("font")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel(tr("Font", "字体", "フォント"), card))
        intro = BodyLabel(
            tr(
                "Automatic picks the system UI fonts for this platform and interface language. "
                "A chosen font leads; characters it lacks fall back to the platform and system fonts. "
                "The window reloads to apply a change.",
                "自动：按当前平台与界面语言选用系统界面字体。也可指定字体，指定的字体缺少的字符"
                "会回退到平台字体与系统默认字体。更改后窗口会重新载入以生效。",
                "自動ではプラットフォームと表示言語に合ったシステムの UI フォントを使います。"
                "フォントを指定した場合、無い文字はプラットフォームとシステムのフォントで補います。"
                "変更するとウィンドウを再読み込みします。",
            ),
            card,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(BodyLabel(tr("Interface font", "界面字体", "表示フォント"), card))
        row.addStretch()
        self._font_current_label = BodyLabel(card)
        row.addWidget(self._font_current_label)
        choose = PushButton(tr("Choose font…", "选择字体…", "フォントを選ぶ…"), card, FluentIcon.FONT)
        choose.clicked.connect(self._choose_ui_font)
        row.addWidget(choose)
        self._font_reset_button = PushButton(tr("Automatic", "恢复自动", "自動に戻す"), card)
        self._font_reset_button.clicked.connect(lambda: self._set_ui_font(AUTO_FONT))
        row.addWidget(self._font_reset_button)
        layout.addLayout(row)

        self._font_preview_label = BodyLabel(PREVIEW_TEXT, card)
        self._font_preview_label.setWordWrap(True)
        layout.addWidget(self._font_preview_label)
        self._font_stack_label = CaptionLabel(card)
        self._font_stack_label.setWordWrap(True)
        set_secondary_text(self._font_stack_label)
        layout.addWidget(self._font_stack_label)
        self._sync_font_card()
        self._settings_board.add_card("font", card)

    def _sync_font_card(self):
        chosen = user_font_family()
        families = interface_font_families()
        if not chosen:
            current = tr("Automatic", "自动", "自動")
        elif font_available(chosen):
            current = chosen
        else:
            current = tr(
                f"{chosen} (not installed, using automatic)",
                f"{chosen}（未安装，已改用自动）",
                f"{chosen}（未インストールのため自動）",
            )
        self._font_current_label.setText(current)
        self._font_reset_button.setEnabled(bool(chosen))
        self._font_preview_label.setFont(_stack_font(families, 13))
        self._font_stack_label.setText(
            tr("In use, in order: ", "生效顺序：", "使用順：") + " → ".join(families)
        )

    def _choose_ui_font(self):
        dialog = FontPickerDialog(self.window(), selectable_font_families(), user_font_family())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set_ui_font(dialog.selected_family)

    def _set_ui_font(self, family: str):
        family = (family or AUTO_FONT).strip()
        if family == user_font_family():
            return
        app_config.ui_font_family = family
        signal_bus.font_changed.emit()  # the window applies it and rebuilds
