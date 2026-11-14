import unittest

from app.config import DEFAULT_PROXY_URL, AppConfig, app_config
from app.core.manager import DownloadManager


class FakeAPI:
    def __init__(self):
        self.proxy_values: list[str] = []

    def set_proxy(self, proxy_url: str):
        self.proxy_values.append(proxy_url)


class ProxyConfigTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "api_proxy_enabled": app_config.api_proxy_enabled,
            "api_proxy_url": app_config.api_proxy_url,
            "download_proxy_enabled": app_config.download_proxy_enabled,
            "download_proxy_url": app_config.download_proxy_url,
        }

    def tearDown(self):
        app_config.api_proxy_enabled = self._saved["api_proxy_enabled"]
        app_config.api_proxy_url = self._saved["api_proxy_url"]
        app_config.download_proxy_enabled = self._saved["download_proxy_enabled"]
        app_config.download_proxy_url = self._saved["download_proxy_url"]

    def test_fresh_install_follows_the_system_proxy_with_7897_prefilled(self):
        defaults = AppConfig._DEFAULTS
        self.assertFalse(defaults["api_proxy_enabled"])
        self.assertFalse(defaults["download_proxy_enabled"])
        self.assertEqual(DEFAULT_PROXY_URL, "http://127.0.0.1:7897")
        self.assertEqual(defaults["api_proxy_url"], DEFAULT_PROXY_URL)
        self.assertEqual(defaults["download_proxy_url"], DEFAULT_PROXY_URL)

    def test_api_proxy_applies_only_to_api_session(self):
        mgr = DownloadManager()
        fake_api = FakeAPI()
        mgr.api = fake_api

        app_config.api_proxy_enabled = True
        app_config.api_proxy_url = "http://127.0.0.1:7890"
        app_config.download_proxy_enabled = False
        app_config.download_proxy_url = "http://127.0.0.1:30006"

        mgr.apply_config()

        self.assertEqual(fake_api.proxy_values[-1], "http://127.0.0.1:7890")
        self.assertEqual(mgr._download_request_proxies(), {"http": None, "https": None})

    def test_download_proxy_is_independent_from_api_proxy(self):
        mgr = DownloadManager()
        app_config.api_proxy_enabled = True
        app_config.api_proxy_url = "http://127.0.0.1:7890"
        app_config.download_proxy_enabled = True
        app_config.download_proxy_url = "http://127.0.0.1:30006"

        self.assertEqual(
            mgr._download_request_proxies(),
            {"http": "http://127.0.0.1:30006", "https": "http://127.0.0.1:30006"},
        )


if __name__ == "__main__":
    unittest.main()
