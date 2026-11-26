"""aria2 on a NAS / server: remote paths, history rows, status and opening."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.config import app_config
from app.core.download import locations
from app.core.download.history_actions import HistoryActionsMixin
from app.core.download.manager import DownloadManager
from app.core.history import DownloadHistory
from app.core.models import DownloadTask, TaskStatus
from app.core.playback import video_player


def _cfg(**overrides):
    values = dict(
        aria2_rpc_enabled=True,
        aria2_remote_enabled=True,
        aria2_remote_dir="/volume1/iwara",
        aria2_remote_mount_dir="",
        aria2_remote_http_base="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _ConfigPatch:
    """Set app_config values for one test and put the old ones back."""

    KEYS = (
        "aria2_rpc_enabled", "aria2_rpc_url", "aria2_remote_enabled", "aria2_remote_dir",
        "aria2_remote_mount_dir", "aria2_remote_http_base", "skip_existing_files",
    )

    def __init__(self, case: unittest.TestCase, **values):
        saved = {key: getattr(app_config, key) for key in self.KEYS}
        case.addCleanup(lambda: [setattr(app_config, k, v) for k, v in saved.items()])
        for key, value in values.items():
            setattr(app_config, key, value)


class LocationTests(unittest.TestCase):
    def test_remote_mode_needs_rpc_switch_and_folder(self):
        self.assertTrue(locations.remote_mode_enabled(_cfg()))
        self.assertFalse(locations.remote_mode_enabled(_cfg(aria2_rpc_enabled=False)))
        self.assertFalse(locations.remote_mode_enabled(_cfg(aria2_remote_enabled=False)))
        self.assertFalse(locations.remote_mode_enabled(_cfg(aria2_remote_dir="  ")))

    def test_join_keeps_the_hosts_separator(self):
        self.assertEqual(
            locations.join_remote_path("/volume1/iwara", os.path.join("bob", "2026_title_abc.mp4")),
            "/volume1/iwara/bob/2026_title_abc.mp4",
        )
        self.assertEqual(
            locations.join_remote_path("D:\\iwara", "bob/x.mp4"),
            "D:\\iwara\\bob\\x.mp4",
        )

    def test_relative_path_and_mapping(self):
        cfg = _cfg(aria2_remote_mount_dir=os.path.join("mnt", "nas"))
        self.assertEqual(locations.remote_relative_path("/volume1/iwara/bob/a b.mp4", "/volume1/iwara/"), "bob/a b.mp4")
        self.assertIsNone(locations.remote_relative_path("/volume2/other.mp4", "/volume1/iwara"))
        self.assertIsNone(locations.remote_relative_path("/volume1/iwara2/x.mp4", "/volume1/iwara"))
        self.assertEqual(locations.remote_relative_path("D:\\Iwara\\bob\\x.mp4", "d:\\iwara"), "bob/x.mp4")
        self.assertEqual(
            locations.mapped_local_path("/volume1/iwara/bob/a.mp4", cfg),
            os.path.join("mnt", "nas", "bob", "a.mp4"),
        )
        self.assertEqual(locations.mapped_local_path("/volume1/iwara/bob/a.mp4", _cfg()), "")

    def test_stream_url_is_quoted(self):
        cfg = _cfg(aria2_remote_http_base="http://nas:8080/iwara/")
        self.assertEqual(
            locations.remote_stream_url("/volume1/iwara/作者/a b#1.mp4", cfg),
            "http://nas:8080/iwara/%E4%BD%9C%E8%80%85/a%20b%231.mp4",
        )
        self.assertEqual(locations.remote_stream_url("/elsewhere/a.mp4", cfg), "")
        self.assertEqual(locations.remote_stream_url("/volume1/iwara/a.mp4", _cfg(aria2_remote_http_base="ftp://x")), "")

    def test_resolve_never_calls_an_unmounted_nas_copy_local(self):
        with tempfile.TemporaryDirectory() as root:
            local = os.path.join(root, "local.mp4")
            open(local, "wb").close()
            loc = locations.resolve_record_location({"file_path": local}, _cfg())
            self.assertFalse(loc.is_remote)
            self.assertEqual(loc.local_path, local)

            record = {"storage": "remote", "remote_path": "/volume1/iwara/bob/a.mp4", "file_path": local}
            loc = locations.resolve_record_location(record, _cfg())
            self.assertTrue(loc.is_remote)
            self.assertEqual(loc.local_path, "")  # file_path is ignored for a remote row
            self.assertFalse(loc.accessible)

            os.makedirs(os.path.join(root, "bob"))
            mounted = os.path.join(root, "bob", "a.mp4")
            open(mounted, "wb").close()
            loc = locations.resolve_record_location(record, _cfg(aria2_remote_mount_dir=root))
            self.assertEqual(loc.local_path, mounted)
            self.assertEqual(loc.display_path, "/volume1/iwara/bob/a.mp4")


class RemoteHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iwaratool-remote-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.history = DownloadHistory(os.path.join(self.tmp, "history.db"))

    def test_remote_rows_store_their_path_and_survive_folder_sync(self):
        self.history.upsert_downloaded({
            "video_id": "remote01", "title": "On NAS", "file_path": "/should/be/dropped.mp4",
            "storage": "remote", "remote_path": "/volume1/iwara/a.mp4",
        })
        self.history.upsert_downloaded({"video_id": "gone01", "file_path": os.path.join(self.tmp, "missing.mp4")})
        record = self.history.get_record("remote01")
        self.assertEqual(record["storage"], "remote")
        self.assertEqual(record["remote_path"], "/volume1/iwara/a.mp4")

        stats = self.history.sync_with_download_folder(self.tmp)
        self.assertIsNotNone(self.history.get_record("remote01"))
        self.assertIsNone(self.history.get_record("gone01"))
        self.assertEqual(stats["removed"], 1)

    def test_old_rows_default_to_local_and_a_local_path_makes_a_row_local(self):
        self.history.upsert_downloaded({"video_id": "old01", "file_path": "/x.mp4"})
        self.assertEqual(self.history.get_record("old01")["storage"], "local")
        self.history.upsert_downloaded({"video_id": "r1", "storage": "remote", "remote_path": "/volume1/iwara/r.mp4"})
        self.history.update_file_paths("r1", file_path=os.path.join(self.tmp, "r.mp4"))
        record = self.history.get_record("r1")
        self.assertEqual((record["storage"], record["remote_path"]), ("local", ""))

    def test_search_status_and_subscription_state_mark_remote_copies(self):
        from app.ui.search.download_status import SearchDownloadStatus, query_download_statuses
        from app.presentation.subscription.item_queries import filter_items, item_state

        self.history.upsert_downloaded({"video_id": "r1", "storage": "remote", "remote_path": "/volume1/iwara/r.mp4"})
        self.history.upsert_downloaded({"video_id": "m1", "file_path": os.path.join(self.tmp, "gone.mp4")})
        statuses = query_download_statuses(self.history, ["r1", "m1", "n1"])
        self.assertEqual(statuses["r1"], SearchDownloadStatus.REMOTE)
        self.assertEqual(statuses["m1"], SearchDownloadStatus.MOVED)
        self.assertEqual(statuses["n1"], SearchDownloadStatus.NOT_DOWNLOADED)

        remote_item = {"video_id": "r1", "downloaded": True, "download_remote": True, "download_file_exists": False}
        moved_item = {"video_id": "m1", "downloaded": True, "download_file_exists": False}
        self.assertEqual(item_state(remote_item), "remote")
        self.assertEqual([i["video_id"] for i in filter_items([remote_item, moved_item], "downloaded")], ["r1"])
        self.assertEqual([i["video_id"] for i in filter_items([remote_item, moved_item], "moved")], ["m1"])


class OpenRemoteCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iwaratool-remote-open-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.record = {"video_id": "r1", "title": "T", "storage": "remote", "remote_path": "/volume1/iwara/bob/a.mp4"}
        self.manager = SimpleNamespace(
            history=mock.Mock(),
            _open_system_path=mock.Mock(return_value=(True, "")),
            _open_video_file=mock.Mock(return_value=(True, "")),
        )
        self.manager.history.get_record.return_value = self.record
        self.manager._open_location = lambda *a, **k: HistoryActionsMixin._open_location(self.manager, *a, **k)

    def _open(self, open_file: bool):
        return HistoryActionsMixin.open_history_output(self.manager, "r1", open_file=open_file)

    def test_unmounted_copy_explains_instead_of_opening(self):
        _ConfigPatch(self, aria2_remote_dir="/volume1/iwara", aria2_remote_mount_dir="", aria2_remote_http_base="")
        ok, message = self._open(False)
        self.assertFalse(ok)
        self.assertIn("/volume1/iwara/bob/a.mp4", message)
        ok, _ = self._open(True)
        self.assertFalse(ok)
        self.manager._open_system_path.assert_not_called()
        self.manager._open_video_file.assert_not_called()

    def test_mounted_copy_opens_folder_and_plays_locally(self):
        os.makedirs(os.path.join(self.tmp, "bob"))
        mounted = os.path.join(self.tmp, "bob", "a.mp4")
        open(mounted, "wb").close()
        _ConfigPatch(self, aria2_remote_dir="/volume1/iwara", aria2_remote_mount_dir=self.tmp, aria2_remote_http_base="")
        self.assertEqual(self._open(False), (True, ""))
        self.manager._open_system_path.assert_called_once_with(os.path.join(self.tmp, "bob"))
        self._open(True)
        self.manager._open_video_file.assert_called_once_with(mounted, video_id="r1", title="T")
        self.assertEqual(video_player.local_video_path("r1", self.manager.history), mounted)

    def test_http_address_streams_the_copy(self):
        _ConfigPatch(self, aria2_remote_dir="/volume1/iwara", aria2_remote_mount_dir="", aria2_remote_http_base="http://nas/iwara")
        self._open(True)
        self.manager._open_video_file.assert_called_once_with("http://nas/iwara/bob/a.mp4", video_id="r1", title="T")
        self.assertEqual(video_player.local_video_path("r1", self.manager.history), "")
        self.assertEqual(video_player.playable_video_target("r1", self.manager.history), "http://nas/iwara/bob/a.mp4")

    def test_urls_go_to_a_custom_player_or_the_builtin_window(self):
        url = "http://nas/iwara/a.mp4"
        self.assertFalse(video_player.opens_externally(url, video_player.MODE_SYSTEM))
        self.assertTrue(video_player.opens_externally(url, video_player.MODE_CUSTOM))
        self.assertFalse(video_player.opens_externally("/x.mp4", video_player.MODE_BUILTIN))
        launched = []
        saved = app_config.preview_player_command
        self.addCleanup(setattr, app_config, "preview_player_command", saved)
        app_config.preview_player_command = "mpv {file}"
        ok, _ = video_player.open_local_video(url, mode=video_player.MODE_CUSTOM, launcher=launched.append)
        self.assertTrue(ok)
        self.assertEqual(launched, [["mpv", url]])
        ok, _ = video_player.open_local_video(url, mode=video_player.MODE_SYSTEM)
        self.assertFalse(ok)


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class RemoteDownloadFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iwaratool-remote-flow-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        _ConfigPatch(
            self, aria2_rpc_enabled=True, aria2_rpc_url="http://nas:6800/jsonrpc", aria2_remote_enabled=True,
            aria2_remote_dir="/volume1/iwara", aria2_remote_mount_dir="", aria2_remote_http_base="",
            skip_existing_files=True,
        )
        manager = object.__new__(DownloadManager)
        manager._lock = threading.Lock()
        manager._tasks = {}
        manager._task_last_activity = {}
        manager.history = DownloadHistory(os.path.join(self.tmp, "history.db"))
        manager._existing_file_index_lock = threading.Lock()
        manager._existing_file_index = {}
        manager._last_aria2_global_limit = "0"
        manager._last_aria2_limit_attempted = ""
        manager._last_aria2_limit_attempt_at = 0.0
        manager._current_token = lambda: "tok"
        self.manager = manager
        self.task = DownloadTask(
            task_id="t1", url="https://www.iwara.tv/video/abc", video_id="abcdefgh12", title="Title",
            author="bob", download_url="https://files/abc.mp4", file_id="fid", file_url="https://i.iwara.tv/x",
        )
        manager._tasks["t1"] = self.task

    def test_resolved_task_gets_a_remote_path_and_no_local_file(self):
        mgr = self.manager
        mgr._build_output_relative_path = lambda **_: os.path.join("bob", "2026-01-01_Title_abcdefgh12.mp4")
        mgr._start_downloading = mock.Mock()
        mgr._cancel_if_requested = lambda _tid: False
        mgr._touch_task_activity_locked = lambda _tid: None
        mgr._schedule_task_persist = lambda: None
        meta = {k: "" for k in ("title", "author", "username", "published_at", "slug", "rating")}
        meta.update(likes=0, views=0, comments=0, duration=0, title="Title")
        with mock.patch("app.core.download.resolving.signal_bus", SimpleNamespace(log_message=_Signal(), task_status_changed=_Signal())):
            mgr._queue_resolved_download(self.task, "t1", meta, {"filename_template": "x"}, "https://dl", "Source")
        self.assertEqual(self.task.storage, "remote")
        self.assertEqual(self.task.remote_path, "/volume1/iwara/bob/2026-01-01_Title_abcdefgh12.mp4")
        self.assertEqual(self.task.file_path, "")
        self.assertEqual(self.task.status, TaskStatus.QUEUED_DOWNLOAD)
        mgr._start_downloading.assert_called_once_with("t1")
        self.assertFalse(os.path.exists(os.path.join(app_config.download_dir, "bob", "2026-01-01_Title_abcdefgh12.mp4")))

    def test_remote_submit_uses_the_hosts_folder_and_never_falls_back(self):
        mgr = self.manager
        self.task.storage = "remote"
        self.task.remote_path = "/volume1/iwara/bob/Title.mp4"
        mgr._cancel_if_requested = lambda _tid: False
        mgr._aria2_cancel_if_requested = lambda *_: False
        mgr._touch_task_activity_locked = lambda _tid: None
        mgr._aria2_rpc_add_uri = mock.Mock(return_value=(None, "connection refused"))
        mgr._fail_task = mock.Mock()
        mgr._download_task_native = mock.Mock()
        with mock.patch("app.core.download.aria2_remote.signal_bus", SimpleNamespace(log_message=_Signal())):
            mgr._download_task_aria2_remote("t1")
        uri, options = mgr._aria2_rpc_add_uri.call_args.args
        self.assertEqual(uri, "https://files/abc.mp4")
        self.assertEqual((options["dir"], options["out"]), ("/volume1/iwara/bob", "Title.mp4"))
        self.assertIn("Authorization: Bearer tok", options["header"])
        mgr._fail_task.assert_called_once()
        mgr._download_task_native.assert_not_called()

    def test_completed_remote_task_records_remote_storage_and_caches_the_cover(self):
        mgr = self.manager
        self.task.storage = "remote"
        self.task.remote_path = "/volume1/iwara/bob/Title.mp4"
        cover = os.path.join(self.tmp, "cover.jpg")
        mgr._is_cancel_requested = lambda _tid: False
        mgr._task_rule_payload = lambda _t: {"download_thumbnail": True, "collect_nfo_info": True, "record_to_history": True}
        mgr._cache_download_cover = mock.Mock(return_value=cover)
        mgr._aria2_rpc_add_uri = mock.Mock(return_value=("gid2", ""))
        mgr._write_nfo = mock.Mock()
        mgr._download_thumbnail = mock.Mock()
        mgr._mark_terminal_locked = lambda _t: None
        mgr._prune_terminal_tasks = lambda: None
        mgr._schedule_task_persist = lambda: None
        mgr._try_activate = lambda: None
        bus = SimpleNamespace(log_message=_Signal(), task_status_changed=_Signal(), history_changed=_Signal())
        with mock.patch("app.core.download.terminal_states.signal_bus", bus), \
                mock.patch("app.core.download.aria2_remote.signal_bus", bus), \
                mock.patch("app.core.history.signal_bus", bus):
            mgr._complete_task("t1")
        record = mgr.history.get_record("abcdefgh12")
        self.assertEqual(record["storage"], "remote")
        self.assertEqual(record["remote_path"], "/volume1/iwara/bob/Title.mp4")
        self.assertEqual(record["file_path"], "")
        self.assertEqual(record["thumbnail_path"], cover)
        mgr._download_thumbnail.assert_not_called()  # nothing is written next to a local file
        mgr._write_nfo.assert_not_called()  # not mounted: the NFO is skipped, not written locally
        cover_uri, cover_options = mgr._aria2_rpc_add_uri.call_args.args
        self.assertTrue(cover_uri.endswith("/image/original/fid/thumbnail-00.jpg"))
        self.assertEqual((cover_options["dir"], cover_options["out"]), ("/volume1/iwara/bob", "Title.jpg"))
        self.assertEqual(mgr._existing_file_index, {})

    def test_remote_copy_counts_as_existing_only_in_remote_mode(self):
        mgr = self.manager
        mgr.history.upsert_downloaded({"video_id": "abcdefgh12", "storage": "remote", "remote_path": "/volume1/iwara/x.mp4"})
        self.assertTrue(mgr._remote_copy_exists("abcdefgh12", "/volume1/iwara/x.mp4"))
        self.assertFalse(mgr._remote_copy_exists("abcdefgh12", "/volume1/iwara/other.mp4"))
        mgr._get_existing_file_index = lambda: {}
        self.assertEqual(mgr._find_existing_local_file("abcdefgh12"), "/volume1/iwara/x.mp4")
        app_config.aria2_remote_enabled = False
        self.assertIsNone(mgr._find_existing_local_file("abcdefgh12"))


class VideoMenuTests(unittest.TestCase):
    """Every page's video menu shows play / folder / path by where the copy is."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtWidgets import QWidget
        from qfluentwidgets import RoundMenu

        _ConfigPatch(self, aria2_remote_dir="/volume1/iwara", aria2_remote_mount_dir="", aria2_remote_http_base="")
        self.tmp = tempfile.mkdtemp(prefix="iwaratool-menu-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.menu = RoundMenu(parent=self.parent)
        self.manager = SimpleNamespace(history=mock.Mock())

    def _texts(self):
        return [action.text() for action in self.menu.actions()]

    def _build(self, record, **kwargs):
        from app.ui.media.video_menu import add_play_actions

        self.manager.history.get_record.return_value = record
        return add_play_actions(self.menu, self.parent, self.manager, "v1", **kwargs)

    def test_local_file(self):
        from app.i18n import tr

        path = os.path.join(self.tmp, "a.mp4")
        open(path, "wb").close()
        self._build({"file_path": path})
        self.assertEqual(self._texts(), [
            tr("Play local file", "播放本地文件", "ローカルファイルを再生"),
            tr("Open containing folder", "打开所在文件夹", "保存先フォルダーを開く"),
            tr("Copy file path", "复制文件路径", "ファイルパスをコピー"),
        ])

    def test_unmounted_nas_copy_is_not_offered_as_local(self):
        from app.i18n import tr

        location = self._build({"storage": "remote", "remote_path": "/volume1/iwara/a.mp4"})
        self.assertFalse(location.local_path)
        texts = self._texts()
        self.assertEqual(texts[0], tr("Play online", "在线播放", "オンライン再生"))
        self.assertIn(tr("Copy NAS path", "复制远端路径", "リモートのパスをコピー"), texts)
        self.assertNotIn(tr("Play local file", "播放本地文件", "ローカルファイルを再生"), texts)

    def test_not_downloaded_and_not_streamable(self):
        self._build(None, can_stream=False)
        self.assertEqual(self._texts(), [])

    def test_sections_never_double_up(self):
        from app.ui.media.video_menu import add_section

        add_section(self.menu)
        self.assertEqual(self.menu.actions(), [])
        self._build(None)
        add_section(self.menu)
        add_section(self.menu)
        from PySide6.QtCore import Qt

        view = self.menu.view
        tags = [view.item(i).data(Qt.ItemDataRole.DecorationRole) for i in range(view.count())]
        self.assertEqual(tags.count("seperator"), 1)
        self.assertEqual(tags[-1], "seperator")


class RemoteSettingsCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_card_loads_and_saves_remote_settings(self):
        from app.ui.settings import page as settings_page

        _ConfigPatch(self, aria2_rpc_enabled=True, aria2_remote_enabled=True, aria2_remote_dir="/volume1/iwara",
                     aria2_remote_mount_dir="", aria2_remote_http_base="")
        manager = mock.Mock()
        manager.restore_cached_login.return_value = False
        manager.api.login.return_value = (False, "")
        patcher = mock.patch.object(settings_page, "download_manager", manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        page = settings_page.SettingsInterface()
        self.addCleanup(lambda: (page.shutdown(timeout_ms=500), page.deleteLater()))
        page.ensure_built()
        self.assertTrue(page._aria2_remote_switch.isChecked())
        self.assertEqual(page._aria2_remote_dir_edit.text(), "/volume1/iwara")
        page._aria2_remote_mount_edit.setText("  /Volumes/iwara ")
        page._aria2_remote_http_edit.setText("http://nas/iwara")
        page._save_aria2_settings()
        self.assertEqual(app_config.aria2_remote_mount_dir, "/Volumes/iwara")
        self.assertEqual(app_config.aria2_remote_http_base, "http://nas/iwara")
        with mock.patch("app.ui.settings.network_cards.InfoBar") as info_bar:
            page._report_aria2_test({"result": {"version": "1.37.0"}}, "")
            page._report_aria2_test(None, "refused")
        info_bar.success.assert_called_once()
        info_bar.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
