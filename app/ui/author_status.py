"""Who-is-this-author status: subscribed in the app? followed on Iwara?

A small bar (two chips and the buttons that change them) shared by the author
page, the subscription grid and the post detail page.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget

from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, PrimaryPushButton, PushButton

from ..core.manager import download_manager
from ..i18n import tr
from ..signal_bus import signal_bus
from .chrome import StatusChip
from .home_workers import ApiCallWorker, stop_workers
from .ui_state import ResponsiveFlowLayout, show_fluent_confirmation


def clean_username(value: str) -> str:
    return str(value or "").strip().lstrip("@").strip("/ ")


def local_chip_state(source: dict[str, Any] | None) -> tuple[str, str]:
    """``(text, tone)`` for whether the author is in this app's subscriptions."""

    if source:
        if not int(source.get("enabled", 1) or 0):
            return tr("Subscribed here (paused)", "已在程序内订阅（已停用）", "アプリで購読中（停止）"), "warning"
        return tr("Subscribed in this app", "已在程序内订阅", "アプリで購読中"), "success"
    return tr("Not subscribed in this app", "未在程序内订阅", "アプリでは未購読"), "neutral"


def web_chip_state(state: str, following: bool = False) -> tuple[str, str]:
    """``(text, tone)`` for the Iwara-account follow state.

    ``state`` is one of ``signed_out`` / ``loading`` / ``ready`` / ``error``.
    """

    if state == "signed_out":
        return tr("Iwara: not signed in", "Iwara：未登录", "Iwara: 未ログイン"), "neutral"
    if state == "loading":
        return tr("Checking Iwara…", "正在查询 Iwara…", "Iwaraを確認中…"), "neutral"
    if state == "error":
        return tr("Iwara follow status unavailable", "无法获取 Iwara 关注状态", "Iwaraのフォロー状態を取得できません"), "warning"
    if following:
        return tr("Followed on Iwara", "已在 Iwara 关注", "Iwaraでフォロー中"), "accent"
    return tr("Not followed on Iwara", "未在 Iwara 关注", "Iwaraでは未フォロー"), "neutral"


