"""Turn an Iwara video id into a URL the built-in player can stream."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import cloudscraper

from ..config import app_config
from ..i18n import tr
from ..logging_setup import get_logger
from .stream_proxy import StreamProxy, StreamSource

logger = get_logger(__name__)

PREVIEW_QUALITIES = ("Source", "540", "360")


class PreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreviewStream:
    url: str  # loopback URL for QMediaPlayer
    title: str
    quality: str


_local = threading.local()
_proxy: StreamProxy | None = None
_proxy_lock = threading.Lock()


def _session():
    """One cloudscraper session per proxy worker thread (sessions are not thread-safe)."""
    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = cloudscraper.create_scraper()
    return session


def get_proxy() -> StreamProxy:
    global _proxy
    with _proxy_lock:
        if _proxy is None:
            _proxy = StreamProxy(_session)
        return _proxy


def shutdown_proxy() -> None:
    global _proxy
    with _proxy_lock:
        proxy, _proxy = _proxy, None
    if proxy is not None:
        proxy.stop()


def _download_proxies() -> dict[str, str] | None:
    if app_config.download_proxy_enabled and app_config.download_proxy_url:
        url = app_config.download_proxy_url
        return {"http": url, "https": url}
    return None


def preview_quality() -> str:
    value = str(app_config.preview_quality or "540")
    return value if value in PREVIEW_QUALITIES else "540"


def resolve_file_url(api, video_id: str, quality: str) -> tuple[str, str, str]:
    """``(signed file url, resolved quality, title)`` or raise ``PreviewError``."""
    info, error = api.get_video_info(video_id)
    if not info:
        raise PreviewError(error or tr("Video info is unavailable", "无法获取视频信息", "動画情報を取得できません"))
    url, resolved, error = api.get_download_info(info, quality)
    if not url:
        raise PreviewError(error or tr("No playable file was found", "没有找到可播放的文件", "再生可能なファイルが見つかりません"))
    return url, resolved or quality, str(info.get("title", "") or "")


def open_stream(api, video_id: str, quality: str | None = None) -> PreviewStream:
    """Resolve ``video_id`` and expose it through the loopback proxy."""
    quality = quality or preview_quality()
    url, resolved, title = resolve_file_url(api, video_id, quality)

    def refresh() -> str:
        try:
            return resolve_file_url(api, video_id, quality)[0]
        except PreviewError:
            logger.warning("Could not refresh the stream URL for %s", video_id, exc_info=True)
            return ""

    def headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {api.token}"} if getattr(api, "token", None) else {}

    source = StreamSource(url, refresh=refresh, headers=headers, proxies=_download_proxies)
    return PreviewStream(url=get_proxy().register(source), title=title, quality=str(resolved))
