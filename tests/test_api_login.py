import unittest

from app.config import app_config
from app.core.api import IwaraAPI


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


if __name__ == "__main__":
    unittest.main()
