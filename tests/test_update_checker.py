from __future__ import annotations

import unittest

from app.core.update_checker import GitHubReleaseChecker, is_newer_version, version_key


class _FakeResponse:
    def __init__(self, payload, *, error: Exception | None = None):
        self._payload = payload
        self._error = error
        self.closed = False

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class UpdateCheckerTests(unittest.TestCase):
    def test_version_comparison_handles_v_prefix_and_padding(self):
        self.assertEqual(version_key("v1.2.3"), (1, 2, 3))
        self.assertTrue(is_newer_version("v1.3.0", "1.2.9"))
        self.assertFalse(is_newer_version("v1.2", "1.2.0"))

    def test_latest_release_is_reported(self):
        response = _FakeResponse(
            {
                "tag_name": "v0.8.0",
                "name": "IwaraTool 0.8.0",
                "html_url": "https://github.com/Moeary/IwaraTool/releases/tag/v0.8.0",
                "body": "notes",
                "published_at": "2026-08-01T00:00:00Z",
            }
        )
        result = GitHubReleaseChecker(
            current_version="0.7.0",
            session=_FakeSession(response),
        ).check()
        self.assertTrue(result.ok)
        self.assertTrue(result.available)
        self.assertEqual(result.version, "v0.8.0")
        self.assertTrue(response.closed)

    def test_network_errors_are_returned_not_raised(self):
        response = _FakeResponse({}, error=RuntimeError("offline"))
        result = GitHubReleaseChecker(
            current_version="0.7.0",
            session=_FakeSession(response),
        ).check()
        self.assertFalse(result.ok)
        self.assertIn("offline", result.error)


if __name__ == "__main__":
    unittest.main()
