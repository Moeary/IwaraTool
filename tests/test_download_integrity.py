from __future__ import annotations

import unittest

from app.core.download_integrity import (
    classify_416,
    incomplete_reason,
    parse_content_range,
    resume_offset_mismatch,
)


class ContentRangeTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_content_range("bytes 100-199/1000"), (100, 199, 1000))
        self.assertEqual(parse_content_range("bytes */1000"), (None, None, 1000))
        self.assertEqual(parse_content_range("bytes 0-9/*"), (0, 9, None))
        self.assertEqual(parse_content_range("garbage"), (None, None, None))
        self.assertEqual(parse_content_range(None), (None, None, None))

    def test_resume_offset(self):
        self.assertEqual(resume_offset_mismatch({"Content-Range": "bytes 500-999/1000"}, 500), "")
        self.assertIn("expected 500", resume_offset_mismatch({"Content-Range": "bytes 0-999/1000"}, 500))
        self.assertEqual(resume_offset_mismatch({}, 500), "")

    def test_416_classification(self):
        self.assertEqual(classify_416({"Content-Range": "bytes */1000"}, 1000), "complete")
        self.assertEqual(classify_416({"Content-Range": "bytes */900"}, 1000), "corrupt")
        self.assertEqual(classify_416({}, 1000), "complete")


class IncompleteTests(unittest.TestCase):
    def test_exact_size_is_fine(self):
        self.assertEqual(incomplete_reason(1000, 1000, {}), "")

    def test_short_body_is_reported(self):
        self.assertIn("400 of 1000", incomplete_reason(400, 1000, {}))

    def test_unknown_total_and_encoded_bodies_are_skipped(self):
        self.assertEqual(incomplete_reason(400, 0, {}), "")
        self.assertEqual(incomplete_reason(400, 1000, {"Content-Encoding": "gzip"}), "")
        self.assertIn("received", incomplete_reason(400, 1000, {"Content-Encoding": "identity"}))


if __name__ == "__main__":
    unittest.main()
