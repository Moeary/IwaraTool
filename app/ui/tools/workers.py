"""Run ffmpeg / ffprobe for the tool pages off the GUI thread."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal

from ...core.tools import covers
from ...core.tools.compress import Cancelled, Compressor, CompressOptions, CompressPlan
from ...core.tools.ffmpeg import FfmpegError, FfmpegInfo, MediaInfo, probe
from ...logging_setup import get_logger
from ..worker_lifecycle import ManagedThread

logger = get_logger(__name__)


class ProbeWorker(ManagedThread):
    """Read a file's duration and streams."""

    probed = Signal(int, object, str)  # generation, MediaInfo | None, error

    def __init__(self, generation: int, ffmpeg: FfmpegInfo, path: str):
        super().__init__()
        self.generation = generation
        self.ffmpeg = ffmpeg
        self.path = path

    def run(self):
        try:
            info = probe(self.ffmpeg, self.path)
        except FfmpegError as exc:
            self.probed.emit(self.generation, None, str(exc))
            return
        except Exception as exc:
            logger.exception("Probing %s failed", self.path)
            self.probed.emit(self.generation, None, str(exc))
            return
        self.probed.emit(self.generation, info, "")


class CompressWorker(ManagedThread):
    """Run one compression; ``requestInterruption`` stops ffmpeg at once."""

    progress = Signal(str, float, float)  # stage, stage fraction, overall fraction
    succeeded = Signal(object)  # CompressResult
    failed = Signal(str, str)  # message, ffmpeg's last output
    cancelled = Signal()

    def __init__(self, ffmpeg: FfmpegInfo, media: MediaInfo, plan: CompressPlan, options: CompressOptions, output: Path):
        super().__init__()
        self._compressor = Compressor(ffmpeg, media, plan, options, output)

    def requestInterruption(self):
        # The thread sits in ffmpeg's output loop; ending ffmpeg ends the loop.
        self._compressor.cancel()
        super().requestInterruption()

    def run(self):
        try:
            result = self._compressor.run(lambda stage, part, overall: self.progress.emit(stage, part, overall))
        except Cancelled:
            self.cancelled.emit()
            return
        except FfmpegError as exc:
            self.failed.emit(str(exc), self._compressor.stderr_tail)
            return
        except Exception as exc:
            logger.exception("Compression failed")
            self.failed.emit(str(exc), self._compressor.stderr_tail)
            return
        self.succeeded.emit(result)


class CoverWorker(ManagedThread):
    """Extract the 16 cover candidates, four ffmpeg runs at a time."""

    frame_ready = Signal(int, int, bytes, str)  # generation, index, png, error

    def __init__(self, generation: int, ffmpeg: FfmpegInfo, path: Path, candidates: list[covers.CoverCandidate]):
        super().__init__()
        self.generation = generation
        self.ffmpeg = ffmpeg
        self.path = path
        self.candidates = list(candidates)

    def run(self):
        def on_frame(candidate, data, error):
            if not self.isInterruptionRequested():
                self.frame_ready.emit(self.generation, candidate.index, data or b"", error)

        try:
            covers.extract_covers(
                self.ffmpeg, self.path, self.candidates, on_frame, should_stop=self.isInterruptionRequested
            )
        except Exception:
            logger.exception("Extracting covers from %s failed", self.path)


class SaveFrameWorker(ManagedThread):
    saved = Signal(str, str)  # target, error

    def __init__(self, ffmpeg: FfmpegInfo, source: Path, seconds: float, target: Path):
        super().__init__()
        self.ffmpeg = ffmpeg
        self.source = source
        self.seconds = seconds
        self.target = target

    def run(self):
        try:
            covers.save_frame(self.ffmpeg, self.source, self.seconds, self.target)
        except Exception as exc:
            self.saved.emit(str(self.target), str(exc))
            return
        self.saved.emit(str(self.target), "")
