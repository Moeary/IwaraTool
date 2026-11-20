"""aria2 running on another machine (NAS / server).

The video is written by aria2 into ``aria2_remote_dir`` on that machine; this
app never sees the bytes.  What stays here is the history row (``storage`` =
``remote``), the cover in the local image cache, and — when the remote folder
is mounted locally — the cover / NFO written next to the video through the
mount.
"""

from __future__ import annotations

import dataclasses
import os

from ...config import app_config
from ...i18n import tr
from ...logging_setup import get_logger
from ...signal_bus import signal_bus
from ..models import DownloadTask
from .locations import (
    mapped_local_path,
    record_is_remote,
    remote_sidecar_path,
    split_remote_path,
)
from .speed_format import _fmt_bytes

logger = get_logger(__name__)


def _same_remote_path(a: str, b: str) -> bool:
    a = str(a or "").replace("\\", "/").rstrip("/")
    b = str(b or "").replace("\\", "/").rstrip("/")
    return bool(a) and (a == b or (":" in a[:3] and a.casefold() == b.casefold()))


class Aria2RemoteMixin:
    """Downloads delegated to an aria2 RPC server that writes to its own disk."""

    # ── skipping a copy that is already there ────────────────────────────────

    def _remote_copy_exists(self, video_id: str, remote_path: str) -> bool:
        """History (or the local mount) shows ``remote_path`` already on the aria2 host."""

        record = self.history.get_record(video_id)
        if record_is_remote(record) and _same_remote_path(str(record.get("remote_path", "") or ""), remote_path):
            return True
        mapped = mapped_local_path(remote_path)
        try:
            return bool(mapped) and os.path.isfile(mapped) and os.path.getsize(mapped) > 0
        except OSError:
            return False

    # ── transfer ─────────────────────────────────────────────────────────────

    def _download_task_aria2_remote(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        remote_path = str(task.remote_path or "").strip()
        rpc_url = str(app_config.aria2_rpc_url or "").strip()
        if not remote_path:
            self._fail_task(task_id, tr(
                "No path on the aria2 host; check the remote download folder in Settings",
                "没有远端保存路径，请在设置中检查 aria2 远端下载目录",
                "aria2 ホスト上の保存先がありません。設定のリモート保存先を確認してください",
            ))
            return
        if not app_config.aria2_rpc_enabled or not rpc_url:
            # Never fall back to the built-in downloader: the file would land on
            # this machine while the task says it is on the NAS.
            self._fail_task(task_id, tr(
                "aria2 RPC is off or has no URL; remote downloads need it",
                "aria2 RPC 未启用或地址为空，远端下载无法进行",
                "aria2 RPC が無効か URL が空のため、リモート保存できません",
            ))
            return

        save_dir, filename = split_remote_path(remote_path)
        signal_bus.log_message.emit(tr(
            f"[Download] \"{task.title}\" [quality:{task.quality}] via remote aria2: {rpc_url}",
            f"[下载] 《{task.title}》 [画质:{task.quality}] 交由远端 aria2：{rpc_url}",
            f"[ダウンロード] 「{task.title}」 [画質:{task.quality}] リモート aria2 へ: {rpc_url}",
        ))
        signal_bus.log_message.emit(tr(
            f"  Save on aria2 host: {remote_path}",
            f"  远端保存至: {remote_path}",
            f"  aria2 ホストの保存先: {remote_path}",
        ))
        if self._cancel_if_requested(task_id):
            return

        options = self._aria2_options("", save_dir=save_dir, filename=filename)
        self._apply_aria2_global_speed_limit()
        gid, add_err = self._aria2_rpc_add_uri(task.download_url, options)
        if not gid:
            self._fail_task(task_id, tr(
                f"Remote aria2 did not accept the download: {add_err}",
                f"远端 aria2 提交失败：{add_err}",
                f"リモート aria2 への送信に失敗: {add_err}",
            ))
            return
        with self._lock:
            task.aria2_gid = gid
            self._touch_task_activity_locked(task_id)
        if self._aria2_cancel_if_requested(task_id, gid):
            return
        self._follow_aria2_download(task, task_id, gid, "", "", remote=True)

    def _finish_aria2_remote_download(self, task, task_id: str, gid: str, done: int, total: int):
        if self._aria2_cancel_if_requested(task_id, gid):
            return
        size = max(done, total)
        if size <= 0:
            self._fail_task(task_id, tr(
                "Remote aria2 reported an empty file",
                "远端 aria2 报告文件为空",
                "リモート aria2 が空のファイルを報告しました",
            ))
            self._aria2_rpc_remove_result(gid)
            return
        with self._lock:
            task.downloaded_bytes = size
            task.total_bytes = size
            task.speed_str = ""
        signal_bus.task_progress_updated.emit(task_id, size, size, "")
        signal_bus.log_message.emit(tr(
            f"[Done] \"{task.title}\" saved on the aria2 host, total size {_fmt_bytes(size)}",
            f"[完成] 《{task.title}》 已保存到远端，总大小 {_fmt_bytes(size)}",
            f"[完了] 「{task.title}」 aria2 ホストに保存、合計サイズ {_fmt_bytes(size)}",
        ))
        self._complete_task(task_id)
        self._aria2_rpc_remove_result(gid)

    # ── cover / NFO ──────────────────────────────────────────────────────────

    def _save_remote_artifacts(self, task: DownloadTask, rule_payload: dict) -> None:
        """Cache the cover locally, and put cover / NFO next to the remote video when possible."""

        cover_url = self._thumbnail_url(task)
        cached = self._cache_download_cover(task.video_id, cover_url) if cover_url else ""
        if cached:
            task.thumbnail_path = cached  # the app only ever shows the local cache

        mapped = mapped_local_path(task.remote_path)
        mounted = bool(mapped) and os.path.isfile(mapped)
        mapped_cover = os.path.splitext(mapped)[0] + ".jpg" if mapped else ""

        if rule_payload.get("download_thumbnail") and cover_url:
            if mounted and cached:
                if self._copy_cached_image(cached, mapped_cover):
                    signal_bus.log_message.emit(tr(
                        f"  [Thumbnail] saved through the mount: {mapped_cover}",
                        f"  [封面] 已通过挂载目录保存: {mapped_cover}",
                        f"  [サムネイル] マウント経由で保存: {mapped_cover}",
                    ))
            else:
                remote_cover = remote_sidecar_path(task.remote_path, ".jpg")
                save_dir, filename = split_remote_path(remote_cover)
                options = {
                    "dir": save_dir, "out": filename, "auto-file-renaming": "false",
                    "allow-overwrite": "false", "max-tries": "3",
                }
                if app_config.download_proxy_enabled and app_config.download_proxy_url:
                    options["all-proxy"] = app_config.download_proxy_url
                gid, err = self._aria2_rpc_add_uri(cover_url, options)
                signal_bus.log_message.emit(
                    tr(
                        f"  [Thumbnail] handed to remote aria2: {remote_cover}",
                        f"  [封面] 已交由远端 aria2 保存: {remote_cover}",
                        f"  [サムネイル] リモート aria2 に送信: {remote_cover}",
                    )
                    if gid
                    else tr(
                        f"  [Thumbnail] remote aria2 refused the cover: {err}",
                        f"  [封面] 远端 aria2 未接收封面：{err}",
                        f"  [サムネイル] リモート aria2 が拒否: {err}",
                    )
                )

        if rule_payload.get("collect_nfo_info"):
            if mounted:
                sidecar_task = dataclasses.replace(
                    task,
                    file_path=mapped,
                    thumbnail_path=mapped_cover if mapped_cover and os.path.isfile(mapped_cover) else "",
                )
                self._write_nfo(sidecar_task, require_video_file=False)
            else:
                signal_bus.log_message.emit(tr(
                    "  [NFO] skipped: the remote folder is not mounted on this machine (set the local mount in Settings)",
                    "  [NFO] 已跳过：远端目录未挂载到本机（可在设置中填写本机挂载路径）",
                    "  [NFO] スキップ: リモートフォルダーがこの PC にマウントされていません（設定でマウント先を指定）",
                ))

    def _thumbnail_url(self, task: DownloadTask) -> str:
        host = self._thumbnail_host(task, require_video_file=False)
        if not host:
            return ""
        index = max(0, int(task.thumbnail_index))
        return f"https://{host}/image/original/{task.file_id}/thumbnail-{index:02d}.jpg"

    def _cache_download_cover(self, video_id: str, cover_url: str) -> str:
        """Download the cover into the local image cache (where every page reads covers)."""

        target = self._subscription_thumbnail_cache_path(video_id, f"local://download/{video_id}.jpg")
        if not target:
            return ""
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return target
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temp_path = f"{target}.download"
        resp = None
        try:
            resp = self.api.scraper.get(cover_url, stream=True, timeout=60)
            if resp.status_code != 200:
                return ""
            with open(temp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
            if os.path.getsize(temp_path) > 0:
                os.replace(temp_path, target)
                return target
        except Exception:
            logger.warning("Cover cache download failed: %s", cover_url, exc_info=True)
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        return ""
