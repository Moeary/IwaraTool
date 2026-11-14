from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.tools import compress, covers, ffmpeg
from app.core.tools.compress import CompressOptions, Compressor, build_commands, plan_compression, scaled_size
from app.core.tools.ffmpeg import FfmpegInfo, MediaInfo


def media(**overrides) -> MediaInfo:
    values = dict(
        path=Path("/videos/clip.mp4"),
        size=200 * compress.MB,
        duration=600.0,
        format_names=("mov", "mp4", "m4a", "3gp", "3g2", "mj2"),
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        pix_fmt="yuv420p",
        video_bit_rate=2_500_000,
        audio_codec="aac",
        audio_bit_rate=160_000,
        audio_channels=2,
    )
    values.update(overrides)
    return MediaInfo(**values)


def oversized(**overrides) -> MediaInfo:
    """An H.264 file whose video stream alone is over 300 MB, so it must be re-encoded."""

    return media(**{"size": 900 * compress.MB, "video_bit_rate": 12_000_000, **overrides})


FAKE_FFMPEG = FfmpegInfo(path=Path("/bin/ffmpeg"), ffprobe=Path("/bin/ffprobe"), version="7.0", encoders=frozenset({"libx264", "aac"}))


class CoverMathTests(unittest.TestCase):
    def test_sixteen_slice_midpoints(self):
        times = covers.cover_times(320.0)
        self.assertEqual(len(times), 16)
        self.assertAlmostEqual(times[0], 10.0)
        self.assertAlmostEqual(times[1], 30.0)
        self.assertAlmostEqual(times[-1], 310.0)

    def test_frames_follow_the_rate(self):
        candidates = covers.cover_candidates(32.0, 60.0)
        self.assertEqual([c.frame for c in candidates[:3]], [60, 180, 300])
        self.assertEqual(candidates[0].number, 1)

    def test_unknown_rate_assumes_thirty(self):
        self.assertEqual(covers.cover_candidates(32.0, 0)[0].frame, 30)

    def test_timecode(self):
        self.assertEqual(covers.format_timecode(3723.4567), "01:02:03.457")
        self.assertEqual(covers.format_timecode(0.0006), "00:00:00.001")

    def test_no_duration_no_candidates(self):
        self.assertEqual(covers.cover_times(0), [])


