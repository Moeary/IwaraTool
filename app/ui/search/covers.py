"""Load result covers and avatars and turn them into item icons."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

from .image_worker import SearchImageWorker
from ..theme import qcolor
from ..worker_lifecycle import on_finished

from .results_layout import _VIDEO_ICON_SIZE
from .page_dependencies import read_pixmap


class SearchCoversMixin:
    """Load result covers and avatars and turn them into item icons."""

    def _start_image_loading(self):
        if self._is_list_view() and not self._all_authors:
            return
        jobs: list[tuple[str, str, str]] = []
        pending_keys = set(self._image_pending_keys)
        for worker in self._image_workers:
            if worker.isRunning():
                pending_keys.update(
                    f"{kind}:{item_key}" for kind, item_key, _image_url in worker.jobs
                )
        if self._all_videos:
            for video in self._all_videos:
                if video.thumbnail_url:
                    key = f"video:{video.video_id}"
                    if key not in self._image_path_by_key and key not in pending_keys:
                        jobs.append(("video", video.video_id, video.thumbnail_url))
        if self._all_authors:
            for author in self._all_authors:
                if author.avatar_url:
                    key = f"author:{author.author_id}"
                    if key not in self._image_path_by_key and key not in pending_keys:
                        jobs.append(("author", author.author_id, author.avatar_url))
        for playlist in self._all_playlists:
            if playlist.thumbnail_url:
                key = f"playlist:{playlist.playlist_id}"
                if key not in self._image_path_by_key and key not in pending_keys:
                    jobs.append(("playlist", playlist.playlist_id, playlist.thumbnail_url))
        if not jobs:
            return
        self._image_pending_keys.update(f"{kind}:{item_key}" for kind, item_key, _ in jobs)
        worker = SearchImageWorker(
            self._generation,
            jobs,
            concurrency=self._cover_download_concurrency(),
        )
        self._image_workers.append(worker)
        worker.image_ready.connect(self._on_image_ready)
        on_finished(worker, self, self._cleanup_image_worker)
        worker.start()

    def _on_image_ready(self, generation: int, kind: str, item_key: str, path: str):
        if generation != self._generation:
            return
        key = f"{kind}:{item_key}"
        if not key:
            return
        self._image_path_by_key[key] = path
        self._image_pending_keys.discard(key)
        item = self._item_by_key.get(key)
        if item:
            item.setIcon(self._image_icon(path))

    def _cleanup_image_worker(self, worker: SearchImageWorker):
        if worker in self._image_workers:
            self._image_workers.remove(worker)
        for kind, item_key, _image_url in worker.jobs:
            key = f"{kind}:{item_key}"
            if key not in self._image_path_by_key:
                self._image_pending_keys.discard(key)
        worker.deleteLater()

    def _placeholder_icon(self, kind: str) -> QIcon:
        # One grey tile per (size, theme colour), shared by every card without a cover yet.
        size = getattr(self, "_grid_icon_size", _VIDEO_ICON_SIZE)
        color = qcolor("placeholder")
        cache = self.__dict__.setdefault("_placeholder_icons", {})
        key = (size.width(), size.height(), color.name())
        icon = cache.get(key)
        if icon is None:
            if len(cache) > 8:
                cache.clear()
            pixmap = QPixmap(size)
            pixmap.fill(color)
            icon = cache[key] = QIcon(pixmap)
        return icon

    def _image_icon(self, path: str) -> QIcon:
        size = getattr(self, "_grid_icon_size", _VIDEO_ICON_SIZE)
        # Decode at the size a card shows (plus some headroom), not at the file's
        # native size: a 1280 x 720 cover is 3.7 MB decoded and a page has
        # 100 of them.  Decoded covers are kept up to a byte budget so a resize
        # can rebuild the cards without reading the files; a cover cached
        # smaller than a bigger card now needs is read again.
        want = max(240, round(size.width() * 1.25))
        cache = self._source_pixmaps
        pixmap = cache.get(path)
        if pixmap is None or (pixmap.width() < want and path not in self._small_sources):
            pixmap = read_pixmap(path, want)
            if pixmap.isNull():
                return QIcon()
            if pixmap.width() < want:
                self._small_sources.add(path)  # the file itself is that small
            cache[path] = pixmap
        if pixmap.isNull():
            return QIcon()
        scaled = pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.width() > size.width() or scaled.height() > size.height():
            x = max(0, (scaled.width() - size.width()) // 2)
            y = max(0, (scaled.height() - size.height()) // 2)
            scaled = scaled.copy(x, y, size.width(), size.height())
        return QIcon(scaled)

    def _refresh_item_icons(self):
        """Re-render every card image at the current grid icon size."""

        for key, item in self._item_by_key.items():
            path = self._image_path_by_key.get(key)
            icon = self._image_icon(path) if path else QIcon()
            item.setIcon(icon if not icon.isNull() else self._placeholder_icon(key.split(":", 1)[0]))
