import os
import tempfile
import unittest
from datetime import date

from app.core.api import IwaraAPI
from app.core.image_cache import SearchImageCache
from app.core.search import (
    SearchFilters,
    build_video_query_params,
    filter_videos,
    normalize_author,
    normalize_video,
    split_search_terms,
)


class SearchCoreTests(unittest.TestCase):
    def test_normalize_video_supports_current_api_shape(self):
        video = normalize_video(
            {
                "id": "video-1",
                "title": "A searchable title",
                "slug": "a-searchable-title",
                "user": {"id": "user-1", "username": "Creator", "name": "Creator Name"},
                "createdAt": "2025-05-20T12:30:00.000Z",
                "numLikes": 12,
                "numViews": 345,
                "numComments": 3,
                "rating": {"name": "general"},
                "tags": [{"name": "3D"}, {"name": "dance"}],
                "file": {"id": "file-1", "duration": 91.5},
                "fileUrl": "https://cdn.example.test/file.mp4",
                "thumbnail": 2,
            }
        )
        self.assertIsNotNone(video)
        assert video is not None
        self.assertEqual(video.video_id, "video-1")
        self.assertEqual(video.author_username, "Creator")
        self.assertEqual(video.likes, 12)
        self.assertEqual(video.views, 345)
        self.assertEqual(video.duration, 91.5)
        self.assertEqual(video.tags, ("3D", "dance"))
        self.assertIn("thumbnail-02.jpg", video.thumbnail_url)

    def test_filter_videos_applies_keyword_tags_ranges_and_dates(self):
        first = normalize_video(
            {
                "id": "one",
                "title": "Blue dancer",
                "user": {"username": "Alice"},
                "createdAt": "2025-05-20T00:00:00Z",
                "numViews": 200,
                "numLikes": 20,
                "file": {"duration": 80},
                "tags": ["3d", "dance"],
            }
        )
        second = normalize_video(
            {
                "id": "two",
                "title": "Blue landscape",
                "user": {"username": "Bob"},
                "createdAt": "2024-01-01T00:00:00Z",
                "numViews": 20,
                "numLikes": 1,
                "file": {"duration": 20},
                "tags": ["2d"],
            }
        )
        assert first is not None and second is not None
        result = filter_videos(
            [first, second],
            SearchFilters(
                keyword="blue",
                include_tags=("3d",),
                exclude_tags=("gore",),
                min_views=100,
                min_likes=10,
                min_duration=60,
                date_from=date(2025, 1, 1),
            ),
        )
        self.assertEqual([video.video_id for video in result], ["one"])

    def test_query_params_keep_server_side_options_small_and_predictable(self):
        params = build_video_query_params(
            SearchFilters(
                keyword="blue dancer",
                sort="views",
                rating="general",
                include_tags=("3d", "dance"),
                page_size=48,
            ),
            page=3,
        )
        self.assertEqual(params["page"], "3")
        self.assertEqual(params["limit"], "48")
        self.assertEqual(params["sort"], "views")
        self.assertEqual(params["rating"], "general")
        self.assertEqual(params["tags"], "3d,dance")
        self.assertEqual(params["q"], "blue dancer")

    def test_author_normalization_accepts_profile_response(self):
        author = normalize_author(
            {
                "user": {
                    "id": "u1",
                    "username": "creator",
                    "name": "Creator",
                    "avatar": {"id": "avatar-1", "name": "avatar.jpg"},
                    "videoCount": 7,
                },
                "profile": {"bio": "A short bio"},
            }
        )
        self.assertIsNotNone(author)
        assert author is not None
        self.assertEqual(author.username, "creator")
        self.assertEqual(author.video_count, 7)
        self.assertIn("avatar-1", author.avatar_url)

    def test_api_page_returns_count_and_has_more(self):
        api = object.__new__(IwaraAPI)
        captured = {}

        def fake_get_json(url, **kwargs):
            captured.update(kwargs)
            return {"results": [{"id": "v1"}], "count": 65}

        api._get_json = fake_get_json
        results, total, has_more, error = api.get_videos_page({"sort": "views"}, page=1, limit=32)
        self.assertEqual(results, [{"id": "v1"}])
        self.assertEqual(total, 65)
        self.assertTrue(has_more)
        self.assertEqual(error, "")
        self.assertEqual(captured["params"], {"sort": "views", "page": "1", "limit": "32"})


class SearchImageCacheTests(unittest.TestCase):
    class _Response:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def iter_content(self, chunk_size=65536):
            del chunk_size
            return [b"fake-image"]

        def close(self):
            pass

    class _Session:
        def __init__(self):
            self.calls = 0

        def get(self, *args, **kwargs):
            del args, kwargs
            self.calls += 1
            return SearchImageCacheTests._Response()

    class _OctetResponse:
        status_code = 200
        headers = {"content-type": "application/octet-stream"}

        def iter_content(self, chunk_size=65536):
            del chunk_size
            return [b"\x89PNG\r\n\x1a\nvalid-image"]

        def close(self):
            pass

    class _OctetSession:
        def get(self, *args, **kwargs):
            del args, kwargs
            return SearchImageCacheTests._OctetResponse()

    def test_cache_is_atomic_and_second_read_is_local(self):
        with tempfile.TemporaryDirectory() as root:
            cache = SearchImageCache(root)
            session = self._Session()
            url = "https://images.example.test/cover.jpg?size=large"
            first = cache.get_or_fetch("video", "unsafe/id", url, session=session)
            second = cache.get_or_fetch("video", "unsafe/id", url, session=session)
            self.assertTrue(first)
            self.assertEqual(first, second)
            self.assertTrue(os.path.isfile(first))
            self.assertEqual(session.calls, 1)
            self.assertIn("video_unsafe_id_", os.path.basename(first))

    def test_split_terms_deduplicates_case_and_hash_prefix(self):
        self.assertEqual(split_search_terms("#3D, 3d；dance|vr"), ("3D", "dance", "vr"))

    def test_cache_accepts_octet_stream_image_from_oreno3d(self):
        with tempfile.TemporaryDirectory() as root:
            cache = SearchImageCache(root)
            path = cache.get_or_fetch(
                "video",
                "oreno3d:movie-1",
                "https://oreno3d.example/storage/thumbnails_small/abc",
                session=self._OctetSession(),
            )
            self.assertTrue(path)
            self.assertTrue(os.path.isfile(path))

    def test_force_cache_refresh_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as root:
            cache = SearchImageCache(root)
            session = self._Session()
            url = "https://images.example.test/cover.jpg"
            first = cache.get_or_fetch("video", "video-1", url, session=session)
            second = cache.get_or_fetch("video", "video-1", url, session=session, force=True)
            self.assertEqual(first, second)
            self.assertEqual(session.calls, 2)


if __name__ == "__main__":
    unittest.main()
