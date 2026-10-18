"""Play, download, like, share and author actions of the detail view."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from qfluentwidgets import InfoBar, InfoBarPosition

from ...core.rules import page_rule_id
from ...core.search.models import SearchVideo
from ..downloads.staging_flow import stage_videos
from ...i18n import tr
from ...signal_bus import signal_bus
from ...core import shortcut_defs
from ..home.workers import ApiCallWorker
from ..formatting import _format_count
from ..player.video_player import VideoPlayer
from .page_dependencies import download_manager


class DetailActionsMixin:
    """Play, download, like, share and author actions of the detail view."""

    def _page_url(self) -> str:
        return f"https://www.iwara.tv/{self._kind}/{self._item_id}"

    def _can_play(self) -> bool:
        return self._video is not None and self._kind == "video" and self._video.downloadable

    def _play(self):
        """Play on the page, in place of the cover (the local file when it is on disk)."""

        if not self._can_play():
            return
        from ...core import video_player

        if self._player is None:
            self._player = VideoPlayer(self.player_backend_factory, self._player_box, allow_popout=True)
            self._player.popout_requested.connect(self._pop_out)
            self._player_box.layout().addWidget(self._player)
        video = self._video
        local = video_player.local_video_path(video.video_id)
        if local:
            self._player.play_local(local, video.title, video.video_id)
        else:
            self._player.play_remote(video.video_id, video.title)
        self._stage.hide()
        self._player_box.show()
        self._player.setFocus(Qt.FocusReason.OtherFocusReason)

    def _play_in_window(self):
        if self._can_play():
            self._stop_player()
            signal_bus.video_preview_requested.emit(self._video.video_id, self._video.title, "")

    def _pop_out(self):
        """Continue the embedded video in the separate player window."""

        player = self._player
        if player is None or not player.video_id:
            return
        video_id, title, position = player.video_id, player.title, player.position()
        self._stop_player()
        signal_bus.video_popout_requested.emit(video_id, title, position)

    def _stop_player(self):
        if self._player is not None:
            if self._player.is_fullscreen():
                self._player.toggle_fullscreen()
            self._player.stop()
        if hasattr(self, "_player_box"):
            self._player_box.hide()
            self._stage.show()

    def _queue(self):
        if self._kind != "video" or not self._item_id:
            return
        # A lone downloadable video starts at once (Settings); otherwise it waits on the workbench.
        video = self._video if self._video is not None and self._video.video_id == self._item_id else None
        videos = [video] if video is not None else [SearchVideo(video_id=self._item_id, title=self._item_id)]
        stage_videos(self, videos, label=video.title if video is not None else self._item_id, rule_id=page_rule_id("detail"))

    def _open_in_browser(self):
        webbrowser.open(self._page_url())

    def _copy_link(self):
        QApplication.clipboard().setText(self._page_url())
        InfoBar.success(
            title=tr("Link copied", "链接已复制", "リンクをコピーしました"), content=self._page_url(),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2000, parent=self.window(),
        )

    def _sync_like(self):
        likes = self._video.likes if self._video is not None else 0
        count = _format_count(likes)
        self._like_btn.blockSignals(True)
        self._like_btn.setChecked(self._liked)
        self._like_btn.blockSignals(False)
        self._like_btn.setText(
            tr(f"Liked · {count}", f"已点赞 · {count}", f"いいね済み · {count}")
            if self._liked
            else tr(f"Like · {count}", f"点赞 · {count}", f"いいね · {count}")
        )
        text = (
            tr("Click to remove your like", "再次点击取消点赞", "もう一度押すと取り消し")
            if self._liked
            else tr("Like this post with your Iwara account", "用你的 Iwara 账号点赞", "Iwaraアカウントでいいねする")
        )
        key = shortcut_defs.key_for("detail_like")
        self._like_btn.setToolTip(f"{text} ({key})" if key else text)

    def _toggle_like(self):
        if self._like_busy or self._video is None:
            self._sync_like()
            return
        if not download_manager.is_logged_in():
            self._sync_like()
            InfoBar.warning(
                title=tr("Sign in to like", "登录后才能点赞", "いいねにはログインが必要です"),
                content=tr("Sign in to Iwara in Settings first.", "请先在设置中登录 Iwara 账号。", "先に設定でIwaraにログインしてください。"),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self.window(),
            )
            return
        wanted = not self._liked
        kind, item_id, token = self._kind, self._item_id, self._token
        self._like_busy = True
        self._like_btn.setEnabled(False)
        worker = ApiCallWorker(lambda client: client.set_liked(kind, item_id, wanted), self)
        worker.done.connect(lambda result, error, wanted=wanted, token=token: self._on_like_done(token, wanted, result, error))
        worker.finished.connect(lambda worker=worker: self._discard_like_worker(worker))
        self._like_workers.append(worker)
        worker.start()

    def _discard_like_worker(self, worker: ApiCallWorker):
        if worker in self._like_workers:
            self._like_workers.remove(worker)
        worker.deleteLater()

    def _on_like_done(self, token: int, wanted: bool, result: object, error: str):
        self._like_busy = False
        if token != self._token:
            return  # the user moved on to another post
        ok, message = result if isinstance(result, tuple) and len(result) == 2 else (False, error)
        self._like_btn.setEnabled(True)
        if ok:
            self._liked = wanted
            if self._video is not None:
                self._video.likes = max(0, self._video.likes + (1 if wanted else -1))
                self._meta.setText(self._meta_text(self._video))
        else:
            InfoBar.error(
                title=tr("Could not change the like", "点赞操作失败", "いいねを変更できません"), content=str(message or error),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=4500, parent=self.window(),
            )
        self._sync_like()

    def _search_tag(self, tag_id: str):
        scope = "images" if self._kind == "image" else "tags"
        signal_bus.search_requested.emit({"scope": scope, "keyword": tag_id})

    def _author_works(self):
        if self._author_target:
            signal_bus.search_requested.emit({"author": self._author_target})

    def _author_page(self):
        if self._author_target:
            signal_bus.author_page_requested.emit(self._author_target)
