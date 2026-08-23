from __future__ import annotations

import unittest

from app.core.rules import default_rule_payload
from app.core.subscription_automation import matches_rule_video


class SubscriptionAutomationRuleTests(unittest.TestCase):
    def setUp(self):
        self.video = {
            "title": "MMD Dance Showcase",
            "numLikes": 120,
            "numViews": 5000,
            "createdAt": "2026-08-01T12:00:00.000Z",
            "tags": [{"id": "mmd"}, {"id": "dance"}],
        }

    def test_rule_accepts_matching_metadata(self):
        payload = default_rule_payload()
        payload.update(
            {
                "filter_enabled": True,
                "filter_min_likes_enabled": True,
                "filter_min_likes": 100,
                "filter_include_tags_enabled": True,
                "filter_include_tags": "mmd",
                "title_include": "showcase",
            }
        )
        passed, reason = matches_rule_video(self.video, payload)
        self.assertTrue(passed, reason)

    def test_rule_rejects_excluded_title(self):
        payload = default_rule_payload()
        payload["title_exclude"] = "dance"
        passed, reason = matches_rule_video(self.video, payload)
        self.assertFalse(passed)
        self.assertTrue(any(token in reason for token in ("exclude", "排除", "除外")))

    def test_rule_rejects_low_metrics_and_wrong_date(self):
        payload = default_rule_payload()
        payload.update(
            {
                "filter_enabled": True,
                "filter_min_views_enabled": True,
                "filter_min_views": 6000,
            }
        )
        passed, reason = matches_rule_video(self.video, payload)
        self.assertFalse(passed)
        self.assertIn("views", reason)


if __name__ == "__main__":
    unittest.main()
