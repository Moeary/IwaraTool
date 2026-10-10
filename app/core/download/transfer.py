"""Transfer a resolved task's file with the built-in downloader (or hand it to aria2)."""

from __future__ import annotations

import os
import time

from ...config import app_config
from ...i18n import tr
from ...logging_setup import get_logger
from ...signal_bus import signal_bus
from .integrity import classify_416, incomplete_reason, resume_offset_mismatch
from .speed_format import _fmt_bytes, _fmt_speed


logger = get_logger(__name__)


class TransferMixin:
    """Transfer a resolved task's file with the built-in downloader (or hand it to aria2)."""

    def _download_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        # Determine save path
        final_path = task.file_path.strip() if task.file_path else ""
        if not final_path:
            fallback_rel = self._build_output_relative_path(
                title=task.title or task.video_id,
                video_id=task.video_id,
                author=task.author,
                username=task.username,
                published_at=task.published_at,
                quality=task.quality,
                likes=task.likes,
                views=task.views,
                comments=task.comments,
                duration=task.duration,
                slug=task.slug,
                rating=task.rating,
                filename_template=self._task_rule_payload(task)["filename_template"],
            )
            final_path = os.path.join(app_config.download_dir, fallback_rel)
        save_dir = os.path.dirname(final_path)
        os.makedirs(save_dir, exist_ok=True)
        temp_path = f"{final_path}_temp"

        with self._lock:
            task.file_path = final_path

        signal_bus.log_message.emit(
            tr(
                f"[Download] \"{task.title}\" [quality:{task.quality}]",
                f"[下载] 《{task.title}》 [画质:{task.quality}]",
                f"[ダウンロード] 「{task.title}」 [画質:{task.quality}]",
            )
        )
        signal_bus.log_message.emit(
            tr(
                f"  Save to: {final_path}",
                f"  保存至: {final_path}",
                f"  保存先: {final_path}",
            )
        )
        signal_bus.log_message.emit(
            tr(
                f"  Temp file: {temp_path}",
                f"  临时文件: {temp_path}",
                f"  一時ファイル: {temp_path}",
            )
        )

        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        if app_config.aria2_rpc_enabled:
            self._download_task_aria2(
                task_id,
                final_path=final_path,
                temp_path=temp_path,
            )
            return

        signal_bus.log_message.emit(
            tr(
                "  aria2 disabled, using built-in downloader",
                "  aria2 未启用，使用内置下载器",
                "  aria2 無効のため内蔵ダウンローダーを使用",
            )
        )
        self._download_task_native(task_id, final_path=final_path, temp_path=temp_path)

    def _download_task_native(self, task_id: str, final_path: str, temp_path: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        resp = None
        try:
            self._touch_task_activity(task_id)
            headers: dict[str, str] = {}
            token = self._current_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

            # Resume support
            existing_size = 0
            if os.path.exists(temp_path):
                existing_size = os.path.getsize(temp_path)
                if existing_size > 0:
                    headers["Range"] = f"bytes={existing_size}-"
                    signal_bus.log_message.emit(
                        tr(
                            f"  Resume download: existing {_fmt_bytes(existing_size)}",
                            f"  断点续传: 已有 {_fmt_bytes(existing_size)}",
                            f"  レジューム: 既存 {_fmt_bytes(existing_size)}",
                        )
                    )

            resp = self.api.scraper.get(
                task.download_url,
                headers=headers,
                stream=True,
                timeout=60,
                proxies=self._download_request_proxies(),
            )
            self._touch_task_activity(task_id)
            if self._is_cancel_requested(task_id):
                self._cancel_task_terminal(
                    task_id,
                    tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                )
                return
            signal_bus.log_message.emit(
                f"  HTTP {resp.status_code}  Content-Length: {resp.headers.get('Content-Length', '?')}"
            )

            if resp.status_code == 416 and classify_416(resp.headers, existing_size) == "corrupt":
                self._discard_temp_file(temp_path)
                self._fail_task(
                    task_id,
                    tr(
                        "Local partial file does not match the server file; it was removed. Retry to download again.",
                        "本地缓存与服务器文件不一致，已清除；请重试以重新下载。",
                        "ローカルの一時ファイルがサーバー側と一致しないため削除しました。再試行してください。",
                    ),
                )
                return

            if resp.status_code == 416:
                signal_bus.log_message.emit(
                    tr(
                        "  File already complete, mark as completed",
                        "  文件已完整，标记完成",
                        "  ファイルは既に完全です。完了扱いにします",
                    )
                )
                if not self._finalize_temp_file(
                    task_id,
                    temp_path=temp_path,
                    final_path=final_path,
                ):
                    return
                self._complete_task(task_id)
                return

            if resp.status_code not in (200, 206):
                self._fail_task(task_id, f"HTTP {resp.status_code}: {resp.text[:200]}")
                return

            if resp.status_code == 206:
                mismatch = resume_offset_mismatch(resp.headers, existing_size)
                if mismatch:
                    self._discard_temp_file(temp_path)
                    self._fail_task(
                        task_id,
                        tr(
                            f"Resume failed ({mismatch}); partial file removed. Retry to download again.",
                            f"断点续传失败（{mismatch}），已清除缓存；请重试以重新下载。",
                            f"レジュームに失敗しました（{mismatch}）。一時ファイルを削除しました。再試行してください。",
                        ),
                    )
                    return

            # Compute total size
            content_length = int(resp.headers.get("Content-Length", 0) or 0)
            if resp.status_code == 206:
                total = existing_size + content_length
            else:
                total = content_length
                existing_size = 0  # Server ignored Range header

            with self._lock:
                task.total_bytes = total
                task.downloaded_bytes = existing_size
                self._touch_task_activity_locked(task_id)

            signal_bus.log_message.emit(
                tr(
                    f"  Total size: {_fmt_bytes(total)}",
                    f"  文件总大小: {_fmt_bytes(total)}",
                    f"  合計サイズ: {_fmt_bytes(total)}",
                )
            )

            mode = "ab" if existing_size > 0 else "wb"
            downloaded = existing_size
            last_time = time.monotonic()
            last_bytes = downloaded

            with open(temp_path, mode) as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if self._is_cancel_requested(task_id):
                        self._cancel_task_terminal(
                            task_id,
                            tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                        )
                        return
                    if not chunk:
                        continue
                    if not self._rate_limiter.throttle(
                        len(chunk),
                        cancelled=lambda: self._is_cancel_requested(task_id),
                        on_wait=lambda: self._touch_task_activity(task_id),
                    ):
                        self._cancel_task_terminal(
                            task_id,
                            tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                        )
                        return
                    fh.write(chunk)
                    downloaded += len(chunk)
                    self._touch_task_activity(task_id)

                    now = time.monotonic()
                    if now - last_time >= 0.5:
                        elapsed = now - last_time
                        speed = (downloaded - last_bytes) / elapsed
                        speed_str = _fmt_speed(speed)
                        last_time = now
                        last_bytes = downloaded

                        with self._lock:
                            task.downloaded_bytes = downloaded
                            task.speed_str = speed_str

                        signal_bus.task_progress_updated.emit(
                            task_id, downloaded, total, speed_str
                        )

            # Final progress update
            if self._is_cancel_requested(task_id):
                self._cancel_task_terminal(
                    task_id,
                    tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                )
                return
            with self._lock:
                task.downloaded_bytes = downloaded
            signal_bus.task_progress_updated.emit(task_id, downloaded, total, "")

            short = incomplete_reason(downloaded, total, resp.headers)
            if short:
                # Keep the temp file: a retry resumes from what we have.
                self._fail_task(
                    task_id,
                    tr(
                        f"Incomplete download ({short}). Retry to resume.",
                        f"下载不完整（{short}），重试可继续。",
                        f"ダウンロードが不完全です（{short}）。再試行で再開できます。",
                    ),
                )
                return

            if not self._finalize_temp_file(task_id, temp_path=temp_path, final_path=final_path):
                return

            signal_bus.log_message.emit(
                tr(
                    f"[Done] \"{task.title}\" total size {_fmt_bytes(downloaded)}",
                    f"[完成] 《{task.title}》 总大小 {_fmt_bytes(downloaded)}",
                    f"[完了] 「{task.title}」 合計サイズ {_fmt_bytes(downloaded)}",
                )
            )
            self._complete_task(task_id)

        except Exception as exc:
            logger.exception("Native download failed for task %s", task_id)
            signal_bus.log_message.emit(
                tr(
                    f"[Download error] \"{task.title}\" -> {exc}",
                    f"[下载异常] 《{task.title}》 → {exc}",
                    f"[ダウンロードエラー] 「{task.title}」 -> {exc}",
                )
            )
            self._fail_task(task_id, str(exc))
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    @staticmethod
    def _discard_temp_file(temp_path: str) -> None:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            logger.warning("Could not remove temp file %s", temp_path, exc_info=True)

    def _finalize_temp_file(self, task_id: str, temp_path: str, final_path: str) -> bool:
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return False
        if not os.path.exists(temp_path):
            self._fail_task(
                task_id,
                tr(
                    "Temp file does not exist, cannot finalize",
                    "临时文件不存在，无法完成重命名",
                    "一時ファイルが存在しないため確定できません",
                ),
            )
            return False
        size = os.path.getsize(temp_path)
        if size <= 0:
            self._fail_task(
                task_id,
                tr(
                    "Temp file is empty, download is incomplete",
                    "临时文件为空，下载不完整",
                    "一時ファイルが空のためダウンロードが不完全です",
                ),
            )
            return False
        try:
            os.replace(temp_path, final_path)
            sidecar = f"{temp_path}.aria2"
            if os.path.exists(sidecar):
                os.remove(sidecar)
            return True
        except Exception as exc:
            self._fail_task(
                task_id,
                tr(
                    f"Failed to rename temp file: {exc}",
                    f"重命名临时文件失败: {exc}",
                    f"一時ファイルのリネームに失敗: {exc}",
                ),
            )
            return False
