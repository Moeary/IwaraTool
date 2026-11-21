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

# Checked before the transient markers: a permission error wrapped in a
# connection message is still a permission error.
_PERMANENT_MARKERS = (
    # permission / visibility
    "private", "privatevideo", "no permission", "not have permission", "without permission",
    "forbidden", "http 403", "403 client error", "http 401", "401 client error", "unauthorized",
    "not found", "http 404", "404 client error", "not visible", "cannot view",
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
    "too many requests", "bad gateway", "service unavailable", "gateway timeout", "http 429",
    "incomplete download", "下载不完整", "ダウンロードが不完全",
    # the partial file was removed after a failed check; the retry starts over
    "retry to download again", "请重试以重新下载", "一時ファイルを削除しました", "一致しないため削除しました",
    "no activity", "无响应", "応答がない",
    "网络", "连接", "超时", "ネットワーク", "接続", "タイムアウト",
)


# Any 5xx reply: "HTTP 520: ...", "503 Server Error: ...", "status=502".
_SERVER_ERROR_RE = re.compile(r"\bhttp\s*5\d\d\b|\b5\d\d\s+server error|\bstatus(?:_code)?\s*[=:]?\s*5\d\d\b")


def split_keywords(text: str) -> list[str]:
    """Keywords separated by commas, semicolons or new lines; blanks dropped."""

    parts = re.split(r"[,，;；\n]+", str(text or ""))
    return [part.strip().casefold() for part in parts if part.strip()]


def classify_failure(reason: str) -> str:
    """``RETRY`` for a transient failure, ``SKIP`` for a lasting one, ``""`` when unknown."""

    text = str(reason or "").casefold()
    if not text:
        return ""
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return SKIP
    if any(marker in text for marker in _TRANSIENT_MARKERS) or _SERVER_ERROR_RE.search(text):
        return RETRY
    return ""


def should_auto_retry(reason: str, *, retry_keywords: str = "", skip_keywords: str = "") -> bool:
    """True when the failure should be retried automatically.

    The user's "never retry" keywords win, then their "always retry"
    keywords, then the built-in classification.  Unknown failures are not
    retried, so an unexpected error cannot loop on its own.
    """

    text = str(reason or "").casefold()
    if any(keyword in text for keyword in split_keywords(skip_keywords)):
        return False
    if any(keyword in text for keyword in split_keywords(retry_keywords)):
        return True
    return classify_failure(reason) == RETRY
