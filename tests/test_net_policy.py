from __future__ import annotations

import unittest

import requests

from app.core import net_policy


class _Response:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, method, url, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install(session, *, retries=2, interval=0.0, sleeps=None, throttle=None):
    sleeps = sleeps if sleeps is not None else []
    net_policy.install(
        session,
        min_interval=lambda: interval,
        max_retries=lambda: retries,
        sleep=sleeps.append,
        throttle=throttle or net_policy.HostThrottle(sleep=lambda _s: None),
    )
    return sleeps


class RetryAfterTests(unittest.TestCase):
    def test_parses_seconds_and_dates(self):
        self.assertEqual(net_policy.parse_retry_after("7"), 7.0)
        self.assertEqual(net_policy.parse_retry_after(" 2.5 "), 2.5)
        self.assertIsNone(net_policy.parse_retry_after(""))
        self.assertIsNone(net_policy.parse_retry_after("soon"))
        future = net_policy.parse_retry_after(
            "Wed, 21 Oct 2015 07:28:10 GMT", now=1445412480.0
        )
        self.assertAlmostEqual(future, 10.0, places=3)

    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual(net_policy.backoff_delay(1), 1.0)
        self.assertEqual(net_policy.backoff_delay(3), 4.0)
        self.assertEqual(net_policy.backoff_delay(20), net_policy.MAX_RETRY_AFTER_SECONDS)
        self.assertEqual(net_policy.backoff_delay(1, 999.0), net_policy.MAX_RETRY_AFTER_SECONDS)
        self.assertEqual(net_policy.backoff_delay(1, 3.0), 3.0)


class InstallTests(unittest.TestCase):
    def test_retries_429_honouring_retry_after(self):
        throttled = _Response(429, {"Retry-After": "5"})
        ok = _Response(200)
        session = _Session([throttled, ok])
        sleeps = _install(session)
        response = session.request("GET", "https://api.iwara.tv/videos")
        self.assertIs(response, ok)
        self.assertEqual(sleeps, [5.0])
        self.assertTrue(throttled.closed)

    def test_gives_up_after_max_retries_and_returns_last_response(self):
        replies = [_Response(503), _Response(503), _Response(503)]
        session = _Session(replies)
        sleeps = _install(session, retries=2)
        response = session.request("GET", "https://api.iwara.tv/x")
        self.assertIs(response, replies[-1])
        self.assertEqual(session.calls, 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_does_not_retry_client_errors(self):
        session = _Session([_Response(404)])
        sleeps = _install(session)
        self.assertEqual(session.request("GET", "https://api.iwara.tv/x").status_code, 404)
        self.assertEqual(session.calls, 1)
        self.assertEqual(sleeps, [])

    def test_retries_connection_errors_but_not_ssl_errors(self):
        ok = _Response(200)
        session = _Session([requests.exceptions.ConnectionError("boom"), ok])
        _install(session)
        self.assertIs(session.request("GET", "https://api.iwara.tv/x"), ok)

        ssl_session = _Session([requests.exceptions.SSLError("bad cert")])
        _install(ssl_session)
        with self.assertRaises(requests.exceptions.SSLError):
            ssl_session.request("GET", "https://api.iwara.tv/x")
        self.assertEqual(ssl_session.calls, 1)

    def test_zero_retries_disables_retrying(self):
        session = _Session([_Response(429)])
        _install(session, retries=0)
        self.assertEqual(session.request("GET", "https://api.iwara.tv/x").status_code, 429)
        self.assertEqual(session.calls, 1)

    def test_session_without_request_is_left_alone(self):
        class Bare:
            pass

        bare = Bare()
        net_policy.install(bare, min_interval=lambda: 0, max_retries=lambda: 1)
        self.assertFalse(hasattr(bare, "request"))


class HostThrottleTests(unittest.TestCase):
    def test_spaces_requests_per_host(self):
        now = [100.0]
        slept: list[float] = []

        def fake_sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        throttle = net_policy.HostThrottle(clock=lambda: now[0], sleep=fake_sleep)
        self.assertEqual(throttle.wait("a.example", 0.5), 0.0)
        self.assertAlmostEqual(throttle.wait("a.example", 0.5), 0.5)
        self.assertEqual(throttle.wait("b.example", 0.5), 0.0)
        self.assertEqual(len(slept), 1)

    def test_disabled_interval_never_sleeps(self):
        throttle = net_policy.HostThrottle(sleep=lambda _s: self.fail("slept"))
        for _ in range(3):
            self.assertEqual(throttle.wait("a.example", 0), 0.0)


if __name__ == "__main__":
    unittest.main()
