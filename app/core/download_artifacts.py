"""Cover images, subscription avatars and NFO sidecars written next to downloads."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

from ..config import app_config
from ..i18n import tr
from ..logging_setup import get_logger
from ..signal_bus import signal_bus
from .models import DownloadTask
from .nfo import build_nfo_text, parse_tags as parse_nfo_tags

logger = get_logger(__name__)


class DownloadArtifactsMixin:
    def _subscription_thumbnail_cache_path(self, video_id: str, thumbnail_url: str) -> str:
        video_id = self._sanitize_path_segment(str(video_id or "").strip())
        thumbnail_url = str(thumbnail_url or "").strip()
        if not video_id or not thumbnail_url:
            return ""
        url_name = os.path.basename(urlparse(thumbnail_url).path)
        ext = os.path.splitext(url_name)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
            ext = ".jpg"
        fingerprint = hashlib.sha1(thumbnail_url.encode("utf-8")).hexdigest()[:12]
        img_dir = os.path.join(app_config.app_data_dir, "img", "sub")
        return os.path.join(img_dir, f"video_{video_id}_{fingerprint}{ext}")

    def _legacy_subscription_v2_thumbnail_cache_path(
        self,
        video_id: str,
        thumbnail_url: str,
    ) -> str:
        """Return the previous ``sub_video`` path for one-time migration."""
        video_id = self._sanitize_path_segment(str(video_id or "").strip())
        thumbnail_url = str(thumbnail_url or "").strip()
        if not video_id or not thumbnail_url:
            return ""
        url_name = os.path.basename(urlparse(thumbnail_url).path)
        ext = os.path.splitext(url_name)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
            ext = ".jpg"
        fingerprint = hashlib.sha1(thumbnail_url.encode("utf-8")).hexdigest()[:12]
        return os.path.join(
            app_config.app_data_dir,
            "img",
            "sub_video",
            f"video_{video_id}_{fingerprint}{ext}",
        )

    def _legacy_subscription_thumbnail_cache_path(self, video_id: str, thumbnail_url: str) -> str:
        """Return the pre-v2 cover path for one-time cache migration."""
        video_id = self._sanitize_path_segment(str(video_id or "").strip())
        thumbnail_url = str(thumbnail_url or "").strip()
        if not video_id or not thumbnail_url:
            return ""
        url_name = os.path.basename(urlparse(thumbnail_url).path)
        ext = os.path.splitext(url_name)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
            ext = ".jpg"
        fingerprint = hashlib.sha1(thumbnail_url.encode("utf-8")).hexdigest()[:12]
        return os.path.join(
            app_config.app_data_dir,
            "img",
            f"cover_{video_id}_{fingerprint}{ext}",
        )

    @staticmethod
    def _copy_cached_image(source_path: str, target_path: str) -> bool:
        source_path = str(source_path or "")
        target_path = str(target_path or "")
        if not source_path or not target_path or os.path.abspath(source_path) == os.path.abspath(target_path):
            return bool(target_path and os.path.isfile(target_path) and os.path.getsize(target_path) > 0)
        try:
            if not os.path.isfile(source_path) or os.path.getsize(source_path) <= 0:
                return False
            if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
                return True
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            temp_path = f"{target_path}.{threading.get_ident()}.tmp"
            shutil.copy2(source_path, temp_path)
            os.replace(temp_path, target_path)
            return True
        except OSError:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except (OSError, UnboundLocalError):
                pass
            return False

    def _ensure_subscription_thumbnail_cache(
        self,
        video_id: str,
        thumbnail_url: str,
        history_thumbnail_path: str = "",
    ) -> str:
        target = self._subscription_thumbnail_cache_path(video_id, thumbnail_url)
        if not target:
            return ""
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return target
        candidates = [
            str(history_thumbnail_path or ""),
            self._legacy_subscription_v2_thumbnail_cache_path(video_id, thumbnail_url),
            self._legacy_subscription_thumbnail_cache_path(video_id, thumbnail_url),
        ]
        for candidate in candidates:
            if self._copy_cached_image(candidate, target):
                return target
        return ""

    def _subscription_avatar_cache_path(
        self,
        source: dict[str, Any],
        avatar_url: str,
        legacy_path: str = "",
    ) -> str:
        source_key = self._sanitize_path_segment(str(source.get("source_key", "") or "author"))
        url_name = os.path.basename(urlparse(str(avatar_url or "")).path)
        if not url_name and legacy_path:
            url_name = os.path.basename(str(legacy_path))
        ext = os.path.splitext(url_name)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
            ext = ".jpg"
        avatar_id = ""
        parts = [part for part in urlparse(str(avatar_url or "")).path.split("/") if part]
        if len(parts) >= 2:
            avatar_id = self._sanitize_path_segment(parts[-2])
        suffix = avatar_id or hashlib.sha1(str(avatar_url or source_key).encode("utf-8")).hexdigest()[:12]
        img_dir = os.path.join(app_config.app_data_dir, "img", "avatar")
        # The username is deliberately the first segment so the directory is
        # understandable without opening the database. No legacy ``avatar_``
        # prefix is used for new files.
        return os.path.join(img_dir, f"{source_key}_{suffix}{ext}")

    def _migrate_subscription_avatar_cache(self):
        """Copy legacy ``data/img/avatar_*`` files into the named avatar cache.

        Migration is intentionally copy-based: an interrupted first launch or
        an older build can still read the original file. The source row is
        updated only after the new file is present.
        """
        try:
            sources = self.subscriptions.list_sources()
        except Exception:
            logger.warning("Could not list subscription sources", exc_info=True)
            return
        for source in sources:
            if str(source.get("source_type", "") or "") != "author":
                continue
            old_path = str(source.get("avatar_path", "") or "")
            avatar_url = str(source.get("avatar_url", "") or "")
            target = self._subscription_avatar_cache_path(source, avatar_url, old_path)
            if not target:
                continue
            if not old_path or not os.path.isfile(old_path):
                source_id = int(source.get("id", 0) or 0)
                old_dir = os.path.join(app_config.app_data_dir, "img")
                prefix = f"avatar_{source_id}_"
                try:
                    old_path = next(
                        (
                            os.path.join(old_dir, name)
                            for name in os.listdir(old_dir)
                            if name.startswith(prefix) and os.path.isfile(os.path.join(old_dir, name))
                        ),
                        "",
                    )
                except OSError:
                    old_path = ""
            migrated = bool(old_path and self._copy_cached_image(old_path, target))
            if not migrated and os.path.isfile(target) and os.path.getsize(target) > 0:
                migrated = True
            if migrated:
                if old_path != target or str(source.get("avatar_path", "") or "") != target:
                    self.subscriptions.update_source_avatar(
                        int(source.get("id", 0) or 0),
                        avatar_url,
                        target,
                    )

    def _download_subscription_avatar(self, avatar_url: str, avatar_path: str) -> bool:
        if not avatar_url or not avatar_path:
            return False
        if os.path.isfile(avatar_path) and os.path.getsize(avatar_path) > 0:
            return True
        os.makedirs(os.path.dirname(avatar_path), exist_ok=True)
        temp_path = f"{avatar_path}.tmp"
        resp = None
        try:
            token = self._current_token()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            resp = self.api.scraper.get(avatar_url, headers=headers, stream=True, timeout=30)
            if resp.status_code != 200:
                return False
            content_type = str(resp.headers.get("content-type", "") or "").lower()
            if content_type and not content_type.startswith("image/"):
                return False
            with open(temp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
            if os.path.isfile(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, avatar_path)
                return True
            return False
        except Exception:
            logger.warning("Avatar download failed: %s", avatar_url, exc_info=True)
            return False
        finally:
            if resp is not None:
                resp.close()
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _download_thumbnail(self, task: DownloadTask, *, require_video_file: bool = True) -> bool:
        if not task.file_path:
            return False
        if require_video_file:
            if not os.path.exists(task.file_path):
                return False
            if os.path.getsize(task.file_path) <= 0:
                return False
        if not task.file_id or not task.file_url:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" missing file_id/file_url, skipped",
                    f"  [封面] 《{task.title}》 缺少 file_id/file_url，跳过",
                    f"  [サムネイル] 「{task.title}」file_id/file_url 欠落のためスキップ",
                )
            )
            return False

        host = urlparse(task.file_url).netloc
        if not host:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" invalid file_url, skipped",
                    f"  [封面] 《{task.title}》 无效 file_url，跳过",
                    f"  [サムネイル] 「{task.title}」無効な file_url のためスキップ",
                )
            )
            return False

        thumbnail_path = os.path.splitext(task.file_path)[0] + ".jpg"
        temp_path = f"{thumbnail_path}_temp"
        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            task.thumbnail_path = thumbnail_path
            return True

        index = max(0, int(task.thumbnail_index))
        thumb_url = f"https://{host}/image/original/{task.file_id}/thumbnail-{index:02d}.jpg"
        resp = None
        try:
            resp = self.api.scraper.get(thumb_url, stream=True, timeout=60)
            if resp.status_code != 200:
                signal_bus.log_message.emit(
                    tr(
                        f"  [Thumbnail] \"{task.title}\" failed HTTP {resp.status_code}",
                        f"  [封面] 《{task.title}》 下载失败 HTTP {resp.status_code}",
                        f"  [サムネイル] 「{task.title}」HTTP {resp.status_code} 失敗",
                    )
                )
                return False
            with open(temp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)

            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, thumbnail_path)
                task.thumbnail_path = thumbnail_path
                signal_bus.log_message.emit(
                    tr(
                        f"  [Thumbnail] saved: {thumbnail_path}",
                        f"  [封面] 已保存: {thumbnail_path}",
                        f"  [サムネイル] 保存完了: {thumbnail_path}",
                    )
                )
                return True

            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as exc:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" error: {exc}",
                    f"  [封面] 《{task.title}》 下载异常: {exc}",
                    f"  [サムネイル] 「{task.title}」エラー: {exc}",
                )
            )
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
        return False

    def _write_nfo(self, task: DownloadTask, *, require_video_file: bool = True) -> bool:
        if not task.file_path:
            return False
        if require_video_file:
            if not os.path.exists(task.file_path):
                return False
            if os.path.getsize(task.file_path) <= 0:
                return False

        nfo_path = os.path.splitext(task.file_path)[0] + ".nfo"
        tags = parse_nfo_tags(task.tags_json)
        nfo_text = build_nfo_text(task, tags)

        temp_path = f"{nfo_path}_temp"
        try:
            os.makedirs(os.path.dirname(nfo_path), exist_ok=True)
            ET.fromstring(nfo_text)
            with open(temp_path, "w", encoding="utf-8") as fh:
                fh.write(nfo_text)
            os.replace(temp_path, nfo_path)
            signal_bus.log_message.emit(
                tr(
                    f"  [NFO] saved: {nfo_path}",
                    f"  [NFO] 已保存: {nfo_path}",
                    f"  [NFO] 保存完了: {nfo_path}",
                )
            )
            return True
        except Exception as exc:
            signal_bus.log_message.emit(
                tr(
                    f"  [NFO] \"{task.title}\" write failed: {exc}",
                    f"  [NFO] 《{task.title}》 写入失败: {exc}",
                    f"  [NFO] 「{task.title}」書き込み失敗: {exc}",
                )
            )
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
        return False
