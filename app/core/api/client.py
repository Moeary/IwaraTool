"""Iwara API client using cloudscraper to bypass Cloudflare protection."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import cloudscraper

from ...config import app_config
from ...i18n import tr
from ...logging_setup import get_logger
from .. import net_policy
from .endpoints import BASE_API, DEFAULT_API_HEADERS
from .errors import (
    _extract_api_message,
    _friendly_http_response,
    _friendly_login_failure,
    _friendly_network_error,
    _try_response_json,
)
from .listings import ListingApiMixin
from .users import UserApiMixin
from .videos import VideoApiMixin

logger = get_logger(__name__)


class IwaraAPI(VideoApiMixin, UserApiMixin, ListingApiMixin):
    """Low-level stateful client for the Iwara REST API.

    All methods are synchronous and designed to run inside worker threads.
    """

    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        net_policy.install(
            self.scraper,
            min_interval=lambda: max(0, app_config.request_min_interval_ms) / 1000.0,
            max_retries=lambda: app_config.request_max_retries,
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

    # ── Proxy ────────────────────────────────────────────────────────────────

    def set_proxy(self, proxy_url: str):
        if proxy_url:
            self.scraper.proxies = {"http": proxy_url, "https": proxy_url}
        else:
            self.scraper.proxies = {}
