"""Settings card for the Tools page: which ffmpeg program to use.

Applies as soon as it changes, like the system cards, so it takes no part in
"Save All Settings".
"""
from __future__ import annotations

import sys

import shiboken6
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, FluentIcon, LineEdit, PushButton, SubtitleLabel

from ...config import app_config
from ...core.tools.ffmpeg import program_at
from ...i18n import tr
from ..theme import set_secondary_text, set_status_text
from ..tools.common import ffmpeg_state


class ToolSettingsCards:
    def __init__(self, board, owner: QWidget):
        self._owner = owner
        card = board.create_card("ffmpeg")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel("ffmpeg", card))
        layout.addWidget(
            BodyLabel(
                tr(
                    "Upload compression and cover preview run the ffmpeg program. Leave the path empty to use the one "
                    "on PATH or in the usual install folders (Homebrew, winget, scoop, Chocolatey, next to IwaraTool). "
                    "Install it with brew install ffmpeg (macOS) or winget install ffmpeg (Windows).",
                    "上传压缩和封面预览需要调用 ffmpeg 程序。路径留空时，自动使用 PATH 或常见安装位置"
                    "（Homebrew、winget、scoop、Chocolatey、IwaraTool 所在目录）中的 ffmpeg。"
                    "安装方法：macOS 用 brew install ffmpeg，Windows 用 winget install ffmpeg。",
                    "アップロード用圧縮とカバーのプレビューは ffmpeg を使います。空欄なら PATH や一般的なインストール先"
                    "（Homebrew・winget・scoop・Chocolatey・IwaraTool と同じフォルダー）から探します。"
                    "macOS は brew install ffmpeg、Windows は winget install ffmpeg でインストールできます。",
                ),
                card,
            )
        )
        row = QHBoxLayout()
        self._edit = LineEdit(card)
        self._edit.setClearButtonEnabled(True)
        self._edit.setPlaceholderText(tr("Detect automatically", "自动检测", "自動検出"))
        self._edit.setText(str(app_config.ffmpeg_path or ""))
        self._edit.editingFinished.connect(self._apply)
        browse = PushButton(tr("Browse…", "浏览…", "参照…"), card, FluentIcon.FOLDER)
        browse.clicked.connect(self._browse)
        detect = PushButton(tr("Check", "检测", "確認"), card, FluentIcon.SYNC)
        detect.clicked.connect(self._check)
        row.addWidget(self._edit, 1)
        row.addWidget(browse)
        row.addWidget(detect)
        layout.addLayout(row)
        self._status = BodyLabel("", card)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._details = BodyLabel("", card)
        self._details.setWordWrap(True)
        set_secondary_text(self._details)
        layout.addWidget(self._details)
        board.add_card("ffmpeg", card)

        state = ffmpeg_state()
        state.changed.connect(self._show_state)
        self._show_state()
        state.ensure()

    def _apply(self):
        path = self._edit.text().strip().strip('"')
        if path != str(app_config.ffmpeg_path or ""):
            app_config.ffmpeg_path = path
            self._check()

    def _browse(self):
        name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        path, _ = QFileDialog.getOpenFileName(
            self._owner,
            tr("Choose ffmpeg", "选择 ffmpeg", "ffmpeg を選択"),
            self._edit.text().strip(),
            f"ffmpeg ({name});;{tr('All files', '所有文件', 'すべてのファイル')} (*)",
        )
        if path:
            self._edit.setText(path)
            self._apply()

    def _check(self):
        self._status.setText(tr("Checking…", "检测中…", "確認中…"))
        set_status_text(self._status, "text")
        self._details.clear()
        ffmpeg_state().refresh()

    def _show_state(self):
        state = ffmpeg_state()
        if not shiboken6.isValid(self._status):
            # The settings page was rebuilt or closed; the shared state outlives it.
            try:
                state.changed.disconnect(self._show_state)
            except (RuntimeError, TypeError):
                pass
            return
        if state.busy or not state.checked:
            self._status.setText(tr("Checking…", "检测中…", "確認中…"))
            set_status_text(self._status, "text")
            self._details.clear()
            return
        info = state.info
        if info is None:
            self._status.setText(tr("✗ ffmpeg not found", "✗ 未找到 ffmpeg", "✗ ffmpeg が見つかりません"))
            set_status_text(self._status, "danger")
            configured = str(app_config.ffmpeg_path or "").strip()
            self._details.setText(
                tr(f"Not usable: {configured}", f"无法使用：{configured}", f"使用できません: {configured}")
                if configured else (state.error if state.error != "ffmpeg was not found" else "")
            )
            return
        configured = str(app_config.ffmpeg_path or "").strip()
        status = tr(f"✓ ffmpeg {info.version}", f"✓ ffmpeg {info.version}", f"✓ ffmpeg {info.version}")
        if configured and program_at(configured, "ffmpeg") is None:
            status += tr(" (the chosen path is unusable; using the detected one)", "（所选路径不可用，已改用自动检测到的）", "（指定パスが使えないため自動検出を使用）")
        self._status.setText(status)
        set_status_text(self._status, "success")
        encoders = ["libx264"] if info.has_libx264 else []
        encoders += list(info.hardware_encoders)
        lines = [
            str(info.path),
            tr("H.264 encoders: ", "H.264 编码器：", "H.264 エンコーダー: ")
            + (", ".join(encoders) if encoders else tr("none", "无", "なし")),
        ]
        if info.ffprobe is None:
            lines.append(tr("ffprobe not found; media info is read from ffmpeg instead.", "未找到 ffprobe，改由 ffmpeg 读取视频信息。", "ffprobe がないため ffmpeg で情報を読み取ります。"))
        self._details.setText("\n".join(lines))
