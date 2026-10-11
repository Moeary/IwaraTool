"""Stop every page's workers before the window closes, is retired or the app exits."""

from __future__ import annotations

import os

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from ...i18n import tr
from ...config import app_config
from ...core import preview_stream, self_update
from ...core import memory
from ...core.manager import download_manager
from ...logging_setup import get_logger
from ..fluent_dialogs import show_fluent_confirmation
from ..worker_lifecycle import ShutdownPoller

logger = get_logger(__name__)


PAGE_SHUTDOWN_TIMEOUT_MS = 30_000


class WindowShutdownMixin:
    """Stop every page's workers before the window closes, is retired or the app exits."""

    def _schedule_memory_trim(self):
        if not self._reloading_language and not self._quitting:
            self._trim_timer.start()

    def _trim_memory_if_idle(self):
        """Nobody is looking (minimized or in the tray): hand unused pages back to the OS."""

        if not self.isVisible() or self.isMinimized():
            memory.trim_memory()

    def closeEvent(self, event: QCloseEvent):
        """Persist active work before the final application window closes.

        Page workers are stopped without blocking the GUI thread: all of them
        are cancelled at once, the window hides, and the close completes when
        they report done (or the shared deadline passes).
        """
        from ..main_window import MainWindow  # the window module imports this one

        if self._close_complete:
            super().closeEvent(event)
            return
        if self._closing:
            event.ignore()
            return
        if self._reloading_language or MainWindow._window_ref is not self:
            self._retire(event)
            return

        if (
            app_config.minimize_to_tray
            and self._tray_icon is not None
            and not self._quitting
        ):
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._tray_icon.showMessage(
                    "IwaraTool",
                    tr(
                        "Still running in the system tray. Use the tray menu to exit.",
                        "仍在系统托盘中运行，可通过托盘菜单退出。",
                        "システムトレイで動作中です。終了するにはトレイメニューを使用してください。",
                    ),
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
            return

        pending = download_manager.pending_task_count()
        if pending and not self._console_interrupted and not show_fluent_confirmation(
            self,
            tr("Exit IwaraTool", "退出 IwaraTool", "IwaraTool を終了"),
            tr(
                f"{pending} tasks are still queued or running.",
                f"仍有 {pending} 个任务正在排队或运行。",
                f"{pending} 件のタスクが待機中または実行中です。",
            ),
            informative=tr(
                "The queue and temporary files will be kept and restored automatically next time.",
                "队列与临时文件会被保留，下次启动时自动恢复。",
                "キューと一時ファイルを保持し、次回起動時に自動復元します。",
            ),
            yes_text=tr("Save and exit", "保存队列并退出", "保存して終了"),
            no_text=tr("Keep running", "继续运行", "実行を続ける"),
        ):
            event.ignore()
            return

        self._close_preview_window()
        self._shutdown_task_notification_worker()
        if self._start_page_shutdown(self._on_exit_pages_stopped):
            self._finish_exit()
            super().closeEvent(event)
            return
        event.ignore()
        self._hide_while_stopping()

    def quit_from_console(self):
        """Ctrl+C in the terminal: save the queue and exit without asking."""

        if self._closing or self._close_complete:
            return
        logger.info("Interrupted from the console; saving the queue and exiting")
        self._console_interrupted = True
        self._quitting = True
        self.close()
        if not self._closing:
            QApplication.quit()  # every page stopped at once; nothing left to wait for

    def _page_shutdown_steps(self) -> list:
        return [
            page.shutdown
            for page in (
                getattr(self, "_home_page", None),
                getattr(self, "_search_page", None),
                getattr(self, "_subscription_page", None),
                getattr(self, "_repair_page", None),
                getattr(self, "_history_page", None),
                getattr(self, "_settings_page", None),
            )
            if callable(getattr(page, "shutdown", None))
        ]

    def _start_page_shutdown(self, on_done) -> bool:
        """Cancel every page's workers at once; True if none had to be waited for.

        Otherwise ``on_done(ok)`` runs once they have stopped (``ok``) or the
        shared deadline passed, while the event loop keeps running.
        """

        poller = ShutdownPoller(self._page_shutdown_steps(), timeout_ms=PAGE_SHUTDOWN_TIMEOUT_MS, parent=self)
        if poller.start():
            poller.deleteLater()
            return True
        self._closing = True
        self._shutdown_poller = poller
        poller.done.connect(on_done)
        return False

    def _hide_while_stopping(self):
        self._closing = True
        self._trim_timer.stop()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self.hide()

    def _finish_exit(self):
        from ...core.background_services import background_service
        from ..main_window import MainWindow

        background_service.stop(wait=False)
        self._shutdown_task_notification_worker()
        self._close_preview_window()
        preview_stream.shutdown_proxy()
        download_manager.shutdown(wait=False)
        if self._pending_update_path:
            try:
                self_update.launch_swap_script(
                    self._pending_update_path,
                    os.path.join(app_config.app_data_dir, "updates"),
                )
            except Exception:
                logger.exception("Could not start the update installer")
        MainWindow._window_ref = None

    def _on_exit_pages_stopped(self, ok: bool):
        self._shutdown_poller = None
        self._finish_exit()
        self._close_complete = True
        self.close()
        if ok:
            QApplication.quit()
            return
        # A page worker is stuck in a request past the deadline.  Destroying a
        # running QThread would crash, so leave without tearing Qt down; the
        # queue and settings were already saved above.
        logger.warning("Page workers did not stop within %d ms; exiting anyway", PAGE_SHUTDOWN_TIMEOUT_MS)
        app_config.sync()
        import logging

        logging.shutdown()
        os._exit(0)

    def _retire(self, event: QCloseEvent):
        """Close a window that was replaced (language change): stop, then destroy it."""
        from ..shortcuts import registry

        registry.unbind_window(self)
        self._close_preview_window()
        self._shutdown_task_notification_worker()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        tray_menu = getattr(self, "_tray_menu", None)
        if tray_menu is not None:
            tray_menu.deleteLater()
        if self._start_page_shutdown(self._on_retired_pages_stopped):
            self._close_complete = True
            super().closeEvent(event)
            self.deleteLater()
            return
        event.ignore()
        self._hide_while_stopping()

    def deleteLater(self):
        """Never destroy the window (and its pages' running QThreads) mid-shutdown.

        While pages are still stopping in the background, deletion is deferred
        until they have; destroying a running QThread would abort the process.
        """
        if self._closing and not self._close_complete:
            return  # _on_retired_pages_stopped deletes it once its workers have stopped
        super().deleteLater()

    def _on_retired_pages_stopped(self, ok: bool):
        self._shutdown_poller = None
        if not ok:
            # Still busy: keep waiting while hidden, never destroy a running thread.
            self._closing = False
            if not self._start_page_shutdown(self._on_retired_pages_stopped):
                return
        self._close_complete = True
        self.close()
        self.deleteLater()
