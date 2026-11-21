"""Decide from a failure message whether a failed task is worth retrying automatically.

Network hiccups (a broken connection, ``IncompleteRead``, a timeout, a 5xx)
usually succeed on the next try; a private video, a missing file or an
account without permission fails the same way every time.  The user can
override either side with their own keywords.
"""

from __future__ import annotations

import re

RETRY = "retry"
SKIP = "skip"

# The aria2 job's state was lost with the RPC connection: it may still be
# writing the file, so a new attempt could share it.  Never retried
# automatically, whatever status or wording the message carries.
_NEVER_AUTO_MARKERS = ("aria2 rpc query failed", "aria2 rpc 查询失败", "aria2 rpc 問い合わせ失敗")

# Checked before the transient markers: a permission error wrapped in a
# connection message is still a permission error.
_PERMANENT_MARKERS = (
    # permission / visibility
    "private", "privatevideo", "no permission", "not have permission", "without permission",
    "forbidden", "unauthorized", "not found", "not visible", "cannot view",
    "私有", "权限", "不可见", "无法观看", "无权", "不存在",
    "非公開", "権限", "表示できません", "存在しない",
    # not downloadable at all
    "youtube", "external embed", "外部嵌入", "埋め込み",
    "no downloadable", "没有返回可下载", "保存可能なファイルソースが返されません",
    "fileurl is empty", "fileurl 为空", "fileurl が空", "no downloadable fileurl",
    # login
    "please login", "请先登录", "ログインして",
    # local disk
    "no space left", "disk full", "磁盘空间", "空き容量",
    "permission denied", "access is denied", "拒绝访问", "アクセスが拒否",
)

_TRANSIENT_MARKERS = (
    "incompleteread", "incomplete read", "connection broken", "connection reset",
    "connection aborted", "connection refused", "connectionerror", "connection error",
    "remote end closed", "remotedisconnected", "chunkedencodingerror", "protocolerror",
    "read timed out", "timed out", "timeout", "max retries exceeded",
    "eof occurred", "sslerror", "ssl:", "temporarily unavailable", "temporary failure",
    "name resolution", "getaddrinfo", "proxyerror", "unable to connect to proxy",
    "too many requests", "bad gateway", "service unavailable", "gateway timeout",
    "incomplete download", "下载不完整", "ダウンロードが不完全",
    # the partial file was removed after a failed check; the retry starts over
    "retry to download again", "请重试以重新下载", "一時ファイルを削除しました", "一致しないため削除しました",
    "no activity", "无响应", "応答がない",
    "网络", "连接", "超时", "ネットワーク", "接続", "タイムアウト",
)


# An explicit HTTP status in the message: "HTTP 520: ...", "HTTP/1.1 503",
# "503 Server Error: ...", "status=502".  It decides before any keyword, so a
# 503 whose body mentions "private" is still retried and a 401 is not.
_HTTP_STATUS_RE = re.compile(
    r"\bhttp(?:/\d(?:\.\d)?)?\s+(\d{3})(?!\d)"
    r"|(?<![\w/])(\d{3})\s+(?:server|client) error\b"
    r"|(?:^|[\s(,;])status(?:_code)?\s*[=:]\s*(\d{3})(?!\d)"
)
_RETRY_STATUSES = frozenset({408, 425, 429})
_SKIP_STATUSES = frozenset({401, 403, 404, 410, 451})


def _http_status(text: str) -> int:
    match = _HTTP_STATUS_RE.search(text)
    if not match:
        return 0
    return int(next(group for group in match.groups() if group))


def split_keywords(text: str) -> list[str]:
    """Keywords separated by commas, semicolons or new lines; blanks dropped."""

    parts = re.split(r"[,，;；\n]+", str(text or ""))
    return [part.strip().casefold() for part in parts if part.strip()]


def classify_failure(reason: str) -> str:
    """``RETRY`` for a transient failure, ``SKIP`` for a lasting one, ``""`` when unknown."""

    text = str(reason or "").casefold()
    if not text:
        return ""
    if any(marker in text for marker in _NEVER_AUTO_MARKERS):
        return SKIP
    status = _http_status(text)
    if status >= 500 or status in _RETRY_STATUSES:
        return RETRY
    if status in _SKIP_STATUSES:
        return SKIP
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return SKIP
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return RETRY
    return ""


def should_auto_retry(reason: str, *, retry_keywords: str = "", skip_keywords: str = "") -> bool:
    """True when the failure should be retried automatically.

    A lost aria2 job is never retried.  Otherwise the user's "never retry"
    keywords win, then their "always retry" keywords, then the built-in
    classification.  Unknown failures are not
    retried, so an unexpected error cannot loop on its own.
    """

    text = str(reason or "").casefold()
    if any(marker in text for marker in _NEVER_AUTO_MARKERS):
        return False  # not even the user's keywords: aria2 may still be writing
    if any(keyword in text for keyword in split_keywords(skip_keywords)):
        return False
    if any(keyword in text for keyword in split_keywords(retry_keywords)):
        return True
    return classify_failure(reason) == RETRY
