"""Author status bar (app subscription / Iwara follow), like button, API actions and cover priority."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from app.core.api import IwaraAPI
from app.core.search import SearchVideo


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)


def _sync_api_calls(case: unittest.TestCase, client: Mock):
    """Make ``ApiCallWorker`` run its call immediately against ``client``."""

    from app.ui import home_workers

    manager = Mock()
    manager.create_worker_api_client.return_value = client
    for patcher in (
        mock.patch.object(home_workers, "download_manager", manager),
        mock.patch.object(home_workers.ApiCallWorker, "start", lambda worker: worker.run()),
    ):
        patcher.start()
        case.addCleanup(patcher.stop)


class ApiActionTests(unittest.TestCase):
    def _api(self, status=201, payload=None, token="tok"):
        api = object.__new__(IwaraAPI)
        api.token = token
        response = Mock(status_code=status)
        response.json.return_value = payload if payload is not None else {}
        response.text = "" if payload is None else "{}"
        response.headers = {"content-type": "application/json"}
        api.scraper = Mock()
        api.scraper.request.return_value = response
        return api

    def test_like_and_unlike_use_post_and_delete_on_the_item(self):
        api = self._api()
        self.assertEqual(api.set_liked("video", "abc", True), (True, ""))
        method, url = api.scraper.request.call_args.args
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/video/abc/like"))
        self.assertEqual(api.set_liked("image", "img1", False), (True, ""))
        method, url = api.scraper.request.call_args.args
        self.assertEqual(method, "DELETE")
        self.assertTrue(url.endswith("/image/img1/like"))
        self.assertIn("Authorization", api.scraper.request.call_args.kwargs["headers"])

    def test_follow_and_unfollow_target_the_followers_endpoint(self):
        api = self._api()
        self.assertTrue(api.set_following("user-1", True)[0])
        self.assertEqual(api.scraper.request.call_args.args[0], "POST")
        self.assertTrue(api.scraper.request.call_args.args[1].endswith("/user/user-1/followers"))
        api.set_following("user-1", False)
        self.assertEqual(api.scraper.request.call_args.args[0], "DELETE")

    def test_actions_need_a_login_and_an_id(self):
        api = self._api(token="")
        ok, message = api.set_liked("video", "abc", True)
        self.assertFalse(ok)
        self.assertTrue(message)
        api.scraper.request.assert_not_called()
        api = self._api()
        self.assertFalse(api.set_liked("video", "", True)[0])
        self.assertFalse(api.set_following("", True)[0])
        api.scraper.request.assert_not_called()

    def test_server_refusal_becomes_a_message(self):
        api = self._api(status=403, payload={"message": "errors.forbidden"})
        ok, message = api.set_liked("video", "abc", True)
        self.assertFalse(ok)
        self.assertIn("403", message)

    def test_network_failure_does_not_raise(self):
        api = self._api()
        api.scraper.request.side_effect = RuntimeError("connection reset")
        ok, message = api.set_following("u", True)
        self.assertFalse(ok)
        self.assertTrue(message)


class ChipStateTests(unittest.TestCase):
    def test_local_states(self):
        from app.ui.author_status import local_chip_state

        self.assertEqual(local_chip_state(None)[1], "neutral")
        self.assertEqual(local_chip_state({"id": 1, "enabled": 1})[1], "success")
        self.assertEqual(local_chip_state({"id": 1, "enabled": 0})[1], "warning")

    def test_web_states(self):
        from app.ui.author_status import web_chip_state

        self.assertEqual(web_chip_state("signed_out")[1], "neutral")
        self.assertEqual(web_chip_state("loading")[1], "neutral")
        self.assertEqual(web_chip_state("error")[1], "warning")
        followed = web_chip_state("ready", True)
        not_followed = web_chip_state("ready", False)
        self.assertEqual(followed[1], "accent")
        self.assertEqual(not_followed[1], "neutral")
        self.assertNotEqual(followed[0], not_followed[0])

    def test_usernames_are_cleaned(self):
        from app.ui.author_status import clean_username

        self.assertEqual(clean_username(" @alice/ "), "alice")


class AuthorStatusBarTests(_QtCase):
    def setUp(self):
        from app.ui import author_status

        self.module = author_status
        self.manager = Mock()
        self.manager.find_author_subscription.return_value = None
        self.manager.is_logged_in.return_value = True
        self.manager.add_author_subscription.return_value = 42
        patcher = mock.patch.object(author_status, "download_manager", self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Mock()
        self.client.get_user_profile.return_value = (
            {"user": {"id": "u1", "name": "Alice", "following": False}}, "",
        )
        self.client.set_following.return_value = (True, "")
        _sync_api_calls(self, self.client)
        for target in ("InfoBar.success", "InfoBar.error", "InfoBar.warning"):
            infobar = mock.patch(f"app.ui.author_status.{target}")
            infobar.start()
            self.addCleanup(infobar.stop)
        self.host = QWidget()
        self.bar = author_status.AuthorStatusBar(self.host)
        self.addCleanup(self._close)

    def _close(self):
        self.bar.shutdown(500)
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_signed_out_shows_both_states_and_disables_the_web_follow(self):
        self.manager.is_logged_in.return_value = False
        self.bar.set_author("alice", "Alice")
        self.assertEqual(self.bar.local_chip.tone(), "neutral")
        self.assertEqual(self.bar.web_chip.text(), self.module.web_chip_state("signed_out")[0])
        self.assertFalse(self.bar.follow_btn.isEnabled())
        self.assertTrue(self.bar.subscribe_btn.isEnabled())  # the local subscription needs no account
        self.client.get_user_profile.assert_not_called()

    def test_followed_on_the_website_is_shown_after_the_profile_loads(self):
        self.client.get_user_profile.return_value = ({"user": {"id": "u1", "following": True}}, "")
        seen = []
        self.bar.profile_loaded.connect(seen.append)
        self.bar.set_author("alice", "Alice")
        self.assertEqual(self.bar.web_chip.text(), self.module.web_chip_state("ready", True)[0])
        self.assertEqual(self.bar.web_chip.tone(), "accent")
        self.assertTrue(self.bar.follow_btn.isEnabled())
        self.assertEqual(seen[0]["id"], "u1")

    def test_local_subscription_is_reported_from_the_database(self):
        self.manager.find_author_subscription.return_value = {"id": 5, "enabled": 1, "source_key": "alice"}
        self.bar.set_author("@Alice")
        self.assertEqual(self.bar.local_chip.tone(), "success")
        self.assertEqual(self.bar.source_id, 5)
        self.manager.find_author_subscription.assert_called_with("Alice")

    def test_subscribing_adds_a_local_author_and_announces_it(self):
        from app.signal_bus import signal_bus

        added = []

        def on_added(source_id):
            added.append(source_id)

        signal_bus.subscription_source_added.connect(on_added)
        self.addCleanup(signal_bus.subscription_source_added.disconnect, on_added)
        self.bar.set_author("alice", "Alice", "u1", "https://i/avatar.jpg")
        self.manager.find_author_subscription.return_value = {"id": 42, "enabled": 1}
        self.bar.subscribe_btn.click()
        kwargs = self.manager.add_author_subscription.call_args.kwargs
        self.assertEqual(self.manager.add_author_subscription.call_args.args, ("alice",))
        self.assertEqual((kwargs["title"], kwargs["remote_id"], kwargs["avatar_url"]), ("Alice", "u1", "https://i/avatar.jpg"))
        self.assertEqual(added, [42])
        self.assertEqual(self.bar.local_chip.tone(), "success")

    def test_removing_the_subscription_asks_first_and_notifies_other_pages(self):
        from app.signal_bus import signal_bus

        changed = []

        def on_changed():
            changed.append(True)

        signal_bus.subscription_sources_changed.connect(on_changed)
        self.addCleanup(signal_bus.subscription_sources_changed.disconnect, on_changed)
        self.manager.find_author_subscription.return_value = {"id": 5, "enabled": 1}
        self.bar.set_author("alice")
        with mock.patch.object(self.module, "show_fluent_confirmation", return_value=False):
            self.bar.subscribe_btn.click()
        self.manager.remove_subscription_source.assert_not_called()
        with mock.patch.object(self.module, "show_fluent_confirmation", return_value=True):
            self.manager.find_author_subscription.return_value = None
            self.bar.subscribe_btn.click()
        self.manager.remove_subscription_source.assert_called_once_with(5)
        self.assertEqual(changed, [True])
        self.assertEqual(self.bar.local_chip.tone(), "neutral")

    def test_following_toggles_on_the_website(self):
        self.bar.set_author("alice", "Alice")
        self.bar.follow_btn.click()
        self.client.set_following.assert_called_once_with("u1", True)
        self.assertEqual(self.bar.web_chip.tone(), "accent")
        self.client.set_following.reset_mock()
        self.bar.follow_btn.click()
        self.client.set_following.assert_called_once_with("u1", False)
        self.assertEqual(self.bar.web_chip.tone(), "neutral")

    def test_a_refused_follow_keeps_the_old_state(self):
        self.client.set_following.return_value = (False, "HTTP 403")
        self.bar.set_author("alice")
        self.bar.follow_btn.click()
        self.assertEqual(self.bar.web_chip.tone(), "neutral")
        self.module.InfoBar.error.assert_called()

    def test_profile_failure_is_a_warning_not_a_crash(self):
        self.client.get_user_profile.return_value = (None, "HTTP 404")
        self.bar.set_author("ghost")
        self.assertEqual(self.bar.web_chip.tone(), "warning")
        self.assertFalse(self.bar.follow_btn.isEnabled())

    def test_a_late_profile_for_a_previous_author_is_ignored(self):
        self.bar.set_author("alice")
        stale = self.bar._token - 1
        self.bar._on_profile(stale, ({"user": {"id": "x", "following": True}}, ""), "")
        self.assertEqual(self.bar.web_chip.tone(), "neutral")

    def test_a_late_follow_reply_for_a_previous_author_does_not_touch_the_next(self):
        from app.ui import home_workers

        held = []
        with mock.patch.object(home_workers.ApiCallWorker, "start", lambda worker: held.append(worker)):
            self.bar.set_author("alice", "Alice")
            held.pop().run()  # alice's profile
            self.bar.follow_btn.click()
            follow_for_alice = held.pop()
            self.client.get_user_profile.return_value = ({"user": {"id": "u2", "following": False}}, "")
            self.bar.set_author("bob", "Bob")
            held.pop().run()  # bob's profile
            follow_for_alice.run()  # alice's follow reply arrives late
        self.client.set_following.assert_called_once_with("u1", True)
        self.assertFalse(self.bar._following)
        self.assertEqual(self.bar.web_chip.tone(), "neutral")
        self.assertEqual(self.bar.username, "bob")

    def test_signing_out_re_reads_the_follow_state(self):
        from app.signal_bus import signal_bus

        self.client.get_user_profile.return_value = ({"user": {"id": "u1", "following": True}}, "")
        self.bar.set_author("alice")
        self.assertEqual(self.bar.web_chip.tone(), "accent")
        self.manager.is_logged_in.return_value = False
        signal_bus.login_state_changed.emit(False)
        self.assertEqual(self.bar.web_chip.text(), self.module.web_chip_state("signed_out")[0])
        self.assertFalse(self.bar.follow_btn.isEnabled())


class DetailLikeTests(_QtCase):
    def setUp(self):
        from app.ui import media_detail
        from app.ui.home_workers import CoverFetcher

        self.module = media_detail
        self.manager = Mock()
        self.manager.is_logged_in.return_value = True
        patcher = mock.patch.object(media_detail, "download_manager", self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Mock()
        self.client.set_liked.return_value = (True, "")
        _sync_api_calls(self, self.client)
        for target in ("success", "error", "warning"):
            infobar = mock.patch.object(media_detail.InfoBar, target)
            self.infobars = getattr(self, "infobars", {})
            self.infobars[target] = infobar.start()
            self.addCleanup(infobar.stop)
        status = mock.patch("app.ui.author_status.download_manager", Mock(find_author_subscription=Mock(return_value=None)))
        status.start()
        self.addCleanup(status.stop)
        self.view = media_detail.DetailView(CoverFetcher())
        self.view._video = SearchVideo(video_id="abc", title="t", likes=5)
        self.view._item_id = "abc"
        self.view._sync_like()
        self.view._like_btn.setEnabled(True)
        self.addCleanup(self._close)

    def _close(self):
        self.view.shutdown(500)
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_like_sends_the_request_and_bumps_the_counter(self):
        self.view._like_btn.click()
        self.client.set_liked.assert_called_once_with("video", "abc", True)
        self.assertTrue(self.view._liked)
        self.assertEqual(self.view._video.likes, 6)
        self.assertTrue(self.view._like_btn.isChecked())
        self.assertIn("6", self.view._like_btn.text())

    def test_clicking_again_removes_the_like(self):
        self.view._liked = True
        self.view._sync_like()
        self.view._like_btn.click()
        self.client.set_liked.assert_called_once_with("video", "abc", False)
        self.assertFalse(self.view._liked)
        self.assertEqual(self.view._video.likes, 4)

    def test_signed_out_users_are_asked_to_sign_in(self):
        self.manager.is_logged_in.return_value = False
        self.view._like_btn.click()
        self.client.set_liked.assert_not_called()
        self.infobars["warning"].assert_called_once()
        self.assertFalse(self.view._like_btn.isChecked())

    def test_a_refused_like_leaves_everything_as_it_was(self):
        self.client.set_liked.return_value = (False, "HTTP 403")
        self.view._like_btn.click()
        self.assertFalse(self.view._liked)
        self.assertEqual(self.view._video.likes, 5)
        self.infobars["error"].assert_called_once()
        self.assertFalse(self.view._like_btn.isChecked())

    def test_the_account_state_from_the_post_is_shown(self):
        info = {"id": "abc", "title": "t", "liked": True, "numLikes": 7, "user": {"username": "alice", "name": "Alice"}}
        self.view._token = 1
        self.view._on_info(1, info, "")
        self.assertTrue(self.view._liked)
        self.assertTrue(self.view._like_btn.isChecked())

    def test_a_like_answered_after_navigating_away_is_ignored(self):
        self.view._token = 4
        self.view._like_busy = True
        self.view._on_like_done(3, True, (True, ""), "")
        self.assertFalse(self.view._liked)
        self.assertEqual(self.view._video.likes, 5)

    def test_author_page_button_opens_the_in_app_author_page(self):
        from app.signal_bus import signal_bus

        requested = []

        def on_request(target):
            requested.append(target)

        signal_bus.author_page_requested.connect(on_request)
        self.addCleanup(signal_bus.author_page_requested.disconnect, on_request)
        self.view._author_target = ("alice", "Alice", "u1", "")
        self.view._page_btn.click()
        self.assertEqual(requested, [("alice", "Alice", "u1", "")])


class _FakeCoverWorker(QObject):
    thumbnail_ready = Signal(str, str)
    finished = Signal()

    def __init__(self, batch):
        super().__init__()
        self.batch = batch
        self.started = False

    def start(self):
        self.started = True

    def requestInterruption(self):
        pass


class CoverPriorityTests(_QtCase):
    def setUp(self):
        from app.ui.subscription_views import CoverLoader

        self.workers: list[_FakeCoverWorker] = []
        self.loader = CoverLoader()
        self.loader._make_worker = lambda batch: self.workers.append(_FakeCoverWorker(list(batch))) or self.workers[-1]
        self.addCleanup(self.loader.deleteLater)

    def _finish(self, worker, delivered=()):
        for video_id in delivered:
            worker.thumbnail_ready.emit(video_id, f"/tmp/{video_id}.jpg")
        worker.finished.emit()

    def test_what_the_user_opened_jumps_ahead_of_background_covers(self):
        self.loader.request([(f"b{i}", "u") for i in range(20)])  # the overview's strips
        self.assertEqual(len(self.workers), 1)
        self.loader.request([("u1", "u"), ("u2", "u")], urgent=True)
        self._finish(self.workers[0], [v for v, _ in self.workers[0].batch])
        second = [video_id for video_id, _url in self.workers[1].batch]
        self.assertEqual(second[:2], ["u1", "u2"])

    def test_a_queued_background_cover_is_promoted_when_asked_for_urgently(self):
        self.loader.request([(f"b{i}", "u") for i in range(20)])
        self.loader.request([("b19", "u")], urgent=True)
        self._finish(self.workers[0], [v for v, _ in self.workers[0].batch])
        self.assertEqual(self.workers[1].batch[0][0], "b19")

    def test_failed_covers_are_asked_for_again_next_time(self):
        self.loader.request([("x", "u"), ("y", "u")], urgent=True)
        self._finish(self.workers[0], ["x"])  # y failed
        self.loader.request([("x", "u"), ("y", "u")], urgent=True)
        self.assertEqual([v for v, _ in self.workers[1].batch], ["y"])  # only the failed one

    def test_leaving_the_overview_drops_its_queued_covers(self):
        self.loader.request([(f"b{i}", "u") for i in range(30)])
        self.loader.request([("u1", "u")], urgent=True)
        self.loader.drop_background()
        self.assertEqual(self.loader.pending(), 16 + 1)  # the running batch plus the urgent one
        # a dropped cover can be requested again later
        self.loader.request([("b25", "u")])
        self.assertEqual(self.loader.pending(), 16 + 2)

    def test_duplicate_requests_are_ignored(self):
        self.loader.request([("a", "u")])
        self.loader.request([("a", "u")])
        self.assertEqual(self.loader.pending(), 1)

    def test_covers_that_arrive_are_reported(self):
        seen = []
        self.loader.cover_ready.connect(lambda video_id, path: seen.append((video_id, path)))
        self.loader.request([("a", "u")])
        self._finish(self.workers[0], ["a"])
        self.assertEqual(seen, [("a", "/tmp/a.jpg")])


if __name__ == "__main__":
    unittest.main()