class PlanTests(unittest.TestCase):
    def test_compliant_mp4_is_ready(self):
        plan = plan_compression(media(), CompressOptions())
        self.assertEqual(plan.mode, "ready")
        self.assertFalse(plan.runnable)

    def test_mov_container_is_not_an_mp4(self):
        self.assertFalse(media(path=Path("/videos/clip.mov")).is_mp4)

    def test_h264_in_mkv_is_remuxed(self):
        plan = plan_compression(
            media(path=Path("/v/clip.mkv"), format_names=("matroska", "webm"), audio_codec="opus", audio_bit_rate=128_000),
            CompressOptions(),
        )
        self.assertEqual(plan.mode, "remux")
        self.assertFalse(plan.copy_audio)
        self.assertEqual(plan.audio_kbps, 160)

    def test_force_encode_overrides_ready(self):
        self.assertEqual(plan_compression(media(), CompressOptions(force_encode=True)).mode, "encode")

    def test_bitrate_fills_the_budget(self):
        source = media(size=900 * compress.MB, video_bit_rate=12_000_000)
        options = CompressOptions(limit_mb=300, margin=0.03, audio_kbps=160)
        plan = plan_compression(source, options)
        self.assertEqual(plan.mode, "encode")
        expected = int(300 * compress.MB * 0.97 * 8 / 600 / 1000 - 160)
        self.assertEqual(plan.video_kbps, expected)
        self.assertLessEqual(plan.estimated_bytes, options.limit_bytes)

    def test_sub_720_is_upscaled(self):
        plan = plan_compression(media(width=854, height=480, size=50 * compress.MB, video_codec="mpeg4"), CompressOptions())
        self.assertEqual((plan.width, plan.height), (1280, 720))
        self.assertTrue(plan.upscaled)
        self.assertIn("upscaled", plan.notes)

    def test_portrait_upscale_keeps_orientation(self):
        self.assertEqual(scaled_size(480, 854, 720), (720, 1280))

    def test_rotation_swaps_the_display_size(self):
        self.assertEqual(media(width=1920, height=1080, rotation=90).display_size, (1080, 1920))

    def test_long_4k_is_scaled_down_but_not_below_720(self):
        source = media(width=3840, height=2160, duration=3600.0, size=8000 * compress.MB, video_bit_rate=18_000_000)
        plan = plan_compression(source, CompressOptions())
        self.assertTrue(plan.downscaled)
        self.assertGreaterEqual(min(plan.width, plan.height), 720)
        self.assertLess(plan.height, 2160)

    def test_downscale_can_be_turned_off(self):
        source = media(width=3840, height=2160, duration=3600.0, size=8000 * compress.MB, video_bit_rate=18_000_000)
        plan = plan_compression(source, CompressOptions(auto_downscale=False))
        self.assertEqual((plan.width, plan.height), (3840, 2160))
        self.assertIn("low_quality", plan.notes)

    def test_too_long_is_impossible(self):
        plan = plan_compression(media(duration=40 * 3600.0, size=9000 * compress.MB), CompressOptions())
        self.assertEqual(plan.mode, "impossible")
        self.assertIn("too_long", plan.notes)

    def test_short_clip_is_not_inflated(self):
        plan = plan_compression(media(duration=10.0, video_codec="hevc", size=50 * compress.MB), CompressOptions())
        self.assertEqual(plan.video_kbps, 10000)  # the 1080p ceiling, not 230 Mbps

    def test_h264_rate_is_never_raised(self):
        plan = plan_compression(media(duration=60.0, video_bit_rate=3_000_000), CompressOptions(force_encode=True))
        self.assertEqual(plan.video_kbps, 3000)

    def test_high_frame_rate_is_capped(self):
        plan = plan_compression(oversized(fps=120.0), CompressOptions(max_fps=60))
        self.assertTrue(plan.fps_capped)
        self.assertEqual(plan.fps, 60.0)

    def test_remux_estimate_trusts_the_stream_bitrate(self):
        # The file is big (say, extra audio tracks) but its H.264 stream fits.
        self.assertEqual(plan_compression(media(size=900 * compress.MB), CompressOptions()).mode, "remux")

    def test_no_audio_spends_everything_on_video(self):
        plan = plan_compression(media(audio_codec="", size=900 * compress.MB, video_bit_rate=0), CompressOptions())
        self.assertEqual(plan.audio_kbps, 0)


class CommandTests(unittest.TestCase):
    def test_x264_runs_two_passes(self):
        source = oversized()
        options = CompressOptions()
        plan = plan_compression(source, options)
        stages = build_commands(FAKE_FFMPEG, source, plan, options, Path("/out/x.mp4.part"), Path("/tmp/log"))
        self.assertEqual([name for name, _ in stages], ["pass1", "pass2"])
        first, second = stages[0][1], stages[1][1]
        self.assertIn("-an", first)
        self.assertEqual(first[-1], os.devnull)
        self.assertEqual(second[second.index("-pass") + 1], "2")
        self.assertEqual(second[second.index("-passlogfile") + 1], "/tmp/log")
        self.assertEqual(second[-3:], ["-f", "mp4", "/out/x.mp4.part"])
        self.assertIn("+faststart", second)
        self.assertIn(f"{plan.video_kbps}k", second)

    def test_upscale_uses_the_chosen_scaler(self):
        source = media(width=640, height=360, video_codec="mpeg4")
        options = CompressOptions(upscaler="spline")
        plan = plan_compression(source, options)
        argv = build_commands(FAKE_FFMPEG, source, plan, options, Path("o"), Path("l"))[1][1]
        self.assertIn("scale=1280:720:flags=spline,setsar=1", argv)

    def test_hardware_encoder_is_one_pass(self):
        source = oversized()
        options = CompressOptions(encoder="h264_videotoolbox")
        stages = build_commands(FAKE_FFMPEG, source, plan_compression(source, options), options, Path("o"), Path("l"))
        self.assertEqual([name for name, _ in stages], ["encode"])
        self.assertIn("h264_videotoolbox", stages[0][1])

    def test_remux_copies_video(self):
        source = media(path=Path("/v/clip.mkv"), format_names=("matroska",))
        options = CompressOptions()
        plan = plan_compression(source, options)
        argv = build_commands(FAKE_FFMPEG, source, plan, options, Path("o"), Path("l"))[0][1]
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
        self.assertEqual(argv[argv.index("-c:a") + 1], "copy")

    def test_retry_runs_only_the_second_pass_at_a_lower_rate(self):
        options = CompressOptions(limit_mb=1)
        plan = plan_compression(oversized(duration=4.0), options)
        sizes = iter([1_300_000, 900_000])
        runs = []

        def fake_run(process, on_progress=None):
            runs.append(process.argv)
            target = Path(process.argv[-1])
            if target.name != os.path.basename(os.devnull):
                target.write_bytes(b"\0" * next(sizes))

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(compress.FfmpegProcess, "run", fake_run):
            output = Path(folder) / "out.mp4"
            result = Compressor(FAKE_FFMPEG, media(path=Path(folder) / "in.mkv", duration=4.0), plan, options, output).run()
            self.assertTrue(output.is_file())
            self.assertFalse(output.with_name("out.mp4.part").exists())
        self.assertEqual(len(runs), 3)  # pass 1, pass 2, pass 2 again
        self.assertEqual(result.attempts, 2)
        self.assertLess(result.plan.video_kbps, plan.video_kbps)
        self.assertIn("retried", result.plan.notes)

    def test_output_may_not_replace_the_source(self):
        source = oversized()
        options = CompressOptions()
        with self.assertRaises(ffmpeg.FfmpegError):
            Compressor(FAKE_FFMPEG, source, plan_compression(source, options), options, source.path).run()


