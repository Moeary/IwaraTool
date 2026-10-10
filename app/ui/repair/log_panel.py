"""The repair page's log: batched appends and clear."""

from __future__ import annotations


class RepairLogMixin:
    """The repair page's log: batched appends and clear."""

    def _append_log(self, message: str):
        text = str(message or "")
        if not self._repair_log_active and not any(
            token in text for token in ("[Repair]", "[修复]", "[修復]", "[NFO]", "[封面]", "[Thumbnail]")
        ):
            return
        self._pending_logs.append(text)
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    def _flush_logs(self):
        if not self._pending_logs:
            self._log_flush_timer.stop()
            return
        chunk = self._pending_logs[:200]
        del self._pending_logs[:200]
        self._log_edit.appendPlainText("\n".join(chunk))
        scrollbar = self._log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        if not self._pending_logs:
            self._log_flush_timer.stop()

    def _clear_log(self):
        self._pending_logs.clear()
        self._log_edit.clear()
