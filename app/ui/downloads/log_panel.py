"""The download page's log: batched appends, copy and clear."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from qfluentwidgets import InfoBar, InfoBarPosition

from ...i18n import tr


class DownloadLogMixin:
    """The download page's log: batched appends, copy and clear."""

    def _copy_log(self):
        text = self._log_edit.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        InfoBar.success(
            title=tr("Copied", "已复制", "コピーしました"),
            content=tr("Runtime log copied", "运行日志已复制到剪贴板", "実行ログをコピーしました"),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=1600,
            parent=self,
        )

    def _append_log(self, msg: str):
        self._pending_logs.append(str(msg))
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    def _flush_logs(self):
        if not self._pending_logs:
            self._log_flush_timer.stop()
            return
        chunk = self._pending_logs[:200]
        del self._pending_logs[:200]
        self._log_edit.appendPlainText("\n".join(chunk))
        sb = self._log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())
        if not self._pending_logs:
            self._log_flush_timer.stop()

    def _clear_log(self):
        self._pending_logs.clear()
        self._log_edit.clear()
