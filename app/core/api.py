"""Iwara API client using cloudscraper to bypass Cloudflare protection."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import cloudscraper

from ..i18n import tr

BASE_API = "https://api.iwara.tv"
ALT_BASE_API = "https://apiq.iwara.tv"
DEFAULT_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.iwara.tv",
    "Referer": "https://www.iwara.tv/",
    "X-Site": "www.iwara.tv",
}
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

    def _headers(self, extra: dict[str, str] | None = None, *, auth: bool = True) -> dict[str, str]:
        headers = dict(DEFAULT_API_HEADERS)
        if auth:
            headers.update(self._auth_headers())
        if extra:
            headers.update(extra)
        return headers

    def _get_json(self, url: str, **kwargs) -> Any:
        """GET request returning parsed JSON, or raises on failure."""
        extra_headers = kwargs.pop("headers", None)
        resp = self.scraper.get(url, headers=self._headers(extra_headers), timeout=30, **kwargs)
        try:
            ok, data, parse_error = _try_response_json(
                resp,
                action=tr("API request", "API 请求", "API リクエスト"),
            )
            if resp.status_code >= 400:
                api_message = _extract_api_message(data) if ok else ""
                if api_message:
                    raise RuntimeError(f"HTTP {resp.status_code}: {api_message}")
                raise RuntimeError(parse_error or _friendly_http_response(resp))
            if not ok:
                raise RuntimeError(parse_error)
            return data
        finally:
            resp.close()

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
        resp = None
        try:
            resp = self.scraper.post(
                f"{BASE_API}/user/login",
                json={"email": credential, "password": password},
                headers=self._headers({"Content-Type": "application/json"}, auth=False),
                timeout=30,
            )
            ok, data, parse_error = _try_response_json(
                resp,
                action=tr("login request", "登录请求", "ログインリクエスト"),
            )
            if not ok:
                return False, parse_error
            if not isinstance(data, dict):
                return False, tr(
                    f"Login API returned unexpected response type: {type(data).__name__}",
                    f"登录接口返回格式异常: {type(data).__name__}",
                    f"ログイン API の応答形式が不正です: {type(data).__name__}",
                )
            token = data.get("token")
            if 200 <= resp.status_code < 300 and token:
                self.token = token
                return True, ""
            msg = _extract_api_message(data)
            if resp.status_code >= 400:
                return False, _friendly_login_failure(msg, resp.status_code)
            return False, _friendly_login_failure(
                msg or tr("token is missing from response", "响应中缺少 token", "応答に token がありません"),
                resp.status_code,
            )
        except Exception as exc:
            return False, _friendly_network_error(
                str(exc),
                action=tr("login", "登录", "ログイン"),
            )
        finally:
            if resp is not None:
                resp.close()

    def logout(self):
        self.token = None

    # ── Video ────────────────────────────────────────────────────────────────

    def get_video_info(self, video_id: str) -> tuple[Optional[dict], str]:
        """Fetch video metadata.

        Returns (data_dict, error_message).
        """
        last_error = ""
        for root in (BASE_API, ALT_BASE_API):
            try:
                data = self._get_json(f"{root}/video/{video_id}")
                # If server returned HTML (bot protection) the first char is '<'
                if isinstance(data, str) and data.startswith("<"):
                    return None, tr(
                        "Received HTML response. Please enable proxy or Cloudflare bypass failed.",
                        "收到 HTML 响应，请启用代理或 Cloudflare 绕过失败",
                        "HTML レスポンスを受信しました。プロキシを有効化するか、Cloudflare 回避に失敗しています。",
                    )
                if not isinstance(data, dict):
                    return None, tr(
                        f"Unexpected API response type: {type(data).__name__}",
                        f"API 返回类型异常: {type(data).__name__}",
                        f"API 応答タイプが不正です: {type(data).__name__}",
                    )
                return data, ""
            except Exception as exc:
                last_error = str(exc)
                if "404" in last_error or "not found" in last_error.lower():
                    return None, tr(
                        "Video not found or not visible to the current account. It may have been deleted, hidden, private, or require another account.",
                        "作品不存在或当前账号不可见，可能已删除、隐藏、私有，或需要换有权限的账号登录。",
                        "動画が存在しないか現在のアカウントでは表示できません。削除・非表示・非公開、または別アカウントが必要な可能性があります。",
                    )
                continue
        return None, _friendly_request_error(last_error)

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
            resp = None
            try:
                resp = self.scraper.get(
                    file_url,
                    headers={**self._auth_headers(), "X-Version": x_version},
                    timeout=30,
                )
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    continue
                ok, data, parse_error = _try_response_json(
                    resp,
                    action=tr("file source request", "文件源请求", "ファイルソースリクエスト"),
                )
                if not ok:
                    last_error = parse_error
                    continue
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
            finally:
                if resp is not None:
                    resp.close()

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
        available_names = [
            s.get("name", "<no name>") if isinstance(s, dict) else "<invalid source>"
            for s in sources
        ]
        _log(
            tr(
                f"  Available qualities: {available_names}",
                f"  可用画质列表: {available_names}",
                f"  利用可能な画質: {available_names}",
            )
        )

        # Case-insensitive sources map
        sources_map: dict[str, dict] = {
            s.get("name", "").lower(): s for s in sources if isinstance(s, dict)
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
                src = entry.get("src")
                raw = src.get("download", "") if isinstance(src, dict) else ""
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

    def get_user_profile(self, username: str) -> tuple[Optional[dict], str]:
        try:
            data = self._get_json(f"{BASE_API}/profile/{username}")
            if isinstance(data, dict) and isinstance(data.get("user"), dict):
                return data, ""
            return None, tr(
                f"API did not return profile.user, response: {data}",
                f"API 未返回 profile.user，响应: {data}",
                f"API が profile.user を返しませんでした。応答: {data}",
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

    def get_user_following(
        self, user_id: str, max_pages: int = 100
    ) -> tuple[list[dict], str]:
        """Fetch users followed by the given user id."""
        if not user_id:
            return [], tr("Missing user id", "缺少用户 ID", "ユーザーIDがありません")
        last_error = ""
        for root in (BASE_API, ALT_BASE_API):
            users: list[dict] = []
            had_response = False
            for page in range(max_pages + 1):
                try:
                    data = self._get_json(
                        f"{root}/user/{user_id}/following",
                        params={"page": page, "limit": 50},
                    )
                    had_response = True
                except Exception as exc:
                    last_error = f"{root}: {exc}"
                    users = []
                    break

                results = data.get("results", [])
                if not isinstance(results, list) or not results:
                    break
                users.extend(results)
                if len(results) < 50:
                    break
            if had_response:
                return users, ""
        return [], last_error or tr(
            "Failed to fetch following users",
            "获取关注作者失败",
            "フォロー中ユーザーの取得に失敗しました",
        )

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

    def get_subscribed_videos(
        self,
        *,
        max_pages: int = 100,
        max_results: int = 0,
    ) -> tuple[list[dict], str]:
        """Fetch the logged-in user's subscribed video feed."""
        if not self.token:
            return [], tr(
                "Login is required for subscribed videos",
                "拉取订阅视频需要先登录",
                "購読動画の取得にはログインが必要です",
            )
        return self.get_videos_by_query(
            {"subscribed": "true", "sort": "date"},
            max_pages=max_pages,
            max_results=max_results,
        )

    # ── Proxy ────────────────────────────────────────────────────────────────

    def set_proxy(self, proxy_url: str):
        if proxy_url:
            self.scraper.proxies = {"http": proxy_url, "https": proxy_url}
        else:
            self.scraper.proxies = {}