class AuthorStatusBar(QWidget):
    """Two status chips (app / Iwara) with the actions that change them."""

    open_subscription_requested = Signal(int)
    profile_loaded = Signal(dict)  # the author's ``user`` object

    def __init__(self, parent: QWidget | None = None, *, show_open: bool = True):
        super().__init__(parent)
        self._show_open = show_open
        self._username = ""
        self._name = ""
        self._user_id = ""
        self._avatar = ""
        self._source: dict[str, Any] | None = None
        self._web_state = "signed_out"
        self._following = False
        self._token = 0
        self._workers: list[ApiCallWorker] = []

        flow = ResponsiveFlowLayout(self, spacing=8)
        flow.setContentsMargins(0, 0, 0, 0)
        self.local_chip = StatusChip(parent=self)
        self.web_chip = StatusChip(parent=self)
        flow.addWidget(self.local_chip)
        flow.addWidget(self.web_chip)
        self.subscribe_btn = PrimaryPushButton(tr("Subscribe in app", "在程序内订阅", "アプリで購読"), self, FluentIcon.PEOPLE)
        self.subscribe_btn.clicked.connect(self._on_subscribe_clicked)
        self.follow_btn = PushButton(tr("Follow on Iwara", "在 Iwara 关注", "Iwaraでフォロー"), self, FluentIcon.HEART)
        self.follow_btn.clicked.connect(self._toggle_follow)
        self.open_btn = PushButton(tr("Open subscription", "打开订阅", "購読を開く"), self, FluentIcon.VIEW)
        self.open_btn.clicked.connect(self._open_subscription)
        for button in (self.subscribe_btn, self.follow_btn, self.open_btn):
            flow.addWidget(button)
        signal_bus.login_state_changed.connect(self._on_login_changed)
        self._sync()

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def username(self) -> str:
        return self._username

    @property
    def source_id(self) -> int:
        return int((self._source or {}).get("id", 0) or 0)

    def set_author(self, username: str, name: str = "", user_id: str = "", avatar: str = ""):
        self._username = clean_username(username)
        self._name = name or self._username
        self._user_id = user_id
        self._avatar = avatar
        self._following = False
        self.refresh_local()
        self.refresh_web()

    def refresh_local(self):
        try:
            self._source = download_manager.find_author_subscription(self._username) if self._username else None
        except Exception:
            self._source = None
        self._sync()

    def _on_login_changed(self, _logged_in: bool):
        # The follow state belongs to the account; re-read it for the new one.
        if self._username:
            self.refresh_web()

    def refresh_web(self):
        # Bumping the token also invalidates in-flight follow replies, so a
        # reply for a previous author or account never lands on this one.
        self._token += 1
        if not self._username:
            return
        if not download_manager.is_logged_in():
            self._web_state, self._following = "signed_out", False
            self._sync()
            return
        self._web_state = "loading"
        self._sync()
        token, username = self._token, self._username
        worker = ApiCallWorker(lambda client: client.get_user_profile(username), self)
        worker.done.connect(lambda result, error, token=token: self._on_profile(token, result, error))
        worker.finished.connect(lambda worker=worker: self._discard(worker))
        self._workers.append(worker)
        worker.start()

    def _discard(self, worker: ApiCallWorker):
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def _on_profile(self, token: int, result: Any, error: str):
        if token != self._token:
            return
        data, api_error = result if isinstance(result, tuple) and len(result) == 2 else (None, error)
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            self._web_state = "error"
            self.web_chip.setToolTip(str(api_error or error))
            self._sync()
            return
        self._web_state = "ready"
        self._following = bool(user.get("following"))
        self._user_id = str(user.get("id") or self._user_id)
        self.web_chip.setToolTip("")
        self._sync()
        self.profile_loaded.emit(user)

    def _sync(self):
        text, tone = local_chip_state(self._source)
        self.local_chip.set_state(text, tone)
        text, tone = web_chip_state(self._web_state, self._following)
        self.web_chip.set_state(text, tone)
        subscribed = self._source is not None
        self.subscribe_btn.setText(
            tr("Remove app subscription", "取消程序内订阅", "アプリの購読を解除") if subscribed else tr("Subscribe in app", "在程序内订阅", "アプリで購読")
        )
        self.subscribe_btn.setIcon(FluentIcon.REMOVE if subscribed else FluentIcon.PEOPLE)
        self.subscribe_btn.setEnabled(bool(self._username))
        usable = self._web_state in {"ready"} and bool(self._user_id)
        self.follow_btn.setEnabled(usable)
        self.follow_btn.setText(
            tr("Unfollow on Iwara", "取消 Iwara 关注", "Iwaraのフォロー解除") if self._following else tr("Follow on Iwara", "在 Iwara 关注", "Iwaraでフォロー")
        )
        self.follow_btn.setToolTip(
            "" if usable or self._web_state == "loading"
            else tr("Sign in to Iwara in Settings to follow on the website.", "在设置中登录 Iwara 后才能在网站上关注。", "設定でIwaraにログインするとサイト上でフォローできます。")
        )
        self.open_btn.setVisible(self._show_open and subscribed)

    # ── actions ──────────────────────────────────────────────────────────────

    def _on_subscribe_clicked(self):
        if self._source is not None:
            self._unsubscribe()
            return
        try:
            source_id = int(download_manager.add_author_subscription(
                self._username, title=self._name, remote_id=self._user_id, avatar_url=self._avatar,
            ) or 0)
        except Exception as exc:
            self._toast(False, tr("Subscription failed", "订阅失败", "購読に失敗"), str(exc))
            return
        if source_id:
            signal_bus.subscription_source_added.emit(source_id)
            self.refresh_local()
            self._toast(
                True, tr("Author added", "作者已加入订阅", "作者を購読に追加しました"),
                tr(f"@{self._username} is now in your local subscriptions", f"@{self._username} 已加入本地订阅", f"@{self._username} をローカル購読に追加しました"),
            )

    def _unsubscribe(self):
        source_id = self.source_id
        if not source_id:
            return
        if not show_fluent_confirmation(
            self.window(),
            tr("Remove subscription", "取消订阅", "購読を解除"),
            tr(
                f"Remove @{self._username} from this app's subscriptions and delete its cached video list? "
                "Your Iwara follow is not affected.",
                f"确定把 @{self._username} 从程序内订阅中移除，并删除其缓存视频列表吗？不会影响你在 Iwara 上的关注。",
                f"@{self._username} をアプリの購読から外し、保存済み動画一覧を削除しますか？Iwaraのフォローには影響しません。",
            ),
        ):
            return
        try:
            download_manager.remove_subscription_source(source_id)
        except Exception as exc:
            self._toast(False, tr("Could not remove", "无法移除", "解除できません"), str(exc))
            return
        signal_bus.subscription_sources_changed.emit()
        self.refresh_local()

    def _open_subscription(self):
        if self.source_id:
            self.open_subscription_requested.emit(self.source_id)

    def _toggle_follow(self):
        if not self._user_id or self._web_state != "ready":
            return
        wanted = not self._following
        user_id, username, token = self._user_id, self._username, self._token
        self.follow_btn.setEnabled(False)
        worker = ApiCallWorker(lambda client: client.set_following(user_id, wanted), self)
        worker.done.connect(
            lambda result, error, wanted=wanted, token=token, user_id=user_id, username=username:
            self._on_follow_done(wanted, result, error, token=token, user_id=user_id, username=username)
        )
        worker.finished.connect(lambda worker=worker: self._discard(worker))
        self._workers.append(worker)
        worker.start()

    def _on_follow_done(
        self, wanted: bool, result: Any, error: str, *, token: int, user_id: str, username: str,
    ):
        ok, message = result if isinstance(result, tuple) and len(result) == 2 else (False, error)
        current = token == self._token and user_id == self._user_id
        if ok:
            if current:
                self._following = wanted
            self._toast(
                True,
                tr("Followed on Iwara", "已在 Iwara 关注", "Iwaraでフォローしました") if wanted else tr("Unfollowed on Iwara", "已取消 Iwara 关注", "Iwaraのフォローを解除しました"),
                f"@{username}",
            )
        else:
            self._toast(False, tr("Could not change the follow", "无法更改关注状态", "フォローを変更できません"), str(message or error))
        if current:
            self._sync()

    def _toast(self, ok: bool, title: str, content: str):
        (InfoBar.success if ok else InfoBar.error)(
            title=title, content=content, orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=3500 if ok else 5000, parent=self.window(),
        )

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return stop_workers(list(self._workers), timeout_ms)
