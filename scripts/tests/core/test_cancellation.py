"""Cancellation tokens stop core work on plain threads and asyncio workers, without Qt."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest

from app.core import net_policy
from app.core.cancellation import CancelToken, cancel_scope, current_token, token_cancelled
from app.core.net_policy import RequestCancelled, cancellable_sleep, cancellation_requested

# Well within a test timeout, well above CANCEL_POLL_SECONDS.
PROMPT = 1.0


class TokenTests(unittest.TestCase):
    def test_token_is_one_way_and_keeps_the_first_reason(self):
        token = CancelToken()
        self.assertFalse(token.cancelled)
        token.cancel("page closed")
        token.cancel("later")
        self.assertTrue(token.cancelled)
        self.assertEqual(token.reason, "page closed")
        self.assertTrue(token.wait(0))

    def test_scope_sets_and_restores_the_current_token(self):
        outer, inner = CancelToken(), CancelToken()
        self.assertIsNone(current_token())
        with cancel_scope(outer):
            self.assertIs(current_token(), outer)
            with cancel_scope(inner):
                self.assertIs(current_token(), inner)
            with cancel_scope(None):
                self.assertIs(current_token(), outer)
            self.assertIs(current_token(), outer)
        self.assertIsNone(current_token())

    def test_without_a_token_nothing_is_cancelled(self):
        self.assertFalse(token_cancelled())
        self.assertFalse(cancellation_requested())

    def test_tokens_do_not_leak_between_threads(self):
        token = CancelToken()
        token.cancel()
        seen = []
        with cancel_scope(token):
            thread = threading.Thread(target=lambda: seen.append(cancellation_requested()))
            thread.start()
            thread.join()
            self.assertTrue(cancellation_requested())
        self.assertEqual(seen, [False])


class SleepTests(unittest.TestCase):
    def test_sleep_ends_early_on_a_plain_thread(self):
        token = CancelToken()
        outcome = {}

        def work():
            started = time.monotonic()
            with cancel_scope(token):
                try:
                    cancellable_sleep(30)
                except RequestCancelled:
                    outcome["elapsed"] = time.monotonic() - started

        thread = threading.Thread(target=work)
        thread.start()
        time.sleep(0.2)
        token.cancel()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertLess(outcome["elapsed"], 0.2 + PROMPT)

    def test_token_reaches_asyncio_to_thread(self):
        async def main():
            token = CancelToken()

            def work():
                cancellable_sleep(30)

            async def cancel_soon():
                await asyncio.sleep(0.2)
                token.cancel()

            with cancel_scope(token):
                task = asyncio.create_task(asyncio.to_thread(work))
            started = time.monotonic()
            await cancel_soon()
            with self.assertRaises(RequestCancelled):
                await task
            return time.monotonic() - started

        self.assertLess(asyncio.run(main()), 0.2 + PROMPT)


class _Response:
    status_code = 503
    headers: dict = {}

    def close(self):
        pass


class _Session:
    def __init__(self):
        self.calls = 0

    def request(self, method, url, *args, **kwargs):
        self.calls += 1
        return _Response()


class RequestPolicyTests(unittest.TestCase):
    def install(self, session, sleep):
        return net_policy.install(
            session,
            min_interval=lambda: 0.0,
            max_retries=lambda: 5,
            sleep=sleep,
            throttle=net_policy.HostThrottle(),
        )

    def test_cancelled_token_refuses_to_start_a_request(self):
        session = self.install(_Session(), sleep=lambda _s: None)
        token = CancelToken()
        token.cancel()
        with cancel_scope(token), self.assertRaises(RequestCancelled):
            session.request("GET", "https://api.iwara.tv/videos")
        self.assertEqual(session.calls, 0)

    def test_cancelling_during_back_off_stops_the_retries(self):
        raw = _Session()
        token = CancelToken()

        def sleep(_seconds):
            token.cancel("left the page")  # cancelled while waiting to retry
            cancellable_sleep(_seconds)

        session = self.install(raw, sleep=sleep)
        with cancel_scope(token), self.assertRaises(RequestCancelled):
            session.request("GET", "https://api.iwara.tv/videos")
        self.assertEqual(raw.calls, 1)


if __name__ == "__main__":
    unittest.main()
