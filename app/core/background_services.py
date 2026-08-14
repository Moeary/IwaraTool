"""Long-lived subscription refresh, scheduler wake-up, and update checks."""
from __future__ import annotations

import threading
import time
from typing import Any

from ..config import app_config
from ..i18n import tr
from ..signal_bus import signal_bus
from .manager import download_manager
from .subscription_automation import auto_enqueue_subscription_videos
from .update_checker import GitHubReleaseChecker


class BackgroundService:
    def __init__(self, manager, *, poll_seconds: float = 15.0, update_checker=None):
        self.manager = manager
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.update_checker = update_checker or GitHubReleaseChecker()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._refresh_requested = False
        self._update_requested = False
        self._manual_update_requested = False
        self._last_refresh_at = time.monotonic()
        self._startup_update_pending = True

    def start(self):
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="iwara-background-service",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, wait: bool = False):
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def settings_changed(self):
        self._wake_event.set()

    def refresh_subscriptions_now(self):
        with self._state_lock:
            self._refresh_requested = True
        self._wake_event.set()

    def check_updates_now(self):
        with self._state_lock:
            self._update_requested = True
            self._manual_update_requested = True
        self._wake_event.set()

    def _take_requests(self) -> tuple[bool, bool]:
        with self._state_lock:
            refresh = self._refresh_requested
            manual_update = self._manual_update_requested
            update = self._update_requested
            self._refresh_requested = False
            self._update_requested = False
            self._manual_update_requested = False
        return refresh, update and manual_update

    def _run(self):
        # Give the main window time to connect notification/update slots.
        startup_update_at = time.monotonic() + 3.0
        while not self._stop_event.is_set():
            now = time.monotonic()
            self.manager.apply_runtime_download_policy()
            refresh_requested, manual_update = self._take_requests()
            refresh_due = (
                app_config.subscription_auto_refresh_enabled
                and now - self._last_refresh_at
                >= app_config.subscription_refresh_interval_minutes * 60
            )
            if refresh_requested or refresh_due:
                self._perform_subscription_refresh()
                self._last_refresh_at = time.monotonic()

            startup_update = (
                self._startup_update_pending
                and app_config.update_check_enabled
                and now >= startup_update_at
            )
            if manual_update or startup_update:
                self._startup_update_pending = False
                self._perform_update_check(manual=manual_update)

            self._wake_event.wait(self.poll_seconds)
            self._wake_event.clear()

    def _perform_subscription_refresh(self) -> dict[str, Any]:
        signal_bus.log_message.emit(
            tr(
                "[Automation] refreshing subscriptions",
                "[自动化] 正在刷新订阅",
                "[自動化] 購読を更新しています",
            )
        )
        summary = self.manager.refresh_all_subscriptions()
        automation: dict[str, Any] | None = None
        new_ids = list(summary.get("new_video_ids", []) or [])
        if app_config.subscription_auto_enqueue_enabled and new_ids:
            automation = auto_enqueue_subscription_videos(
                self.manager,
                new_ids,
                rule_id=app_config.subscription_auto_enqueue_rule_id,
            )
            summary["automation"] = automation

        new_count = int(summary.get("new", 0) or 0)
        queued = int((automation or {}).get("queued", 0) or 0)
        errors = list(summary.get("errors", []) or [])
        if new_count or queued or errors:
            signal_bus.desktop_notification_requested.emit(
                tr("Subscription refresh", "订阅刷新", "購読更新"),
                tr(
                    f"New: {new_count}; auto queued: {queued}; errors: {len(errors)}",
                    f"新增：{new_count}；自动入队：{queued}；错误：{len(errors)}",
                    f"新着：{new_count}；自動追加：{queued}；エラー：{len(errors)}",
                ),
            )
        return summary

    def _perform_update_check(self, *, manual: bool) -> dict[str, Any]:
        result = self.update_checker.check().to_dict()
        result["manual"] = manual
        if result.get("available"):
            signal_bus.release_update_available.emit(result)
        elif manual:
            if result.get("ok"):
                message = tr(
                    f"Already up to date ({result.get('current_version', '')})",
                    f"已是最新版本（{result.get('current_version', '')}）",
                    f"最新版です（{result.get('current_version', '')}）",
                )
            else:
                message = tr(
                    f"Update check failed: {result.get('error', '')}",
                    f"检查更新失败：{result.get('error', '')}",
                    f"更新確認に失敗：{result.get('error', '')}",
                )
            signal_bus.desktop_notification_requested.emit(
                tr("Update check", "检查更新", "更新確認"),
                message,
            )
        return result


background_service = BackgroundService(download_manager)
signal_bus.background_settings_changed.connect(background_service.settings_changed)