def _friendly_request_error(error: str) -> str:
    err = str(error or "").strip()
    lower = err.lower()
    if "ssleoferror" in lower or "unexpected_eof" in lower:
        return tr(
            f"Network/TLS connection was interrupted while fetching video info: {err}",
            f"获取作品信息时网络/TLS 连接被中断：{err}",
            f"動画情報の取得中にネットワーク/TLS 接続が中断されました: {err}",
        )
    if "403" in lower or "forbidden" in lower:
        return tr(
            "The current account has no permission to view this video.",
            "当前账号没有权限查看该作品。",
            "現在のアカウントにはこの動画を表示する権限がありません。",
        )
    if "timeout" in lower:
        return tr(
            f"Request timed out while fetching video info: {err}",
            f"获取作品信息超时：{err}",
            f"動画情報の取得がタイムアウトしました: {err}",
        )
    return err or tr("Unknown request error", "未知请求错误", "不明なリクエストエラー")


def _try_response_json(resp: Any, *, action: str) -> tuple[bool, Any, str]:
    try:
        return True, resp.json(), ""
    except ValueError:
        return False, None, _non_json_response_error(resp, action=action)


def _extract_api_message(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("message", "error", "detail"):
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value if item is not None)
        if isinstance(value, dict):
            nested = value.get("message") or value.get("error") or value.get("detail")
            if nested:
                return str(nested)
        if value:
            return str(value)
    return ""


def _non_json_response_error(resp: Any, *, action: str) -> str:
    status = getattr(resp, "status_code", "?")
    headers = getattr(resp, "headers", {}) or {}
    content_type = headers.get("content-type") or headers.get("Content-Type") or "unknown"
    preview = _response_preview(resp)
    return tr(
        f"{action} did not return JSON (HTTP {status}, Content-Type: {content_type}). "
        f"This usually means the request was blocked by the network/proxy/Cloudflare, "
        f"or the server returned an error page. Response preview: {preview}",
        f"{action}没有返回 JSON（HTTP {status}，Content-Type: {content_type}）。"
        f"通常是网络/代理/Cloudflare 拦截，或服务器返回了错误页面。响应预览：{preview}",
        f"{action}が JSON を返しませんでした（HTTP {status}, Content-Type: {content_type}）。"
        f"ネットワーク/プロキシ/Cloudflare にブロックされたか、サーバーがエラーページを返した可能性があります。応答プレビュー: {preview}",
    )


