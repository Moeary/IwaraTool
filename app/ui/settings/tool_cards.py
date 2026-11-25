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
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from ..style.theme import set_secondary_text, set_status_text
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
                stext("upload_compression_and_cover_preview_run"),
                card,
            )
        )
        row = QHBoxLayout()
        self._edit = LineEdit(card)
        self._edit.setClearButtonEnabled(True)
        self._edit.setPlaceholderText(stext("detect_automatically"))
        self._edit.setText(str(app_config.ffmpeg_path or ""))
        self._edit.editingFinished.connect(self._apply)
        browse = PushButton(stext("browse"), card, FluentIcon.FOLDER)
        browse.clicked.connect(self._browse)
        detect = PushButton(stext("check"), card, FluentIcon.SYNC)
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
        if setting_values.set_ffmpeg_path(self._edit.text()):
            self._check()

    def _browse(self):
        name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        path, _ = QFileDialog.getOpenFileName(
            self._owner,
            stext("choose_ffmpeg"),
            self._edit.text().strip(),
            f"ffmpeg ({name});;{stext('all_files')} (*)",
        )
        if path:
            self._edit.setText(path)
            self._apply()

    def _check(self):
        self._status.setText(stext("checking"))
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
        kind, status, details = setting_values.ffmpeg_report(
            state.info, state.error, checking=state.busy or not state.checked
        )
        self._status.setText(status)
        set_status_text(self._status, {"checking": "text", "missing": "danger"}.get(kind, "success"))
        self._details.setText(details)
