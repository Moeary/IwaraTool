"""Subscription covers without a URL (list stubs) still load: the core looks the video up."""
from __future__ import annotations

import asyncio
import importlib.util
import unittest

HAS_FLET = importlib.util.find_spec("flet") is not None


class _Manager:
    def __init__(self):
        self.calls = []

    def cache_subscription_thumbnail(self, video_id, url, *, api_client=None):
        self.calls.append(("subscription", video_id, url))
        return f"/cache/{video_id}.jpg"

    def cache_search_image(self, kind, key, url, *, api_client=None):
        self.calls.append((kind, key, url))
        return f"/cache/{key}.jpg"


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class SubscriptionCoverTests(unittest.TestCase):
    def test_a_stub_without_url_is_resolved_by_the_core(self):
        from app.ui_flet.services.images import SUBSCRIPTION, ImageService

        manager = _Manager()
        service = ImageService(manager, workers=1)
        try:
            self.assertEqual(asyncio.run(service.get(SUBSCRIPTION, "abc", "")), "/cache/abc.jpg")
            self.assertEqual(asyncio.run(service.get("video", "xyz", "")), "")  # other kinds still need a URL
        finally:
            service.close()
        self.assertEqual(manager.calls, [("subscription", "abc", "")])

    def test_subscription_cards_use_the_subscription_cache(self):
        from app.core.search.models import SearchVideo
        from app.ui_flet.services.images import SUBSCRIPTION
        from app.ui_flet.stores.subscriptions import subscription_card

        card = subscription_card(SearchVideo(video_id="abc", title="t", raw={"_thumbnail_path": "/x.jpg"}))
        self.assertEqual((card.cover_kind, card.cover_key, card.cover_local), (SUBSCRIPTION, "abc", "/x.jpg"))


if __name__ == "__main__":
    unittest.main()
