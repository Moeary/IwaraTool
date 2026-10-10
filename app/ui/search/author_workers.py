"""Look up result authors on Iwara and Oreno3D in the background."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal

from ...core.search import SearchAuthor, SearchVideo, normalize_author
from ...i18n import tr
from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager
from .result_authors import _extract_iwara_video_id


class SearchIwaraAuthorWorker(ManagedThread):
    """Hydrate one Iwara video's metadata before an author action."""

    result_ready = Signal(object)

    def __init__(self, generation: int, video_id: str):
        super().__init__()
        self.generation = generation
        self.video_id = str(video_id or "").strip()

    def run(self):
        metadata: dict[str, Any] = {}
        error = ""
        try:
            value, error = download_manager.get_iwara_video_info(self.video_id)
            if isinstance(value, dict):
                metadata = dict(value)
        except Exception as exc:
            error = str(exc)
        self.result_ready.emit(
            {
                "generation": self.generation,
                "video_id": self.video_id,
                "metadata": metadata,
                "error": str(error or ""),
            }
        )


class SearchAuthorProfileWorker(ManagedThread):
    """Resolve a known Iwara account to its real user ID off the UI thread."""

    result_ready = Signal(object)

    def __init__(
        self,
        generation: int,
        action_generation: int,
        target: tuple[str, str, str, str],
    ):
        super().__init__()
        self.generation = generation
        self.action_generation = action_generation
        self.target = target

    def run(self):
        author: SearchAuthor | None = None
        error = ""
        try:
            profile, error = download_manager.get_search_user_profile(self.target[0])
            user = profile.get("user") if isinstance(profile, dict) else None
            # normalize_author historically falls back to the username for
            # card identity. That fallback cannot be sent as a /videos user ID.
            if isinstance(user, dict) and str(user.get("id") or user.get("userId") or user.get("user_id") or "").strip():
                author = normalize_author(profile)
            if author is None and not error:
                error = tr(
                    "The Iwara profile did not contain a user ID",
                    "Iwara 作者资料未包含用户 ID",
                    "Iwara作者プロフィールにユーザーIDがありません",
                )
        except Exception as exc:
            error = str(exc)
        if self.isInterruptionRequested():
            return
        self.result_ready.emit(
            {
                "generation": self.generation,
                "action_generation": self.action_generation,
                "author": author,
                "error": str(error or ""),
            }
        )


class SearchOrenoAuthorWorker(ManagedThread):
    """Resolve an Oreno3D author page and map it to an Iwara profile."""

    result_ready = Signal(object)

    def __init__(self, generation: int, video: SearchVideo, *, max_videos: int = 8):
        super().__init__()
        self.generation = generation
        self.video = video
        self.video_id = str(video.video_id or "").strip()
        self.max_videos = max(1, min(20, int(max_videos)))

    def run(self):
        result: dict[str, Any] = {}
        error = ""
        try:
            resolver = getattr(download_manager, "resolve_oreno3d_author", None)
            if not callable(resolver):
                raise RuntimeError("Oreno3D author resolver is unavailable")
            raw = self.video.raw if isinstance(self.video.raw, dict) else {}
            source_id = self.video_id.removeprefix("oreno3d:")
            source_url = str(raw.get("oreno3d_url") or self.video.source_url or "").strip()
            kwargs = {
                "author_url": str(raw.get("oreno3d_author_url") or "").strip(),
                "author_name": str(raw.get("oreno3d_author_name") or self.video.author_name or "").strip(),
                "max_videos": self.max_videos,
                "parallel": True,
            }
            iwara_id = self.video.download_video_id or _extract_iwara_video_id(self.video.iwara_url)
            if iwara_id:
                kwargs["iwara_video_id"] = iwara_id
            if isinstance(raw.get("oreno_iwara_author"), dict):
                kwargs["iwara_author"] = dict(raw["oreno_iwara_author"])
            try:
                value = resolver(source_id, source_url, **kwargs)
            except TypeError as exc:
                # Keep older integrations/fakes usable while the manager API
                # rolls out the durable-author parameters.
                if not any(name in str(exc) for name in ("author_url", "author_name", "max_videos", "parallel", "iwara_author", "iwara_video_id")):
                    raise
                value = resolver(source_id, source_url)
            if isinstance(value, dict):
                result = dict(value)
                error = str(result.get("error") or "")
            else:
                error = "Oreno3D author resolver returned no result"
        except Exception as exc:
            error = str(exc)
        self.result_ready.emit(
            {
                "generation": self.generation,
                "video_id": self.video_id,
                "result": result,
                "error": error,
            }
        )
