import base64
import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QSettings

from app.config import app_config
from app.core.api import IwaraAPI, token_is_expired
from app.core.manager import DownloadManager


_MISSING = object()


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        json_data=_MISSING,
        json_exc: Exception | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self._json_data = json_data
        self._json_exc = json_exc
        self.text = text
        self.headers = headers or {"content-type": "application/json; charset=utf-8"}
        self.closed = False

    def json(self):
        if self._json_exc:
            raise self._json_exc
        if self._json_data is _MISSING:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json_data

    def close(self):
        self.closed = True


class FakeScraper:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class IwaraAPILoginTests(unittest.TestCase):
    def setUp(self):
        self._language = app_config.ui_language
        app_config.ui_language = "en_US"
        self.api = IwaraAPI()

    def tearDown(self):
        app_config.ui_language = self._language

    def test_login_success_sets_token_and_browser_headers(self):
        response = FakeResponse(200, json_data={"token": "token-123"})
        scraper = FakeScraper(response)
        self.api.scraper = scraper

        ok, msg = self.api.login("user", "password")

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(self.api.token, "token-123")
        _, kwargs = scraper.calls[0]
        headers = kwargs["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Origin"], "https://www.iwara.tv")
        self.assertEqual(headers["Referer"], "https://www.iwara.tv/")
        self.assertEqual(headers["X-Site"], "www.iwara.tv")
        self.assertTrue(response.closed)

    def test_login_invalid_credentials_uses_friendly_message(self):
        response = FakeResponse(
            400,
            json_data={"message": "errors.invalidLogin"},
            text='{"message":"errors.invalidLogin"}',
        )
        self.api.scraper = FakeScraper(response)

        ok, msg = self.api.login("bad-user", "bad-password")

        self.assertFalse(ok)
        self.assertIn("Invalid username/email or password", msg)
        self.assertNotIn("errors.invalidLogin", msg)
        self.assertTrue(response.closed)

    def test_login_non_json_response_does_not_leak_json_decode_error(self):
        response = FakeResponse(
            200,
            json_exc=ValueError("Expecting value: line 1 column 1 (char 0)"),
            text="",
            headers={"content-type": "text/html"},
        )
        self.api.scraper = FakeScraper(response)

        ok, msg = self.api.login("user", "password")

        self.assertFalse(ok)
        self.assertIn("did not return JSON", msg)
        self.assertIn("HTTP 200", msg)
        self.assertNotIn("Expecting value", msg)
        self.assertTrue(response.closed)


def _jwt(exp: float | None) -> str:
    def part(data: dict) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {"type": "refresh_token"}
    if exp is not None:
        claims["exp"] = int(exp)
    return f"{part({'alg': 'HS256'})}.{part(claims)}.signature"


class CachedTokenExpiryTests(unittest.TestCase):
    """Issue #19: Iwara answers an expired token as a guest instead of 401."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = QSettings(os.path.join(self.temp.name, "settings.ini"), QSettings.Format.IniFormat)
        patcher = patch.object(app_config, "_qs", settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        app_config.auth_enabled = True
        self.manager = SimpleNamespace(api=IwaraAPI(), _auth_lock=threading.Lock())

    def _restore(self) -> bool:
        with patch("app.core.manager.signal_bus"):
            return DownloadManager.restore_cached_login(self.manager)

    def test_token_expiry_helpers(self):
        self.assertTrue(token_is_expired(_jwt(time.time() - 60)))
        self.assertFalse(token_is_expired(_jwt(time.time() + 7 * 86400)))
        self.assertTrue(token_is_expired(_jwt(time.time() + 3600), margin_seconds=86400))
        self.assertFalse(token_is_expired("opaque-token"))
        self.assertFalse(token_is_expired(_jwt(None)))

    def test_valid_cached_token_is_restored(self):
        token = _jwt(time.time() + 20 * 86400)
        app_config.auth_token = token

        self.assertTrue(self._restore())
        self.assertEqual(self.manager.api.token, token)

    def test_expired_cached_token_is_dropped(self):
        app_config.auth_token = _jwt(time.time() - 60)
        app_config.auth_token_saved_at = "2026-01-01T00:00:00"

        self.assertFalse(self._restore())
        self.assertIsNone(self.manager.api.token)
        self.assertEqual(app_config.auth_token, "")
        self.assertEqual(app_config.auth_token_saved_at, "")


if __name__ == "__main__":
    unittest.main()
