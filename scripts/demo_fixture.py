"""An offline, made-up Iwara for recording the README demo and screenshots.

``install()`` must run before anything from ``app`` is imported.  It

* points the app's data and download folders at a throw-away directory, so the
  user's settings, history and subscriptions are never read or touched;
* replaces every HTTP session the app creates (``cloudscraper.create_scraper``)
  with one whose transport answers from made-up data: invented posts, authors,
  comments and playlists for ``api.iwara.tv``, generated abstract artwork for
  every image (no real cover or avatar is ever shown), and slowly streamed
  filler bytes for video files, so downloads show real progress.

Nothing leaves the machine.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import random
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

AUTHORS = [
    ("aoi_motion", "Aoi Motion"),
    ("kumo_studio", "Kumo Studio"),
    ("lumen3d", "Lumen 3D"),
    ("mmd_sakura", "Sakura MMD"),
    ("night_owl", "Night Owl"),
    ("pastel_works", "Pastel Works"),
    ("riverlight", "Riverlight"),
    ("tsuki_anim", "Tsuki Animation"),
]
TITLE_WORDS = [
    ("Starlight", "Dance"), ("Summer", "Breeze"), ("Neon", "Night"), ("Cherry", "Blossom"),
    ("Midnight", "Waltz"), ("Ocean", "Wave"), ("Crystal", "Heart"), ("Aurora", "Stage"),
    ("Velvet", "Moon"), ("Lantern", "Festival"), ("Snow", "Melody"), ("Golden", "Hour"),
    ("Rainy", "Station"), ("Sky", "Parade"), ("Comet", "Trail"), ("Paper", "Planes"),
]
TAGS = ["dance", "mmd", "original", "music", "loop", "4k", "60fps", "animation", "cute", "stage"]
VIDEO_BYTES = 48 * 1024 * 1024  # each fake video file
STREAM_BYTES_PER_SECOND = 6 * 1024 * 1024
LOG: list[str] = []  # every URL asked for (handy when the app learns a new endpoint)


# ── made-up catalogue ───────────────────────────────────────────────────────


def _rng(*parts) -> random.Random:
    return random.Random(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest())


def _now() -> datetime:
    return datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _user(index: int) -> dict:
    username, name = AUTHORS[index % len(AUTHORS)]
    return {
        "id": f"user-{username}",
        "username": username,
        "name": name,
        "avatar": {"id": f"avatar-{username}", "name": "avatar.png", "mime": "image/png"},
        "premium": index % 3 == 0,
        "following": index % 2 == 0,
    }


def video(index: int, site: str = "iwara") -> dict:
    rng = _rng("video", site, index)
    first, second = TITLE_WORDS[index % len(TITLE_WORDS)]
    title = f"{first} {second}" + (f" {index // len(TITLE_WORDS) + 1}" if index >= len(TITLE_WORDS) else "")
    if site == "iwara_ai":
        title = f"AI {title}"
    created = _now() - timedelta(hours=index * 7 + rng.randint(0, 5))
    vid = f"demo{'ai' if site == 'iwara_ai' else ''}{index:03d}"
    return {
        "id": vid,
        "slug": title.lower().replace(" ", "-"),
        "title": title,
        "body": f"A made-up post for the IwaraTool demo.\n#{TAGS[index % len(TAGS)]} #{TAGS[(index + 3) % len(TAGS)]}",
        "siteId": site,
        "rating": "general",
        "numLikes": rng.randint(300, 9000),
        "numViews": rng.randint(8000, 260000),
        "numComments": rng.randint(4, 160),
        "createdAt": created.isoformat().replace("+00:00", "Z"),
        "updatedAt": created.isoformat().replace("+00:00", "Z"),
        "user": _user(index),
        "tags": [{"id": TAGS[index % len(TAGS)]}, {"id": TAGS[(index + 3) % len(TAGS)]}, {"id": TAGS[(index + 5) % len(TAGS)]}],
        "file": {"id": f"file-{vid}", "duration": rng.randint(60, 300), "type": "video"},
        "fileUrl": f"https://files.iwara.tv/file/file-{vid}?expires=9999999999&hash=demo",
        "thumbnail": rng.randint(0, 11),
        "liked": False,
        "private": False,
    }


def image_post(index: int, site: str = "iwara") -> dict:
    rng = _rng("image", site, index)
    first, second = TITLE_WORDS[(index + 5) % len(TITLE_WORDS)]
    iid = f"img{index:03d}"
    files = [{"id": f"gallery-{iid}-{n}", "name": f"{n}.png", "type": "image"} for n in range(rng.randint(2, 5))]
    return {
        "id": iid,
        "title": f"{first} {second} Illustration",
        "body": "A made-up gallery for the IwaraTool demo.",
        "siteId": site,
        "rating": "general",
        "numLikes": rng.randint(100, 4000),
        "numViews": rng.randint(2000, 90000),
        "numImages": len(files),
        "createdAt": (_now() - timedelta(hours=index * 9)).isoformat().replace("+00:00", "Z"),
        "user": _user(index + 2),
        "tags": [{"id": "original"}, {"id": TAGS[index % len(TAGS)]}],
        "thumbnail": files[0],
        "files": files,
    }


def _page(items: list, query: dict) -> dict:
    limit = int((query.get("limit") or ["32"])[0])
    page = int((query.get("page") or ["0"])[0])
    return {"count": len(items), "limit": limit, "page": page, "results": items[page * limit:(page + 1) * limit]}


def _site_id(headers) -> str:
    return "iwara_ai" if "iwara.ai" in str(headers.get("X-Site", "")) else "iwara"


def api_answer(method: str, url: str, headers) -> tuple[int, object]:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)
    site = _site_id(headers)
    count = 24 if site == "iwara_ai" else 64
    videos = [video(i, site) for i in range(count)]
    if path == "/videos":
        sort = (query.get("sort") or ["date"])[0]
        ordered = videos if sort == "date" else sorted(videos, key=lambda v: -v["numViews"])
        user = (query.get("user") or [""])[0]
        if user:
            ordered = [v for v in ordered if v["user"]["id"] == user]
        return 200, _page(ordered, query)
    if path == "/videos/subscriptions":
        return 200, _page(videos[3:], query)
    if path == "/images":
        return 200, _page([image_post(i, site) for i in range(30)], query)
    if path == "/search":
        kind = (query.get("type") or ["videos"])[0]
        words = (query.get("query") or [""])[0].casefold()
        if kind == "users":
            users = [_user(i) for i in range(len(AUTHORS))]
            return 200, _page([u for u in users if words in u["username"] or words in u["name"].casefold()] or users, query)
        if kind == "images":
            return 200, _page([image_post(i, site) for i in range(30)], query)
        hits = [
            v for v in videos
            if words in v["title"].casefold() or any(words == tag["id"] for tag in v["tags"])
        ] or videos
        return 200, _page(hits, query)
    if path.startswith("/video/") and path.endswith("/related"):
        return 200, {"results": videos[8:20]}
    if path.startswith("/video/") or path.startswith("/image/"):
        parts = path.split("/")
        item_id = parts[2]
        if len(parts) > 3 and parts[3] == "comments":
            return 200, _comments(item_id, query)
        if path.startswith("/image/"):
            index = int("".join(ch for ch in item_id if ch.isdigit()) or 0)
            return 200, image_post(index, site)
        index = int("".join(ch for ch in item_id if ch.isdigit()) or 0)
        post_site = "iwara_ai" if "ai" in item_id else "iwara"
        return 200, video(index, post_site)
    if path.startswith("/profile/"):
        username = path.split("/")[2]
        index = next((i for i, (name, _) in enumerate(AUTHORS) if name == username), 0)
        return 200, {"user": _user(index), "body": "Made-up creator for the demo.", "header": None}
    if path.startswith("/user/") and path.endswith("/following"):
        return 200, _page([{"user": _user(i)} for i in range(len(AUTHORS))], query)
    if path.startswith("/user/") and path.endswith("/followers"):
        return 200, _page([], query)
    if path in ("/user", "/user/me"):
        return 200, {"user": {"id": "user-demo", "username": "demo_user", "name": "Demo User", "premium": False}}
    if path == "/playlists":
        return 200, _page([], query)
    if path.startswith("/playlist/"):
        return 200, _page(videos[:12], query)
    if path.startswith("/user/token") or path.startswith("/user/login"):
        return 200, {"token": "demo-token"}
    return 404, {"message": "errors.notFound"}


def _comments(item_id: str, query: dict) -> dict:
    rng = _rng("comments", item_id)
    lines = [
        "Lovely motion, the lighting is great!", "The camera work in the chorus is so smooth.",
        "Saved for later, thank you!", "This loop is perfect background music.",
        "How long did the render take?", "The colours remind me of summer festivals.",
    ]
    results = []
    for n in range(rng.randint(3, 6)):
        user = _user(rng.randint(0, 20))
        results.append({
            "id": f"c-{item_id}-{n}", "body": lines[(n + rng.randint(0, 5)) % len(lines)],
            "user": user, "numReplies": 0,
            "createdAt": (_now() - timedelta(hours=n * 5 + 1)).isoformat().replace("+00:00", "Z"),
        })
    return {"count": len(results), "limit": 8, "page": 0, "results": results}


def file_sources(url: str) -> list[dict]:
    file_id = urlparse(url).path.rsplit("/", 1)[-1]
    return [
        {"name": quality, "src": {"view": f"//demo-cdn.iwara.tv/view/{file_id}/{quality}.mp4",
                                  "download": f"//demo-cdn.iwara.tv/download/{file_id}/{quality}.mp4"}}
        for quality in ("Source", "540", "360")
    ]


# ── artwork ─────────────────────────────────────────────────────────────────

_PALETTES = [
    ("#a78bfa", "#f0abfc"), ("#60a5fa", "#a7f3d0"), ("#f9a8d4", "#fde68a"), ("#818cf8", "#67e8f9"),
    ("#fb7185", "#fdba74"), ("#34d399", "#93c5fd"), ("#c084fc", "#fca5a5"), ("#38bdf8", "#c4b5fd"),
]
_IMAGE_LOCK = threading.Lock()
_IMAGE_CACHE: dict[str, bytes] = {}


def artwork(key: str, *, avatar: bool = False) -> bytes:
    """Abstract generated artwork (gradient, soft circles, a wave): never a real picture."""

    with _IMAGE_LOCK:
        cached = _IMAGE_CACHE.get(key)
    if cached is not None:
        return cached
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient

    rng = _rng("art", key)
    width, height = (160, 160) if avatar else (640, 360)
    top, bottom = _PALETTES[rng.randrange(len(_PALETTES))]
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0, QColor(top))
    gradient.setColorAt(1, QColor(bottom))
    painter.fillRect(QRectF(0, 0, width, height), gradient)
    for _ in range(3 if avatar else 6):
        r = rng.uniform(0.12, 0.45) * height
        center = QPointF(rng.uniform(0, width), rng.uniform(0, height))
        glow = QRadialGradient(center, r)
        glow.setColorAt(0, QColor(255, 255, 255, rng.randint(70, 140)))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, r, r)
    if not avatar:
        wave = QPainterPath(QPointF(0, height * 0.72))
        phase = rng.uniform(0, math.tau)
        for x in range(0, width + 8, 8):
            wave.lineTo(x, height * 0.72 + math.sin(x / width * math.tau * 1.5 + phase) * height * 0.06)
        wave.lineTo(width, height)
        wave.lineTo(0, height)
        wave.closeSubpath()
        painter.setBrush(QColor(255, 255, 255, 60))
        painter.drawPath(wave)
    else:
        painter.setBrush(QColor(255, 255, 255, 170))
        painter.drawEllipse(QPointF(width / 2, height * 0.42), width * 0.17, width * 0.17)
        painter.drawEllipse(QRectF(width * 0.24, height * 0.66, width * 0.52, height * 0.5))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG" if not avatar else "PNG", 88)
    payload = bytes(data.data())
    with _IMAGE_LOCK:
        _IMAGE_CACHE[key] = payload
    return payload


# ── transport ───────────────────────────────────────────────────────────────


class _SlowBody(io.RawIOBase):
    """Filler bytes at a steady pace, so a download shows progress."""

    def __init__(self, size: int, start: int = 0):
        self._left = max(0, size - start)
        self._last = time.monotonic()

    def readable(self):
        return True

    def readinto(self, buffer):
        if self._left <= 0:
            return 0
        wanted = min(len(buffer), self._left, 256 * 1024)
        time.sleep(wanted / STREAM_BYTES_PER_SECOND)
        buffer[:wanted] = b"\0" * wanted
        self._left -= wanted
        return wanted

    def stream(self, amt=65536, decode_content=None):
        while True:
            chunk = self.read(amt)
            if not chunk:
                break
            yield chunk

    def release_conn(self):
        pass


class FakeAdapter(BaseAdapter):
    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        url = request.url
        LOG.append(f"{request.method} {url}")
        host = urlparse(url).netloc
        status, headers, body = 200, {}, b""
        raw = None
        if "/image/" in urlparse(url).path:
            avatar = "/avatar/" in url or "avatar-" in url
            body = artwork(urlparse(url).path, avatar=avatar)
            headers["Content-Type"] = "image/png" if avatar else "image/jpeg"
        elif host.startswith("api.") or host.startswith("apiq."):
            status, data = api_answer(request.method, url, request.headers)
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        elif host.startswith("files.") or host.startswith("filesq."):
            headers["Content-Type"] = "application/json"
            body = json.dumps(file_sources(url)).encode()
        elif host.startswith("demo-cdn."):
            start = 0
            range_header = request.headers.get("Range", "")
            if range_header.startswith("bytes="):
                start = int(range_header[6:].split("-")[0] or 0)
                status = 206
                headers["Content-Range"] = f"bytes {start}-{VIDEO_BYTES - 1}/{VIDEO_BYTES}"
            headers["Content-Type"] = "video/mp4"
            headers["Content-Length"] = str(VIDEO_BYTES - start)
            headers["Accept-Ranges"] = "bytes"
            if request.method != "HEAD":
                raw = _SlowBody(VIDEO_BYTES, start)
        elif host.startswith("i.") or "iwara" in host:
            avatar = "/avatar/" in url or "avatar-" in url
            body = artwork(urlparse(url).path, avatar=avatar)
            headers["Content-Type"] = "image/png" if avatar else "image/jpeg"
        else:
            status, body = 404, b""
        response = requests.Response()
        response.status_code = status
        response.headers = CaseInsensitiveDict(headers)
        if raw is None:
            headers.setdefault("Content-Length", str(len(body)))
            response.headers = CaseInsensitiveDict(headers)
            raw = io.BytesIO(body)
        response.raw = raw
        response.url = url
        response.request = request
        response.reason = "OK" if status < 400 else "Not Found"
        response.encoding = "utf-8"
        return response

    def close(self):
        pass


def fake_session(*_args, **_kwargs) -> requests.Session:
    session = requests.Session()
    adapter = FakeAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ── environment ─────────────────────────────────────────────────────────────


def install(language: str = "en", theme: str = "light") -> Path:
    """Isolate the app's folders and network; returns the throw-away data folder."""

    import cloudscraper

    # A short folder, since its path shows in the log, the history page and settings.
    root = Path(tempfile.mkdtemp(prefix="IwaraTool-", dir=None if os.name == "nt" else "/tmp"))
    data, downloads = root / "data", root / "Downloads"
    data.mkdir()
    downloads.mkdir()

    import app.paths as paths

    paths.app_data_dir = lambda: data
    paths.default_download_dir = lambda: downloads
    cloudscraper.create_scraper = fake_session
    requests.Session.get_adapter = lambda self, url: FakeAdapter()  # any plain requests session too

    from app.config import app_config

    for key, value in {
        "ui_language": language,
        "theme_mode": theme,
        "download_dir": str(downloads),
        "aria2_rpc_enabled": False,
        "update_check_enabled": False,
        "subscription_auto_refresh_enabled": False,
        "desktop_notifications_enabled": False,
        "request_min_interval_ms": 0,
        "download_thumbnail": False,
        "collect_nfo_info": False,
        "minimize_to_tray": False,
        "search_default_source": "iwara",
        "subscription_prompt_mode": "never",
        "max_concurrent": 3,
    }.items():
        setattr(app_config, key, value)
    return root
