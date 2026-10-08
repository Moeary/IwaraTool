"""aria2 RPC download backend and its global speed limit."""
from __future__ import annotations

import os
import time
import uuid

from ..config import app_config
from ..i18n import tr
from ..signal_bus import signal_bus
from .download_runtime import _fmt_bytes, _fmt_speed


class Aria2DownloadMixin:
    def _download_task_aria2(
        self,
        task_id: str,
        final_path: str,
        temp_path: str,
    ):
        task = self._tasks.get(task_id)
        if not task:
            return

        rpc_url = app_config.aria2_rpc_url.strip()
        if not rpc_url:
            signal_bus.log_message.emit(
                tr(
                    "  aria2 RPC URL is empty, fallback to built-in downloader",
                    "  aria2 RPC 地址为空，回退到内置下载器",
                    "  aria2 RPC URL が空のため内蔵ダウンローダーへフォールバック",
                )
            )
            self._download_task_native(task_id, final_path=final_path, temp_path=temp_path)
            return

        save_dir = os.path.dirname(temp_path)
        filename = os.path.basename(temp_path)
        headers: list[str] = []
        token = self._current_token()
        if token:
            headers.append(f"Authorization: Bearer {token}")

        options: dict[str, str | list[str]] = {
            "dir": save_dir,
            "out": filename,
            "continue": "true",
            "max-connection-per-server": "16",
            "split": "16",
            "min-split-size": "1M",
            "timeout": "60",
            "max-tries": "5",
            "retry-wait": "2",
            "auto-file-renaming": "false",
            "allow-overwrite": "false",
            "file-allocation": "none",
        }
        if headers:
            options["header"] = headers
        if app_config.download_proxy_enabled and app_config.download_proxy_url:
            options["all-proxy"] = app_config.download_proxy_url

        self._apply_aria2_global_speed_limit()

        signal_bus.log_message.emit(
            tr(
                f"  Download via aria2 RPC: {rpc_url}",
                f"  使用 aria2 RPC 下载: {rpc_url}",
                f"  aria2 RPC でダウンロード: {rpc_url}",
            )
        )
        gid, add_err = self._aria2_rpc_add_uri(task.download_url, options)
        if not gid:
            signal_bus.log_message.emit(
                tr(
                    f"  aria2 RPC submit failed, fallback to built-in downloader: {add_err}",
                    f"  aria2 RPC 提交失败，回退到内置下载器: {add_err}",
                    f"  aria2 RPC 送信失敗、内蔵ダウンローダーへフォールバック: {add_err}",
                )
            )
            self._download_task_native(task_id, final_path=final_path, temp_path=temp_path)
            return

        with self._lock:
            task.aria2_gid = gid
            self._touch_task_activity_locked(task_id)
        if self._is_cancel_requested(task_id):
            self._aria2_rpc_cancel(gid)
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        last_emit = 0.0
        last_done = -1
        last_status = ""
        while True:
            if self._is_cancel_requested(task_id):
                self._aria2_rpc_cancel(gid)
                self._cancel_task_terminal(
                    task_id,
                    tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                )
                return
            status_info, err = self._aria2_rpc_tell_status(gid)
            if not status_info:
                self._fail_task(
                    task_id,
                    tr(
                        f"aria2 RPC query failed: {err}",
                        f"aria2 RPC 查询失败: {err}",
                        f"aria2 RPC 問い合わせ失敗: {err}",
                    ),
                )
                return

            status = str(status_info.get("status", ""))
            done = int(status_info.get("completedLength", "0") or 0)
            total = int(status_info.get("totalLength", "0") or 0)
            speed = int(status_info.get("downloadSpeed", "0") or 0)
            speed_str = _fmt_speed(float(speed)) if speed > 0 else ""
            if status != last_status or done > last_done or speed > 0:
                self._touch_task_activity(task_id)
                last_status = status
                last_done = max(last_done, done)

            now = time.monotonic()
            if now - last_emit >= 0.5:
                with self._lock:
                    task.downloaded_bytes = done
                    task.total_bytes = total
                    task.speed_str = speed_str
                signal_bus.task_progress_updated.emit(task_id, done, total, speed_str)
                last_emit = now

            if status == "complete":
                if self._is_cancel_requested(task_id):
                    self._aria2_rpc_cancel(gid)
                    self._cancel_task_terminal(
                        task_id,
                        tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                    )
                    return
                downloaded = os.path.getsize(temp_path) if os.path.exists(temp_path) else done
                if not self._finalize_temp_file(task_id, temp_path=temp_path, final_path=final_path):
                    return
                with self._lock:
                    task.downloaded_bytes = downloaded
                    task.total_bytes = max(total, downloaded)
                    task.speed_str = ""
                signal_bus.task_progress_updated.emit(task_id, downloaded, max(total, downloaded), "")
                signal_bus.log_message.emit(
                    tr(
                        f"[Done] \"{task.title}\" total size {_fmt_bytes(downloaded)}",
                        f"[完成] 《{task.title}》 总大小 {_fmt_bytes(downloaded)}",
                        f"[完了] 「{task.title}」 合計サイズ {_fmt_bytes(downloaded)}",
                    )
                )
                self._complete_task(task_id)
                self._aria2_rpc_remove_result(gid)
                return

            if status in ("error", "removed"):
                if self._is_cancel_requested(task_id):
                    self._cancel_task_terminal(
                        task_id,
                        tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                    )
                    self._aria2_rpc_remove_result(gid)
                    return
                err_msg = str(
                    status_info.get(
                        "errorMessage",
                        tr("aria2 unknown error", "aria2 未知错误", "aria2 不明エラー"),
                    )
                    or tr("aria2 unknown error", "aria2 未知错误", "aria2 不明エラー")
                )
                self._fail_task(task_id, f"aria2 {status}: {err_msg}")
                self._aria2_rpc_remove_result(gid)
                return

            time.sleep(0.5)

    def _apply_aria2_global_speed_limit(self):
        limit = (
            f"{max(0, int(app_config.global_speed_limit_kib))}K"
            if app_config.global_speed_limit_enabled and app_config.global_speed_limit_kib > 0
            else "0"
        )
        if limit == self._last_aria2_global_limit:
            return
        now = time.monotonic()
        if (
            limit == self._last_aria2_limit_attempted
            and now - self._last_aria2_limit_attempt_at < 60
        ):
            return
        self._last_aria2_limit_attempted = limit
        self._last_aria2_limit_attempt_at = now
        data, error = self._aria2_rpc_call(
            "aria2.changeGlobalOption",
            [{"max-overall-download-limit": limit}],
        )
        if data:
            self._last_aria2_global_limit = limit
        elif error:
            signal_bus.log_message.emit(
                tr(
                    f"[Speed limit] aria2 global limit failed: {error}",
                    f"[限速] aria2 全局限速应用失败：{error}",
                    f"[速度制限] aria2 の全体制限に失敗：{error}",
                )
            )

    def _aria2_rpc_call(self, method: str, params: list) -> tuple[dict | None, str]:
        rpc_url = app_config.aria2_rpc_url.strip()
        if not rpc_url:
            return None, tr(
                "aria2 RPC URL is empty",
                "aria2 RPC URL 为空",
                "aria2 RPC URL が空です",
            )

        payload_params = list(params)
        token = app_config.aria2_rpc_token.strip()
        if token:
            payload_params.insert(0, f"token:{token}")

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": payload_params,
        }

        resp = None
        try:
            resp = self.api.scraper.post(
                rpc_url,
                json=payload,
                timeout=15,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return None, str(exc)
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

        if data.get("error"):
            return None, str(data.get("error"))
        return data, ""

    def _aria2_rpc_add_uri(self, uri: str, options: dict) -> tuple[str | None, str]:
        data, err = self._aria2_rpc_call("aria2.addUri", [[uri], options])
        if not data:
            return None, err
        gid = str(data.get("result", "") or "")
        if not gid:
            return None, tr(
                "aria2 did not return gid",
                "aria2 未返回 gid",
                "aria2 が gid を返しませんでした",
            )
        return gid, ""

    def _aria2_rpc_tell_status(self, gid: str) -> tuple[dict | None, str]:
        keys = ["status", "completedLength", "totalLength", "downloadSpeed", "errorMessage"]
        data, err = self._aria2_rpc_call("aria2.tellStatus", [gid, keys])
        if not data:
            return None, err
        result = data.get("result")
        if not isinstance(result, dict):
            return None, tr(
                f"Unexpected aria2 tellStatus result: {result!r}",
                f"aria2 tellStatus 返回异常: {result!r}",
                f"aria2 tellStatus の戻り値が不正です: {result!r}",
            )
        return result, ""

    def _aria2_rpc_remove_result(self, gid: str):
        self._aria2_rpc_call("aria2.removeDownloadResult", [gid])

    def _aria2_rpc_cancel(self, gid: str):
        if not gid:
            return
        data, err = self._aria2_rpc_call("aria2.remove", [gid])
        if not data and err:
            self._aria2_rpc_call("aria2.forceRemove", [gid])
        self._aria2_rpc_remove_result(gid)
