"""Tools page rules shared by both interfaces: compressor options and verdicts, cover texts and cropping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


def media(**changes):
    from app.core.tools.ffmpeg import MediaInfo

    values = dict(path=Path("/v/a.webm"), size=50_000_000, duration=60.0, format_names=("matroska", "webm"), bit_rate=0,
                  video_codec="vp9", width=640, height=360, fps=30.0, audio_codec="opus")
    values.update(changes)
    return MediaInfo(**values)


class CompressPresentationTests(unittest.TestCase):
    def test_form_round_trip_and_options(self):
        from app.presentation.tools import compress as cr

        ui: dict = {}
        with mock.patch.object(cr.app_config, "get_ui_value", lambda key, default=None: ui.get(key, default)), \
                mock.patch.object(cr.app_config, "set_ui_value", lambda key, value: ui.__setitem__(key, value)):
            self.assertEqual(cr.saved_form(), cr.CompressForm())
            cr.save_form(cr.CompressForm(limit="custom", custom_mb=123, encoder="h264_nvenc", force_encode=True))
            saved = cr.saved_form()
            self.assertEqual((saved.limit, saved.custom_mb, saved.encoder, saved.force_encode), ("custom", 123, "h264_nvenc", False))
            self.assertEqual(saved.options().limit_mb, 123.0)
            self.assertFalse(saved.x264)
            ui[cr.OPTIONS_KEY] = "not json"
            self.assertEqual(cr.saved_form(), cr.CompressForm())
        self.assertEqual(cr.CompressForm(limit="premium").options().limit_mb, 600.0)

    def test_encoders_follow_the_ffmpeg_build(self):
        from app.core.tools.ffmpeg import FfmpegInfo
        from app.presentation.tools import compress as cr

        hardware_only = FfmpegInfo(Path("ffmpeg"), None, "7", encoders=frozenset({"h264_videotoolbox"}))
        self.assertEqual(cr.available_encoders(hardware_only), ["h264_videotoolbox"])
        self.assertEqual(cr.pick_encoder(cr.CompressForm(), hardware_only).encoder, "h264_videotoolbox")
        self.assertFalse(cr.ffmpeg_ready(FfmpegInfo(Path("ffmpeg"), None, "7")))
        self.assertFalse(cr.ffmpeg_ready(None))

    def test_verdicts(self):
        from app.core.tools.compress import plan_compression
        from app.presentation.tools import compress as cr

        options = cr.CompressForm().options()
        needs = media()
        result = cr.verdict(needs, plan_compression(needs, options), options)
        self.assertEqual(result.role, "warning")
        self.assertIn("vp9", result.title)
        self.assertIn("1280×720", result.detail)
        ready = media(path=Path("/v/a.mp4"), format_names=("mov", "mp4"), video_codec="h264", audio_codec="aac",
                      width=1280, height=720, size=10_000_000)
        self.assertEqual(cr.verdict(ready, plan_compression(ready, options), options).role, "success")
        self.assertIn("kbps", cr.media_summary(needs))

    def test_output_and_progress_texts(self):
        from app.presentation.tools import compress as cr

        folder = Path(tempfile.mkdtemp())
        source = folder / "a.mp4"
        source.write_bytes(b"")
        output, error = cr.output_for("", source)
        self.assertEqual(output.suffix, ".mp4")
        self.assertNotEqual(output, source)
        self.assertEqual(cr.output_for(str(folder / "b.mkv"), source)[0], folder / "b.mp4")
        self.assertEqual(cr.output_for(str(source), source), (None, cr.output_for(str(source), source)[1]))
        self.assertTrue(cr.output_for(str(source), source)[1])
        self.assertIn("50%", cr.progress_text("encode", 0.5, 10))
        self.assertIn("0:10", cr.progress_text("encode", 0.5, 10))


class CoverPresentationTests(unittest.TestCase):
    def test_crop_and_moves(self):
        from app.presentation.tools import covers as cv

        self.assertEqual(cv.crop_box(1920, 1080), (150.0, 0.0, 1620.0, 1080.0))
        self.assertEqual(cv.crop_box(300, 400), (0.0, 100.0, 300.0, 200.0))
        self.assertEqual(cv.crop_box(0, 10), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(cv.moved(-1, cv.KEY_STEPS["Right"], 16), 1)
        self.assertEqual(cv.moved(2, cv.KEY_STEPS["Up"], 16), 0)
        self.assertEqual(cv.moved(14, cv.KEY_STEPS["Down"], 16), 15)

    def test_texts(self):
        from app.core.tools.covers import cover_candidates
        from app.presentation.tools import covers as cv

        candidate = cover_candidates(12.0, 30.0)[5]
        self.assertIn("6", cv.detail_title(candidate))
        self.assertEqual(cv.default_frame_path(Path("/v/clip.mp4"), candidate), Path("/v/clip_cover06.png"))
        self.assertIn("2", cv.finished_text(2))
        self.assertIn("frames", cv.media_summary(media()).replace("帧", "frames").replace("フレーム", "frames"))

    def test_common(self):
        from app.presentation.tools import common as tc

        self.assertEqual(tc.format_bytes(300_000_000), "300.0 MB")
        self.assertEqual(tc.format_clock(3725), "1:02:05")
        self.assertEqual(tc.clean_path('  "/a b.mp4" '), "/a b.mp4")
        self.assertEqual([t.key for t in tc.tools()], ["compress", "covers"])
        self.assertIn("libx264", tc.ffmpeg_missing_text(no_h264=True))
        self.assertNotIn("ffmpeg was not found\n", tc.ffmpeg_missing_text("ffmpeg was not found"))


if __name__ == "__main__":
    unittest.main()
