"""Show the answer to "why was this video not downloaded?"."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout

from qfluentwidgets import BodyLabel, CaptionLabel, IconWidget, InfoBarIcon, MessageBoxBase, StrongBodyLabel, SubtitleLabel

from ...i18n import tr
from ..theme import set_secondary_text


_LEVEL_ICONS = {
    "block": InfoBarIcon.ERROR,
    "warn": InfoBarIcon.WARNING,
    "ok": InfoBarIcon.SUCCESS,
    "info": InfoBarIcon.INFORMATION,
}


class DiagnosisDialog(MessageBoxBase):
    """Verdict first, then every finding with its weight; optionally offers to download."""

    def __init__(self, result: dict[str, Any], parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(tr("Why not downloaded?", "为什么没下载？", "なぜ保存されない？"), self))
        video_id = str(result.get("video_id", "") or "")
        title = str(result.get("title", "") or "")
        if video_id:
            subject = CaptionLabel(f"{title}  ·  {video_id}" if title else video_id, self)
            subject.setWordWrap(True)
            set_secondary_text(subject)
            self.viewLayout.addWidget(subject)

        verdict = StrongBodyLabel(str(result.get("verdict", "") or ""), self)
        verdict.setWordWrap(True)
        verdict.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.viewLayout.addWidget(verdict)

        findings = [f for f in result.get("findings") or [] if f.get("text")]
        if findings:
            details = CaptionLabel(tr("Everything that was checked:", "检查结果：", "確認した項目:"), self)
            set_secondary_text(details)
            self.viewLayout.addWidget(details)
        for finding in findings:
            self.viewLayout.addLayout(self._finding_row(finding))

        can_download = bool(result.get("can_download"))
        self.yesButton.setText(tr("Download now", "立即下载", "今すぐ保存") if can_download else tr("OK", "知道了", "OK"))
        self.cancelButton.setVisible(can_download)
        self.cancelButton.setText(tr("Close", "关闭", "閉じる"))
        self._can_download = can_download
        self.widget.setMinimumWidth(640)

    def _finding_row(self, finding: dict[str, str]) -> QHBoxLayout:
        icon = _LEVEL_ICONS.get(str(finding.get("level", "")), InfoBarIcon.INFORMATION)
        row = QHBoxLayout()
        row.setSpacing(8)
        badge = IconWidget(icon, self)
        badge.setFixedSize(16, 16)
        text = BodyLabel(str(finding.get("text", "")), self)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text, 1)
        return row

    def ask(self) -> bool:
        """Show the dialog; True when the user chose to download the video now."""

        accepted = self.exec() == QDialog.DialogCode.Accepted
        return accepted and self._can_download
