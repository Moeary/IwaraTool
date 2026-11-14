"""Find the ffmpeg command-line program, read media info and run it.

The packaged app only carries the FFmpeg libraries Qt Multimedia plays video
with, not the ``ffmpeg`` program, so the tools look for one in this order:
the path chosen in Settings, ``PATH``, then the usual install locations
(Homebrew, winget, scoop, Chocolatey, next to the app). A GUI app started from
Finder or the Start menu often has a shorter ``PATH`` than a terminal, which
is why the install locations are checked directly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ...paths import app_root_dir


class FfmpegError(RuntimeError):
    """ffmpeg is missing, failed, or could not read the file."""


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def subprocess_flags() -> dict:
    """Keep Windows from flashing a console window for every ffmpeg call."""

    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def candidate_locations() -> list[Path]:
    """Directories where ffmpeg is commonly installed on this platform."""

    root = app_root_dir()
    places = [root, root / "ffmpeg", root / "ffmpeg" / "bin", root / "bin"]
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        places += [
            local / "Microsoft" / "WinGet" / "Links",
            Path.home() / "scoop" / "shims",
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "chocolatey" / "bin",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin",
            Path(r"C:\ffmpeg\bin"),
        ]
    elif sys.platform == "darwin":
        places += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin")]
    else:
        places += [Path("/usr/bin"), Path("/usr/local/bin"), Path("/snap/bin")]
    return places


def program_at(path: str | os.PathLike, name: str) -> Path | None:
    """``path`` itself if it is the program, or ``path/name`` for a folder."""

    candidate = Path(os.path.expanduser(str(path).strip().strip('"')))
    if candidate.is_dir():
        candidate = candidate / _exe(name)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def find_ffmpeg(configured: str = "") -> Path | None:
    """The ffmpeg program to use, or ``None`` when there is none."""

    if str(configured or "").strip():
        # A chosen path that no longer exists falls back to auto-detection
        # rather than leaving the tools unusable.
        found = program_at(configured, "ffmpeg")
        if found is not None:
            return found
    on_path = shutil.which("ffmpeg")
    if on_path:
        return Path(on_path)
    for place in candidate_locations():
        found = program_at(place, "ffmpeg")
        if found is not None:
            return found
    return None


def find_ffprobe(ffmpeg: Path) -> Path | None:
    """ffprobe next to ``ffmpeg`` (builds ship them together), else on PATH."""

    sibling = program_at(ffmpeg.parent, "ffprobe")
    if sibling is not None:
        return sibling
    on_path = shutil.which("ffprobe")
    return Path(on_path) if on_path else None


# ── Capabilities ─────────────────────────────────────────────────────────────

HARDWARE_H264_ENCODERS = ("h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf")


@dataclass(frozen=True)
class FfmpegInfo:
    path: Path
    ffprobe: Path | None
    version: str
    encoders: frozenset[str] = field(default_factory=frozenset)
    filters: frozenset[str] = field(default_factory=frozenset)

    @property
    def has_libx264(self) -> bool:
        return "libx264" in self.encoders

    @property
    def hardware_encoders(self) -> tuple[str, ...]:
        return tuple(name for name in HARDWARE_H264_ENCODERS if name in self.encoders)


def _names_from_listing(text: str) -> frozenset[str]:
    """Second column of ``ffmpeg -encoders`` / ``-filters`` rows."""

    names = set()
    for line in text.splitlines():
        parts = line.split()
        # Rows look like " V....D libx264  description" / " TS. scale  V->V ...";
        # the header lines before "------" have no flag column.
        # The legend above the "------" line reads " V..... = Video".
        if len(parts) >= 2 and parts[1] != "=" and re.fullmatch(r"[A-Z.|]{2,7}", parts[0]):
            names.add(parts[1])
    return frozenset(names)


def _run_text(args: list[str], timeout: float = 15) -> str:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **subprocess_flags(),
    )
    return (result.stdout or "") + (result.stderr or "")


_INFO_CACHE: dict[tuple[str, float], FfmpegInfo] = {}
_INFO_LOCK = threading.Lock()


def inspect_ffmpeg(path: Path) -> FfmpegInfo:
    """Version and the encoders / filters this build has (cached per binary)."""

    try:
        key = (str(path), path.stat().st_mtime)
    except OSError as exc:
        raise FfmpegError(str(exc)) from exc
    with _INFO_LOCK:
        cached = _INFO_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        banner = _run_text([str(path), "-hide_banner", "-version"])
        encoders = _names_from_listing(_run_text([str(path), "-hide_banner", "-encoders"]))
        filters = _names_from_listing(_run_text([str(path), "-hide_banner", "-filters"]))
    except (OSError, subprocess.SubprocessError) as exc:
        raise FfmpegError(str(exc)) from exc
    first = banner.strip().splitlines()[0] if banner.strip() else ""
    match = re.match(r"ffmpeg version (\S+)", first)
    if not match:
        raise FfmpegError(f"Not an ffmpeg program: {path}")
    info = FfmpegInfo(
        path=path,
        ffprobe=find_ffprobe(path),
        version=match.group(1),
        encoders=encoders,
        filters=filters,
    )
    with _INFO_LOCK:
        _INFO_CACHE[key] = info
    return info


def locate(configured: str = "") -> FfmpegInfo:
    """Find and inspect ffmpeg, raising ``FfmpegError`` when it is unusable."""

    path = find_ffmpeg(configured)
    if path is None:
        raise FfmpegError("ffmpeg was not found")
    return inspect_ffmpeg(path)


# ── Media info ───────────────────────────────────────────────────────────────


@dataclass
class MediaInfo:
    path: Path
    size: int
    duration: float
    format_names: tuple[str, ...] = ()
    bit_rate: int = 0
    video_codec: str = ""
    width: int = 0
    height: int = 0
    rotation: int = 0
    fps: float = 0.0
    pix_fmt: str = ""
    video_bit_rate: int = 0
    frame_count: int = 0
    audio_codec: str = ""
    audio_bit_rate: int = 0
    audio_channels: int = 0
    video_duration: float = 0.0  # the video stream alone; 0 when unknown

    @property
    def picture_duration(self) -> float:
        """How long the picture lasts: the video stream, else the container.

        The container duration is the longest stream, so music that runs on
        after the last frame would push cover times past the picture.
        """

        return self.video_duration or self.duration

    @property
    def display_size(self) -> tuple[int, int]:
        """Width × height as played: ffmpeg applies the rotation tag."""

        if self.rotation % 180:
            return self.height, self.width
        return self.width, self.height

    @property
    def short_side(self) -> int:
        return min(self.width, self.height) if self.width and self.height else 0

    @property
    def is_mp4(self) -> bool:
        # The mov demuxer names itself "mov,mp4,m4a,..." for .mov files too.
        return "mp4" in self.format_names and self.path.suffix.lower() in (".mp4", ".m4v")

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)


def _fps(text: str) -> float:
    try:
        value = Fraction(str(text or "0/1"))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return float(value) if value > 0 else 0.0


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stream_fps(stream: dict) -> float:
    """The nominal rate for constant-frame-rate video, else the average.

    ``avg_frame_rate`` is frames / stream duration, so an audio track or edit
    list that runs a little longer drags it off 30 to 29.67.
    """

    nominal = _fps(stream.get("r_frame_rate"))
    average = _fps(stream.get("avg_frame_rate"))
    if nominal and average and abs(nominal - average) / nominal <= 0.02:
        return nominal
    return average or nominal


def _stream_duration(stream: dict) -> float:
    """Seconds from the stream, or Matroska's ``DURATION`` tag (``HH:MM:SS.nnn``)."""

    seconds = _float(stream.get("duration"))
    if seconds > 0:
        return seconds
    tags = stream.get("tags") or {}
    tag = next((str(v) for k, v in tags.items() if k.upper() == "DURATION"), "")
    parts = tag.split(":")
    if len(parts) != 3:
        return 0.0
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return 0.0