def _response_preview(resp: Any, limit: int = 240) -> str:
    try:
        text = str(getattr(resp, "text", "") or "")
    except Exception:
        return tr("<unable to read response>", "<无法读取响应>", "<応答を読み取れません>")
    text = " ".join(text.strip().split())
    if not text:
        return tr("<empty response>", "<空响应>", "<空の応答>")
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _friendly_http_response(resp: Any) -> str:
    status = int(getattr(resp, "status_code", 0) or 0)
    preview = _response_preview(resp)
    if status == 401:
        return tr(
            "HTTP 401: login is required or the token is invalid.",
            "HTTP 401：需要登录，或当前 token 已失效。",
            "HTTP 401: ログインが必要、または token が無効です。",
        )
    if status == 403:
        return tr(
            "HTTP 403: request was forbidden. Try enabling/changing proxy, or login again.",
            "HTTP 403：请求被拒绝。请尝试开启/更换代理，或重新登录。",
            "HTTP 403: リクエストが拒否されました。プロキシの有効化/変更、または再ログインを試してください。",
        )
    if status == 429:
        return tr(
            "HTTP 429: too many requests. Please wait and try again, or change proxy/IP.",
            "HTTP 429：请求过于频繁。请稍后重试，或更换代理/IP。",
            "HTTP 429: リクエストが多すぎます。しばらく待つか、プロキシ/IP を変更してください。",
        )
    if 500 <= status < 600:
        return tr(
            f"HTTP {status}: Iwara server error. Please try again later. Response preview: {preview}",
            f"HTTP {status}：Iwara 服务器错误，请稍后再试。响应预览：{preview}",
            f"HTTP {status}: Iwara サーバーエラーです。後ほど再試行してください。応答プレビュー: {preview}",
        )
    return tr(
        f"HTTP {status}: request failed. Response preview: {preview}",
        f"HTTP {status}：请求失败。响应预览：{preview}",
        f"HTTP {status}: リクエストに失敗しました。応答プレビュー: {preview}",
    )


def _friendly_login_failure(message: str, status_code: int) -> str:
    raw = str(message or "").strip()
    compact = raw.lower()
    if raw == "errors.invalidLogin" or "invalidlogin" in compact:
        return tr(
            "Invalid username/email or password. Please check the account credentials.",
            "账号或密码错误，请检查用户名/邮箱和密码。",
            "ユーザー名/メールまたはパスワードが正しくありません。",
        )
    if raw == "errors.tooManyRequests" or "toomanyrequests" in compact or status_code == 429:
        return tr(
            "Too many login attempts. Please wait and try again, or change proxy/IP.",
            "登录请求过于频繁，请稍后重试，或更换代理/IP。",
            "ログイン試行が多すぎます。しばらく待つか、プロキシ/IP を変更してください。",
        )
    if status_code in (401, 403):
        return tr(
            f"Login was rejected by the server (HTTP {status_code}). Try enabling/changing proxy, then login again.",
            f"登录请求被服务器拒绝（HTTP {status_code}）。请尝试开启/更换代理后重新登录。",
            f"ログインリクエストがサーバーに拒否されました（HTTP {status_code}）。プロキシの有効化/変更後、再ログインしてください。",
        )
    if raw:
        return tr(
            f"Login failed (HTTP {status_code}): {raw}",
            f"登录失败（HTTP {status_code}）：{raw}",
            f"ログイン失敗（HTTP {status_code}）: {raw}",
        )
    return tr(
        f"Login failed (HTTP {status_code}).",
        f"登录失败（HTTP {status_code}）。",
        f"ログイン失敗（HTTP {status_code}）。",
    )


def _friendly_network_error(error: str, *, action: str) -> str:
    err = str(error or "").strip()
    lower = err.lower()
    if "expecting value" in lower:
        return tr(
            f"{action} failed because the server did not return JSON. Please check proxy/network settings and try again.",
            f"{action}失败：服务器没有返回 JSON。请检查代理/网络设置后重试。",
            f"{action}に失敗しました: サーバーが JSON を返しませんでした。プロキシ/ネットワーク設定を確認して再試行してください。",
        )
    if "timeout" in lower:
        return tr(
            f"{action} timed out. Please check the network/proxy and try again.",
            f"{action}超时，请检查网络/代理后重试。",
            f"{action}がタイムアウトしました。ネットワーク/プロキシを確認して再試行してください。",
        )
    if any(token in lower for token in ("proxyerror", "connection", "ssleoferror", "unexpected_eof", "tls")):
        return tr(
            f"{action} network connection failed: {err}",
            f"{action}网络连接失败：{err}",
            f"{action}のネットワーク接続に失敗しました: {err}",
        )
    return err or tr(
        f"{action} failed with an unknown error.",
        f"{action}失败，原因未知。",
        f"{action}に失敗しました。不明なエラーです。",
    )
