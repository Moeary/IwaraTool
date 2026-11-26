"""Who-is-this-author status: subscribed in the app? followed on Iwara? (both interfaces)"""
from __future__ import annotations

from typing import Any

from ...i18n import tr

WEB_SIGNED_OUT, WEB_LOADING, WEB_READY, WEB_ERROR = "signed_out", "loading", "ready", "error"


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
    """``(text, tone)`` for the Iwara-account follow state (``signed_out`` / ``loading`` / ``ready`` / ``error``)."""

    if state == WEB_SIGNED_OUT:
        return tr("Iwara: not signed in", "Iwara：未登录", "Iwara: 未ログイン"), "neutral"
    if state == WEB_LOADING:
        return tr("Checking Iwara…", "正在查询 Iwara…", "Iwaraを確認中…"), "neutral"
    if state == WEB_ERROR:
        return tr("Iwara follow status unavailable", "无法获取 Iwara 关注状态", "Iwaraのフォロー状態を取得できません"), "warning"
    if following:
        return tr("Followed on Iwara", "已在 Iwara 关注", "Iwaraでフォロー中"), "accent"
    return tr("Not followed on Iwara", "未在 Iwara 关注", "Iwaraでは未フォロー"), "neutral"


def profile_follow(result: Any, error: str = "") -> tuple[dict[str, Any] | None, str]:
    """``(user, error)`` from a ``get_user_profile`` answer (``(data, error)`` or an exception text)."""

    data, api_error = result if isinstance(result, tuple) and len(result) == 2 else (None, error)
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return None, str(api_error or error)
    return user, ""


def subscribe_button_text(subscribed: bool) -> str:
    if subscribed:
        return tr("Remove app subscription", "取消程序内订阅", "アプリの購読を解除")
    return tr("Subscribe in app", "在程序内订阅", "アプリで購読")


def follow_button_text(following: bool) -> str:
    return tr("Unfollow on Iwara", "取消 Iwara 关注", "Iwaraのフォロー解除") if following else tr("Follow on Iwara", "在 Iwara 关注", "Iwaraでフォロー")


def follow_unavailable_tooltip() -> str:
    return tr("Sign in to Iwara in Settings to follow on the website.", "在设置中登录 Iwara 后才能在网站上关注。",
              "設定でIwaraにログインするとサイト上でフォローできます。")


def open_subscription_text() -> str:
    return tr("Open subscription", "打开订阅", "購読を開く")


def subscribe_failed_title() -> str:
    return tr("Subscription failed", "订阅失败", "購読に失敗")


def subscribed_notice(username: str) -> tuple[str, str]:
    return tr("Author added", "作者已加入订阅", "作者を購読に追加しました"), tr(
        f"@{username} is now in your local subscriptions", f"@{username} 已加入本地订阅", f"@{username} をローカル購読に追加しました")


def unsubscribe_confirmation(username: str) -> tuple[str, str]:
    return tr("Remove subscription", "取消订阅", "購読を解除"), tr(
        f"Remove @{username} from this app's subscriptions and delete its cached video list? "
        "Your Iwara follow is not affected.",
        f"确定把 @{username} 从程序内订阅中移除，并删除其缓存视频列表吗？不会影响你在 Iwara 上的关注。",
        f"@{username} をアプリの購読から外し、保存済み動画一覧を削除しますか？Iwaraのフォローには影響しません。",
    )


def remove_failed_title() -> str:
    return tr("Could not remove", "无法移除", "解除できません")


def follow_done_title(wanted: bool) -> str:
    return tr("Followed on Iwara", "已在 Iwara 关注", "Iwaraでフォローしました") if wanted else tr(
        "Unfollowed on Iwara", "已取消 Iwara 关注", "Iwaraのフォローを解除しました")


def follow_failed_title() -> str:
    return tr("Could not change the follow", "无法更改关注状态", "フォローを変更できません")
