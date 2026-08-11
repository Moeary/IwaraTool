import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.core.oreno3d import (
    Oreno3DClient,
    parse_detail_page,
    parse_listing_page,
)
from app.core.search import SearchVideo, normalize_oreno3d_listing
from app.core.tag_dictionary import TagDictionary
from app.ui.search_page import SearchInterface, SearchOrenoLinkWorker


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, html: str):
        self.html = html
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.html)


class SearchOnlineTests(unittest.TestCase):
    def test_oreno_worker_uses_iwara_id_url_and_api_metadata(self):
        class _FakeManager:
            def resolve_oreno3d_video_id(self, source_id, source_url):
                self.assert_source = (source_id, source_url)
                return "iwara-1"

            def get_iwara_video_info(self, video_id):
                return {"id": video_id, "title": "Iwara title"}, ""

        fake_manager = _FakeManager()
        video = SearchVideo(
            video_id="oreno3d:movie-1",
            title="Bridge title",
            source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/movie-1",
            downloadable=False,
        )
        results = []
        with patch("app.ui.search_page.download_manager", fake_manager):
            worker = SearchOrenoLinkWorker(7, [video])
            worker.result_ready.connect(results.append)
            worker.run()

        link = results[0]["links"]["oreno3d:movie-1"]
        self.assertEqual(link["id"], "iwara-1")
        self.assertEqual(link["url"], "https://www.iwara.tv/video/iwara-1")
        self.assertEqual(link["metadata"]["title"], "Iwara title")

    def test_oreno_worker_can_emit_ids_without_waiting_for_metadata(self):
        class _FakeManager:
            def __init__(self):
                self.metadata_calls = 0

            def resolve_oreno3d_video_id(self, source_id, source_url, **kwargs):
                return f"iwara-{source_id}"

            def get_iwara_video_info(self, video_id):
                self.metadata_calls += 1
                return {"id": video_id}, ""

        fake_manager = _FakeManager()
        videos = [
            SearchVideo(
                video_id=f"oreno3d:movie-{index}",
                title=f"Bridge title {index}",
                source_kind="oreno3d",
                source_url=f"https://oreno3d.com/movies/movie-{index}",
                downloadable=False,
            )
            for index in range(2)
        ]
        items = []
        with patch("app.ui.search_page.download_manager", fake_manager):
            worker = SearchOrenoLinkWorker(
                8,
                videos,
                concurrency=2,
                hydrate_metadata=False,
            )
            worker.item_ready.connect(items.append)
            worker.run()

        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["stage"] == "id" for item in items))
        self.assertEqual(fake_manager.metadata_calls, 0)
        self.assertTrue(
            all(item["link"]["url"].startswith("https://www.iwara.tv/video/") for item in items)
        )

    def test_oreno_hydration_keeps_bridge_thumbnail_cache(self):
        interface = SearchInterface.__new__(SearchInterface)
        interface._image_path_by_key = {"video:oreno3d:movie-1": "cached-cover"}
        video = SearchVideo(
            video_id="oreno3d:movie-1",
            title="Bridge title",
            source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/movie-1",
            thumbnail_url="https://oreno3d.com/images/bridge.jpg",
        )

        changed = interface._apply_oreno_link(
            video,
            {
                "id": "iwara-1",
                "url": "https://www.iwara.tv/video/iwara-1",
                "metadata": {
                    "id": "iwara-1",
                    "title": "Iwara title",
                    "thumbnail": "https://i.iwara.tv/image/original/iwara.jpg",
                },
            },
        )

        self.assertTrue(changed)
        self.assertEqual(video.thumbnail_url, "https://oreno3d.com/images/bridge.jpg")
        self.assertIn("video:oreno3d:movie-1", interface._image_path_by_key)

    def test_tag_dictionary_suggests_and_canonicalizes_localized_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "iwara_tags.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "genshin": {
                            "en": "Genshin Impact",
                            "zh-CN": "原神",
                            "ja": "原神",
                        }
                    },
                    stream,
                    ensure_ascii=False,
                )
            dictionary = TagDictionary(data_dir=directory)
            self.assertEqual(dictionary.canonical_key("原神"), "genshin")
            self.assertEqual(dictionary.canonical_key("Genshin Impact"), "genshin")
            self.assertEqual(dictionary.entry_for("genshin").zh, "原神")
            self.assertEqual(dictionary.suggest("原", limit=1)[0].key, "genshin")

    def test_online_search_forwards_keyword_and_page_to_oreno3d(self):
        session = _FakeSession(
            """
            <div class="g-main-grid">
              <article>
                <a class="box" href="/movies/movie-1">
                  <h2 class="box-h2">原神舞蹈</h2>
                  <img class="main-thumbnail" src="/images/one.jpg">
                  <div class="box-text1"><span class="box-text-in">测试作者</span></div>
                  <span class="figure-text-in">1.2K</span>
                  <span class="figure-text-in">34</span>
                  <div class="box-text2"><i class="material-icons">local_offer</i>
                    <a href="/tags/genshin">genshin</a>
                    <a href="/tags/genshin-cn">原神</a>
                  </div>
                </a>
              </article>
            </div>
            <ul class="pagination"><li><a class="page-link" href="/search?page=4">4</a></li></ul>
            """
        )
        items, last_page = Oreno3DClient(session).fetch_search_page(
            "原神", page=2, sort="latest"
        )

        self.assertEqual(session.calls[0][0], "https://oreno3d.com/search")
        self.assertEqual(
            session.calls[0][1]["params"],
            {"keyword": "原神", "page": 2, "sort": "latest"},
        )
        self.assertEqual(last_page, 4)
        self.assertEqual(items[0].tags, ("genshin", "原神"))
        normalized = normalize_oreno3d_listing(items[0])
        self.assertEqual(normalized.source_kind, "oreno3d")
        self.assertEqual(normalized.video_id, "oreno3d:movie-1")
        self.assertFalse(normalized.downloadable)


