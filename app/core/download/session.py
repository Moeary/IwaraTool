"""Login state, API calls and download proxies of the manager."""

from __future__ import annotations

from datetime import datetime

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..api import token_is_expired


class SessionMixin:
    """Login state, API calls and download proxies of the manager."""

    def set_login(self, logged_in: bool, token: str | None = None):
        with self._auth_lock:
            if logged_in and token:
                self.api.token = token
                app_config.auth_token = token
                app_config.auth_token_saved_at = datetime.now().isoformat(timespec="seconds")
            elif not logged_in:
                self.api.token = None
                app_config.auth_token = ""
                app_config.auth_token_saved_at = ""

    def restore_cached_login(self) -> bool:
        token = (app_config.auth_token or "").strip()
        if not (app_config.auth_enabled and token):
            return False
        # An expired token is not rejected by Iwara; it just turns every
        # request into a guest request (issue #19: an author's account-only
        # videos vanish). Drop it so startup signs in again with credentials.
        if token_is_expired(token, margin_seconds=24 * 3600):
            with self._auth_lock:
                self.api.token = None
                app_config.auth_token = ""
                app_config.auth_token_saved_at = ""
            signal_bus.log_message.emit(
                tr(
                    "[Login] Cached token expired, signing in again",
                    "[登录] 缓存的 Token 已过期，将重新登录",
                    "[ログイン] 保存済み Token の期限が切れたため再ログインします",
                )
            )
            return False
        with self._auth_lock:
            self.api.token = token
        return True

    def apply_config(self):
        """Apply proxy settings from app_config to the scraper."""
        with self._auth_lock:
            if app_config.api_proxy_enabled and app_config.api_proxy_url:
                self.api.set_proxy(app_config.api_proxy_url)
            else:
                self.api.set_proxy("")

    def _download_request_proxies(self) -> dict[str, str | None]:
        proxy_url = (app_config.download_proxy_url or "").strip()
        if app_config.download_proxy_enabled and proxy_url:
            return {"http": proxy_url, "https": proxy_url}
        return {"http": None, "https": None}

    def _api_call(self, method_name: str, *args, **kwargs):
        """Serialize access to the shared cloudscraper session."""
        with self._api_lock:
            method = getattr(self.api, method_name)
            return method(*args, **kwargs)

    def _current_token(self) -> str:
        # Lock-free snapshot: must not wait for an in-flight _api_call.
        return self.api.token or ""
