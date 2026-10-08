from __future__ import annotations

import http.server
import os
import tempfile
import threading
import unittest
from unittest import mock

import requests

from app.config import app_config
from app.core import preview_stream, video_player
from app.core.stream_proxy import StreamProxy, StreamSource

DATA = bytes(range(256)) * 400  # 102400 bytes


class _History:
    def __init__(self, record):
        self.record = record

    def get_record(self, video_id):
        return self.record


class PlayerCommandTests(unittest.TestCase):
    def test_placeholder_is_replaced_and_quotes_are_honoured(self):
        command = video_player.build_custom_command(
            '"C:\\Program Files\\mpv\\mpv.exe" --fs "{file}"', r"D:\v\a b.mp4", windows=True
        )
        self.assertEqual(command, [r"C:\Program Files\mpv\mpv.exe", "--fs", r"D:\v\a b.mp4"])

    def test_path_is_appended_without_placeholder(self):
        self.assertEqual(
            video_player.build_custom_command("vlc --fullscreen", "/v/a.mp4", windows=False),
            ["vlc", "--fullscreen", "/v/a.mp4"],
        )

    def test_empty_command(self):
        self.assertEqual(video_player.build_custom_command("  ", "/v/a.mp4"), [])

    def test_mode_falls_back_to_system(self):
        saved = app_config.preview_player_mode
        try:
            app_config.preview_player_mode = "nonsense"
            self.assertEqual(video_player.player_mode(), video_player.MODE_SYSTEM)
            app_config.preview_player_mode = "BUILTIN"
            self.assertEqual(video_player.player_mode(), video_player.MODE_BUILTIN)
        finally:
            app_config.preview_player_mode = saved


class LocalFileTests(unittest.TestCase):
    def test_local_path_requires_existing_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "a.mp4")
            open(path, "wb").close()
            self.assertEqual(video_player.local_video_path("x", _History({"file_path": path})), path)
            self.assertEqual(
                video_player.local_video_path("x", _History({"file_path": os.path.join(root, "moved.mp4")})), ""
            )
            self.assertEqual(video_player.local_video_path("x", _History(None)), "")
            self.assertEqual(video_player.local_video_path("", _History({"file_path": path})), "")

    def test_custom_player_is_launched_with_the_file(self):
        saved = app_config.preview_player_command
        try:
            app_config.preview_player_command = "myplayer --fs {file}"
            with tempfile.TemporaryDirectory() as root:
                path = os.path.join(root, "a.mp4")
                open(path, "wb").close()
                launched = []
                ok, message = video_player.open_local_video(
                    path, mode=video_player.MODE_CUSTOM, launcher=launched.append
                )
            self.assertTrue(ok, message)
            self.assertEqual(launched, [["myplayer", "--fs", path]])
        finally:
            app_config.preview_player_command = saved

    def test_missing_file_and_missing_command_are_reported(self):
        ok, _ = video_player.open_local_video("/definitely/missing.mp4")
        self.assertFalse(ok)
        saved = app_config.preview_player_command
        try:
            app_config.preview_player_command = ""
            with tempfile.TemporaryDirectory() as root:
                path = os.path.join(root, "a.mp4")
                open(path, "wb").close()
                ok, message = video_player.open_local_video(path, mode=video_player.MODE_CUSTOM)
            self.assertFalse(ok)
            self.assertTrue(message)
        finally:
            app_config.preview_player_command = saved

    def test_system_mode_uses_the_os_opener(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "a.mp4")
            open(path, "wb").close()
            with mock.patch.object(video_player, "_open_with_system") as opener:
                ok, _ = video_player.open_local_video(path, mode=video_player.MODE_SYSTEM)
            self.assertTrue(ok)
            opener.assert_called_once_with(path)