class Oreno3DParserTests(unittest.TestCase):
    def test_listing_and_detail_parser_extracts_search_metadata(self):
        listing_html = """
        <div class="g-main-grid">
          <article>
            <a class="box" href="/movies/movie-1">
              <h2 class="box-h2">原神舞蹈</h2>
              <img class="main-thumbnail" src="/images/one.jpg">
              <div class="box-text1"><span class="box-text-in">测试作者</span></div>
              <span class="figure-text-in">1.2K</span>
              <span class="figure-text-in">34</span>
            </a>
          </article>
        </div>
        <ul class="pagination"><li><a class="page-link" href="/?page=3">3</a></li></ul>
        """
        listings, last_page = parse_listing_page(listing_html, page=1)
        self.assertEqual(last_page, 3)
        self.assertEqual(listings[0].source_id, "movie-1")
        self.assertEqual(listings[0].thumbnail_url, "https://oreno3d.com/images/one.jpg")
        self.assertEqual(listings[0].view_count, 1200)

        detail_html = """
        <h1 class="video-h1">原神舞蹈</h1>
        <meta property="og:image" content="/images/detail.jpg">
        <ul class="video-views">
          <li class="f-label-in"><i class="material-icons">remove_red_eye</i><span class="video-text">1.2K</span></li>
          <li class="f-label-in"><i class="material-icons">favorite</i><span class="video-text">34</span></li>
          <li class="f-label-in"><i class="material-icons">event</i><span class="video-text">2025-01-02</span><span class="video-text">12:00:00</span></li>
        </ul>
        <section class="video-section-tag">
          <a href="/authors/creator-1"><span class="video-center">测试作者</span></a>
          <a href="/tags/genshin"><span class="tag-text">原神</span></a>
          <a href="/origins/game-1"><span class="tag-text">Genshin Impact</span></a>
          <a href="/characters/char-1"><span class="tag-text">旅行者</span></a>
        </section>
        <a class="video-watch-btn2" href="https://www.iwara.tv/video/iwara-1">Watch</a>
        <blockquote class="video-information-comment">说明文字</blockquote>
        """
        detail = parse_detail_page(
            detail_html,
            source_id="movie-1",
            oreno3d_url="https://oreno3d.com/movies/movie-1",
        )
        self.assertEqual(detail.external_video_url, "https://www.iwara.tv/video/iwara-1")
        self.assertEqual(detail.author.name, "测试作者")
        self.assertEqual([item.name for item in detail.tags], ["原神"])
        self.assertEqual(detail.published_at, "2025-01-02 12:00:00")


if __name__ == "__main__":
    unittest.main()