class ProbeParsingTests(unittest.TestCase):
    def test_ffprobe_json(self):
        data = {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.5", "bit_rate": "4000000", "size": "1"},
            "streams": [
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {
                    "codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
                    "r_frame_rate": "30/1", "avg_frame_rate": "2670/89", "pix_fmt": "yuv420p",
                    "bit_rate": "3800000", "nb_frames": "375",
                    "side_data_list": [{"rotation": -90}],
                },
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000", "channels": 2},
            ],
        }
        info = ffmpeg.media_info_from_probe(Path("a.mp4"), data, 1234)
        self.assertEqual((info.video_codec, info.width, info.height), ("h264", 1280, 720))
        self.assertEqual(info.fps, 30.0)  # the nominal rate, not 29.99
        self.assertEqual(info.rotation, 270)
        self.assertEqual(info.display_size, (720, 1280))
        self.assertEqual((info.audio_codec, info.size, info.frame_count), ("aac", 1234, 375))
        self.assertTrue(info.is_mp4)

    def test_banner_fallback(self):
        text = """Input #0, matroska,webm, from 'clip.mkv':
  Duration: 00:01:02.50, start: 0.000000, bitrate: 2500 kb/s
  Stream #0:0(jpn): Video: hevc (Main), yuv420p(tv), 1920x1080 [SAR 1:1 DAR 16:9], 59.94 fps, 59.94 tbr, 1k tbn
  Stream #0:1(jpn): Audio: opus, 48000 Hz, stereo, fltp
"""
        info = ffmpeg.media_info_from_banner(Path("clip.mkv"), text, 99)
        self.assertAlmostEqual(info.duration, 62.5)
        self.assertEqual((info.video_codec, info.pix_fmt, info.width, info.height), ("hevc", "yuv420p", 1920, 1080))
        self.assertAlmostEqual(info.fps, 59.94)
        self.assertEqual(info.audio_codec, "opus")
        self.assertEqual(info.bit_rate, 2_500_000)
        self.assertEqual(info.format_names, ("matroska", "webm"))

    def test_encoder_listing(self):
        listing = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC
 V....D h264_videotoolbox    VideoToolbox H.264 Encoder
 A....D aac                  AAC (Advanced Audio Coding)