def _stream_rotation(stream: dict) -> int:
    rotation = _int((stream.get("tags") or {}).get("rotate"))
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            rotation = _int(side.get("rotation"))
    return rotation % 360


def media_info_from_probe(path: Path, data: dict, size: int | None = None) -> MediaInfo:
    """Build ``MediaInfo`` from ``ffprobe -print_format json`` output."""

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video_duration = _stream_duration(video) if video else 0.0
    duration = _float(fmt.get("duration")) or video_duration
    info = MediaInfo(
        path=path,
        size=size if size is not None else _int(fmt.get("size")),
        duration=duration,
        format_names=tuple(str(fmt.get("format_name") or "").split(",")) if fmt.get("format_name") else (),
        bit_rate=_int(fmt.get("bit_rate")),
    )
    if video:
        info.video_codec = str(video.get("codec_name") or "")
        info.width = _int(video.get("width"))
        info.height = _int(video.get("height"))
        info.rotation = _stream_rotation(video)
        info.fps = _stream_fps(video)
        info.pix_fmt = str(video.get("pix_fmt") or "")
        info.video_bit_rate = _int(video.get("bit_rate"))
        info.frame_count = _int(video.get("nb_frames"))
        info.video_duration = video_duration
    if audio:
        info.audio_codec = str(audio.get("codec_name") or "")
        info.audio_bit_rate = _int(audio.get("bit_rate"))
        info.audio_channels = _int(audio.get("channels"))
    return info


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_BITRATE_RE = re.compile(r"Duration:.*?bitrate:\s*(\d+)\s*kb/s")
_INPUT_RE = re.compile(r"Input #0,\s*([^,]+(?:,[^,\s]+)*),\s*from")
_VIDEO_RE = re.compile(r"Stream #0:\d+.*?: Video: (\w+)[^,]*,\s*(\w+)?.*?(\d{2,5})x(\d{2,5})")
_AUDIO_RE = re.compile(r"Stream #0:\d+.*?: Audio: (\w+)")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_VBR_RE = re.compile(r"(\d+)\s*kb/s")