class _Upstream(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []
    reject_first = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen.append(dict(self.headers))
        if type(self).reject_first and "good" not in self.path:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = 0, len(DATA) - 1
        status = 200
        header = self.headers.get("Range")
        if header and header.startswith("bytes="):
            first, _, last = header[6:].partition("-")
            start = int(first or 0)
            end = int(last) if last else end
            status = 206
        body = DATA[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body)))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.end_headers()
        self.wfile.write(body)


class StreamProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        cls.upstream.daemon_threads = True
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.upstream.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def setUp(self):
        _Upstream.seen = []
        _Upstream.reject_first = False
        self.proxy = StreamProxy(requests.Session)
        self.addCleanup(self.proxy.stop)

    def _local(self, source):
        return self.proxy.register(source)

    def test_full_and_ranged_reads_are_relayed(self):
        url = self._local(StreamSource(f"{self.base}/file", headers=lambda: {"Authorization": "Bearer t"}))
        full = requests.get(url, timeout=5)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.content, DATA)
        part = requests.get(url, headers={"Range": "bytes=100-199"}, timeout=5)
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, DATA[100:200])
        self.assertEqual(part.headers["Content-Range"], f"bytes 100-199/{len(DATA)}")
        self.assertTrue(all(seen.get("Authorization") == "Bearer t" for seen in _Upstream.seen))

    def test_head_reports_full_length(self):
        url = self._local(StreamSource(f"{self.base}/file"))
        head = requests.head(url, timeout=5)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(int(head.headers["Content-Length"]), len(DATA))

    def test_unknown_token_is_404(self):
        self.proxy.start()
        port = self.proxy._server.server_address[1]
        self.assertEqual(requests.get(f"http://127.0.0.1:{port}/s/nope", timeout=5).status_code, 404)

    def test_expired_url_is_refreshed_once(self):
        _Upstream.reject_first = True
        source = StreamSource(f"{self.base}/stale", refresh=lambda: f"{self.base}/good")
        url = self._local(source)
        reply = requests.get(url, timeout=5)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.content, DATA)
        self.assertEqual(len(_Upstream.seen), 2)

    def test_unregister_all_invalidates_tokens(self):
        url = self._local(StreamSource(f"{self.base}/file"))
        self.proxy.unregister_all()
        self.assertEqual(requests.get(url, timeout=5).status_code, 404)


class _FakeAPI:
    token = "tok"

    def __init__(self, info=None, url="https://files.example/v.mp4", error=""):
        self.info, self.url, self.error = info, url, error
        self.quality_asked = None

    def get_video_info(self, video_id):
        return (self.info, "") if self.info else (None, "no such video")

    def get_download_info(self, info, quality):
        self.quality_asked = quality
        return (self.url, "540", "") if self.url else (None, None, self.error or "nothing")


class PreviewStreamTests(unittest.TestCase):
    def tearDown(self):
        preview_stream.shutdown_proxy()

    def test_open_stream_returns_loopback_url_and_title(self):
        api = _FakeAPI(info={"title": "Hello"})
        stream = preview_stream.open_stream(api, "abc", "360")
        self.assertTrue(stream.url.startswith("http://127.0.0.1:"))
        self.assertEqual((stream.title, stream.quality, api.quality_asked), ("Hello", "540", "360"))

    def test_errors_surface_as_preview_error(self):
        with self.assertRaises(preview_stream.PreviewError) as ctx:
            preview_stream.open_stream(_FakeAPI(info=None), "abc")
        self.assertIn("no such video", str(ctx.exception))
        with self.assertRaises(preview_stream.PreviewError):
            preview_stream.open_stream(_FakeAPI(info={"title": "x"}, url=None, error="private"), "abc")

    def test_quality_setting_is_validated(self):
        saved = app_config.preview_quality
        try:
            app_config.preview_quality = "bogus"
            self.assertEqual(preview_stream.preview_quality(), "540")
            app_config.preview_quality = "Source"
            self.assertEqual(preview_stream.preview_quality(), "Source")
        finally:
            app_config.preview_quality = saved


if __name__ == "__main__":
    unittest.main()
