"""Iwara sign-in shared by both interfaces: restore at start-up, complete a login, log out.

Blocking (the login is an HTTP request): call from a worker thread.  Each
step saves the state and announces it on ``signal_bus.login_state_changed``.
"""
from __future__ import annotations

from ..config import app_config
from ..i18n import tr
from ..signal_bus import signal_bus


def is_logged_in(manager) -> bool:
    return bool(getattr(manager.api, "token", None))


def saved_credentials() -> tuple[str, str] | None:
    if app_config.auth_enabled and app_config.username and app_config.password:
        return str(app_config.username), str(app_config.password)
    return None


def complete_login(manager, credential: str, password: str) -> None:
    """After ``api.login`` succeeded: keep the credentials and the token, tell everyone."""

    app_config.username = str(credential or "").strip()
    app_config.password = str(password or "")
    app_config.auth_enabled = True
    manager.set_login(True, manager.api.token)
    signal_bus.login_state_changed.emit(True)
    signal_bus.log_message.emit(
        tr("[Login] Success. Token cached", "[登录] 成功！已获取并缓存 Token", "[ログイン] 成功。Token を保存しました")
    )


def login(manager, credential: str, password: str) -> tuple[bool, str]:
    """Sign in with a user name or e-mail; on success the state is saved and announced."""

    manager.set_login_pending(True)
    try:
        ok, message = manager.api.login(credential, password)
        if ok:
            complete_login(manager, credential, password)
        else:
            signal_bus.log_message.emit(tr(f"[Login failed] {message}", f"[登录失败] {message}", f"[ログイン失敗] {message}"))
        return bool(ok), str(message or "")
    finally:
        manager.set_login_pending(False)


def logout(manager) -> None:
    manager.api.logout()
    manager.set_login(False)
    signal_bus.login_state_changed.emit(False)
    signal_bus.log_message.emit(tr("[Logout] Token cleared", "[退出登录] Token 已清除", "[ログアウト] Token を削除しました"))


def restore_login(manager) -> bool:
    """At start-up: the cached token if still valid, else a silent login with saved credentials."""

    if manager.restore_cached_login():
        signal_bus.login_state_changed.emit(True)
        return True
    credentials = saved_credentials()
    if credentials is None:
        return False
    ok, _message = login(manager, *credentials)
    return ok
