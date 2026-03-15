"""Iwara API client using cloudscraper to bypass Cloudflare protection."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import cloudscraper

from ..i18n import tr

BASE_API = "https://api.iwara.tv"
# X-Version shared secrets (new first, legacy fallback)
_X_VERSION_SALTS = (
    "mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN",
    "5nFp9kmbNnHdAFhaqMvt",
)


class IwaraAPI:
    """Low-level stateful client for the Iwara REST API.

    All methods are synchronous and designed to run inside worker threads.
    """

    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        self.token: Optional[str] = None

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _get_json(self, url: str, **kwargs) -> Any:
        """GET request returning parsed JSON, or raises on failure."""
        resp = self.scraper.get(url, headers=self._auth_headers(), timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # ── X-Version computation ────────────────────────────────────────────────

    @staticmethod
    def compute_x_version(file_url: str, salt: str) -> str:
        """sha1("{uuid}_{expires}_{salt}") as required by Iwara's file API."""
        m = re.search(r"/file/([^?#]+)", file_url)
        filename = m.group(1) if m else ""
        qs = parse_qs(urlparse(file_url).query)
        expires = qs.get("expires", [""])[0]
        raw = f"{filename}_{expires}_{salt}"
        return hashlib.sha1(raw.encode()).hexdigest()

    # ── Auth ─────────────────────────────────────────────────────────────────

    def login(self, credential: str, password: str) -> tuple[bool, str]:
        """Login with username or email + password.

        Returns (success, error_message).
        The token is stored in self.token on success.
        """
        try:
            resp = self.scraper.post(
                f"{BASE_API}/user/login",
                json={"email": credential, "password": password},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            data = resp.json()
            token = data.get("token")
            if token:
                self.token = token
                return True, ""
            msg = data.get("message", tr("Unknown error", "未知错误", "不明なエラー"))
            return False, msg
        except Exception as exc:
            return False, str(exc)

    def logout(self):
        self.token = None

    # ── Video ────────────────────────────────────────────────────────────────

    def get_video_info(self, video_id: str) -> tuple[Optional[dict], str]:
        """Fetch video metadata.

        Returns (data_dict, error_message).
        """
        try:
            data = self._get_json(f"{BASE_API}/video/{video_id}")
            # If server returned HTML (bot protection) the first char is '<'
            if isinstance(data, str) and data.startswith("<"):
                return None, tr(
                    "Received HTML response. Please enable proxy or Cloudflare bypass failed.",
                    "收到 HTML 响应，请启用代理或 Cloudflare 绕过失败",
                    "HTML レスポンスを受信しました。プロキシを有効化するか、Cloudflare 回避に失敗しています。",
                )
            return data, ""
        except Exception as exc:
            return None, str(exc)

    def get_download_info(
        self,
        video_info: dict,
        preferred_quality: str = "Source",
        log_cb=None,
    ) -> tuple[Optional[str], Optional[str], str]:
        """Resolve the best available download URL from video metadata.

        Args:
            video_info: parsed video metadata dict from the Iwara API.
            preferred_quality: one of "Source", "540", "360".  Falls back
                to lower resolutions automatically.
            log_cb: optional callable(str) for verbose logging.

        Returns (download_url, quality_name, error_message).
        """
        def _log(msg: str):
            if log_cb:
                log_cb(msg)

        file_url: Optional[str] = video_info.get("fileUrl")
        if not file_url:
            message = video_info.get("message", "")
            embed = video_info.get("embedUrl", "")
            if "youtube" in embed or "youtu.be" in embed:
                return None, None, tr(
                    f"This video is a YouTube embed and cannot be downloaded ({embed})",
                    f"该视频为 YouTube 嵌入，无法下载（{embed}）",
                    f"この動画は YouTube 埋め込みのためダウンロードできません（{embed}）",
                )
            if message == "errors.privateVideo":
                return None, None, tr(
                    "Private video. Please login and try again.",
                    "私有视频，请先登录后重试",
                    "非公開動画です。ログインして再試行してください。",
                )
            return None, None, tr(
                f"fileUrl is empty. API response keys: {list(video_info.keys())}",
                f"fileUrl 为空，API 回复键: {list(video_info.keys())}",
                f"fileUrl が空です。API レスポンスキー: {list(video_info.keys())}",
            )

        _log(f"  fileUrl: {file_url[:80]}…")
        _log(
            tr(
                f"  Login state: {'logged in (Bearer token attached)' if self.token else 'not logged in (no token)'}",
                f"  登录态: {'已登录（携带 Token）' if self.token else '未登录（无 Token）'}",
                f"  ログイン状態: {'ログイン済み（Bearer トークン付き）' if self.token else '未ログイン（トークンなし）'}",
            )
        )

        sources: Optional[list[dict]] = None
        last_error = ""
        for idx, salt in enumerate(_X_VERSION_SALTS, start=1):
            x_version = self.compute_x_version(file_url, salt)
            _log(f"  X-Version[{idx}]: {x_version}")
            try:
                resp = self.scraper.get(
                    file_url,
                    headers={**self._auth_headers(), "X-Version": x_version},
                    timeout=30,
                )
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    continue
                data = resp.json()
                if isinstance(data, list) and data:
                    sources = data
                    break
                last_error = tr(
                    f"response is empty or not a list: {data!r}",
                    f"响应为空或非列表: {data!r}",
                    f"応答が空、またはリストではありません: {data!r}",
                )
            except Exception as exc:
                last_error = str(exc)

        if sources is None:
            return None, None, tr(
                f"Failed to get file source list: {last_error or 'unknown error'}",
                f"获取文件列表失败: {last_error or '未知错误'}",
                f"ファイルソース一覧の取得に失敗: {last_error or '不明なエラー'}",
            )

        if not isinstance(sources, list) or not sources:
            return None, None, tr(
                f"Empty file source list. Raw response: {sources!r}",
                f"文件列表为空，原始响应: {sources!r}",
                f"ファイルソース一覧が空です。生レスポンス: {sources!r}",
            )

        # Log all available qualities with their URL prefixes
        available_names = [s.get("name", "<no name>") for s in sources]
        _log(
            tr(
                f"  Available qualities: {available_names}",
                f"  可用画质列表: {available_names}",
                f"  利用可能な画質: {available_names}",
            )
        )

        # Case-insensitive sources map
        sources_map: dict[str, dict] = {
            s.get("name", "").lower(): s for s in sources
        }

        # Build preference order based on selected quality
        # preferred "Source" → ["Source","540","360"]
        # preferred "540"   → ["540","360"]
        # preferred "360"   → ["360"]
        _all_qualities = ["Source", "540", "360"]
        try:
            start_idx = _all_qualities.index(preferred_quality)
        except ValueError:
            start_idx = 0
        quality_order = _all_qualities[start_idx:]

        for quality in quality_order:
            entry = sources_map.get(quality.lower())
            if entry:
                raw = entry.get("src", {}).get("download", "")
                if raw:
                    dl_url = f"https:{raw}" if raw.startswith("//") else raw
                    # Use the original name from the entry (preserves casing)
                    actual_name = entry.get("name", quality)
                    _log(
                        tr(
                            f"  Selected quality: {actual_name}  URL prefix: {dl_url[:60]}...",
                            f"  选择画质: {actual_name}  URL 前缀: {dl_url[:60]}…",
                            f"  選択画質: {actual_name}  URL プレフィックス: {dl_url[:60]}...",
                        )
                    )
                    return dl_url, actual_name, ""
                else:
                    _log(
                        tr(
                            f"  Quality {quality} exists but src.download is empty, skipped",
                            f"  画质 {quality} 存在但 src.download 为空，跳过",
                            f"  画質 {quality} は存在しますが src.download が空のためスキップ",
                        )
                    )

        tried = ", ".join(quality_order)
        return None, None, tr(
            f"No usable quality found (tried: {tried}; server returned: {available_names})",
            f"找不到可用画质（尝试过: {tried}；服务器返回: {available_names}）",
            f"利用可能な画質が見つかりません（試行: {tried}、サーバー応答: {available_names}）",
        )

    # ── User ─────────────────────────────────────────────────────────────────

    def get_user_id(self, username: str) -> tuple[Optional[str], str]:
        try:
            data = self._get_json(f"{BASE_API}/profile/{username}")
            uid = data.get("user", {}).get("id")
            if uid:
                return uid, ""
            return None, tr(
                f"API did not return user.id, response: {data}",
                f"API 未返回 user.id，响应: {data}",
                f"API が user.id を返しませんでした。応答: {data}",
            )
        except Exception as exc:
            return None, str(exc)

    def get_user_videos(
        self, user_id: str, max_pages: int = 100
    ) -> list[dict]:
        """Fetch all video stubs for a user (paginated)."""
        videos: list[dict] = []
        for page in range(max_pages + 1):
            try:
                data = self._get_json(
                    f"{BASE_API}/videos",
                    params={"page": page, "sort": "date", "user": user_id},
                )
                results: list[dict] = data.get("results", [])
                if not results:
                    break
                videos.extend(results)
            except Exception:
                break
        return videos

    # ── Playlist ─────────────────────────────────────────────────────────────

    def get_playlist_videos(
        self, playlist_id: str, max_pages: int = 100
    ) -> list[dict]:
        """Fetch all video stubs in a playlist (paginated)."""
        videos: list[dict] = []
        for page in range(max_pages + 1):
            try:
                data = self._get_json(
                    f"{BASE_API}/playlist/{playlist_id}",
                    params={"page": page},
                )
                results: list[dict] = data.get("results", [])
                if not results:
                    break
                videos.extend(results)
            except Exception:
                break
        return videos

    # ── Search / videos query ───────────────────────────────────────────────

    def get_videos_by_query(
        self,
        query_params: dict[str, str],
        *,
        max_pages: int = 100,
        max_results: int = 0,
    ) -> tuple[list[dict], str]:
        """Fetch videos from /videos with arbitrary query parameters.

        Args:
            query_params: query-string key/value params, e.g. {"tags": "2d", "sort": "date"}.
            max_pages: safety page cap.
            max_results: hard cap for returned video stubs. 0 means unlimited.

        Returns:
            (videos, error_message). Partial results can be returned with error.
        """
        base_params = {str(k): str(v) for k, v in query_params.items() if str(v).strip()}
        start_page_raw = base_params.pop("page", "0")
        try:
            start_page = max(0, int(start_page_raw))
        except Exception:
            start_page = 0

        explicit_limit = 0
        if "limit" in base_params:
            try:
                explicit_limit = max(0, int(base_params["limit"]))
            except Exception:
                explicit_limit = 0

        if max_results > 0 and explicit_limit > 0:
            effective_limit = min(max_results, explicit_limit)
        else:
            effective_limit = max_results or explicit_limit

        videos: list[dict] = []
        for page in range(start_page, start_page + max_pages):
            page_params = dict(base_params)
            page_params["page"] = str(page)
            try:
                data = self._get_json(f"{BASE_API}/videos", params=page_params)
            except Exception as exc:
                if videos:
                    return videos, str(exc)
                return [], str(exc)

            results = data.get("results", [])
            if not isinstance(results, list) or not results:
                break
            videos.extend(results)
            if effective_limit > 0 and len(videos) >= effective_limit:
                videos = videos[:effective_limit]
                break

            count = data.get("count")
            if isinstance(count, int) and len(videos) >= count:
                break

        return videos, ""

    # ── Proxy ────────────────────────────────────────────────────────────────

    def set_proxy(self, proxy_url: str):
        if proxy_url:
            self.scraper.proxies = {"http": proxy_url, "https": proxy_url}
        else:
            self.scraper.proxies = {}