"""
        self.assertEqual(ffmpeg._names_from_listing(listing), {"libx264", "h264_videotoolbox", "aac"})


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.folder = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def _program(self, folder: Path, name: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (f"{name}.exe" if os.name == "nt" else name)
        path.write_text("")
        path.chmod(0o755)
        return path

    def test_configured_file_or_folder_wins(self):
        program = self._program(self.folder / "custom", "ffmpeg")
        with mock.patch.object(ffmpeg.shutil, "which", return_value="/elsewhere/ffmpeg"):
            self.assertEqual(ffmpeg.find_ffmpeg(str(program)), program)
            self.assertEqual(ffmpeg.find_ffmpeg(str(program.parent)), program)

    def test_missing_configured_path_falls_back_to_path(self):
        with mock.patch.object(ffmpeg.shutil, "which", return_value="/usr/bin/ffmpeg"):
            self.assertEqual(ffmpeg.find_ffmpeg(str(self.folder / "gone")), Path("/usr/bin/ffmpeg"))

    def test_install_folders_are_searched_when_path_has_none(self):
        program = self._program(self.folder / "brew", "ffmpeg")
        with mock.patch.object(ffmpeg.shutil, "which", return_value=None), mock.patch.object(
            ffmpeg, "candidate_locations", return_value=[self.folder / "none", program.parent]
        ):
            self.assertEqual(ffmpeg.find_ffmpeg(""), program)

    def test_nothing_found(self):
        with mock.patch.object(ffmpeg.shutil, "which", return_value=None), mock.patch.object(
            ffmpeg, "candidate_locations", return_value=[self.folder]
        ):
            self.assertIsNone(ffmpeg.find_ffmpeg(""))
            with self.assertRaises(ffmpeg.FfmpegError):
                ffmpeg.locate("")

    def test_ffprobe_next_to_ffmpeg(self):
        program = self._program(self.folder, "ffmpeg")
        probe = self._program(self.folder, "ffprobe")
        self.assertEqual(ffmpeg.find_ffprobe(program), probe)


REAL_FFMPEG = ffmpeg.find_ffmpeg("")


@unittest.skipUnless(REAL_FFMPEG is not None, "ffmpeg is not installed")
class RealFfmpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = ffmpeg.inspect_ffmpeg(REAL_FFMPEG)
        if not cls.info.has_libx264:
            raise unittest.SkipTest("this ffmpeg has no libx264")
        cls._dir = tempfile.TemporaryDirectory()
        cls.folder = Path(cls._dir.name)
        cls.source = cls.folder / "source.mkv"
        subprocess.run(
            [
                str(REAL_FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "3", "-c:v", "mpeg4", "-c:a", "mp3", str(cls.source),
            ],
            check=True,
            **ffmpeg.subprocess_flags(),
        )

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_compress_meets_the_upload_rules(self):
        source = ffmpeg.probe(self.info, self.source)
        self.assertEqual((source.video_codec, source.width, source.height), ("mpeg4", 640, 360))
        options = CompressOptions(limit_mb=1)
        plan = plan_compression(source, options)
        output = self.folder / "out.mp4"
        progress = []
        result = Compressor(self.info, source, plan, options, output).run(lambda s, p, o: progress.append(o))
        self.assertLessEqual(result.size, options.limit_bytes)
        self.assertEqual(progress[-1], 1.0)
        done = ffmpeg.probe(self.info, output)
        self.assertEqual((done.video_codec, done.audio_codec), ("h264", "aac"))
        self.assertEqual(done.display_size, (1280, 720))
        self.assertEqual(plan_compression(done, CompressOptions()).mode, "ready")

    def test_covers_are_extracted(self):
        source = ffmpeg.probe(self.info, self.source)
        got = {}
        covers.extract_covers(
            self.info, self.source, covers.cover_candidates(source.duration, source.fps),
            lambda c, data, error: got.__setitem__(c.index, (data, error)),
        )
        self.assertEqual(sorted(got), list(range(16)))
        for data, error in got.values():
            self.assertEqual(error, "")
            self.assertTrue(data.startswith(b"\x89PNG"))
        target = self.folder / "frame.png"
        covers.save_frame(self.info, self.source, 1.0, target)
        self.assertGreater(target.stat().st_size, 0)


class ToolsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_tools_are_built_on_first_use(self):
        from app.ui.tools.page import ToolsInterface, tool_entries

        page = ToolsInterface()
        self.addCleanup(page.deleteLater)
        self.assertEqual([entry.key for entry in tool_entries()], ["compress", "covers"])
        self.assertEqual(page._tools, {})
        page.select_tool("covers")
        self.assertIn("covers", page._tools)
        self.assertIs(page._stack.currentWidget(), page._tools["covers"])
        self.assertNotIn("compress", page._tools)
        self.assertTrue(page.shutdown(timeout_ms=10_000))

    def test_settings_has_a_tools_category_for_ffmpeg(self):
        from app.ui.settings.sections import settings_categories

        categories = {key: cards for key, _icon, _title, _summary, cards in settings_categories()}
        self.assertIn("ffmpeg", categories["tools"])


if __name__ == "__main__":
    unittest.main()