def media_info_from_banner(path: Path, text: str, size: int) -> MediaInfo:
    """Fallback when there is no ffprobe: parse ``ffmpeg -i`` stderr."""

    match = _DURATION_RE.search(text)
    duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3)) if match else 0.0
    info = MediaInfo(path=path, size=size, duration=duration)
    if match := _INPUT_RE.search(text):
        info.format_names = tuple(part.strip() for part in match.group(1).split(","))
    if match := _BITRATE_RE.search(text):
        info.bit_rate = int(match.group(1)) * 1000
    for line in text.splitlines():
        if not info.video_codec and (video := _VIDEO_RE.search(line)) and "attached pic" not in line:
            info.video_codec = video.group(1)
            info.pix_fmt = (video.group(2) or "").strip()
            info.width, info.height = int(video.group(3)), int(video.group(4))
            if fps := _FPS_RE.search(line):
                info.fps = float(fps.group(1))
            if rate := _VBR_RE.search(line):
                info.video_bit_rate = int(rate.group(1)) * 1000
        elif not info.audio_codec and (audio := _AUDIO_RE.search(line)):
            info.audio_codec = audio.group(1)
            if rate := _VBR_RE.search(line):
                info.audio_bit_rate = int(rate.group(1)) * 1000
            info.audio_channels = 1 if " mono" in line else 2
    if match := re.search(r"rotate\s*:\s*(-?\d+)", text):
        info.rotation = int(match.group(1)) % 360
    elif match := re.search(r"rotation of (-?\d+(?:\.\d+)?) degrees", text):
        info.rotation = int(float(match.group(1))) % 360
    return info


def probe(ffmpeg: FfmpegInfo, path: str | os.PathLike) -> MediaInfo:
    """Duration, streams and size of a local media file."""

    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise FfmpegError(str(exc)) from exc
    try:
        if ffmpeg.ffprobe is not None:
            output = _run_text(
                [
                    str(ffmpeg.ffprobe), "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(source),
                ],
                timeout=60,
            )
            start = output.find("{")
            if start < 0:
                raise FfmpegError(output.strip() or "ffprobe returned nothing")
            info = media_info_from_probe(source, json.loads(output[start:]), size)
        else:
            info = media_info_from_banner(source, _run_text([str(ffmpeg.path), "-hide_banner", "-i", str(source)], 60), size)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise FfmpegError(str(exc)) from exc
    if not info.video_codec:
        raise FfmpegError("No video stream")
    if info.duration <= 0:
        raise FfmpegError("Unknown duration")
    return info
