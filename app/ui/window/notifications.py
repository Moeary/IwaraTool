"""Tray icon and desktop notifications for finished downloads."""

from __future__ import annotations

from concurrent.futures import Future

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from qfluentwidgets import InfoBar, InfoBarPosition

from ...i18n import tr
from ...signal_bus import signal_bus
from ...config import app_config
from ...core.manager import download_manager
from ..notification_dispatcher import PreparedTaskNotifications, prepare_task_notifications


class WindowNotificationsMixin:
    """Tray icon and desktop notifications for finished downloads."""

    def _init_desktop_notifications(self):
        self._tray_icon: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_icon = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        self._tray_icon.setToolTip("IwaraTool")
        self._tray_menu = QMenu()
        self._tray_menu.addAction(
            tr("Show / Hide", "显示 / 隐藏", "表示 / 非表示"), self._toggle_window_visibility
        )
        self._tray_menu.addSeparator()
        self._tray_menu.addAction(tr("Exit", "退出", "終了"), self._quit_from_tray)
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_window_visibility()

    def _toggle_window_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._quitting = True
        self.showNormal()
        self.close()
        # The user may have kept the queue running; allow tray hiding again.
        if self.isVisible():
            self._quitting = False

    def _show_desktop_notification(self, title: str, message: str):
        signal_bus.log_message.emit(f"[{title}] {message}")
        if QApplication.activeWindow() is self:
            InfoBar.info(
                title=title,
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        if not app_config.desktop_notifications_enabled or self._tray_icon is None:
            return
        self._tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    def _on_task_status_notification(self, task_id: str, status: str):
        if self._task_notification_executor is None or status not in {"completed", "failed"}:
            return
        if not self._task_notification_batch.add(task_id, status):
            return
        # Do not build or animate an InfoBar from every worker completion.
        # The short debounce combines bursts into one paint operation.
        if not self._task_notification_timer.isActive():
            self._task_notification_timer.start()

    def _flush_task_notifications(self):
        events = self._task_notification_batch.drain()
        if not events or self._task_notification_executor is None:
            return
        future = self._task_notification_executor.submit(
            prepare_task_notifications,
            events,
            download_manager.get_task,
        )
        future.add_done_callback(self._on_task_notifications_prepared)

    def _on_task_notifications_prepared(self, future: Future):
        if self._task_notification_executor is None:
            return
        try:
            prepared = future.result()
        except Exception as exc:
            signal_bus.log_message.emit(f"[Notification] {exc}")
            return
        if isinstance(prepared, PreparedTaskNotifications):
            # Emitting from the worker is safe; the bridge's receiver lives in
            # the GUI thread, so Qt queues this slot for the next event turn.
            self._notification_bridge.prepared.emit(prepared)

    def _present_task_notifications(self, prepared: PreparedTaskNotifications):
        completed = prepared.completed_titles
        failed = prepared.failed_messages
        if not completed and not failed:
            return

        if len(completed) == 1 and not failed:
            self._show_desktop_notification(
                tr("Download completed", "下载完成", "ダウンロード完了"),
                completed[0],
            )
            return
        if len(failed) == 1 and not completed:
            self._show_desktop_notification(
                tr("Download failed", "下载失败", "ダウンロード失敗"),
                failed[0],
            )
            return

        parts: list[str] = []
        if completed:
            parts.append(
                tr(
                    f"{len(completed)} downloads completed",
                    f"{len(completed)} 个下载已完成",
                    f"{len(completed)} 件のダウンロードが完了",
                )
            )
        if failed:
            parts.append(
                tr(
                    f"{len(failed)} downloads failed",
                    f"{len(failed)} 个下载失败",
                    f"{len(failed)} 件のダウンロードに失敗",
                )
            )
        self._show_desktop_notification(
            tr("Download updates", "下载任务更新", "ダウンロード更新"),
            "；".join(parts),
        )

    def _shutdown_task_notification_worker(self):
        self._task_notification_timer.stop()
        self._task_notification_batch.clear()
        executor = getattr(self, "_task_notification_executor", None)
        if executor is not None:
            self._task_notification_executor = None
            executor.shutdown(wait=False, cancel_futures=True)
