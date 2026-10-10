"""Turn failed requests and responses into short, translated error messages."""

from __future__ import annotations

from typing import Any

from ...i18n import tr


def _friendly_request_error(error: str) -> str:
    err = str(error or "").strip()
    lower = err.lower()
    if "ssleoferror" in lower or "unexpected_eof" in lower:
        return tr(
            f"Network/TLS connection was interrupted while fetching video info: {err}",
            f"获取作品信息时网络/TLS 连接被中断：{err}",
            f"動画情報の取得中にネットワーク/TLS 接続が中断されました: {err}",
        )
    if "403" in lower or "forbidden" in lower:
        return tr(
            "The current account has no permission to view this video.",
            "当前账号没有权限查看该作品。",
            "現在のアカウントにはこの動画を表示する権限がありません。",
        )
    if "timeout" in lower:
        return tr(
            f"Request timed out while fetching video info: {err}",
            f"获取作品信息超时：{err}",
            f"動画情報の取得がタイムアウトしました: {err}",
        )
    return err or tr("Unknown request error", "未知请求错误", "不明なリクエストエラー")


def _try_response_json(resp: Any, *, action: str) -> tuple[bool, Any, str]:
    try:
        return True, resp.json(), ""
    except ValueError:
        return False, None, _non_json_response_error(resp, action=action)


def _extract_api_message(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("message", "error", "detail"):
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value if item is not None)
        if isinstance(value, dict):
            nested = value.get("message") or value.get("error") or value.get("detail")
            if nested:
                return str(nested)
        if value:
            return str(value)
    return ""


def _non_json_response_error(resp: Any, *, action: str) -> str:
    status = getattr(resp, "status_code", "?")
    headers = getattr(resp, "headers", {}) or {}
    content_type = headers.get("content-type") or headers.get("Content-Type") or "unknown"
    preview = _response_preview(resp)
    return tr(
        f"{action} did not return JSON (HTTP {status}, Content-Type: {content_type}). "
        f"This usually means the request was blocked by the network/proxy/Cloudflare, "
        f"or the server returned an error page. Response preview: {preview}",
        f"{action}没有返回 JSON（HTTP {status}，Content-Type: {content_type}）。"
        f"通常是网络/代理/Cloudflare 拦截，或服务器返回了错误页面。响应预览：{preview}",
        f"{action}が JSON を返しませんでした（HTTP {status}, Content-Type: {content_type}）。"
        f"ネットワーク/プロキシ/Cloudflare にブロックされたか、サーバーがエラーページを返した可能性があります。応答プレビュー: {preview}",
    )


def _response_preview(resp: Any, limit: int = 240) -> str:
    try:
        text = str(getattr(resp, "text", "") or "")
    except Exception:
        return tr("<unable to read response>", "<无法读取响应>", "<応答を読み取れません>")
    text = " ".join(text.strip().split())
    if not text:
        return tr("<empty response>", "<空响应>", "<空の応答>")
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _friendly_http_response(resp: Any) -> str:
    status = int(getattr(resp, "status_code", 0) or 0)
    preview = _response_preview(resp)
    if status == 401:
        return tr(
            "HTTP 401: login is required or the token is invalid.",
            "HTTP 401：需要登录，或当前 token 已失效。",
            "HTTP 401: ログインが必要、または token が無効です。",
        )
    if status == 403:
        return tr(
            "HTTP 403: request was forbidden. Try enabling/changing proxy, or login again.",
            "HTTP 403：请求被拒绝。请尝试开启/更换代理，或重新登录。",
            "HTTP 403: リクエストが拒否されました。プロキシの有効化/変更、または再ログインを試してください。",
        )
    if status == 429:
        return tr(
            "HTTP 429: too many requests. Please wait and try again, or change proxy/IP.",
            "HTTP 429：请求过于频繁。请稍后重试，或更换代理/IP。",
            "HTTP 429: リクエストが多すぎます。しばらく待つか、プロキシ/IP を変更してください。",
        )
    if 500 <= status < 600:
        return tr(
            f"HTTP {status}: Iwara server error. Please try again later. Response preview: {preview}",
            f"HTTP {status}：Iwara 服务器错误，请稍后再试。响应预览：{preview}",
            f"HTTP {status}: Iwara サーバーエラーです。後ほど再試行してください。応答プレビュー: {preview}",
        )
    return tr(
        f"HTTP {status}: request failed. Response preview: {preview}",
        f"HTTP {status}：请求失败。响应预览：{preview}",
        f"HTTP {status}: リクエストに失敗しました。応答プレビュー: {preview}",
    )


def _friendly_login_failure(message: str, status_code: int) -> str:
    raw = str(message or "").strip()
    compact = raw.lower()
    if raw == "errors.invalidLogin" or "invalidlogin" in compact:
        return tr(
            "Invalid username/email or password. Please check the account credentials.",
            "账号或密码错误，请检查用户名/邮箱和密码。",
            "ユーザー名/メールまたはパスワードが正しくありません。",
        )
    if raw == "errors.tooManyRequests" or "toomanyrequests" in compact or status_code == 429:
        return tr(
            "Too many login attempts. Please wait and try again, or change proxy/IP.",
            "登录请求过于频繁，请稍后重试，或更换代理/IP。",
            "ログイン試行が多すぎます。しばらく待つか、プロキシ/IP を変更してください。",
        )
    if status_code in (401, 403):
        return tr(
            f"Login was rejected by the server (HTTP {status_code}). Try enabling/changing proxy, then login again.",
            f"登录请求被服务器拒绝（HTTP {status_code}）。请尝试开启/更换代理后重新登录。",
            f"ログインリクエストがサーバーに拒否されました（HTTP {status_code}）。プロキシの有効化/変更後、再ログインしてください。",
        )
    if raw:
        return tr(
            f"Login failed (HTTP {status_code}): {raw}",
            f"登录失败（HTTP {status_code}）：{raw}",
            f"ログイン失敗（HTTP {status_code}）: {raw}",
        )
    return tr(
        f"Login failed (HTTP {status_code}).",
        f"登录失败（HTTP {status_code}）。",
        f"ログイン失敗（HTTP {status_code}）。",
    )


def _friendly_network_error(error: str, *, action: str) -> str:
    err = str(error or "").strip()
    lower = err.lower()
    if "expecting value" in lower:
        return tr(
            f"{action} failed because the server did not return JSON. Please check proxy/network settings and try again.",
            f"{action}失败：服务器没有返回 JSON。请检查代理/网络设置后重试。",
            f"{action}に失敗しました: サーバーが JSON を返しませんでした。プロキシ/ネットワーク設定を確認して再試行してください。",
        )
    if "timeout" in lower:
        return tr(
            f"{action} timed out. Please check the network/proxy and try again.",
            f"{action}超时，请检查网络/代理后重试。",
            f"{action}がタイムアウトしました。ネットワーク/プロキシを確認して再試行してください。",
        )
    if any(token in lower for token in ("proxyerror", "connection", "ssleoferror", "unexpected_eof", "tls")):
        return tr(
            f"{action} network connection failed: {err}",
            f"{action}网络连接失败：{err}",
            f"{action}のネットワーク接続に失敗しました: {err}",
        )
    return err or tr(
        f"{action} failed with an unknown error.",
        f"{action}失败，原因未知。",
        f"{action}に失敗しました。不明なエラーです。",
    )
