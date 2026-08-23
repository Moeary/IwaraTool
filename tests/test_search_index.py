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
from app.core.search import SearchAuthor, SearchFilters, SearchVideo, normalize_oreno3d_listing
from app.core.tag_dictionary import TagDictionary
from app.ui.search_page import (
    SearchInterface,
    SearchIwaraAuthorWorker,
    SearchOrenoAuthorWorker,
    SearchOrenoLinkWorker,
    SearchWorker,
    _author_source_info,
    _author_subscription_target,
    _decode_search_history,
    _oreno3d_video_url,
    _upsert_search_history,
)


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
    def test_iwara_tag_search_keeps_remote_or_results_visible(self):
        class _FakeManager:
            def get_search_video_page(self, query_params, *, page, limit):
                self.call = (query_params, page, limit)
                return (
                    [
                        {"id": "loli-video", "title": "Loli result", "tags": [{"id": "loli"}]},
                        {"id": "hmv-video", "title": "HMV result", "tags": [{"id": "hmv"}]},
                    ],
                    97,
                    True,
                    "",
                )

        fake_manager = _FakeManager()
        results = []
        with patch("app.ui.search_page.download_manager", fake_manager):
            worker = SearchWorker(
                SearchFilters(keyword="loli,hmv", sort="date"),
                "tags",
                page=2,
                generation=1,
                replace_results=True,
                source="iwara",
            )
            worker.result_ready.connect(results.append)
            worker.run()

        self.assertEqual(fake_manager.call[0]["tags"], "loli,hmv")
        self.assertEqual([video.video_id for video in results[0].videos], ["loli-video", "hmv-video"])
        self.assertEqual(results[0].total, 97)

    def test_iwara_open_ended_page_keeps_next_navigation_available(self):
        class _FakeManager:
            def get_search_video_page(self, _query_params, *, page, limit):
                self.call = (page, limit)
                return (
                    [{"id": f"video-{index}", "title": str(index)} for index in range(limit)],
                    None,
                    True,
                    "",
                )

        fake_manager = _FakeManager()
        results = []
        with patch("app.ui.search_page.download_manager", fake_manager):
            worker = SearchWorker(
                SearchFilters(keyword="loli,hmv", sort="date"),
                "tags",
                page=2,
                generation=1,
                replace_results=True,
                source="iwara",
            )
            worker.result_ready.connect(results.append)
            worker.run()

        self.assertEqual(len(results[0].videos), 32)
        self.assertIsNone(results[0].total)
        self.assertIsNone(results[0].last_page)
        self.assertEqual(results[0].next_page, 3)

    def test_search_history_is_mru_deduplicated_and_limited(self):
        history = _upsert_search_history(
            [
                {"keyword": "old", "source": "iwara", "scope": "videos", "sort": "date"},
                {"keyword": "azur_lane", "source": "oreno3d", "scope": "tags", "sort": "date"},
            ],
            {"keyword": "AZUR_LANE", "source": "oreno3d", "scope": "tags", "sort": "date"},
            limit=2,
        )
        self.assertEqual([item["keyword"] for item in history], ["AZUR_LANE", "old"])
        self.assertEqual(_decode_search_history("not-json"), [])

    def test_oreno_source_url_survives_bridge_hydration(self):
        video = SearchVideo(
            video_id="oreno3d:movie-1",
            title="Bridge title",
            source_kind="iwara",
            source_url="https://www.iwara.tv/video/iwara-1",
            raw={"oreno3d_url": "https://oreno3d.com/movies/movie-1"},
        )
        self.assertEqual(_oreno3d_video_url(video), "https://oreno3d.com/movies/movie-1")

    def test_open_oreno_video_page_uses_source_url_without_resolving(self):
        video = SearchVideo(
            video_id="oreno3d:movie-1",
            title="Bridge title",
            source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/movie-1",
        )
        with patch("app.ui.search_page.webbrowser.open") as open_browser:
            SearchInterface._open_oreno3d_video_page(object(), video)
        open_browser.assert_called_once_with("https://oreno3d.com/movies/movie-1")

    def test_search_result_author_target_is_normalized_for_subscription(self):
        author = _author_subscription_target(
            SearchAuthor(
                author_id="user-1",
                username="@creator",
                name="Creator Display",
                avatar_url="https://img/avatar.jpg",
            )
        )
        self.assertEqual(
            author,
            ("creator", "Creator Display", "user-1", "https://img/avatar.jpg"),
        )

        video = SearchVideo(
            video_id="video-1",
            title="Example",
            author_username="",
            author_name="Oreno Creator",
            raw={"user": {"username": "oreno-creator", "id": "user-2"}},
        )
        self.assertEqual(
            _author_subscription_target(video),
            ("oreno-creator", "Oreno Creator", "user-2", ""),
        )

        bridge = normalize_oreno3d_listing(
            {
                "id": "movie-1",
                "title": "Oreno title",
                "author": "oreno-uploader",
                "oreno3d_url": "https://oreno3d.com/movies/movie-1",
            }
        )
        self.assertIsNotNone(bridge)
        self.assertIsNone(_author_subscription_target(bridge))
        bridge.raw.update(
            {
                "_iwara_metadata_loaded": True,
                "user": {"id": "iwara-1", "username": "iwara-author", "name": "Iwara Author"},
            }
        )
        self.assertEqual(
            _author_subscription_target(bridge),
            ("iwara-author", "Iwara Author", "iwara-1", ""),
        )
        bridge.raw["user"] = {}
        self.assertIsNone(_author_subscription_target(bridge))

    def test_author_context_action_persists_source_and_emits_feedback(self):
        class _FakeManager:
            def __init__(self):
                self.call = None

            def add_author_subscription(self, *args, **kwargs):
                self.call = (args, kwargs)
                return 42

        fake_manager = _FakeManager()
        # The success branch only uses the object as the InfoBar parent; an
        # ordinary object keeps this regression test independent of Qt object
        # lifetime and still exercises the real unbound method.
        page = object()
        class _FakeSignal:
            def __init__(self):
                self.calls = []

            def emit(self, *args):
                self.calls.append(args)

        class _FakeBus:
            def __init__(self):
                self.subscription_source_added = _FakeSignal()
                self.log_message = _FakeSignal()

        fake_bus = _FakeBus()
        with (
            patch("app.ui.search_page.download_manager", fake_manager),
            patch("app.ui.search_page.InfoBar.success") as success,
            patch("app.ui.search_page.signal_bus", fake_bus),
        ):
            SearchInterface._subscribe_to_author(
                page,
                ("creator", "Creator Display", "user-1", "https://img/avatar.jpg")
            )

        self.assertEqual(
            fake_manager.call,
            (
                ("creator",),
                {
                    "title": "Creator Display",
                    "remote_id": "user-1",
                    "avatar_url": "https://img/avatar.jpg",
                },
            ),
        )
        self.assertEqual(fake_bus.subscription_source_added.calls, [(42,)])
        success.assert_called_once()

    def test_author_menu_callback_keeps_target_when_qaction_emits_checked(self):
        target = ("creator", "Creator Display", "user-1", "")
        captured = []
        callback = lambda _checked=False, target=target: captured.append(target)
        # QAction.triggered emits a checked bool; preserve the closed-over
        # target instead of letting that bool replace it.
        callback(False)
        self.assertEqual(captured, [target])

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

    def test_iwara_author_worker_fetches_video_details_before_subscription(self):
        class _FakeManager:
            def __init__(self):
                self.video_ids = []

            def get_iwara_video_info(self, video_id):
                self.video_ids.append(video_id)
                return {
                    "id": video_id,
                    "title": "Iwara title",
                    "user": {"id": "author-1", "username": "iwara-author"},
                }, ""

        fake_manager = _FakeManager()
        results = []
        with patch("app.ui.search_page.download_manager", fake_manager):
            worker = SearchIwaraAuthorWorker(9, "iwara-1")
            worker.result_ready.connect(results.append)
            worker.run()

        self.assertEqual(fake_manager.video_ids, ["iwara-1"])
        self.assertEqual(results[0]["generation"], 9)
        self.assertEqual(results[0]["video_id"], "iwara-1")
        self.assertEqual(results[0]["metadata"]["user"]["username"], "iwara-author")
        self.assertEqual(results[0]["error"], "")

    def test_oreno_author_worker_uses_durable_author_resolution(self):
        class _FakeManager:
            def resolve_oreno3d_author(self, source_id, source_url, **kwargs):
                self.args = (source_id, source_url, kwargs)
                return {
                    "oreno_author_url": "https://oreno3d.com/authors/1411",
                    "oreno_author_name": "Flim13",
                    "iwara_author": {
                        "id": "iwara-user",
                        "username": "flim13",
                        "name": "Flim13",
                    },
                }

        fake_manager = _FakeManager()
        video = SearchVideo(
            video_id="oreno3d:movie-1",
            title="Bridge title",
            author_name="Flim13",
            source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/movie-1",
            raw={"oreno3d_author_url": "https://oreno3d.com/authors/1411"},
        )
        results = []
        with patch("app.ui.search_page.download_manager", fake_manager):
            worker = SearchOrenoAuthorWorker(11, video)
            worker.result_ready.connect(results.append)
            worker.run()

        self.assertEqual(fake_manager.args[0], "movie-1")
        self.assertEqual(fake_manager.args[1], video.source_url)
        self.assertEqual(fake_manager.args[2]["author_url"], "https://oreno3d.com/authors/1411")
        self.assertEqual(results[0]["result"]["iwara_author"]["username"], "flim13")
        self.assertEqual(results[0]["error"], "")

    def test_oreno_author_mapping_provides_durable_subscription_source(self):
        video = SearchVideo(
            video_id="oreno3d:movie-1",
            title="Bridge title",
            author_name="Flim13",
            source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/movie-1",
            raw={
                "oreno3d_author_url": "https://oreno3d.com/authors/1411",
                "oreno_iwara_author": {
                    "id": "iwara-user",
                    "username": "flim13",
                    "name": "Flim13",
                },
            },
        )
        self.assertEqual(
            _author_subscription_target(video),
            ("flim13", "Flim13", "iwara-user", ""),
        )
        self.assertEqual(
            _author_source_info(video),
            ("https://oreno3d.com/authors/1411", "oreno3d"),
        )

    def test_iwara_author_result_hydrates_bridge_and_subscribes_iwara_user(self):
        bridge = normalize_oreno3d_listing(
            {
                "id": "movie-2",
                "title": "Oreno title",
                "author": "oreno-uploader",
                "oreno3d_url": "https://oreno3d.com/movies/movie-2",
            }
        )
        self.assertIsNotNone(bridge)
        interface = SearchInterface.__new__(SearchInterface)
        interface._generation = 10
        interface._all_videos = [bridge]
        interface._pending_author_subscription_video_ids = {bridge.video_id}
        subscribed = []
        interface._update_video_presentation = lambda _video: None
        interface._start_image_loading = lambda: None
        interface._subscribe_to_author = subscribed.append
        interface._show_warning = lambda _message: self.fail(_message)

        SearchInterface._apply_oreno_link(
            interface,
            bridge,
            {
                "id": "iwara-2",
                "url": "https://www.iwara.tv/video/iwara-2",
                "metadata": {},
            },
        )
        SearchInterface._on_iwara_author_result(
            interface,
            {
                "generation": 10,
                "video_id": "iwara-2",
                "metadata": {
                    "id": "iwara-2",
                    "title": "Iwara title",
                    "user": {
                        "id": "iwara-author-2",
                        "username": "iwara-author-2",
                        "name": "Iwara Author",
                    },
                },
                "error": "",
            },
        )

        self.assertEqual(
            subscribed,
            [("iwara-author-2", "Iwara Author", "iwara-author-2", "")],
        )
        self.assertEqual(bridge.raw["_iwara_metadata_loaded"], True)

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
            translation_directory = os.path.join(directory, "tag_translations")
            os.makedirs(translation_directory)
            path = os.path.join(
                translation_directory,
                "loveiwara_iwara_tags_localized.json",
            )
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

    def test_tag_dictionary_expands_bundled_dictionary_to_empty_data_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = TagDictionary(data_dir=directory)
            cache_path = os.path.join(
                directory,
                "tag_translations",
                "loveiwara_iwara_tags_localized.json",
            )
            self.assertTrue(os.path.isfile(cache_path))
            with open(cache_path, "r", encoding="utf-8") as stream:
                self.assertTrue(json.load(stream))
            self.assertGreater(dictionary.count, 0)

    def test_tag_dictionary_preserves_existing_translation_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_directory = os.path.join(directory, "tag_translations")
            os.makedirs(cache_directory)
            cache_path = os.path.join(
                cache_directory,
                "loveiwara_iwara_tags_localized.json",
            )
            with open(cache_path, "w", encoding="utf-8") as stream:
                json.dump({"custom-tag": {"en": "Custom tag"}}, stream)

            dictionary = TagDictionary(data_dir=directory)

            self.assertEqual(dictionary.canonical_key("Custom tag"), "custom-tag")
            self.assertEqual(dictionary.count, 1)

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

    def test_author_page_uses_stable_author_route(self):
        session = _FakeSession(
            """
            <div class="g-main-grid">
              <article><a class="box" href="/movies/movie-1">
                <h2 class="box-h2">作品</h2>
              </a></article>
            </div>
            """
        )
        Oreno3DClient(session).fetch_author_page(
            "https://oreno3d.com/authors/1411", page=2
        )
        self.assertEqual(session.calls[0][0], "https://oreno3d.com/authors/1411")
        self.assertEqual(session.calls[0][1]["params"], {"page": 2, "sort": "latest"})


if __name__ == "__main__":
    unittest.main()
