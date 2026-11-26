"""Who-is-this-author status: subscribed in the app? followed on Iwara?

A small bar (two chips and the buttons that change them) shared by the author
page, the subscription grid and the post detail page.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget

from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, PrimaryPushButton, PushButton

from ...core.download.manager import download_manager
from ...presentation.author import status as author_text
from ...presentation.author.status import clean_username, local_chip_state, web_chip_state  # noqa: F401  (re-exported)
from ...signal_bus import signal_bus
from ..common.chrome import StatusChip
from ..home.workers import ApiCallWorker, stop_workers
from ..common.flow_layout import ResponsiveFlowLayout
from ..common.fluent_dialogs import show_fluent_confirmation


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
        self.subscribe_btn = PrimaryPushButton(author_text.subscribe_button_text(False), self, FluentIcon.PEOPLE)
        self.subscribe_btn.clicked.connect(self._on_subscribe_clicked)
        self.follow_btn = PushButton(author_text.follow_button_text(False), self, FluentIcon.HEART)
        self.follow_btn.clicked.connect(self._toggle_follow)
        self.open_btn = PushButton(author_text.open_subscription_text(), self, FluentIcon.VIEW)
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
        user, problem = author_text.profile_follow(result, error)
        if user is None:
            self._web_state = "error"
            self.web_chip.setToolTip(problem)
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
            author_text.subscribe_button_text(subscribed)
        )
        self.subscribe_btn.setIcon(FluentIcon.REMOVE if subscribed else FluentIcon.PEOPLE)
        self.subscribe_btn.setEnabled(bool(self._username))
        usable = self._web_state in {"ready"} and bool(self._user_id)
        self.follow_btn.setEnabled(usable)
        self.follow_btn.setText(
            author_text.follow_button_text(self._following)
        )
        self.follow_btn.setToolTip(
            "" if usable or self._web_state == "loading"
            else author_text.follow_unavailable_tooltip()
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
            self._toast(False, author_text.subscribe_failed_title(), str(exc))
            return
        if source_id:
            signal_bus.subscription_source_added.emit(source_id)
            self.refresh_local()
            self._toast(True, *author_text.subscribed_notice(self._username))

    def _unsubscribe(self):
        source_id = self.source_id
        if not source_id:
            return
        if not show_fluent_confirmation(self.window(), *author_text.unsubscribe_confirmation(self._username)):
            return
        try:
            download_manager.remove_subscription_source(source_id)
        except Exception as exc:
            self._toast(False, author_text.remove_failed_title(), str(exc))
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
                author_text.follow_done_title(wanted),
                f"@{username}",
            )
        else:
            self._toast(False, author_text.follow_failed_title(), str(message or error))
        if current:
            self._sync()

    def _toast(self, ok: bool, title: str, content: str):
        (InfoBar.success if ok else InfoBar.error)(
            title=title, content=content, orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=3500 if ok else 5000, parent=self.window(),
        )

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return stop_workers(list(self._workers), timeout_ms)
