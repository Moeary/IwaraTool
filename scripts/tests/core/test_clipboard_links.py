"""Copied Iwara links (the phone build's stand-in for share intents)."""

import unittest


class CopiedLinkTests(unittest.TestCase):
    def test_works_and_profiles(self):
        from app.presentation.clipboard_links import copied_link

        link = copied_link("look https://www.iwara.tv/video/AbCdEf123456/some-title nice")
        self.assertEqual((link.kind, link.key), ("video", "AbCdEf123456"))
        self.assertEqual(copied_link("https://iwara.tv/image/xyz789/t").kind, "image")
        self.assertEqual(copied_link("https://www.iwara.tv/profile/someone/videos").key, "someone")

    def test_other_text_is_ignored(self):
        from app.presentation.clipboard_links import copied_link

        for text in ("", "hello", "https://example.com/video/abc", "https://www.iwara.tv/", "https://www.iwara.tv/search?q=x"):
            self.assertIsNone(copied_link(text), text)


if __name__ == "__main__":
    unittest.main()
